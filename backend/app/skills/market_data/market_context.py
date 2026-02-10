"""Skill: get aggregated market context for sentiment analysis."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillResult

logger = logging.getLogger(__name__)


class GetMarketContextSkill(BaseSkill):
    """Fetch aggregated market context including major indices and capital flows."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_market_context",
            description=(
                "Get aggregated market context for sentiment analysis, including "
                "major market indices (S&P 500, NASDAQ, etc.) and northbound "
                "capital flow summary. No parameters required."
            ),
            category="market_data",
            parameters=[],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        from app.services.providers import get_provider_router

        router = await get_provider_router()
        result = await router.get_market_context()

        if not result:
            return SkillResult(
                success=False,
                error="No market context data available",
            )

        return SkillResult(success=True, data=result)
