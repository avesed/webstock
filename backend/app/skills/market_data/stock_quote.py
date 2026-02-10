"""Skill: get real-time stock quote data."""

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


class GetStockQuoteSkill(BaseSkill):
    """Fetch real-time stock quote including price, change, volume, and market cap."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_stock_quote",
            description=(
                "Get real-time stock quote including price, change, volume, "
                "and market cap. Use when the user asks about current price."
            ),
            category="market_data",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock ticker (e.g. AAPL, 0700.HK, 600519.SS)",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = _normalize_symbol(kwargs.get("symbol"))

        from app.services.stock_service import get_stock_service

        service = await get_stock_service()
        result = await service.get_quote(symbol)

        if not result:
            return SkillResult(
                success=False,
                error=f"No quote data available for {symbol}",
            )

        return SkillResult(success=True, data=result)
