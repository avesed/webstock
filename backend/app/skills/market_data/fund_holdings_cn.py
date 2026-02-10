"""Skill: get China A-share fund holdings data (via akshare)."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


def _normalize_symbol(raw: Any) -> str:
    """Sanitize and normalize a stock symbol."""
    from app.prompts.analysis.sanitizer import sanitize_symbol
    from app.utils.symbol_validation import validate_symbol

    sanitized = sanitize_symbol(raw)
    try:
        return validate_symbol(sanitized)
    except Exception:
        return sanitized


class GetFundHoldingsCnSkill(BaseSkill):
    """Fetch China A-share mutual fund holdings data via akshare."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_fund_holdings_cn",
            description=(
                "Get China A-share mutual fund holdings data for a stock, "
                "including which funds hold the stock and their position sizes. "
                "Only available for China A-share market (SS/SZ symbols)."
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
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = _normalize_symbol(kwargs.get("symbol"))

        # Extract the bare 6-digit stock code for akshare
        stock_code = symbol.split(".")[0]

        from app.services.providers import get_provider_router

        router = await get_provider_router()
        result = await router.akshare.get_fund_holdings_cn(stock_code)

        if not result:
            return SkillResult(
                success=False,
                error=f"No fund holdings data available for {symbol}",
            )

        return SkillResult(success=True, data=result)
