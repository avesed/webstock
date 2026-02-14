"""Layer 2 news scoring and routing service.

DEPRECATED: This module has been replaced by ``layer1_scoring_service.py`` which
performs scoring in Layer 1 (monitor tasks) instead of Layer 2 (LangGraph pipeline).
The critical event keyword detection has been extracted to
``app/utils/critical_event_detection.py``. This file is kept for reference only
and should not be imported by new code.

Original description:
Evaluates articles on a 100-point scale across 4 dimensions and routes
to full 5-agent analysis (score >= threshold) or lightweight processing.

Scoring Dimensions (100 points total):
1. Information Value (40pts): data exclusivity, timeliness, depth
2. Investment Relevance (30pts): market impact, stock impact, actionability
3. Content Completeness (20pts): data completeness, logical coherence
4. Scarcity (10pts): exclusive reports, expert analysis

Critical events (war, circuit breaker, bankruptcy, etc.) bypass scoring
and are automatically assigned 100 points via keyword-based fast path.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring prompt
# ---------------------------------------------------------------------------

SCORING_PROMPT = """你是财经新闻价值评估专家。请对以下新闻进行100分制评分。

评分维度（共100分）：
1. **信息价值 (40分)**: 数据独家性(15) + 时效性(10) + 深度(15)
2. **投资相关性 (30分)**: 市场影响(15) + 个股影响(10) + 可操作性(5)
3. **内容完整性 (20分)**: 数据完整度(10) + 逻辑连贯性(10)
4. **稀缺性 (10分)**: 独家报道(5) + 专业性(5)

**关键事件自动满分（100分）**：战争/军事冲突、熔断/交易暂停、破产/退市/重大欺诈、央行政策突变、监管禁令、IPO/首次上市、重大并购/合并重组/收购要约、拆分上市/私有化

输出JSON：
{{
  "total_score": 0-100,
  "dimension_scores": {{
    "information_value": 0-40,
    "investment_relevance": 0-30,
    "completeness": 0-20,
    "scarcity": 0-10
  }},
  "is_critical_event": true/false,
  "reasoning": "评分理由..."
}}

当前文章：
【标题】{title}
【内容摘要】{text}
【图片数据】{image_insights}
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScoringResult:
    """Result of article scoring."""

    total_score: Optional[int]
    dimension_scores: Dict[str, int]
    is_critical_event: bool
    reasoning: str
    processing_path: str  # "full_analysis", "lightweight", or "error"
    raw_response: str = ""  # Raw LLM output for debugging (stored in pipeline_events)


# ---------------------------------------------------------------------------
# Critical event keywords (Chinese + English)
# ---------------------------------------------------------------------------

_CRITICAL_KEYWORDS_ZH = [
    # 地缘/安全
    "战争", "军事冲突", "武装冲突", "紧急", "恐怖袭击",
    # 交易异常
    "熔断", "暂停交易", "交易暂停",
    # 公司重大事件
    "破产", "退市", "重大欺诈",
    # 资本市场重大事件
    "IPO", "上市冲刺", "拟上市", "首次公开募股",
    "重大并购", "合并重组", "收购要约", "借壳上市",
    "股权结构", "拆分上市", "私有化",
    # 宏观政策
    "加息", "降息", "央行政策",
    "市场崩盘", "监管禁令", "行业整顿",
]

_CRITICAL_KEYWORDS_EN = [
    # Geopolitical/security
    "war", "military conflict", "armed conflict",
    "emergency", "terrorist attack",
    # Trading anomalies
    "circuit breaker", "trading halt", "trading suspended",
    # Corporate critical events
    "bankruptcy", "delisting", "major fraud",
    # Capital markets
    "IPO", "initial public offering", "going public",
    "major acquisition", "merger", "takeover bid", "restructuring",
    "stock split", "privatization", "spinoff",
    # Macro policy
    "rate hike", "rate cut", "central bank",
    "market crash", "regulatory ban", "industry crackdown",
]

# Pre-compile a single regex for fast matching (case-insensitive for English)
_CRITICAL_PATTERN = re.compile(
    "|".join(
        re.escape(kw)
        for kw in _CRITICAL_KEYWORDS_ZH + _CRITICAL_KEYWORDS_EN
    ),
    re.IGNORECASE,
)

# Maximum text length sent to the LLM for scoring (chars)
_MAX_TEXT_LENGTH = 5000

# Default score on error (fail-safe → lightweight path)
_DEFAULT_SCORE = 50
_DEFAULT_THRESHOLD = 50


# ---------------------------------------------------------------------------
# JSON extraction (same pattern as news_layer3_analysis_service.py)
# ---------------------------------------------------------------------------

def _extract_json_from_response(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response, handling markdown code blocks.

    Args:
        text: Raw LLM response text

    Returns:
        Parsed JSON as dict, or empty dict on failure
    """
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        # Remove closing fence
        text = re.sub(r"\n?```\s*$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("Scoring JSON parse failed: %s, text: %s", e, text[:200])
        return {}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class NewsScoringService:
    """Score articles on a 100-point scale and route to appropriate processing path.

    Processing paths:
    - ``full_analysis``: score >= threshold, routed to 5-agent deep analysis
    - ``lightweight``:   score < threshold, routed to lightweight summarisation
    """

    # ------------------------------------------------------------------
    # Critical event fast path
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_critical_event(title: str, text: str) -> bool:
        """Check if title or text contains critical event keywords.

        Uses a pre-compiled regex for O(n) matching across all keywords
        in a single pass.

        Args:
            title: Article title
            text: Article body (may be truncated)

        Returns:
            True if any critical event keyword is found
        """
        combined = f"{title} {text[:2000]}"
        return bool(_CRITICAL_PATTERN.search(combined))

    # ------------------------------------------------------------------
    # Threshold from system settings
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_score_threshold(db: AsyncSession) -> int:
        """Read ``phase2_score_threshold`` from system_settings.

        Falls back to ``_DEFAULT_THRESHOLD`` (50) when the setting
        is missing or the query fails.

        Args:
            db: Async database session

        Returns:
            Integer threshold value
        """
        try:
            from app.services.settings_service import get_settings_service

            settings_service = get_settings_service()
            system = await settings_service.get_system_settings(db)
            return system.phase2_score_threshold
        except Exception as e:
            logger.warning(
                "Failed to read phase2_score_threshold, using default %d: %s",
                _DEFAULT_THRESHOLD,
                e,
            )
            return _DEFAULT_THRESHOLD

    # ------------------------------------------------------------------
    # LLM scoring
    # ------------------------------------------------------------------

    async def _llm_score(
        self,
        db: AsyncSession,
        title: str,
        text: str,
        image_insights: str,
        image_urls: Optional[List[str]],
    ) -> Tuple[Dict[str, Any], str]:
        """Call the LLM gateway and return the parsed scoring JSON + raw output.

        Args:
            db: Async database session (used for model config resolution)
            title: Article title
            text: Article body (already truncated by caller)
            image_insights: Pre-extracted image context or empty string
            image_urls: Optional list of image URLs for multimodal input

        Returns:
            Tuple of (parsed JSON dict, raw LLM response text).
            On parse failure the dict is empty but raw text is preserved.
        """
        from app.services.settings_service import get_settings_service

        settings_service = get_settings_service()
        model_config = await settings_service.resolve_model_provider(
            db, "phase2_layer2_scoring"
        )

        prompt_text = SCORING_PROMPT.format(
            title=title,
            text=text,
            image_insights=image_insights or "无",
        )

        # Build message content — multimodal if image_urls provided
        if image_urls:
            content_parts: List[Dict[str, Any]] = [
                {"type": "text", "text": prompt_text},
            ]
            for url in image_urls[:5]:  # Cap at 5 images
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                })
            messages = [Message(role=Role.USER, content=content_parts)]
        else:
            messages = [Message(role=Role.USER, content=prompt_text)]

        gateway = get_llm_gateway()
        chat_request = ChatRequest(
            model=model_config.model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=60,
        )

        response = await gateway.chat(
            chat_request,
            system_api_key=model_config.api_key,
            system_base_url=model_config.base_url,
            use_user_config=False,
        )

        # Track token usage
        if response.usage:
            try:
                from app.services.filter_stats_service import get_filter_stats_service

                stats_service = get_filter_stats_service()
                await stats_service.track_tokens(
                    stage="layer2_scoring",
                    input_tokens=response.usage.prompt_tokens,
                    output_tokens=response.usage.completion_tokens,
                )
            except Exception:
                # Token tracking is best-effort; never crash the pipeline
                logger.debug("Token tracking failed for layer2_scoring", exc_info=True)

        content = response.content or ""
        return _extract_json_from_response(content), content[:5000]

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_dimension_scores(raw: Any) -> Dict[str, int]:
        """Validate and clamp dimension scores from LLM output.

        Args:
            raw: The ``dimension_scores`` value from the LLM JSON

        Returns:
            Dict with four validated integer scores, clamped to their
            respective maximums
        """
        defaults = {
            "information_value": 0,
            "investment_relevance": 0,
            "completeness": 0,
            "scarcity": 0,
        }
        maxes = {
            "information_value": 40,
            "investment_relevance": 30,
            "completeness": 20,
            "scarcity": 10,
        }

        if not isinstance(raw, dict):
            return defaults

        result: Dict[str, int] = {}
        for key, default_val in defaults.items():
            try:
                val = int(raw.get(key, default_val))
                val = max(0, min(val, maxes[key]))
            except (ValueError, TypeError):
                val = default_val
            result[key] = val

        return result

    @staticmethod
    def _validate_total_score(raw_score: Any, dimension_scores: Dict[str, int]) -> int:
        """Validate the total score, falling back to the sum of dimensions.

        Args:
            raw_score: The ``total_score`` value from the LLM JSON
            dimension_scores: Already-validated dimension scores

        Returns:
            Integer total score clamped to [0, 100]
        """
        try:
            score = int(raw_score)
            return max(0, min(score, 100))
        except (ValueError, TypeError):
            # Recompute from dimension scores
            return max(0, min(sum(dimension_scores.values()), 100))

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def score_article(
        self,
        db: AsyncSession,
        title: str,
        text: str,
        image_insights: str = "",
        image_urls: Optional[List[str]] = None,
    ) -> ScoringResult:
        """Score an article on a 100-point scale and determine processing path.

        The method follows a two-step approach:
        1. **Fast path** -- keyword-based critical event detection (auto 100 pts).
        2. **LLM path** -- 4-dimension scoring via the configured LLM provider.

        On any error the method defaults to ``score=50`` with the
        ``"lightweight"`` processing path (fail-safe).

        Args:
            db: Async database session
            title: Article title
            text: Article body text (will be truncated to 5 000 chars)
            image_insights: Pre-extracted image context string (optional)
            image_urls: Optional list of image URLs for multimodal scoring

        Returns:
            ScoringResult with score, dimension breakdown, and routing decision
        """
        t0 = time.monotonic()
        truncated_text = text[:_MAX_TEXT_LENGTH] if text else ""

        # ---- 1. Critical event fast path --------------------------------
        if self._detect_critical_event(title, truncated_text):
            threshold = await self._get_score_threshold(db)
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "[Scoring] Critical event detected for '%s', auto score=100, "
                "path=full_analysis, elapsed=%.0fms",
                title[:80],
                elapsed_ms,
            )
            return ScoringResult(
                total_score=100,
                dimension_scores={
                    "information_value": 40,
                    "investment_relevance": 30,
                    "completeness": 20,
                    "scarcity": 10,
                },
                is_critical_event=True,
                reasoning="关键事件自动满分",
                processing_path="full_analysis",
            )

        # ---- 2. LLM scoring path ---------------------------------------
        try:
            parsed, raw_response = await self._llm_score(
                db, title, truncated_text, image_insights, image_urls
            )

            if not parsed:
                raise ValueError("Empty JSON from LLM response")

            dimension_scores = self._validate_dimension_scores(
                parsed.get("dimension_scores")
            )
            total_score = self._validate_total_score(
                parsed.get("total_score"), dimension_scores
            )
            is_critical = bool(parsed.get("is_critical_event", False))
            reasoning = str(parsed.get("reasoning", ""))[:500]

            # Override score if LLM itself flagged as critical
            if is_critical:
                total_score = 100
                dimension_scores = {
                    "information_value": 40,
                    "investment_relevance": 30,
                    "completeness": 20,
                    "scarcity": 10,
                }

            # ---- 3. Routing decision ------------------------------------
            threshold = await self._get_score_threshold(db)
            processing_path = (
                "full_analysis" if total_score >= threshold else "lightweight"
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "[Scoring] title='%s', score=%d (iv=%d ir=%d c=%d s=%d), "
                "threshold=%d, path=%s, critical=%s, elapsed=%.0fms",
                title[:80],
                total_score,
                dimension_scores.get("information_value", 0),
                dimension_scores.get("investment_relevance", 0),
                dimension_scores.get("completeness", 0),
                dimension_scores.get("scarcity", 0),
                threshold,
                processing_path,
                is_critical,
                elapsed_ms,
            )

            return ScoringResult(
                total_score=total_score,
                dimension_scores=dimension_scores,
                is_critical_event=is_critical,
                reasoning=reasoning,
                processing_path=processing_path,
                raw_response=raw_response,
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "[Scoring] Failed for '%s' (%.0fms): %s. "
                "Marking as error (no score assigned)",
                title[:80],
                elapsed_ms,
                e,
            )
            return ScoringResult(
                total_score=None,
                dimension_scores={},
                is_critical_event=False,
                reasoning=f"Scoring error: {str(e)[:200]}",
                processing_path="error",
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_service: Optional[NewsScoringService] = None


def get_news_scoring_service() -> NewsScoringService:
    """Get singleton instance of NewsScoringService."""
    global _service
    if _service is None:
        _service = NewsScoringService()
    return _service
