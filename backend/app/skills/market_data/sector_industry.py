"""Skill: get sector and industry classification for a stock."""

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


class GetSectorIndustrySkill(BaseSkill):
    """Fetch sector and industry classification.

    Routes to yfinance for US/HK markets and akshare for China A-shares.
    """

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_sector_industry",
            description=(
                "Get sector and industry classification for a stock. "
                "Routes to the appropriate provider based on market: "
                "yfinance for US/HK, akshare for China A-shares."
            ),
            category="market_data",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock ticker (e.g. AAPL, 0700.HK, 600519.SS)",
                    required=True,
                ),
                SkillParameter(
                    name="market",
                    type="string",
                    description=(
                        "Market identifier to determine provider routing. "
                        "Use 'CN' or 'A' for China A-shares (akshare), "
                        "anything else uses yfinance (US, HK, etc.)."
                    ),
                    required=False,
                    enum=["US", "HK", "CN", "A"],
                    default="US",
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = _normalize_symbol(kwargs.get("symbol"))
        market = kwargs.get("market", "US")

        from app.services.providers import get_provider_router

        router = await get_provider_router()

        if market in ("CN", "A"):
            # China A-share: use akshare provider with bare stock code
            stock_code = symbol.split(".")[0]
            result = await router.akshare.get_stock_industry_cn(stock_code)
        else:
            # US, HK, or other: use yfinance provider
            result = await router.yfinance.get_sector_industry(symbol)

        if not result:
            return SkillResult(
                success=False,
                error=f"No sector/industry data available for {symbol}",
            )

        return SkillResult(success=True, data=result)
