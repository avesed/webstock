"""Layer 1 three-agent batch scoring service for the news pipeline.

Evaluates articles on a 0-300 point scale (3 agents x 0-100 each) using
a tier-first rubric methodology. Each agent assesses investment importance
from a distinct perspective (macro, market, signal quality), classifying
articles into a tier before assigning a numeric score within that tier's
range.

Routing decisions:
- ``discard``:       total_score < layer1_discard_threshold (default 105)
- ``lightweight``:   total_score < layer1_full_analysis_threshold (default 195)
- ``full_analysis``: total_score >= layer1_full_analysis_threshold

Critical event keywords bypass LLM scoring entirely and route directly
to ``full_analysis`` with an automatic 300-point score.

Each agent uses ``tool_choice`` with a single ``submit_score`` tool
that accepts a ``results`` array containing all articles' scores in one
call.  This eliminates the ``response_format`` compatibility issue and
guarantees structured output via the tool-call protocol.

Prompt cache strategy:
    tools  (submit_score definition — identical for all 3 agents)
    SYSTEM (scoring framework + all 3 rubrics ~800 tokens)  [cache_control]
    USER   (numbered article batch ~3000 tokens)            [cache_control]
    USER   (agent-specific perspective prompt)               [no cache]

Agents 2 and 3 hit the prompt cache on the shared prefix (tools +
SYSTEM + batch), reducing token costs by ~60-70%.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
from app.utils.critical_event_detection import detect_critical_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum characters per article in the batch prompt. Titles and text are
# truncated independently before formatting.
_MAX_TEXT_LENGTH = 3000

# Default score assigned to an agent on failure (fail-open).
_DEFAULT_AGENT_SCORE = 50

# Default routing thresholds (overridden by system_settings when available).
_DEFAULT_DISCARD_THRESHOLD = 105
_DEFAULT_FULL_ANALYSIS_THRESHOLD = 195


# ---------------------------------------------------------------------------
# Prompts (Chinese-oriented)
# ---------------------------------------------------------------------------

# Shared SYSTEM prompt: scoring framework, tier-first methodology, and all
# three rubric definitions.  Approximately 800 tokens.  Marked with
# cache_control so that agents 2 and 3 can reuse the KV cache.
_SYSTEM_PROMPT = """\
你是专业的金融新闻投资价值评估系统。你需要对一批新闻进行投资重要性评分。

## 评分方法论：层级优先法（Tier-First Scoring）

1. 先将文章归入一个层级（tier）
2. 再在该层级的分数范围内给出具体分数
3. 必须同时返回层级名称和分数

## 输出方式

调用一次 `submit_score` 工具，在 `results` 数组中包含所有文章的评分。
不要遗漏任何一篇文章。

**注意**：
- index 为文章编号（从1开始）
- score 必须在对应 tier 的分数范围内
- reason 必须简洁，不超过20字

---

## 三个评估视角及层级定义

### 视角A：宏观视角（macro_agent）
从宏观经济和政策角度评估新闻对投资者的重要性。
| 层级 | 分数范围 | 标准 |
|------|---------|------|
| 极端 | 90-100 | 全球系统性事件（战争、央行紧急行动、主权违约、全球大流行） |
| 重大 | 70-89 | 重大宏观政策变动（利率决议非预期、重大制裁、关键数据大幅偏离预期） |
| 重要 | 50-69 | 常规重要宏观信息（定期经济数据符合预期、政策官员讲话、国际峰会） |
| 一般 | 30-49 | 有宏观背景但非核心（地区经济新闻、二级国家政策、行业监管微调） |
| 边缘 | 10-29 | 微弱宏观关联（个股新闻附带宏观评论、市场综述） |
| 无关 | 0-9 | 无宏观关联（纯个股/产品/娱乐） |

### 视角B：市场视角（market_agent）
从市场交易和资本运作角度评估新闻对投资者的重要性。
| 层级 | 分数范围 | 标准 |
|------|---------|------|
| 极端 | 90-100 | 全市场级冲击（触发熔断、大型蓝筹破产、市值TOP10重大事件） |
| 重大 | 70-89 | 显著板块级影响（龙头财报大幅超/低预期、重大并购、行业颠覆） |
| 重要 | 50-69 | 明确个股/板块影响（普通财报、评级调整、中等资本运作） |
| 一般 | 30-49 | 影响有限（次要公司动态、常规行业报告、小规模交易） |
| 边缘 | 10-29 | 无明确交易信号（泛泛市场评论、无具体标的建议） |
| 无关 | 0-9 | 无市场关联（纯技术/娱乐/社会新闻） |

### 视角C：信息质量视角（signal_agent）
从信息源质量和可操作性角度评估投资价值。
| 层级 | 分数范围 | 标准 |
|------|---------|------|
| 极高 | 90-100 | 独家突发+高度可操作（首发重大消息、实时数据披露） |
| 高 | 70-89 | 高质量一手信息（深度调查、独家采访、研报首发） |
| 中等 | 50-69 | 有价值信息（及时综合报道、有新数据点、专业解读） |
| 一般 | 30-49 | 部分参考价值（综合转载有补充、新闻通稿、会议纪要） |
| 低 | 10-29 | 信息量少（纯转载、翻炒旧闻、标题党） |
| 噪音 | 0-9 | 广告/水文/无信息量（软文、推广、完全过时信息） |
"""

# Per-agent perspective prompts.  Appended as a second USER message.
_AGENT_PROMPTS: Dict[str, str] = {
    "macro": (
        "请从【宏观视角（视角A）】评估以上每一篇新闻的投资重要性。"
        "使用宏观视角的层级定义，调用 `submit_score` 工具提交所有文章的评分。"
    ),
    "market": (
        "请从【市场视角（视角B）】评估以上每一篇新闻的投资重要性。"
        "使用市场视角的层级定义，调用 `submit_score` 工具提交所有文章的评分。"
    ),
    "signal": (
        "请从【信息质量视角（视角C）】评估以上每一篇新闻的投资价值。"
        "使用信息质量视角的层级定义，调用 `submit_score` 工具提交所有文章的评分。"
    ),
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AgentScore:
    """Scoring result from a single agent for a single article."""

    agent: str   # "macro", "market", or "signal"
    tier: str    # Tier name from the rubric (e.g., "重大", "边缘")
    score: int   # 0-100
    reason: str  # Brief justification


@dataclass
class Layer1ScoringResult:
    """Aggregated scoring result for a single article across all 3 agents."""

    url: str
    total_score: int                        # 0-300 (sum of 3 agents)
    agent_scores: Dict[str, AgentScore]     # Keyed by agent name
    routing_decision: str                   # "discard", "lightweight", "full_analysis"
    is_critical: bool
    reasoning: str
    raw_responses: Dict[str, str] = field(default_factory=dict)


@dataclass
class BatchScoringOutcome:
    """Result of batch_score_articles with partial success support.

    When a batch times out, its articles move to ``timed_out_articles``
    instead of receiving default scores.  The caller decides whether to
    retry them via a Celery task or fail-open to lightweight.
    """

    results: List[Layer1ScoringResult]          # Successfully scored
    timed_out_articles: List[Dict[str, str]]    # Article dicts whose batch timed out


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class Layer1ScoringService:
    """Three-agent batch scoring service for Layer 1 of the news pipeline.

    Three agents (macro, market, signal) evaluate every article in a batch
    concurrently via ``asyncio.gather()``.  Each agent scores 0-100 using
    a tier-first rubric.  The total score (0-300) determines routing:

    - ``discard``:       below ``layer1_discard_threshold`` (default 105)
    - ``lightweight``:   below ``layer1_full_analysis_threshold`` (default 195)
    - ``full_analysis``: at or above full-analysis threshold

    A prompt-cache-friendly message layout is used: the SYSTEM and batch
    USER messages carry ``cache_control`` hints so that agents 2 and 3
    can reuse the cached prefix from agent 1's request.
    """

    # Agent names in execution order.
    AGENT_NAMES = ("macro", "market", "signal")

    # ------------------------------------------------------------------
    # Threshold resolution
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_thresholds(db: AsyncSession) -> Tuple[int, int]:
        """Read scoring thresholds from system_settings.

        Returns:
            Tuple of (discard_threshold, full_analysis_threshold).
            Falls back to defaults on any error.
        """
        try:
            from app.services.settings_service import get_settings_service

            settings_service = get_settings_service()
            system = await settings_service.get_system_settings(db)
            discard = getattr(system, "layer1_discard_threshold", _DEFAULT_DISCARD_THRESHOLD) or _DEFAULT_DISCARD_THRESHOLD
            full_analysis = getattr(system, "layer1_full_analysis_threshold", _DEFAULT_FULL_ANALYSIS_THRESHOLD) or _DEFAULT_FULL_ANALYSIS_THRESHOLD
            return discard, full_analysis
        except Exception as e:
            logger.warning(
                "Failed to read Layer 1 thresholds, using defaults: %s", e,
            )
            return _DEFAULT_DISCARD_THRESHOLD, _DEFAULT_FULL_ANALYSIS_THRESHOLD

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    @staticmethod
    async def _resolve_model(db: AsyncSession):
        """Resolve the LLM model configuration for Layer 1 scoring.

        Uses the ``layer1_scoring`` purpose.  Falls back to ``news_filter``
        if ``layer1_scoring`` is not configured, to allow gradual migration.

        Returns:
            ``ResolvedModelConfig`` with model name, API key, and base URL.
        """
        from app.services.settings_service import get_settings_service

        settings_service = get_settings_service()
        try:
            return await settings_service.resolve_model_provider(db, "layer1_scoring")
        except (ValueError, AttributeError):
            logger.info(
                "layer1_scoring model not configured, falling back to news_filter",
            )
            return await settings_service.resolve_model_provider(db, "news_filter")

    # ------------------------------------------------------------------
    # Batch formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_batch_text(articles: List[Dict[str, str]]) -> str:
        """Format a batch of articles into a numbered text block.

        Each article is rendered as::

            [1] 标题
            摘要（truncated to _MAX_TEXT_LENGTH chars）

        Args:
            articles: List of dicts with ``title`` and ``text`` keys.

        Returns:
            Formatted string ready for insertion into the USER message.
        """
        parts: List[str] = []
        for idx, article in enumerate(articles, start=1):
            title = (article.get("title") or "")[:200]
            text = (article.get("text") or "")[:_MAX_TEXT_LENGTH]
            parts.append(f"[{idx}] {title}\n{text}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Single-agent LLM call
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        agent_name: str,
        base_messages: List[Message],
        model_config,
        batch_size: int,
        tools: List,
    ) -> Tuple[str, Dict[str, Any], str]:
        """Run a single scoring agent against the shared batch.

        The agent calls ``submit_score`` once with a ``results`` array
        containing all articles' scores.  Results are extracted into a
        dict keyed by article index (str, 1-based).

        Args:
            agent_name: One of ``"macro"``, ``"market"``, ``"signal"``.
            base_messages: Shared SYSTEM + batch USER messages (with cache hints).
            model_config: Resolved model configuration.
            batch_size: Number of articles in the batch (for validation).
            tools: Shared tool definitions list (for prompt cache).

        Returns:
            Tuple of (agent_name, parsed dict, raw debug string).
            On LLM or parse error, the dict is empty.
        """
        agent_prompt = _AGENT_PROMPTS[agent_name]

        messages = list(base_messages) + [
            Message(role=Role.USER, content=agent_prompt),
        ]

        gateway = get_llm_gateway()
        chat_request = ChatRequest(
            model=model_config.model,
            messages=messages,
            tools=tools,
            tool_choice={
                "type": "function",
                "function": {"name": "submit_score"},
            },
            temperature=0.2,
            timeout=60,
        )

        t0 = time.monotonic()
        try:
            response = await gateway.chat(
                chat_request,
                system_api_key=model_config.api_key,
                system_base_url=model_config.base_url,
                use_user_config=False,
                purpose="layer1_scoring",
                usage_metadata={"agent": agent_name, "batch_size": batch_size},
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

            # Track token usage (non-fatal)
            if response.usage:
                try:
                    from app.services.filter_stats_service import get_filter_stats_service

                    stats_service = get_filter_stats_service()
                    await stats_service.track_tokens(
                        stage=f"layer1_{agent_name}",
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                except Exception:
                    logger.debug(
                        "Token tracking failed for layer1_%s", agent_name, exc_info=True,
                    )

            # Extract results array from the single tool call
            parsed: Dict[str, Any] = {}
            raw_parts: List[str] = []

            if response.tool_calls:
                tc = response.tool_calls[0]
                try:
                    args = json.loads(tc.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}

                results_list = args.get("results", [])
                if isinstance(results_list, list):
                    for item in results_list:
                        if not isinstance(item, dict):
                            continue
                        idx = str(item.get("index", ""))
                        if idx:
                            parsed[idx] = item
                            raw_parts.append(f"[{idx}] {item}")

            raw = "; ".join(raw_parts)[:5000]

            logger.info(
                "[Layer1/%s] LLM call completed, elapsed=%.0fms, "
                "articles=%d, results_count=%d",
                agent_name, elapsed_ms, batch_size, len(parsed),
            )

            return agent_name, parsed, raw

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "[Layer1/%s] LLM call failed (%.0fms): %s",
                agent_name, elapsed_ms, e,
            )
            return agent_name, {}, ""

    # ------------------------------------------------------------------
    # Score extraction & validation
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_agent_score(
        agent_name: str,
        parsed: Dict[str, Any],
        article_idx: int,
    ) -> AgentScore:
        """Extract and validate one agent's score for one article.

        Args:
            agent_name: Agent identifier.
            parsed: Full parsed JSON from the agent's response.
            article_idx: 1-based article index in the batch.

        Returns:
            ``AgentScore`` with validated fields.  Defaults to score 50
            ("fail-open") on any extraction or validation error.
        """
        key = str(article_idx)
        item = parsed.get(key)
        if not isinstance(item, dict):
            return AgentScore(
                agent=agent_name,
                tier="unknown",
                score=_DEFAULT_AGENT_SCORE,
                reason="parse_missing",
            )

        tier = str(item.get("tier", "unknown"))[:20]

        try:
            score = int(item.get("score", _DEFAULT_AGENT_SCORE))
            score = max(0, min(score, 100))
        except (ValueError, TypeError):
            score = _DEFAULT_AGENT_SCORE

        reason = str(item.get("reason", ""))[:100]

        return AgentScore(
            agent=agent_name,
            tier=tier,
            score=score,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Stats tracking
    # ------------------------------------------------------------------

    @staticmethod
    async def _track_routing_stats(results: List[Layer1ScoringResult]) -> None:
        """Increment Redis counters for routing decisions (non-fatal).

        Args:
            results: List of scoring results for the batch.
        """
        try:
            from app.services.filter_stats_service import get_filter_stats_service

            stats = get_filter_stats_service()
            for result in results:
                if result.routing_decision == "discard":
                    await stats.increment("layer1_discard")
                elif result.routing_decision == "lightweight":
                    await stats.increment("layer1_lightweight")
                elif result.routing_decision == "full_analysis":
                    await stats.increment("layer1_full_analysis")
                if result.is_critical:
                    await stats.increment("layer1_critical_event")
        except Exception:
            logger.debug("Layer1 routing stats tracking failed", exc_info=True)

    # ------------------------------------------------------------------
    # Single-batch scoring
    # ------------------------------------------------------------------

    async def _score_batch(
        self,
        db: AsyncSession,
        articles: List[Dict[str, str]],
        discard_threshold: int,
        full_analysis_threshold: int,
    ) -> List[Layer1ScoringResult]:
        """Score a single batch of articles with 3 concurrent agents.

        Args:
            db: Database session for model config resolution.
            articles: Batch of articles (each has ``url``, ``title``, ``text``).
            discard_threshold: Score below which articles are discarded.
            full_analysis_threshold: Score at or above which articles get full analysis.

        Returns:
            List of ``Layer1ScoringResult``, one per article (in input order).
        """
        t0 = time.monotonic()
        batch_size = len(articles)

        # --- 1. Critical event fast path ---
        # Check each article for critical keywords. Those that match skip
        # LLM scoring entirely.
        critical_flags: List[bool] = []
        for article in articles:
            title = article.get("title", "")
            text = article.get("text", "")
            critical_flags.append(detect_critical_event(title, text))

        # If ALL articles are critical, skip LLM entirely.
        non_critical_indices = [
            i for i, is_crit in enumerate(critical_flags) if not is_crit
        ]

        # --- 2. Build shared messages for non-critical articles ---
        agent_results_map: Dict[str, Dict[str, Any]] = {}
        agent_raw_map: Dict[str, str] = {}

        if non_critical_indices:
            # Only include non-critical articles in the LLM batch to save tokens.
            # We need to map between original indices and batch indices.
            batch_articles = [articles[i] for i in non_critical_indices]
            batch_text = self._format_batch_text(batch_articles)

            base_messages = [
                Message(
                    role=Role.SYSTEM,
                    content=_SYSTEM_PROMPT,
                    cache_control={"type": "ephemeral"},
                ),
                Message(
                    role=Role.USER,
                    content=f"以下是待评估的 {len(batch_articles)} 篇新闻：\n\n{batch_text}",
                    cache_control={"type": "ephemeral"},
                ),
            ]

            # --- 3. Resolve model + build tools ---
            model_config = await self._resolve_model(db)

            from app.skills.chat_adapter import skill_to_tool_definition
            from app.skills.news.submit_score import SubmitScoreSkill

            tools = [skill_to_tool_definition(SubmitScoreSkill())]

            # --- 4. Run 3 agents concurrently ---
            tasks = [
                self._run_agent(
                    name, base_messages, model_config,
                    len(batch_articles), tools,
                )
                for name in self.AGENT_NAMES
            ]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

            failed_agents: List[str] = []
            for item in raw_results:
                if isinstance(item, Exception):
                    logger.error("[Layer1] Agent task raised exception: %s", item)
                    failed_agents.append(str(item))
                    continue
                agent_name, parsed, raw = item
                agent_results_map[agent_name] = parsed
                agent_raw_map[agent_name] = raw

            # Check agent completeness — log missing agents for observability
            missing_agents = [
                name for name in self.AGENT_NAMES
                if name not in agent_results_map
            ]
            if missing_agents:
                logger.warning(
                    "[Layer1] %d/%d agents failed (%s), scores will use "
                    "fail-open defaults for missing agents",
                    len(missing_agents), len(self.AGENT_NAMES),
                    ", ".join(missing_agents),
                )

        # --- 5. Assemble per-article results ---
        results: List[Layer1ScoringResult] = []

        # Mapping: for non-critical articles, their position in the LLM batch
        # (1-based index used in the JSON keys) differs from their position
        # in the original articles list.
        llm_batch_idx = 0  # 0-based counter into the LLM batch

        for orig_idx, article in enumerate(articles):
            url = article.get("url", "")
            title = article.get("title", "")

            if critical_flags[orig_idx]:
                # Critical event fast path
                agent_scores = {}
                for name in self.AGENT_NAMES:
                    agent_scores[name] = AgentScore(
                        agent=name,
                        tier="critical_event",
                        score=100,
                        reason="关键事件自动满分",
                    )
                results.append(Layer1ScoringResult(
                    url=url,
                    total_score=300,
                    agent_scores=agent_scores,
                    routing_decision="full_analysis",
                    is_critical=True,
                    reasoning=f"关键事件关键词命中: {title[:60]}",
                    raw_responses={},
                ))
            else:
                # LLM-scored article.  The article's index in the LLM batch
                # is llm_batch_idx (0-based); the JSON key is 1-based.
                llm_batch_idx += 1
                article_key_idx = llm_batch_idx  # 1-based

                agent_scores: Dict[str, AgentScore] = {}
                for name in self.AGENT_NAMES:
                    parsed = agent_results_map.get(name, {})
                    agent_scores[name] = self._extract_agent_score(
                        name, parsed, article_key_idx,
                    )

                total_score = sum(s.score for s in agent_scores.values())

                # Routing decision
                if total_score < discard_threshold:
                    routing = "discard"
                elif total_score < full_analysis_threshold:
                    routing = "lightweight"
                else:
                    routing = "full_analysis"

                reasoning_parts = [
                    f"{s.agent}={s.score}({s.tier})" for s in agent_scores.values()
                ]
                reasoning = f"total={total_score}, {', '.join(reasoning_parts)}"
                if missing_agents:
                    reasoning += f" [failOpen: {','.join(missing_agents)}]"

                results.append(Layer1ScoringResult(
                    url=url,
                    total_score=total_score,
                    agent_scores=agent_scores,
                    routing_decision=routing,
                    is_critical=False,
                    reasoning=reasoning,
                    raw_responses={
                        name: agent_raw_map.get(name, "")
                        for name in self.AGENT_NAMES
                    },
                ))

        elapsed_ms = (time.monotonic() - t0) * 1000
        critical_count = sum(1 for f in critical_flags if f)
        routing_counts = {}
        for r in results:
            routing_counts[r.routing_decision] = routing_counts.get(r.routing_decision, 0) + 1

        logger.info(
            "[Layer1] Batch scored %d articles (%.0fms): "
            "critical=%d, routing=%s, thresholds=(%d/%d)",
            batch_size, elapsed_ms, critical_count, routing_counts,
            discard_threshold, full_analysis_threshold,
        )

        return results

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def batch_score_articles(
        self,
        db: AsyncSession,
        articles: List[Dict[str, str]],
        batch_size: int = 20,
        batch_timeout: int = 90,
    ) -> BatchScoringOutcome:
        """Score a list of articles and determine routing decisions.

        This is the main entry point for Layer 1 scoring.  Articles are
        processed in batches of ``batch_size`` to keep LLM context windows
        manageable.  Each batch has a per-batch timeout; timed-out batches
        are returned in ``BatchScoringOutcome.timed_out_articles`` for the
        caller to retry via a Celery task.

        On service-level failure (e.g., unable to resolve model config),
        all articles are returned as timed-out so the caller can decide
        whether to retry or fail-open.

        Args:
            db: Async database session.
            articles: List of dicts, each containing:
                - ``url``: Article URL (used as identifier).
                - ``title``: Article title.
                - ``text``: Article summary or full text (will be truncated
                  to ``_MAX_TEXT_LENGTH`` characters per article).
            batch_size: Maximum number of articles per LLM call.
                Defaults to 20.
            batch_timeout: Per-batch timeout in seconds.  Defaults to 90.

        Returns:
            ``BatchScoringOutcome`` with successfully scored results and
            any timed-out article dicts.
        """
        if not articles:
            return BatchScoringOutcome(results=[], timed_out_articles=[])

        t0 = time.monotonic()

        try:
            # Read thresholds once for the entire scoring run.
            discard_threshold, full_analysis_threshold = await self._get_thresholds(db)

            all_results: List[Layer1ScoringResult] = []
            all_timed_out: List[Dict[str, str]] = []

            # Process in batches with per-batch timeout
            for i in range(0, len(articles), batch_size):
                batch = articles[i : i + batch_size]
                batch_num = i // batch_size + 1
                try:
                    batch_results = await asyncio.wait_for(
                        self._score_batch(
                            db, batch, discard_threshold, full_analysis_threshold,
                        ),
                        timeout=batch_timeout,
                    )
                    all_results.extend(batch_results)
                except asyncio.TimeoutError:
                    logger.warning(
                        "[Layer1] Batch %d timed out after %ds (%d articles)",
                        batch_num, batch_timeout, len(batch),
                    )
                    all_timed_out.extend(batch)

            # Track routing stats (non-fatal)
            if all_results:
                await self._track_routing_stats(all_results)

            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "[Layer1] Total scoring complete: %d articles in %.0fms "
                "(scored=%d, timed_out=%d)",
                len(articles), elapsed_ms,
                len(all_results), len(all_timed_out),
            )

            return BatchScoringOutcome(
                results=all_results,
                timed_out_articles=all_timed_out,
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "[Layer1] Service-level failure (%.0fms): %s. "
                "All %d articles marked as timed-out for caller retry.",
                elapsed_ms, e, len(articles),
            )

            # Service failure: return all articles as timed-out so the
            # caller can decide whether to retry or fail-open.
            return BatchScoringOutcome(
                results=[],
                timed_out_articles=list(articles),
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_service: Optional[Layer1ScoringService] = None


def get_layer1_scoring_service() -> Layer1ScoringService:
    """Get singleton instance of Layer1ScoringService."""
    global _service
    if _service is None:
        _service = Layer1ScoringService()
    return _service
