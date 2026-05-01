"""Skill: get analyst ratings and recommendations."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult
from app.skills.utils import normalize_symbol

logger = logging.getLogger(__name__)


class GetAnalystRatingsSkill(BaseSkill):
    """Fetch analyst ratings and consensus recommendations via yfinance."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_analyst_ratings",
            description=(
                "Get analyst ratings and consensus recommendations for a stock, "
                "including target price, number of analysts, and buy/hold/sell "
                "distribution. Data sourced from yfinance."
            ),
            category="market_data",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock ticker (e.g. AAPL, MSFT, 0700.HK)",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = normalize_symbol(kwargs.get("symbol"))

        from app.services.stockpulse_client import get_stockpulse_client

        client = await get_stockpulse_client()
        result = await client.get_analyst_ratings(symbol)

        if not result:
            return SkillResult(
                success=False,
                error=f"No analyst ratings available for {symbol}",
            )

        return SkillResult(success=True, data=result)
