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

    def get_system_prompt(self, market: str) -> str:
        """Get the system prompt for sentiment analysis."""
        return get_system_prompt(market)

    async def build_user_prompt(
        self,
        symbol: str,
        market: str,
        data: Dict[str, Any],
    ) -> str:
        """Build the user prompt with sentiment data."""
        return build_sentiment_prompt(
            symbol=symbol,
            market=market,
            quote=data.get("quote"),
            history_summary=data.get("history_summary"),
            news=data.get("news"),
            market_context=data.get("market_context"),
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
        - Market context (placeholder for now)
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

        # Calculate history summary for momentum analysis
        history_summary = None
        if history and history.get("bars"):
            history_summary = self._calculate_momentum_metrics(history["bars"])

        # News placeholder - will be populated when news service is implemented
        news = await self._fetch_news(symbol, market)

        # Market context placeholder
        market_context = await self._get_market_context(symbol, market)

        return {
            "quote": quote,
            "history_summary": history_summary,
            "news": news,
            "market_context": market_context,
        }

    def _calculate_momentum_metrics(
        self,
        bars: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Calculate momentum and sentiment metrics from price history.

        Args:
            bars: OHLCV bar data

        Returns:
            Dictionary with momentum metrics
        """
        if not bars:
            return {}

        try:
            df = pd.DataFrame(bars)
            df["date"] = pd.to_datetime(df["date"])
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

            return summary

        except Exception as e:
            logger.error(f"Error calculating momentum metrics: {e}")
            return {}

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
        Get broader market context.

        Currently a placeholder - will add market indices comparison.

        Args:
            symbol: Stock symbol
            market: Market identifier

        Returns:
            Market context data or None
        """
        # TODO: Implement market context
        # - Fetch market index data (SPY, HSI, SSE Composite)
        # - Compare stock performance vs index
        # - Add sector analysis

        # Placeholder context based on market
        context = {
            "market_trend": "See current market conditions",
            "sector_trend": "Sector analysis pending",
            "relative_strength": "Relative performance calculation pending",
        }

        return context


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
