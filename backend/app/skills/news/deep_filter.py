"""Skill: deep (phase 2) news filtering based on full article text."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class DeepFilterNewsSkill(BaseSkill):
    """Deep filter a single article using LLM-based full-text analysis.

    Wraps ``TwoPhaseFilterService.deep_filter_article`` which performs
    detailed analysis on the full article text, extracting entities, tags,
    sentiment, and an investment summary.  Makes a final KEEP / DELETE
    decision.

    Requires a ``db`` (AsyncSession) kwarg injected by the caller (not
    exposed as a SkillParameter) for LLM provider configuration resolution.

    Designed to be called by LangGraph news pipeline nodes.
    """

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="deep_filter_news",
            description=(
                "Deep filter a news article based on full text. Extracts entities, "
                "industry/event tags, sentiment, and investment summary. Makes a "
                "final keep/delete decision. Requires a db session injected by the caller."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="title",
                    type="string",
                    description="Article title.",
                    required=True,
                ),
                SkillParameter(
                    name="full_text",
                    type="string",
                    description="Full article text content.",
                    required=True,
                ),
                SkillParameter(
                    name="source",
                    type="string",
                    description="News source name (e.g. 'reuters', 'eastmoney').",
                    required=True,
                ),
                SkillParameter(
                    name="url",
                    type="string",
                    description="Article URL (used for logging).",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        # db is injected by the caller, not exposed as a SkillParameter
        db = kwargs.get("db")
        if db is None:
            return SkillResult(
                success=False,
                error="db (AsyncSession) must be provided by the caller",
            )

        title = kwargs.get("title")
        full_text = kwargs.get("full_text")
        source = kwargs.get("source")
        url = kwargs.get("url")

        if not title:
            return SkillResult(success=False, error="title parameter is required")
        if not full_text:
            return SkillResult(success=False, error="full_text parameter is required")
        if not source:
            return SkillResult(success=False, error="source parameter is required")
        if not url:
            return SkillResult(success=False, error="url parameter is required")

        from app.services.two_phase_filter_service import get_two_phase_filter_service

        service = get_two_phase_filter_service()

        try:
            result = await service.deep_filter_article(
                db=db,
                title=title,
                full_text=full_text,
                source=source,
                url=url,
            )
        except ValueError as e:
            return SkillResult(
                success=False,
                error=f"LLM config error: {e}",
                metadata={"url": url},
            )
        except Exception as e:
            logger.exception("DeepFilterNewsSkill failed for %s: %s", url, e)
            return SkillResult(
                success=False,
                error=f"Deep filter failed: {e}",
                metadata={"url": url},
            )

        return SkillResult(
            success=True,
            data=dict(result),
            metadata={
                "url": url,
                "decision": result["decision"],
                "entity_count": len(result.get("entities", [])),
                "sentiment": result.get("sentiment", "neutral"),
            },
        )
