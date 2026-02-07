"""Technical analysis agent."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from app.agents.base import AgentType, BaseAgent
from app.prompts.analysis.technical_prompt import (
    build_technical_prompt,
    get_system_prompt,
)
from app.core.circuit_breaker import CircuitBreaker
from app.core.token_bucket import TokenBucket
from app.services.stock_service import (
    HistoryInterval,
    HistoryPeriod,
    get_stock_service,
)

logger = logging.getLogger(__name__)


class TechnicalAgent(BaseAgent):
    """
    Agent for technical analysis of stocks.

    Analyzes:
    - Price trends (short, medium, long term)
    - Support and resistance levels
    - Moving averages (SMA, EMA)
    - Momentum indicators (RSI, MACD)
    - Volume patterns
    - Volatility (ATR, Bollinger Bands)
    """

    def __init__(
        self,
        rate_limiter: Optional[TokenBucket] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        super().__init__(rate_limiter, circuit_breaker)

    @property
    def agent_type(self) -> AgentType:
        """Return the agent type."""
        return AgentType.TECHNICAL

    def get_system_prompt(self, market: str, language: str = "en") -> str:
        """Get the system prompt for technical analysis."""
        return get_system_prompt(market, language)

    async def build_user_prompt(
        self,
        symbol: str,
        market: str,
        data: Dict[str, Any],
        language: str = "en",
    ) -> str:
        """Build the user prompt with technical data."""
        return build_technical_prompt(
            symbol=symbol,
            market=market,
            quote=data.get("quote"),
            indicators=data.get("indicators"),
            history_summary=data.get("history_summary"),
            language=language,
        )

    async def prepare_data(
        self,
        symbol: str,
        market: str,
    ) -> Dict[str, Any]:
        """
        Prepare technical data for analysis.

        Fetches:
        - Current quote
        - Historical price data
        - Calculates technical indicators using ta library
        """
        stock_service = await get_stock_service()

        # Fetch data
        import asyncio

        quote_task = stock_service.get_quote(symbol)
        history_task = stock_service.get_history(
            symbol,
            period=HistoryPeriod.ONE_YEAR,
            interval=HistoryInterval.DAILY,
        )

        quote, history = await asyncio.gather(
            quote_task,
            history_task,
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(quote, Exception):
            logger.warning(f"Failed to get quote for {symbol}: {quote}")
            quote = None
        if isinstance(history, Exception):
            logger.warning(f"Failed to get history for {symbol}: {history}")
            history = None

        # Calculate indicators if we have history
        indicators = None
        history_summary = None

        if history and history.get("bars"):
            df = self._create_dataframe(history["bars"])
            if not df.empty:
                indicators = self._calculate_indicators(df)
                history_summary = self._summarize_history(df, history["bars"])

        return {
            "quote": quote,
            "indicators": indicators,
            "history_summary": history_summary,
        }

    def _create_dataframe(self, bars: List[Dict[str, Any]]) -> pd.DataFrame:
        """Create a pandas DataFrame from OHLCV bars."""
        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.sort_index()

        # Ensure numeric columns
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _calculate_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculate technical indicators using ta library.

        Args:
            df: DataFrame with OHLCV data

        Returns:
            Dictionary of indicator values
        """
        if df.empty or len(df) < 20:
            return {}

        try:
            from ta.momentum import RSIIndicator
            from ta.trend import MACD, SMAIndicator, EMAIndicator
            from ta.volatility import AverageTrueRange, BollingerBands
            from ta.volume import OnBalanceVolumeIndicator

            indicators = {}

            # Moving Averages
            if len(df) >= 20:
                sma20 = SMAIndicator(df["close"], window=20).sma_indicator()
                if sma20 is not None and len(sma20) > 0:
                    indicators["sma_20"] = float(sma20.iloc[-1]) if pd.notna(sma20.iloc[-1]) else None

            if len(df) >= 50:
                sma50 = SMAIndicator(df["close"], window=50).sma_indicator()
                if sma50 is not None and len(sma50) > 0:
                    indicators["sma_50"] = float(sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else None

            if len(df) >= 200:
                sma200 = SMAIndicator(df["close"], window=200).sma_indicator()
                if sma200 is not None and len(sma200) > 0:
                    indicators["sma_200"] = float(sma200.iloc[-1]) if pd.notna(sma200.iloc[-1]) else None

            # EMA for MACD
            ema12 = EMAIndicator(df["close"], window=12).ema_indicator()
            ema26 = EMAIndicator(df["close"], window=26).ema_indicator()
            if ema12 is not None and len(ema12) > 0:
                indicators["ema_12"] = float(ema12.iloc[-1]) if pd.notna(ema12.iloc[-1]) else None
            if ema26 is not None and len(ema26) > 0:
                indicators["ema_26"] = float(ema26.iloc[-1]) if pd.notna(ema26.iloc[-1]) else None

            # RSI
            rsi = RSIIndicator(df["close"], window=14).rsi()
            if rsi is not None and len(rsi) > 0:
                indicators["rsi_14"] = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None

            # MACD
            macd_indicator = MACD(df["close"])
            macd_line = macd_indicator.macd()
            macd_signal = macd_indicator.macd_signal()
            macd_diff = macd_indicator.macd_diff()
            if macd_line is not None and len(macd_line) > 0:
                indicators["macd"] = float(macd_line.iloc[-1]) if pd.notna(macd_line.iloc[-1]) else None
            if macd_diff is not None and len(macd_diff) > 0:
                indicators["macd_hist"] = float(macd_diff.iloc[-1]) if pd.notna(macd_diff.iloc[-1]) else None
            if macd_signal is not None and len(macd_signal) > 0:
                indicators["macd_signal"] = float(macd_signal.iloc[-1]) if pd.notna(macd_signal.iloc[-1]) else None

            # ATR
            atr = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
            if atr is not None and len(atr) > 0:
                indicators["atr_14"] = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else None

            # Bollinger Bands
            bb = BollingerBands(df["close"], window=20)
            bb_lower = bb.bollinger_lband()
            bb_middle = bb.bollinger_mavg()
            bb_upper = bb.bollinger_hband()
            if bb_lower is not None and len(bb_lower) > 0:
                indicators["bb_lower"] = float(bb_lower.iloc[-1]) if pd.notna(bb_lower.iloc[-1]) else None
            if bb_middle is not None and len(bb_middle) > 0:
                indicators["bb_middle"] = float(bb_middle.iloc[-1]) if pd.notna(bb_middle.iloc[-1]) else None
            if bb_upper is not None and len(bb_upper) > 0:
                indicators["bb_upper"] = float(bb_upper.iloc[-1]) if pd.notna(bb_upper.iloc[-1]) else None

            # Volume ratio (current vs 20-day average)
            if len(df) >= 20:
                avg_vol = df["volume"].tail(20).mean()
                current_vol = df["volume"].iloc[-1]
                if avg_vol > 0:
                    indicators["volume_ratio"] = float(current_vol / avg_vol)

            # OBV trend
            obv = OnBalanceVolumeIndicator(df["close"], df["volume"]).on_balance_volume()
            if obv is not None and len(obv) >= 20:
                obv_sma = obv.tail(20).mean()
                obv_current = obv.iloc[-1]
                if obv_current > obv_sma:
                    indicators["obv_trend"] = "Rising (Accumulation)"
                else:
                    indicators["obv_trend"] = "Falling (Distribution)"

            return indicators

        except ImportError:
            logger.warning("ta library not installed, returning empty indicators")
            return {}
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return {}

    def _summarize_history(
        self,
        df: pd.DataFrame,
        bars: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Create a summary of price history.

        Args:
            df: DataFrame with OHLCV data
            bars: Original bar data

        Returns:
            Dictionary with history summary
        """
        if df.empty:
            return {}

        summary = {}

        try:
            current_price = df["close"].iloc[-1]

            # 52-week high/low
            if len(df) >= 252:
                year_data = df.tail(252)
            else:
                year_data = df

            summary["high_52w"] = float(year_data["high"].max())
            summary["low_52w"] = float(year_data["low"].min())

            # Distance from high/low
            if summary["high_52w"] > 0:
                summary["pct_from_high"] = ((current_price - summary["high_52w"]) / summary["high_52w"]) * 100
            if summary["low_52w"] > 0:
                summary["pct_from_low"] = ((current_price - summary["low_52w"]) / summary["low_52w"]) * 100

            # Price changes
            if len(df) >= 1:
                summary["change_1d"] = ((current_price - df["close"].iloc[-2]) / df["close"].iloc[-2]) * 100 if len(df) >= 2 else None

            if len(df) >= 5:
                summary["change_1w"] = ((current_price - df["close"].iloc[-5]) / df["close"].iloc[-5]) * 100

            if len(df) >= 22:
                summary["change_1m"] = ((current_price - df["close"].iloc[-22]) / df["close"].iloc[-22]) * 100

            if len(df) >= 66:
                summary["change_3m"] = ((current_price - df["close"].iloc[-66]) / df["close"].iloc[-66]) * 100

            if len(df) >= 252:
                summary["change_1y"] = ((current_price - df["close"].iloc[-252]) / df["close"].iloc[-252]) * 100

            # Average volume
            if len(df) >= 20:
                summary["avg_volume_20d"] = int(df["volume"].tail(20).mean())

            # Volume ratio
            if len(df) >= 20:
                avg_vol = df["volume"].tail(20).mean()
                current_vol = df["volume"].iloc[-1]
                if avg_vol > 0:
                    summary["volume_ratio"] = current_vol / avg_vol
                    if summary["volume_ratio"] > 1.5:
                        summary["volume_trend"] = "Above average"
                    elif summary["volume_ratio"] < 0.5:
                        summary["volume_trend"] = "Below average"
                    else:
                        summary["volume_trend"] = "Normal"

            # Volatility (20-day standard deviation of returns)
            if len(df) >= 20:
                returns = df["close"].pct_change().tail(20)
                summary["volatility_20d"] = float(returns.std() * 100 * (252 ** 0.5))  # Annualized

            # Recent prices for table
            summary["recent_prices"] = bars[-5:] if len(bars) >= 5 else bars

        except Exception as e:
            logger.error(f"Error summarizing history: {e}")

        return summary


# Factory function for creating agent with shared resources
async def create_technical_agent(
    rate_limiter: Optional[TokenBucket] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> TechnicalAgent:
    """Create a technical analysis agent."""
    return TechnicalAgent(
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker,
    )
