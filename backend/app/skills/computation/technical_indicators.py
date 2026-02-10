"""Skill: calculate technical indicators from OHLCV bar data."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


def _calculate_technical_indicators(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate technical indicators from price history.

    Extracted from analysis_nodes.py for reuse across agents and chat tools.
    """
    if not bars or len(bars) < 20:
        return {}

    try:
        import pandas as pd

        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.sort_index()

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        indicators: Dict[str, Any] = {}

        # Moving Averages
        if len(df) >= 20:
            indicators["sma_20"] = float(df["close"].tail(20).mean())
        if len(df) >= 50:
            indicators["sma_50"] = float(df["close"].tail(50).mean())
        if len(df) >= 200:
            indicators["sma_200"] = float(df["close"].tail(200).mean())

        # RSI (14-period)
        if len(df) >= 15:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            indicators["rsi_14"] = float(rsi.iloc[-1])

        # MACD
        if len(df) >= 35:
            ema_12 = df["close"].ewm(span=12, adjust=False).mean()
            ema_26 = df["close"].ewm(span=26, adjust=False).mean()
            macd_line = ema_12 - ema_26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line

            indicators["macd"] = float(macd_line.iloc[-1])
            indicators["macd_signal"] = float(signal_line.iloc[-1])
            indicators["macd_histogram"] = float(histogram.iloc[-1])

        # Volume ratio
        if len(df) >= 20:
            avg_vol = df["volume"].tail(20).mean()
            current_vol = df["volume"].iloc[-1]
            if avg_vol > 0:
                indicators["volume_ratio"] = float(current_vol / avg_vol)

        # Volatility
        if len(df) >= 20:
            returns = df["close"].pct_change().tail(20)
            indicators["volatility_20d"] = float(returns.std() * 100 * (252**0.5))

        return indicators

    except Exception as e:
        logger.error("Error calculating technical indicators: %s", e)
        return {}


class CalculateTechnicalIndicatorsSkill(BaseSkill):
    """Calculate SMA, RSI, MACD, volume ratio, and volatility from OHLCV bars."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="calculate_technical_indicators",
            description=(
                "Calculate technical indicators (SMA 20/50/200, RSI 14, MACD, "
                "volume ratio, 20-day volatility) from OHLCV bar data."
            ),
            category="computation",
            parameters=[
                SkillParameter(
                    name="bars",
                    type="array",
                    description="OHLCV bar data list with date, open, high, low, close, volume fields",
                    required=True,
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

        indicators = _calculate_technical_indicators(bars)

        if not indicators:
            return SkillResult(
                success=False,
                error="Insufficient data to calculate indicators (need at least 20 bars)",
            )

        return SkillResult(
            success=True,
            data=indicators,
            metadata={"bar_count": len(bars)},
        )
