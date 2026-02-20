"""Skill: search for stocks by name or ticker using local in-memory index."""

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
                "Search for stocks by exact name or ticker symbol (e.g. 'AAPL', '小米', '0700'). "
                "Only matches stock names and ticker codes — cannot search by concept, industry, "
                "index constituents, or peer relationships. "
                "For those, use search_related_stocks instead."
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

        from app.services.stock_list_service import get_stock_list_service

        service = await get_stock_list_service()
        results = service.search(query, limit=10)

        return SkillResult(success=True, data=results)
