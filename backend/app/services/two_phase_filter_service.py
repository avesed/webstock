"""Two-phase news filtering service for improved quality and cost optimization.

This service implements a two-stage filtering pipeline:
1. Initial Filter: Based on title + summary, classifies as USEFUL/UNCERTAIN/SKIP
2. Deep Filter: Based on full text, extracts entities, tags, summary, and makes final KEEP/DELETE decision
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
from app.models.news import FilterStatus

logger = logging.getLogger(__name__)


class InitialFilterResult(TypedDict):
    """Result of initial (title + summary) filtering."""

    url: str
    decision: Literal["useful", "uncertain", "skip"]
    reason: str


class EntityInfo(TypedDict):
    """Extracted entity with relevance score."""

    entity: str
    type: Literal["stock", "index", "macro"]
    score: float


class DeepFilterResult(TypedDict):
    """Result of deep (full text) filtering."""

    decision: Literal["keep", "delete"]
    entities: List[EntityInfo]
    industry_tags: List[str]
    event_tags: List[str]
    sentiment: Literal["bullish", "bearish", "neutral"]
    investment_summary: str


@dataclass
class NewsLLMSettings:
    """LLM settings for news filtering."""

    api_key: str
    base_url: Optional[str]
    model: str


@dataclass
class FilterAgent:
    """Configuration for one initial filter agent."""

    name: str
    prompt_template: str
    model: Optional[str] = None  # None = use default news_filter model


async def get_news_llm_settings(db: AsyncSession) -> NewsLLMSettings:
    """
    Get LLM settings for news filtering via unified provider system.

    Uses resolve_model_provider() with "news_filter" purpose.

    Args:
        db: Database session

    Returns:
        NewsLLMSettings with API key, base URL, and model
    """
    from app.services.settings_service import get_settings_service

    settings_service = get_settings_service()
    try:
        resolved = await settings_service.resolve_model_provider(db, "news_filter")
    except ValueError as e:
        raise ValueError(
            "No API key configured for news filtering. "
            "Please configure a news_filter model assignment in Admin Settings."
        ) from e

    if not resolved.api_key:
        raise ValueError(
            "No API key configured for news filtering. "
            "Please configure it in Admin Settings."
        )

    return NewsLLMSettings(
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        model=resolved.model or "gpt-4o-mini",
    )


def extract_json_from_response(text: str) -> Dict[str, Any]:
    """
    Extract JSON from LLM response, handling markdown code blocks.

    Args:
        text: Raw LLM response text

    Returns:
        Parsed JSON as dict
    """
    # Remove markdown code blocks if present
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        # Remove closing fence
        text = re.sub(r"\n?```\s*$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON: {e}, text: {text[:200]}")
        return {}


def validate_entities(entities: List[Any]) -> List[EntityInfo]:
    """
    Validate and normalize entity list from LLM response.

    Args:
        entities: Raw entity list from LLM

    Returns:
        Validated list of EntityInfo dicts
    """
    validated = []
    for e in entities[:6]:  # Max 6 entities
        if not isinstance(e, dict):
            continue
        entity_name = e.get("entity", "")
        entity_type = e.get("type", "stock")
        if entity_type not in ("stock", "index", "macro"):
            entity_type = "stock"
        try:
            score = float(e.get("score", 0.5))
            score = max(0.0, min(1.0, score))  # Clamp to [0, 1]
        except (ValueError, TypeError):
            score = 0.5

        if entity_name:
            validated.append(EntityInfo(
                entity=str(entity_name),
                type=entity_type,
                score=score,
            ))

    # Sort by score descending
    validated.sort(key=lambda x: x["score"], reverse=True)
    return validated


class TwoPhaseFilterService:
    """
    Service for two-phase news filtering.

    Stage 1 (Initial Filter): Fast, cheap screening based on title + summary.
    Stage 2 (Deep Filter): Full text analysis with entity extraction and tagging.
    """

    # Prompt for initial filtering (title + summary only) — used by "moderate" agent
    INITIAL_FILTER_PROMPT = """你是金融新闻筛选专家。评估以下新闻对投资者的价值。

判断标准:
- USEFUL: 明确影响股价、公司业绩、市场走势的新闻（财报、并购、监管、重大事件等）
- UNCERTAIN: 可能有价值但需要看全文才能判断
- SKIP: 明显无投资价值（广告、水文、重复、娱乐八卦等）

返回 JSON 格式:
{{"1": {{"decision": "useful", "reason": "财报发布"}}, "2": {{"decision": "skip", "reason": "广告软文"}}}}

新闻:
{news_text}"""

    # Strict initial filter prompt — only keeps articles with concrete investment data
    INITIAL_FILTER_PROMPT_STRICT = """你是严格的金融新闻筛选专家。只保留对投资决策有直接、明确影响的新闻。

判断标准:
- USEFUL: 直接包含可量化的投资信息（财报数据、并购金额、监管处罚、价格变动等具体数字）
- UNCERTAIN: 提到上市公司或市场，但缺少具体数据
- SKIP: 无具体投资数据、评论性文章、行业综述、生活类内容

返回 JSON 格式:
{{"1": {{"decision": "useful", "reason": "..."}}, "2": {{"decision": "skip", "reason": "..."}}}}

新闻:
{news_text}"""

    # Permissive initial filter prompt — errs on the side of keeping articles
    INITIAL_FILTER_PROMPT_PERMISSIVE = """你是金融新闻筛选专家。你的原则是"宁可放过，不可错杀"。

判断标准:
- USEFUL: 任何提及上市公司、行业政策、经济数据、市场走势的新闻
- UNCERTAIN: 可能间接影响市场的内容（国际关系、科技突破、社会事件等）
- SKIP: 100%确定无任何投资关联的内容（纯娱乐、体育、生活方式、明显广告）

如果不确定，请选择 UNCERTAIN 而不是 SKIP。

返回 JSON 格式:
{{"1": {{"decision": "useful", "reason": "..."}}, "2": {{"decision": "skip", "reason": "..."}}}}

新闻:
{news_text}"""

    # Three filter agents with different strictness levels for majority voting
    FILTER_AGENTS = [
        FilterAgent(name="strict", prompt_template="INITIAL_FILTER_PROMPT_STRICT", model=None),
        FilterAgent(name="moderate", prompt_template="INITIAL_FILTER_PROMPT", model=None),
        FilterAgent(name="permissive", prompt_template="INITIAL_FILTER_PROMPT_PERMISSIVE", model=None),
    ]

    # Prompt for deep filtering (full text)
    DEEP_FILTER_PROMPT = """你是金融新闻分析专家。分析以下新闻全文。

标题: {title}
来源: {source}
全文:
{full_text}

请返回 JSON 格式:
{{
  "decision": "keep" 或 "delete",
  "entities": [
    {{"entity": "AAPL", "type": "stock", "score": 0.95}},
    {{"entity": "Fed利率", "type": "macro", "score": 0.7}}
  ],
  "industry_tags": ["tech", "semiconductor"],
  "event_tags": ["earnings", "guidance"],
  "sentiment": "bullish",
  "investment_summary": "苹果Q4财报超预期，营收同比增长15%，iPhone销量创新高。管理层上调下季度指引，利好股价。"
}}

字段说明:
- decision: 是否有投资价值（delete = 广告/水文/无价值）
- entities: 关联实体，score 0.0-1.0，最多6个
  - type=stock: **必须使用股票代码**，不要用公司名。例：苹果→AAPL，特斯拉/SpaceX→TSLA，英伟达→NVDA，首都机场→00694.HK，贵州茅台→600519.SH，腾讯→0700.HK，比亚迪→002594.SZ
  - type=index: 指数代码。例：标普500→SPX，纳指→IXIC，上证→000001.SH，恒生→HSI
  - type=macro: 宏观因素，用简短中文/英文名。例：Fed利率、CPI、美元指数
- industry_tags: 行业（tech/finance/healthcare/energy/consumer/industrial/materials/utilities/realestate/telecom）
- event_tags: 事件类型（earnings/merger/ipo/regulatory/executive/product/lawsuit/dividend/buyback/guidance/macro）
- sentiment: 对市场/个股的情绪（bullish/bearish/neutral）
- investment_summary: 2-3句投资导向摘要，突出关键数据和影响

注意:
- 如果是广告软文或无投资价值，decision 为 delete
- 摘要要简洁有力，包含关键数字
- entities 中 type=stock 的 entity 字段**必须**是可交易的股票代码（如 TSLA, 00694.HK, 600519.SH），不能是公司名称"""

    async def batch_initial_filter(
        self,
        db: AsyncSession,
        articles: List[Dict[str, str]],
        batch_size: int = 20,
    ) -> Dict[str, InitialFilterResult]:
        """
        Batch initial filter for news articles using multi-agent voting.

        Three agents (strict, moderate, permissive) evaluate each article
        independently. An article is only skipped if 2+ agents vote "skip".

        Args:
            db: Database session
            articles: List of dicts with keys: url, headline, summary
            batch_size: Number of articles per LLM call

        Returns:
            Dict mapping URL to InitialFilterResult
        """
        if not articles:
            return {}

        return await self.multi_agent_initial_filter(db, articles, batch_size)

    async def multi_agent_initial_filter(
        self,
        db: AsyncSession,
        articles: List[Dict[str, str]],
        batch_size: int = 20,
    ) -> Dict[str, InitialFilterResult]:
        """
        Run 3 filter agents concurrently and aggregate votes.

        Each agent evaluates all articles using its own prompt. Final decisions
        are determined by majority vote: 2+ skip = skip, 2+ useful = useful,
        otherwise uncertain.

        Args:
            db: Database session
            articles: List of dicts with keys: url, headline, summary
            batch_size: Number of articles per LLM call

        Returns:
            Dict mapping URL to InitialFilterResult with aggregated decisions
        """
        try:
            llm_settings = await get_news_llm_settings(db)
        except ValueError as e:
            logger.error(f"Cannot run multi-agent filter: LLM config error: {e}")
            raise

        # Run all 3 agents concurrently
        tasks = [
            self._run_single_agent_filter(agent, llm_settings, articles, batch_size)
            for agent in self.FILTER_AGENTS
        ]

        logger.info(
            f"Starting multi-agent initial filter with {len(articles)} articles, "
            f"agents: {[a.name for a in self.FILTER_AGENTS]}"
        )

        agent_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Map agent name to results, handling failures
        agent_votes: Dict[str, Dict[str, InitialFilterResult]] = {}
        failed_agents = []
        for agent, result in zip(self.FILTER_AGENTS, agent_results):
            if isinstance(result, Exception):
                logger.warning(
                    f"Filter agent '{agent.name}' failed: {result}. "
                    f"Treating all votes as 'uncertain'."
                )
                agent_votes[agent.name] = {}
                failed_agents.append(agent.name)
            else:
                agent_votes[agent.name] = result

        if len(failed_agents) == len(self.FILTER_AGENTS):
            logger.error(
                f"All {len(self.FILTER_AGENTS)} filter agents failed: {failed_agents}. "
                f"All {len(articles)} articles will default to 'uncertain'."
            )

        # Aggregate votes per article
        from app.services.filter_stats_service import get_filter_stats_service
        stats_service = get_filter_stats_service()

        results: Dict[str, InitialFilterResult] = {}
        vote_unanimous_skip = 0
        vote_majority_skip = 0
        vote_majority_pass = 0
        vote_unanimous_pass = 0

        for article in articles:
            url = article.get("url", "")

            # Collect each agent's decision for this article
            decisions: Dict[str, str] = {}
            for agent in self.FILTER_AGENTS:
                agent_result = agent_votes.get(agent.name, {}).get(url)
                if agent_result:
                    decisions[agent.name] = agent_result["decision"]
                else:
                    # Agent failed or missing result — treat as uncertain
                    decisions[agent.name] = "uncertain"

            # Count votes
            skip_count = sum(1 for d in decisions.values() if d == "skip")
            useful_count = sum(1 for d in decisions.values() if d == "useful")

            # Determine final decision by majority
            if skip_count >= 2:
                final_decision = "skip"
            elif useful_count >= 2:
                final_decision = "useful"
            else:
                final_decision = "uncertain"

            # Track voting statistics (4-bucket distribution by skip_count)
            if skip_count == 3:
                vote_unanimous_skip += 1
            elif skip_count == 2:
                vote_majority_skip += 1
            elif skip_count == 1:
                vote_majority_pass += 1
            else:
                vote_unanimous_pass += 1

            # Build reason showing all votes
            reason = (
                f"votes: {decisions.get('strict', '?')}/"
                f"{decisions.get('moderate', '?')}/"
                f"{decisions.get('permissive', '?')}"
            )

            results[url] = InitialFilterResult(
                url=url,
                decision=final_decision,
                reason=reason,
            )

            logger.debug(
                f"Vote detail: {article.get('headline', url)[:60]} → "
                f"{reason} → final={final_decision}"
            )

        # Track aggregate voting stats
        if vote_unanimous_skip > 0:
            await stats_service.increment("vote_unanimous_skip", vote_unanimous_skip)
        if vote_majority_skip > 0:
            await stats_service.increment("vote_majority_skip", vote_majority_skip)
        if vote_majority_pass > 0:
            await stats_service.increment("vote_majority_pass", vote_majority_pass)
        if vote_unanimous_pass > 0:
            await stats_service.increment("vote_unanimous_pass", vote_unanimous_pass)

        # Log summary
        useful_total = sum(1 for r in results.values() if r["decision"] == "useful")
        uncertain_total = sum(1 for r in results.values() if r["decision"] == "uncertain")
        skip_total = sum(1 for r in results.values() if r["decision"] == "skip")

        logger.info(
            f"Multi-agent initial filter complete: "
            f"{useful_total} useful, {uncertain_total} uncertain, {skip_total} skip "
            f"(unanimous_skip={vote_unanimous_skip}, majority_skip={vote_majority_skip}, "
            f"majority_pass={vote_majority_pass}, unanimous_pass={vote_unanimous_pass})"
        )

        return results

    async def _run_single_agent_filter(
        self,
        agent: FilterAgent,
        llm_settings: NewsLLMSettings,
        articles: List[Dict[str, str]],
        batch_size: int = 20,
    ) -> Dict[str, InitialFilterResult]:
        """
        Run a single filter agent across all article batches.

        This is the core filtering logic extracted from the original
        batch_initial_filter, parameterized by agent configuration.

        Args:
            agent: FilterAgent configuration (name, prompt, optional model)
            llm_settings: LLM settings (API key, base URL, default model)
            articles: List of dicts with keys: url, headline, summary
            batch_size: Number of articles per LLM call

        Returns:
            Dict mapping URL to InitialFilterResult
        """
        gateway = get_llm_gateway()

        # Resolve prompt template from class attribute name
        prompt_template = getattr(self, agent.prompt_template)
        model = agent.model or llm_settings.model

        # Map agent name to token tracking stage
        token_stage_map = {
            "strict": "initial_strict",
            "moderate": "initial",
            "permissive": "initial_permissive",
        }
        token_stage = token_stage_map.get(agent.name, "initial")

        results: Dict[str, InitialFilterResult] = {}

        for i in range(0, len(articles), batch_size):
            batch = articles[i : i + batch_size]

            # Build numbered list of articles
            news_text = "\n\n".join(
                [
                    f"[{j + 1}] {a.get('headline', '')}\n{a.get('summary', '')}"
                    for j, a in enumerate(batch)
                ]
            )

            prompt = prompt_template.format(news_text=news_text)

            try:
                chat_request = ChatRequest(
                    model=model,
                    messages=[Message(role=Role.USER, content=prompt)],
                )
                response = await gateway.chat(
                    chat_request,
                    system_api_key=llm_settings.api_key,
                    system_base_url=llm_settings.base_url,
                    use_user_config=False,
                )

                # Track token usage
                if response.usage:
                    from app.services.filter_stats_service import get_filter_stats_service
                    stats_service = get_filter_stats_service()
                    await stats_service.track_tokens(
                        stage=token_stage,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )

                content = response.content or ""
                parsed = extract_json_from_response(content or "")

                for j, a in enumerate(batch):
                    url = a.get("url", "")
                    item = parsed.get(str(j + 1), {"decision": "uncertain", "reason": "parse error"})
                    decision = item.get("decision", "uncertain")
                    if decision not in ("useful", "uncertain", "skip"):
                        decision = "uncertain"

                    results[url] = InitialFilterResult(
                        url=url,
                        decision=decision,
                        reason=item.get("reason", ""),
                    )

                logger.info(
                    f"Agent '{agent.name}' batch {i // batch_size + 1}: "
                    f"{sum(1 for r in list(results.values())[-len(batch):] if r['decision'] == 'useful')} useful, "
                    f"{sum(1 for r in list(results.values())[-len(batch):] if r['decision'] == 'uncertain')} uncertain, "
                    f"{sum(1 for r in list(results.values())[-len(batch):] if r['decision'] == 'skip')} skip"
                )

            except Exception as e:
                logger.warning(f"Agent '{agent.name}' batch {i // batch_size + 1} failed: {e}")
                # On error, mark all batch articles as uncertain
                for a in batch:
                    url = a.get("url", "")
                    results[url] = InitialFilterResult(
                        url=url,
                        decision="uncertain",
                        reason=f"error ({agent.name}): {str(e)[:50]}",
                    )

        return results

    async def deep_filter_article(
        self,
        db: AsyncSession,
        title: str,
        full_text: str,
        source: str,
        url: str,
    ) -> DeepFilterResult:
        """
        Deep filter a single article based on full text.

        Extracts entities, tags, sentiment, and investment summary.
        Makes final KEEP/DELETE decision.

        Args:
            db: Database session
            title: Article title
            full_text: Full article text
            source: News source
            url: Article URL (for logging)

        Returns:
            DeepFilterResult with decision and extracted information
        """
        llm_settings = await get_news_llm_settings(db)
        gateway = get_llm_gateway()

        # Full text passed as-is; max 50K chars enforced at fetch time by full_content_service
        content_text = full_text if full_text else ""

        prompt = self.DEEP_FILTER_PROMPT.format(
            title=title,
            source=source,
            full_text=content_text,
        )

        try:
            chat_request = ChatRequest(
                model=llm_settings.model,
                messages=[Message(role=Role.USER, content=prompt)],
            )
            response = await gateway.chat(
                chat_request,
                system_api_key=llm_settings.api_key,
                system_base_url=llm_settings.base_url,
                use_user_config=False,
            )

            # Track token usage
            if response.usage:
                from app.services.filter_stats_service import get_filter_stats_service
                stats_service = get_filter_stats_service()
                await stats_service.track_tokens(
                    stage="deep",
                    input_tokens=response.usage.prompt_tokens,
                    output_tokens=response.usage.completion_tokens,
                )

            content = response.content or ""
            result = extract_json_from_response(content or "")

            decision = result.get("decision", "keep")
            if decision not in ("keep", "delete"):
                decision = "keep"

            sentiment = result.get("sentiment", "neutral")
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"

            deep_result = DeepFilterResult(
                decision=decision,
                entities=validate_entities(result.get("entities", [])),
                industry_tags=result.get("industry_tags", [])[:5],  # Max 5 tags
                event_tags=result.get("event_tags", [])[:5],
                sentiment=sentiment,
                investment_summary=result.get("investment_summary", "")[:500],  # Max 500 chars
            )

            logger.info(
                f"Deep filter result for {url[:50]}: decision={decision}, "
                f"entities={len(deep_result['entities'])}, sentiment={sentiment}"
            )

            return deep_result

        except Exception as e:
            logger.error(f"Deep filter failed for {url}: {e}")
            # On error, default to keep with empty metadata
            return DeepFilterResult(
                decision="keep",
                entities=[],
                industry_tags=[],
                event_tags=[],
                sentiment="neutral",
                investment_summary="",
            )

    def map_initial_decision_to_status(self, decision: str) -> FilterStatus:
        """Map initial filter decision to FilterStatus enum."""
        if decision == "useful":
            return FilterStatus.INITIAL_USEFUL
        elif decision == "uncertain":
            return FilterStatus.INITIAL_UNCERTAIN
        elif decision == "skip":
            return FilterStatus.INITIAL_SKIPPED
        else:
            return FilterStatus.PENDING

    def map_deep_decision_to_status(self, decision: str) -> FilterStatus:
        """Map deep filter decision to FilterStatus enum."""
        if decision == "keep":
            return FilterStatus.FINE_KEEP
        elif decision == "delete":
            return FilterStatus.FINE_DELETE
        else:
            return FilterStatus.FILTER_FAILED


# Singleton instance
_service: Optional[TwoPhaseFilterService] = None


def get_two_phase_filter_service() -> TwoPhaseFilterService:
    """Get singleton instance of TwoPhaseFilterService."""
    global _service
    if _service is None:
        _service = TwoPhaseFilterService()
    return _service
