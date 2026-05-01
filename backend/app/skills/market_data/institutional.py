"""Skill: get institutional holders data (US/HK markets via yfinance)."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult
from app.skills.utils import normalize_symbol

logger = logging.getLogger(__name__)


class GetInstitutionalHoldersSkill(BaseSkill):
    """Fetch institutional holders data for US and HK stocks via yfinance."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_institutional_holders",
            description=(
                "Get institutional holders data for a stock, including major "
                "institutional shareholders and their positions. "
                "Primarily available for US and HK markets."
            ),
            category="market_data",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock ticker (e.g. AAPL, 0700.HK)",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = normalize_symbol(kwargs.get("symbol"))

        from app.services.stockpulse_client import get_stockpulse_client

        client = await get_stockpulse_client()
        result = await client.get_institutional(symbol)

        if not result:
            return SkillResult(
                success=False,
                error=f"No institutional holders data available for {symbol}",
            )

        return SkillResult(success=True, data=result)
