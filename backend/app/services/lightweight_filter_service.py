"""Layer 2 lightweight article processing service.

Fast entity/tag extraction for low-score articles (below threshold).
Skips detailed_summary and analysis_report generation to save costs.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
from app.services.news_layer3_analysis_service import (
    extract_json_from_response,
    validate_entities,
)

logger = logging.getLogger(__name__)


LIGHTWEIGHT_PROMPT = """快速提取以下新闻信息，返回JSON格式：

{{
  "decision": "keep" 或 "delete",
  "entities": [{{"entity": "AAPL", "type": "stock", "score": 0.8}}],
  "sentiment": "bullish/bearish/neutral",
  "industry_tags": ["tech"],
  "event_tags": ["earnings"],
  "investment_summary": "1句话概况（≤50字）"
}}

- decision: 是否有投资价值（delete = 广告/水文/完全无价值）
- entities: 关联实体，最多4个，type: stock(必须用代码)/index/macro
- investment_summary: 精炼的1句话
- 不需要生成detailed_summary和analysis_report

标题: {title}
摘要: {text}"""


@dataclass
class LightweightResult:
    """Result of lightweight processing."""

    decision: str  # "keep" or "delete"
    entities: List[Dict[str, Any]]
    sentiment: str
    industry_tags: List[str]
    event_tags: List[str]
    investment_summary: str
    detailed_summary: str  # Always empty string
    analysis_report: str  # Always empty string
    raw_response: str = ""  # Raw LLM output for debugging (stored in pipeline_events)


class LightweightFilterService:
    """Fast lightweight processing for low-score articles.

    Performs quick entity/tag extraction without generating detailed_summary
    or analysis_report, saving LLM cost and latency for articles that scored
    below the deep-analysis threshold.
    """

    # Maximum text length sent to LLM (chars)
    MAX_TEXT_LENGTH = 3000

    async def process_article(
        self,
        db: AsyncSession,
        title: str,
        text: str,
        url: str = "",
    ) -> LightweightResult:
        """Quick entity/tag extraction without deep analysis.

        Args:
            db: Database session for resolving model config.
            title: Article title.
            text: Article text (full_text or summary).
            url: Article URL for logging.

        Returns:
            LightweightResult with basic extraction results.
            On any error, defaults to decision="keep" with empty metadata
            (fail-open policy).
        """
        t0 = time.monotonic()

        try:
            # 1. Resolve model config
            from app.services.settings_service import get_settings_service

            settings_service = get_settings_service()
            model_config = await settings_service.resolve_model_provider(
                db, "phase2_layer2_lightweight"
            )

            # 2. Build prompt with truncated text
            truncated_text = text[: self.MAX_TEXT_LENGTH] if text else ""
            prompt = LIGHTWEIGHT_PROMPT.format(
                title=title,
                text=truncated_text,
            )

            # 3. Call LLM gateway
            gateway = get_llm_gateway()
            chat_request = ChatRequest(
                model=model_config.model,
                messages=[Message(role=Role.USER, content=prompt)],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=500,
                timeout=30,
            )

            llm_start = time.monotonic()
            logger.info(
                "[Lightweight] Starting LLM call for article, "
                "url=%s, model=%s",
                url[:80],
                model_config.model,
            )

            response = await gateway.chat(
                chat_request,
                system_api_key=model_config.api_key,
                system_base_url=model_config.base_url,
                use_user_config=False,
            )

            llm_elapsed_ms = (time.monotonic() - llm_start) * 1000
            logger.info(
                "[Lightweight] LLM call completed for article, "
                "url=%s, elapsed=%.0fms",
                url[:80],
                llm_elapsed_ms,
            )

            # Track token usage (non-fatal)
            if response.usage:
                try:
                    from app.services.filter_stats_service import (
                        get_filter_stats_service,
                    )

                    stats_service = get_filter_stats_service()
                    await stats_service.track_tokens(
                        stage="lightweight",
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                except Exception as stats_err:
                    logger.warning(
                        "[Lightweight] Token tracking failed (non-fatal): %s",
                        stats_err,
                    )

            # 4. Parse JSON response
            content = response.content or ""
            raw_response = content[:5000]  # Retain for debugging

            result: dict = {}
            try:
                result = extract_json_from_response(content)
            except (ValueError, Exception) as json_err:
                logger.warning(
                    "[Lightweight] JSON extraction error for %s: %s (%d chars)",
                    url[:80], json_err, len(content),
                )

            if not result:
                total_elapsed_ms = (time.monotonic() - t0) * 1000
                logger.warning(
                    "[Lightweight] JSON parse failed for %s (%.0fms), "
                    "response length=%d, defaulting to keep",
                    url[:80],
                    total_elapsed_ms,
                    len(content),
                )
                default = self._default_result()
                default.raw_response = raw_response
                return default

            # 5. Validate and return
            decision = result.get("decision", "keep")
            if decision not in ("keep", "delete"):
                decision = "keep"

            sentiment = result.get("sentiment", "neutral")
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"

            # Entities: reuse shared validator, but cap at 4 for lightweight
            entities = validate_entities(result.get("entities", []))[:4]

            lightweight_result = LightweightResult(
                decision=decision,
                entities=entities,
                sentiment=sentiment,
                industry_tags=result.get("industry_tags", [])[:5],
                event_tags=result.get("event_tags", [])[:5],
                investment_summary=result.get("investment_summary", "")[:500],
                detailed_summary="",
                analysis_report="",
                raw_response=raw_response,
            )

            total_elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "[Lightweight] Result for %s: decision=%s, entities=%d, "
                "sentiment=%s, total=%.0fms",
                url[:80],
                decision,
                len(entities),
                sentiment,
                total_elapsed_ms,
            )

            return lightweight_result

        except Exception as e:
            total_elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "[Lightweight] Failed for %s (%.0fms): %s",
                url[:80],
                total_elapsed_ms,
                e,
            )
            # Fail-open: default to keep with empty metadata
            return self._default_result()

    @staticmethod
    def _default_result() -> LightweightResult:
        """Return a safe default result (fail-open)."""
        return LightweightResult(
            decision="keep",
            entities=[],
            sentiment="neutral",
            industry_tags=[],
            event_tags=[],
            investment_summary="",
            detailed_summary="",
            analysis_report="",
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_service: Optional[LightweightFilterService] = None


def get_lightweight_filter_service() -> LightweightFilterService:
    """Get singleton instance of LightweightFilterService."""
    global _service
    if _service is None:
        _service = LightweightFilterService()
    return _service
