"""Skill: get northbound (Stock Connect) holding data for China A-shares."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult
from app.skills.utils import normalize_symbol

logger = logging.getLogger(__name__)


class GetNorthboundHoldingSkill(BaseSkill):
    """Fetch northbound (Stock Connect) holding data for China A-shares via akshare."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_northbound_holding",
            description=(
                "Get northbound (Hong Kong Stock Connect) holding data for a "
                "China A-share stock, showing foreign institutional interest "
                "and capital flow trends. Only available for China A-share market."
            ),
            category="market_data",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description=(
                        "Stock ticker for China A-share (e.g. 600519.SS, 000001.SZ). "
                        "The .SS/.SZ suffix will be stripped automatically."
                    ),
                    required=True,
                ),
                SkillParameter(
                    name="days",
                    type="integer",
                    description="Number of days of holding data to retrieve (default 30)",
                    required=False,
                    default=30,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = normalize_symbol(kwargs.get("symbol"))
        days = kwargs.get("days", 30)

        # Ensure days is a reasonable integer
        try:
            days = int(days)
            if days < 1:
                days = 30
            elif days > 365:
                days = 365
        except (TypeError, ValueError):
            days = 30

        # Extract the bare 6-digit stock code for akshare
        stock_code = symbol.split(".")[0]

        from app.services.stockpulse_client import get_stockpulse_client

        client = await get_stockpulse_client()
        result = await client.get_northbound_holding(stock_code, days=days)

        if not result:
            return SkillResult(
                success=False,
                error=f"No northbound holding data available for {symbol}",
            )

        return SkillResult(success=True, data=result)
