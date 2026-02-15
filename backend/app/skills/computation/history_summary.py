"""Skill: calculate price history summary from OHLCV bar data."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


def _calculate_history_summary(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate price history summary.

    Extracted from analysis_nodes.py for reuse across agents and chat tools.
    """
    if not bars:
        return {}

    try:
        import pandas as pd

        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.sort_index()

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        current_price = df["close"].iloc[-1]
        summary: Dict[str, Any] = {}

        # 52-week high/low
        year_data = df.tail(252) if len(df) >= 252 else df
        summary["high_52w"] = float(year_data["high"].max())
        summary["low_52w"] = float(year_data["low"].min())

        # Price changes
        if len(df) >= 5:
            summary["change_1w"] = (
                (current_price - df["close"].iloc[-5]) / df["close"].iloc[-5]
            ) * 100
        if len(df) >= 22:
            summary["change_1m"] = (
                (current_price - df["close"].iloc[-22]) / df["close"].iloc[-22]
            ) * 100
        if len(df) >= 66:
            summary["change_3m"] = (
                (current_price - df["close"].iloc[-66]) / df["close"].iloc[-66]
            ) * 100

        return summary

    except Exception as e:
        logger.error("Error calculating history summary: %s", e)
        return {}


class CalculateHistorySummarySkill(BaseSkill):
    """Calculate 52-week high/low and period price changes from OHLCV bars."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="calculate_history_summary",
            description=(
                "Calculate price history summary including 52-week high/low "
                "and 1-week, 1-month, 3-month price changes from OHLCV bar data."
            ),
            category="computation",
            parameters=[
                SkillParameter(
                    name="bars",
                    type="array",
                    description="OHLCV bar data list with date, open, high, low, close, volume fields",
                    required=True,
                    items={"type": "object"},
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        bars = kwargs.get("bars")

        if not bars or not isinstance(bars, list):
            return SkillResult(
                success=False,
                error="bars parameter is required and must be a non-empty list",
            )

        summary = _calculate_history_summary(bars)

        if not summary:
            return SkillResult(
                success=False,
                error="Insufficient data to calculate history summary",
            )

        return SkillResult(
            success=True,
            data=summary,
            metadata={"bar_count": len(bars)},
        )
