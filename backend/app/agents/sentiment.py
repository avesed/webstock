"""Sentiment analysis agent."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from app.agents.base import AgentType, BaseAgent
from app.agents.prompts.sentiment_prompt import (
    build_sentiment_prompt,
    get_system_prompt,
)
from app.core.circuit_breaker import CircuitBreaker
from app.core.token_bucket import TokenBucket
from app.services.providers import get_provider_router
from app.services.stock_service import (
    HistoryInterval,
    HistoryPeriod,
    get_stock_service,
)

logger = logging.getLogger(__name__)


class SentimentAgent(BaseAgent):
    """
    Agent for sentiment analysis of stocks.

    Analyzes:
    - Price momentum as sentiment indicator
    - Volume patterns (accumulation/distribution)
    - News sentiment (when available)
    - Market context and relative strength
    - Potential catalysts
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
        return AgentType.SENTIMENT

    def get_system_prompt(self, market: str, language: str = "en") -> str:
        """Get the system prompt for sentiment analysis."""
        return get_system_prompt(market, language)

    async def build_user_prompt(
        self,
        symbol: str,
        market: str,
        data: Dict[str, Any],
        language: str = "en",
    ) -> str:
        """Build the user prompt with sentiment data."""
        return build_sentiment_prompt(
            symbol=symbol,
            market=market,
            quote=data.get("quote"),
            history_summary=data.get("history_summary"),
            news=data.get("news"),
            market_context=data.get("market_context"),
            analyst_ratings=data.get("analyst_ratings"),
            language=language,
        )

    async def prepare_data(
        self,
        symbol: str,
        market: str,
    ) -> Dict[str, Any]:
        """
        Prepare sentiment data for analysis.

        Fetches:
        - Current quote
        - Historical price data for momentum analysis
        - News (when news service is available)
        - Market context
        - Analyst ratings and price targets
        - Technical data from yfinance (SMA 50/200, ADTV, etc.)
        """
        import asyncio

        stock_service = await get_stock_service()
        router = await get_provider_router()

        # Fetch data in parallel
        quote_task = stock_service.get_quote(symbol)
        history_task = stock_service.get_history(
            symbol,
            period=HistoryPeriod.ONE_YEAR,
            interval=HistoryInterval.DAILY,
        )
        analyst_task = router.yfinance.get_analyst_ratings(symbol)
        # Get technical data from yfinance (SMA 50/200, ADTV, beta, etc.)
        # This is separate from stock_service.get_info() which returns structured StockInfo
        technical_task = router.yfinance.get_technical_info(symbol)

        quote, history, analyst_ratings, technical_info = await asyncio.gather(
            quote_task,
            history_task,
            analyst_task,
            technical_task,
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(quote, Exception):
            logger.warning(f"Failed to get quote for {symbol}: {quote}")
            quote = None
        if isinstance(history, Exception):
            logger.warning(f"Failed to get history for {symbol}: {history}")
            history = None
        if isinstance(analyst_ratings, Exception):
            logger.warning(f"Failed to get analyst ratings for {symbol}: {analyst_ratings}")
            analyst_ratings = None
        if isinstance(technical_info, Exception):
            logger.warning(f"Failed to get technical info for {symbol}: {technical_info}")
            technical_info = None

        # Calculate history summary for momentum analysis
        # Pass yfinance technical data for pre-calculated indicators
        history_summary = None
        if history and history.get("bars"):
            history_summary = self._calculate_momentum_metrics(history["bars"], technical_info)

        # News placeholder - will be populated when news service is implemented
        news = await self._fetch_news(symbol, market)

        # Market context placeholder
        market_context = await self._get_market_context(symbol, market)

        return {
            "quote": quote,
            "history_summary": history_summary,
            "news": news,
            "market_context": market_context,
            "analyst_ratings": analyst_ratings,
        }

    def _calculate_momentum_metrics(
        self,
        bars: List[Dict[str, Any]],
        yf_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate momentum and sentiment metrics from price history.

        Uses yfinance pre-calculated data where available (SMA 50/200, ADTV),
        and calculates RSI/MACD/SMA20 from price history.

        Args:
            bars: OHLCV bar data
            yf_info: yfinance info dict with pre-calculated technical data

        Returns:
            Dictionary with momentum metrics
        """
        if not bars:
            return {}

        try:
            df = pd.DataFrame(bars)
            # Handle mixed timezones by converting to UTC and removing timezone
            df["date"] = pd.to_datetime(df["date"], utc=True)
            df["date"] = df["date"].dt.tz_localize(None)
            df.set_index("date", inplace=True)
            df = df.sort_index()

            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            if df.empty:
                return {}

            current_price = df["close"].iloc[-1]
            summary = {}

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

            # Price changes at different timeframes
            if len(df) >= 2:
                summary["change_1d"] = ((current_price - df["close"].iloc[-2]) / df["close"].iloc[-2]) * 100

            if len(df) >= 5:
                summary["change_1w"] = ((current_price - df["close"].iloc[-5]) / df["close"].iloc[-5]) * 100

            if len(df) >= 22:
                summary["change_1m"] = ((current_price - df["close"].iloc[-22]) / df["close"].iloc[-22]) * 100

            if len(df) >= 66:
                summary["change_3m"] = ((current_price - df["close"].iloc[-66]) / df["close"].iloc[-66]) * 100

            # YTD change
            current_year = datetime.now().year
            ytd_data = df[df.index.year == current_year]
            if len(ytd_data) > 1:
                ytd_start = ytd_data["close"].iloc[0]
                summary["change_ytd"] = ((current_price - ytd_start) / ytd_start) * 100

            if len(df) >= 252:
                summary["change_1y"] = ((current_price - df["close"].iloc[-252]) / df["close"].iloc[-252]) * 100

            # Volume analysis
            if len(df) >= 20:
                summary["avg_volume_20d"] = int(df["volume"].tail(20).mean())
                current_vol = df["volume"].iloc[-1]
                avg_vol = df["volume"].tail(20).mean()

                if avg_vol > 0:
                    summary["volume_ratio"] = current_vol / avg_vol

                    # Volume trend
                    vol_5d = df["volume"].tail(5).mean()
                    vol_20d = df["volume"].tail(20).mean()
                    if vol_5d > vol_20d * 1.2:
                        summary["volume_trend"] = "Increasing"
                    elif vol_5d < vol_20d * 0.8:
                        summary["volume_trend"] = "Decreasing"
                    else:
                        summary["volume_trend"] = "Stable"

            # Volatility
            if len(df) >= 20:
                returns = df["close"].pct_change().tail(20)
                vol_20d = returns.std() * 100 * (252 ** 0.5)  # Annualized
                summary["volatility_20d"] = float(vol_20d)

                # Volatility rank (simple percentile over year)
                if len(df) >= 252:
                    rolling_vol = df["close"].pct_change().rolling(20).std() * 100 * (252 ** 0.5)
                    current_vol_rank = (rolling_vol < vol_20d).mean() * 100
                    if current_vol_rank > 80:
                        summary["volatility_rank"] = "High (top 20%)"
                    elif current_vol_rank < 20:
                        summary["volatility_rank"] = "Low (bottom 20%)"
                    else:
                        summary["volatility_rank"] = "Normal"

            # === Technical Indicators ===
            # Use yfinance pre-calculated data where available, calculate the rest

            # Moving Averages - prefer yfinance data (more accurate)
            if yf_info:
                # SMA 50 from yfinance (fiftyDayAverage)
                if yf_info.get("fiftyDayAverage"):
                    summary["sma_50"] = float(yf_info["fiftyDayAverage"])
                # SMA 200 from yfinance (twoHundredDayAverage)
                if yf_info.get("twoHundredDayAverage"):
                    summary["sma_200"] = float(yf_info["twoHundredDayAverage"])
                # 52-week high/low from yfinance
                if yf_info.get("fiftyTwoWeekHigh"):
                    summary["high_52w"] = float(yf_info["fiftyTwoWeekHigh"])
                if yf_info.get("fiftyTwoWeekLow"):
                    summary["low_52w"] = float(yf_info["fiftyTwoWeekLow"])
                # Beta
                if yf_info.get("beta"):
                    summary["beta"] = float(yf_info["beta"])

            # SMA 20 - must calculate (yfinance doesn't provide)
            if len(df) >= 20:
                summary["sma_20"] = float(df["close"].tail(20).mean())

            # Fallback: calculate SMA 50/200 if yfinance didn't provide
            if "sma_50" not in summary and len(df) >= 50:
                summary["sma_50"] = float(df["close"].tail(50).mean())
            if "sma_200" not in summary and len(df) >= 200:
                summary["sma_200"] = float(df["close"].tail(200).mean())

            # Price vs Moving Averages
            if "sma_20" in summary:
                summary["price_vs_sma20"] = ((current_price - summary["sma_20"]) / summary["sma_20"]) * 100
            if "sma_50" in summary:
                summary["price_vs_sma50"] = ((current_price - summary["sma_50"]) / summary["sma_50"]) * 100
            if "sma_200" in summary:
                summary["price_vs_sma200"] = ((current_price - summary["sma_200"]) / summary["sma_200"]) * 100

            # MA Trend (Golden Cross / Death Cross)
            if "sma_50" in summary and "sma_200" in summary:
                if summary["sma_50"] > summary["sma_200"]:
                    summary["ma_trend"] = "Bullish (SMA50 > SMA200)"
                else:
                    summary["ma_trend"] = "Bearish (SMA50 < SMA200)"

            # RSI (14-period) - must calculate (yfinance doesn't provide)
            if len(df) >= 15:
                summary["rsi_14"] = self._calculate_rsi(df["close"], period=14)

            # MACD - must calculate (yfinance doesn't provide)
            if len(df) >= 35:
                macd_data = self._calculate_macd(df["close"])
                summary.update(macd_data)

            # === Recent Price Series (last 5 trading days) ===
            recent_days = min(5, len(df))
            if recent_days > 0:
                recent_data = df.tail(recent_days)
                price_series = []
                for idx, row in recent_data.iterrows():
                    price_series.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row["volume"]),
                    })
                summary["recent_prices"] = price_series

            # === ADTV (Average Daily Trading Volume) ===
            # Prefer yfinance data
            if yf_info:
                if yf_info.get("averageVolume10days"):
                    summary["adtv_10d"] = int(yf_info["averageVolume10days"])
                if yf_info.get("averageVolume"):
                    summary["adtv_3m"] = int(yf_info["averageVolume"])

            # Calculate remaining ADTV from history
            if "adtv_10d" not in summary and len(df) >= 10:
                summary["adtv_10d"] = int(df["volume"].tail(10).mean())
            if len(df) >= 30:
                summary["adtv_30d"] = int(df["volume"].tail(30).mean())
            if len(df) >= 90:
                summary["adtv_90d"] = int(df["volume"].tail(90).mean())

            return summary

        except Exception as e:
            logger.error(f"Error calculating momentum metrics: {e}")
            return {}

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float:
        """
        Calculate RSI (Relative Strength Index) using Wilder's smoothing method.

        This is the industry-standard algorithm used by TradingView, Yahoo Finance,
        同花顺, 东方财富, and most trading platforms.

        Args:
            prices: Close price series
            period: RSI period (default 14)

        Returns:
            RSI value (0-100)
        """
        delta = prices.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        # Wilder's smoothing uses alpha = 1/period (not the standard EMA alpha = 2/(period+1))
        # This is equivalent to RMA (Running Moving Average) or SMMA (Smoothed MA)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi.iloc[-1])

    def _calculate_macd(
        self,
        prices: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Dict[str, Any]:
        """
        Calculate MACD (Moving Average Convergence Divergence).

        Args:
            prices: Close price series
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal line period (default 9)

        Returns:
            Dictionary with MACD, Signal, and Histogram values
        """
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()

        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        macd_value = float(macd_line.iloc[-1])
        signal_value = float(signal_line.iloc[-1])
        histogram_value = float(histogram.iloc[-1])

        # Determine MACD signal
        if macd_value > signal_value and histogram_value > 0:
            macd_signal = "Bullish"
        elif macd_value < signal_value and histogram_value < 0:
            macd_signal = "Bearish"
        else:
            macd_signal = "Neutral"

        # Check for crossover (recent change in signal)
        if len(histogram) >= 2:
            prev_hist = histogram.iloc[-2]
            if prev_hist < 0 and histogram_value > 0:
                macd_signal = "Bullish Crossover"
            elif prev_hist > 0 and histogram_value < 0:
                macd_signal = "Bearish Crossover"

        return {
            "macd": macd_value,
            "macd_signal": signal_value,
            "macd_histogram": histogram_value,
            "macd_trend": macd_signal,
        }

    async def _fetch_news(
        self,
        symbol: str,
        market: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch news for the stock from the news service.

        Args:
            symbol: Stock symbol
            market: Market identifier

        Returns:
            List of news articles or None
        """
        try:
            from app.services.news_service import get_news_service

            news_service = await get_news_service()
            articles = await news_service.get_news_by_symbol(symbol)

            if not articles:
                return None

            # Normalize camelCase keys to snake_case for prompt template
            normalized = []
            for a in articles[:10]:
                normalized.append({
                    "title": a.get("title", ""),
                    "source": a.get("source", ""),
                    "published_at": a.get("publishedAt", ""),
                    "summary": a.get("summary", ""),
                })
            return normalized if normalized else None
        except Exception as e:
            logger.error(f"Error fetching news for sentiment: {e}")
            return None

    async def _get_market_context(
        self,
        symbol: str,
        market: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get broader market context from ProviderRouter.

        Fetches:
        - Major market indices (S&P 500, HSI, Shanghai, Shenzhen)
        - Northbound capital flow summary (for A-shares)
        - Individual stock northbound holding (for A-shares)

        Args:
            symbol: Stock symbol
            market: Market identifier

        Returns:
            Market context data or None
        """
        try:
            router = await get_provider_router()
            context = await router.get_market_context()

            # For A-share stocks, also fetch individual northbound holding
            if market in ("CN", "A"):
                # Extract pure stock code (e.g., "600519" from "600519.SS")
                stock_code = symbol.split(".")[0]
                northbound_holding = await router.akshare.get_northbound_holding(
                    stock_code, days=30
                )
                if northbound_holding:
                    context["northbound_stock_holding"] = northbound_holding

            return context

        except Exception as e:
            logger.error(f"Error fetching market context: {e}")
            return {
                "error": str(e),
                "market_trend": "数据获取失败",
            }


# Factory function for creating agent with shared resources
async def create_sentiment_agent(
    rate_limiter: Optional[TokenBucket] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> SentimentAgent:
    """Create a sentiment analysis agent."""
    return SentimentAgent(
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker,
    )
