"""Skill: initial (phase 1) news filtering based on title + summary."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class InitialFilterNewsSkill(BaseSkill):
    """Batch initial filter for news articles using LLM-based relevance screening.

    Wraps ``TwoPhaseFilterService.batch_initial_filter`` which classifies articles
    as USEFUL / UNCERTAIN / SKIP based solely on title + summary.  This is the
    fast, cheap first pass of the two-phase filtering pipeline.

    Requires a ``db`` (AsyncSession) kwarg injected by the caller (not exposed
    as a SkillParameter) because the underlying service needs DB access to
    resolve LLM provider configuration.

    Designed to be called by LangGraph news pipeline nodes.
    """

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="initial_filter_news",
            description=(
                "Fast initial screening of news articles based on title and summary. "
                "Classifies each article as useful, uncertain, or skip using LLM. "
                "Requires a db session injected by the caller."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="articles",
                    type="array",
                    description=(
                        "Array of article objects, each with keys: url, headline, summary. "
                        "Example: [{\"url\": \"...\", \"headline\": \"...\", \"summary\": \"...\"}]"
                    ),
                    required=True,
                ),
                SkillParameter(
                    name="batch_size",
                    type="integer",
                    description="Number of articles per LLM call. Default 20.",
                    required=False,
                    default=20,
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

        articles = kwargs.get("articles")
        if not articles or not isinstance(articles, list):
            return SkillResult(
                success=False,
                error="articles parameter is required and must be a non-empty list",
            )

        batch_size = kwargs.get("batch_size", 20)
        if not isinstance(batch_size, int) or batch_size < 1:
            batch_size = 20

        from app.services.two_phase_filter_service import get_two_phase_filter_service

        service = get_two_phase_filter_service()

        try:
            results = await service.batch_initial_filter(
                db=db,
                articles=articles,
                batch_size=batch_size,
            )
        except ValueError as e:
            return SkillResult(
                success=False,
                error=f"LLM config error: {e}",
                metadata={"article_count": len(articles)},
            )
        except Exception as e:
            logger.exception("InitialFilterNewsSkill failed: %s", e)
            return SkillResult(
                success=False,
                error=f"Initial filter failed: {e}",
                metadata={"article_count": len(articles)},
            )

        # Compute summary counts
        useful = sum(1 for r in results.values() if r["decision"] == "useful")
        uncertain = sum(1 for r in results.values() if r["decision"] == "uncertain")
        skip = sum(1 for r in results.values() if r["decision"] == "skip")

        return SkillResult(
            success=True,
            data=results,
            metadata={
                "article_count": len(articles),
                "useful": useful,
                "uncertain": uncertain,
                "skip": skip,
            },
        )
