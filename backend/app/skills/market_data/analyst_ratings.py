"""Skill: get analyst ratings and recommendations."""

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
        symbol = _normalize_symbol(kwargs.get("symbol"))

        from app.services.providers import get_provider_router

        router = await get_provider_router()
        result = await router.yfinance.get_analyst_ratings(symbol)

        if not result:
            return SkillResult(
                success=False,
                error=f"No analyst ratings available for {symbol}",
            )

        return SkillResult(success=True, data=result)
