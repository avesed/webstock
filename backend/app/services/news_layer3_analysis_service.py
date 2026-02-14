"""News Layer 3 deep analysis service for full-text filtering and entity extraction.

This service implements the deep (Layer 3) analysis stage of the news pipeline:
- Deep Filter: Based on full text, extracts entities, tags, summary, and makes final KEEP/DELETE decision

The initial filter stage (multi-agent voting) has been replaced by Layer 1 scoring
in ``layer1_scoring_service.py``.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
from app.models.news import FilterStatus

logger = logging.getLogger(__name__)


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
    detailed_summary: str       # Complete summary preserving all key details
    analysis_report: str        # Markdown-formatted professional analysis report


@dataclass
class NewsLLMSettings:
    """LLM settings for news filtering."""

    api_key: str
    base_url: Optional[str]
    model: str


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


class NewsLayer3AnalysisService:
    """
    Service for news Layer 3 deep analysis.

    Provides full-text analysis with entity extraction, tagging, summary
    generation, and analysis report generation. Makes final KEEP/DELETE decision.

    The initial filter stage (multi-agent voting with strict/moderate/permissive
    agents) has been removed and replaced by Layer 1 scoring in
    ``layer1_scoring_service.py``.
    """

    # Prompt for deep filtering (full text)
    DEEP_FILTER_PROMPT = """你是专业的金融新闻分析专家。请分析以下新闻全文，并提供结构化的过滤和分析结果。

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
  "investment_summary": "1句话概况，简洁总结核心投资价值（不超过50字）",
  "detailed_summary": "保留所有关键细节的完整总结",
  "analysis_report": "Markdown格式的专业分析报告"
}}

---

【字段说明】
- decision: 是否有投资价值（delete = 广告/水文/无价值）
- entities: 关联实体，score 0.0-1.0，最多6个
  - type=stock: **必须使用股票代码**，不要用公司名。例：苹果→AAPL，特斯拉/SpaceX→TSLA，英伟达→NVDA，首都机场→00694.HK，贵州茅台→600519.SH，腾讯→0700.HK，比亚迪→002594.SZ
  - type=index: 指数代码。例：标普500→SPX，纳指→IXIC，上证→000001.SH，恒生→HSI
  - type=macro: 宏观因素，用简短中文/英文名。例：Fed利率、CPI、美元指数
- industry_tags: 行业（tech/finance/healthcare/energy/consumer/industrial/materials/utilities/realestate/telecom）
- event_tags: 事件类型（earnings/merger/ipo/regulatory/executive/product/lawsuit/dividend/buyback/guidance/macro）
- sentiment: 对市场/个股的情绪（bullish/bearish/neutral）
- investment_summary: 1句话概况，不超过50字，用于卡片预览
- detailed_summary: 保留所有关键细节的完整总结，包含所有重要数据、时间线、人物、因果关系，删除冗余表述。长度应恰好足够传达完整信息（简单新闻5-8句，复杂深度报道15-20句）
- analysis_report: Markdown格式的专业分析报告，包含以下结构：

```markdown
## 核心解读
[用通俗易懂的语言解释这篇新闻的本质和影响，3-5句话]

## 投资洞察
- **机会点**：[具体的投资机会]
- **关注点**：[需要重点关注的方面]
- **时间窗口**：[投资时机分析]

## 风险分析
- **短期风险**：[近期可能面临的风险]
- **长期风险**：[长期潜在风险]
- **不确定性**：[不确定因素分析]

## 市场影响
- **直接影响板块**：[直接受影响的行业/板块]
- **间接影响**：[间接影响的相关领域]

## 情绪指数
**综合情绪**：看涨 / 中性 / 看跌
**情绪强度**：X/5
**理由**：[简要说明情绪判断的依据]

## 专业信息
- **相关公司**：[股票代码和公司名称]
- **关键数据**：[重要的财务数据、数字指标]
- **时间线**：[事件发展时间线]
```

---

【重要注意】
- 严格按照JSON格式输出，不要添加任何额外文字
- 如果是广告软文或无投资价值，decision为delete
- **如果decision是delete**，则 detailed_summary 和 analysis_report 填空字符串 ""
- investment_summary 必须是1句话，不超过50字
- detailed_summary 要保留所有关键信息，但尽可能精炼
- analysis_report 使用Markdown格式，包含上述6个章节
- entities 中 type=stock 的 entity 字段**必须**是可交易的股票代码（如 TSLA, 00694.HK, 600519.SH），不能是公司名称"""

    # JSON Schema for OpenAI Structured Outputs (deep filter)
    DEEP_FILTER_SCHEMA = {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["keep", "delete"]},
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string"},
                        "type": {"type": "string", "enum": ["stock", "index", "macro"]},
                        "score": {"type": "number"},
                    },
                    "required": ["entity", "type", "score"],
                    "additionalProperties": False,
                },
            },
            "industry_tags": {"type": "array", "items": {"type": "string"}},
            "event_tags": {"type": "array", "items": {"type": "string"}},
            "sentiment": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "investment_summary": {"type": "string"},
            "detailed_summary": {"type": "string"},
            "analysis_report": {"type": "string"},
        },
        "required": [
            "decision", "entities", "industry_tags", "event_tags",
            "sentiment", "investment_summary", "detailed_summary", "analysis_report",
        ],
        "additionalProperties": False,
    }

    def _build_structured_response_format(self) -> Dict[str, Any]:
        """Build response_format dict for OpenAI Structured Outputs.

        Returns the json_schema format for models that support it.
        Falls back to simple json_object mode if strict schema is not
        supported by the provider.
        """
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "deep_filter_result",
                "strict": True,
                "schema": self.DEEP_FILTER_SCHEMA,
            },
        }

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

        Extracts entities, tags, sentiment, investment summary, detailed
        summary, and analysis report.  Makes final KEEP/DELETE decision.

        Uses OpenAI Structured Outputs when available, falling back to
        plain JSON mode + manual validation for non-OpenAI models.

        Args:
            db: Database session
            title: Article title
            full_text: Full article text
            source: News source
            url: Article URL (for logging)

        Returns:
            DeepFilterResult with decision and extracted information
        """
        import time as _time

        llm_settings = await get_news_llm_settings(db)
        gateway = get_llm_gateway()

        # Full text passed as-is; max 50K chars enforced at fetch time by full_content_service
        content_text = full_text if full_text else ""

        prompt = self.DEEP_FILTER_PROMPT.format(
            title=title,
            source=source,
            full_text=content_text,
        )

        t0 = _time.monotonic()

        try:
            # Determine response_format based on model
            # OpenAI models support json_schema structured outputs;
            # other providers fall back to plain json_object mode.
            model_lower = llm_settings.model.lower()
            is_openai_model = any(
                p in model_lower
                for p in ("gpt-", "o1-", "o3-", "o4-", "chatgpt-")
            )

            if is_openai_model:
                response_format = self._build_structured_response_format()
            else:
                # Fallback: simple JSON mode for non-OpenAI providers
                response_format = {"type": "json_object"}

            chat_request = ChatRequest(
                model=llm_settings.model,
                messages=[Message(role=Role.USER, content=prompt)],
                response_format=response_format,
                temperature=0.3,
                timeout=90,  # Explicit 90s timeout for LLM call (skill has 60s wrapper, increase that too)
            )

            # Timing: start LLM call
            llm_start = _time.monotonic()
            logger.info(f"[Deep Filter] Starting LLM call for article, url={url[:80]}, model={llm_settings.model}")

            response = await gateway.chat(
                chat_request,
                system_api_key=llm_settings.api_key,
                system_base_url=llm_settings.base_url,
                use_user_config=False,
                purpose="deep_filter",
                usage_metadata={"url": url[:200]},
            )

            llm_call_elapsed = (_time.monotonic() - llm_start) * 1000
            logger.info(f"[Deep Filter] LLM call completed for article, url={url[:80]}, elapsed={llm_call_elapsed:.0f}ms")

            llm_elapsed = (_time.monotonic() - t0) * 1000

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

            if not result:
                logger.warning(
                    "Deep filter JSON parse failed for %s (%.0fms), "
                    "response length=%d",
                    url[:80], llm_elapsed, len(content),
                )

            decision = result.get("decision", "keep")
            if decision not in ("keep", "delete"):
                decision = "keep"

            sentiment = result.get("sentiment", "neutral")
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"

            # Extract new fields
            raw_detailed_summary = result.get("detailed_summary", "")
            raw_analysis_report = result.get("analysis_report", "")

            # Validation and lenient error handling for new fields
            # Thresholds lowered to support brief news bulletins
            if decision == "keep":
                if len(raw_detailed_summary) < 10:  # Relaxed from 50 to support brief news
                    logger.warning(
                        "detailed_summary too short (%d chars) for %s, setting to empty",
                        len(raw_detailed_summary), url[:80],
                    )
                    raw_detailed_summary = ""
                if len(raw_analysis_report) < 30:  # Relaxed from 200 to support brief news
                    logger.warning(
                        "analysis_report too short (%d chars) for %s, setting to empty",
                        len(raw_analysis_report), url[:80],
                    )
                    raw_analysis_report = ""
            else:
                # decision=delete: force clear these fields
                raw_detailed_summary = ""
                raw_analysis_report = ""

            deep_result = DeepFilterResult(
                decision=decision,
                entities=validate_entities(result.get("entities", [])),
                industry_tags=result.get("industry_tags", [])[:5],  # Max 5 tags
                event_tags=result.get("event_tags", [])[:5],
                sentiment=sentiment,
                investment_summary=result.get("investment_summary", "")[:500],  # Max 500 chars
                detailed_summary=raw_detailed_summary,
                analysis_report=raw_analysis_report,
            )

            logger.info(
                "Deep filter result for %s: decision=%s, entities=%d, sentiment=%s, "
                "detailed_summary=%d chars, analysis_report=%d chars, llm_time=%.0fms",
                url[:80], decision, len(deep_result["entities"]), sentiment,
                len(raw_detailed_summary), len(raw_analysis_report), llm_elapsed,
            )

            return deep_result

        except Exception as e:
            llm_elapsed = (_time.monotonic() - t0) * 1000
            logger.error(
                "Deep filter failed for %s (%.0fms): %s", url[:80], llm_elapsed, e,
            )
            # On error, default to keep with empty metadata
            return DeepFilterResult(
                decision="keep",
                entities=[],
                industry_tags=[],
                event_tags=[],
                sentiment="neutral",
                investment_summary="",
                detailed_summary="",
                analysis_report="",
            )

    def map_deep_decision_to_status(self, decision: str) -> FilterStatus:
        """Map deep filter decision to FilterStatus enum."""
        if decision == "keep":
            return FilterStatus.FINE_KEEP
        elif decision == "delete":
            return FilterStatus.FINE_DELETE
        else:
            return FilterStatus.FILTER_FAILED


# Singleton instance
_service: Optional[NewsLayer3AnalysisService] = None


def get_news_layer3_analysis_service() -> NewsLayer3AnalysisService:
    """Get singleton instance of NewsLayer3AnalysisService."""
    global _service
    if _service is None:
        _service = NewsLayer3AnalysisService()
    return _service
