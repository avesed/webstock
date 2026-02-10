"""Skill: search for stocks by name or ticker."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class SearchStocksSkill(BaseSkill):
    """Search for stocks by name or ticker across US, HK, and China A-share markets."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="search_stocks",
            description=(
                "Search for stocks by name or ticker across US, HK, "
                "and China A-share markets."
            ),
            category="market_data",
            parameters=[
                SkillParameter(
                    name="query",
                    type="string",
                    description="Company name or partial ticker",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        from app.prompts.analysis.sanitizer import sanitize_input

        query = sanitize_input(kwargs.get("query", ""), max_length=100)
        if not query or query == "N/A":
            return SkillResult(
                success=False,
                error="Search query is required",
            )

        from app.services.stock_service import get_stock_service

        service = await get_stock_service()
        results = await service.search(query)

        items = [
            r.to_dict() if hasattr(r, "to_dict") else r
            for r in results[:10]
        ]

        return SkillResult(success=True, data=items)
