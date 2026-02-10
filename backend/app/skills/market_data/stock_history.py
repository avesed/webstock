"""Skill: get historical OHLCV price data."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)

VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}
VALID_INTERVALS = {"1m", "5m", "15m", "1h", "1d", "1wk", "1mo"}


def _normalize_symbol(raw: Any) -> str:
    """Sanitize and normalize a stock symbol."""
    from app.prompts.analysis.sanitizer import sanitize_symbol
    from app.utils.symbol_validation import validate_symbol

    sanitized = sanitize_symbol(raw)
    try:
        return validate_symbol(sanitized)
    except Exception:
        return sanitized


class GetStockHistorySkill(BaseSkill):
    """Fetch historical OHLCV price data for trend analysis."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_stock_history",
            description=(
                "Get historical OHLCV price data. Use for trend analysis "
                "or when the user asks about past performance."
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
                    name="period",
                    type="string",
                    description="Time period (default 1y)",
                    required=False,
                    enum=sorted(VALID_PERIODS),
                    default="1y",
                ),
                SkillParameter(
                    name="interval",
                    type="string",
                    description="Data interval (default 1d)",
                    required=False,
                    enum=sorted(VALID_INTERVALS),
                    default="1d",
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        from app.services.stock_service import (
            HistoryInterval,
            HistoryPeriod,
            get_stock_service,
        )

        symbol = _normalize_symbol(kwargs.get("symbol"))
        period_str = kwargs.get("period", "1y")
        interval_str = kwargs.get("interval", "1d")

        # Validate enum values, fall back to defaults
        if period_str not in VALID_PERIODS:
            period_str = "1y"
        if interval_str not in VALID_INTERVALS:
            interval_str = "1d"

        period = HistoryPeriod(period_str)
        interval = HistoryInterval(interval_str)

        service = await get_stock_service()
        data = await service.get_history(symbol, period, interval)

        if not data:
            return SkillResult(
                success=False,
                error=f"No history data available for {symbol}",
            )

        return SkillResult(
            success=True,
            data=data,
            metadata={
                "symbol": symbol,
                "period": period_str,
                "interval": interval_str,
            },
        )
