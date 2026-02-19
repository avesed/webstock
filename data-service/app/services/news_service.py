"""News aggregation service for the data-service.

Orchestrates multiple news providers (Finnhub, YFinance, AKShare) with
market-based routing and Redis caching. Only raw news fetching is handled
here -- entity extraction, LLM processing, and database writes remain
in the main backend.

Provider routing:
- US stocks: YFinance (primary) + Finnhub (fallback)
- HK stocks: YFinance Ticker (primary) + Finnhub (fallback)
- A-shares (SH/SZ): AKShare/Eastmoney
- Precious metals: YFinance Ticker
- General/trending: Finnhub (US) + AKShare (CN)
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

from app.core.cache import cache_get, cache_set
from app.providers.akshare_news_provider import AKShareNewsProvider
from app.providers.constants import HK, METAL, SH, SZ, US, detect_market
from app.providers.finnhub_provider import FinnhubNewsProvider
from app.providers.yfinance_news_provider import YFinanceNewsProvider

logger = logging.getLogger(__name__)

# Cache TTL: 30 min base + random 0-5 min to prevent thundering herd
_NEWS_CACHE_BASE_TTL = 1800  # 30 minutes
_NEWS_CACHE_RAND_TTL = 300   # 5 minutes


def _cache_ttl() -> int:
    """Get cache TTL with randomization to prevent thundering herd."""
    return _NEWS_CACHE_BASE_TTL + random.randint(0, _NEWS_CACHE_RAND_TTL)


class NewsService:
    """Multi-source news aggregation service.

    Stateless orchestration layer that routes requests to the appropriate
    provider based on market detection and returns plain dicts.
    """

    def __init__(self) -> None:
        self._finnhub = FinnhubNewsProvider()
        self._yfinance = YFinanceNewsProvider()
        self._akshare = AKShareNewsProvider()

    async def get_company_news(
        self,
        symbol: str,
        market: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get news for a specific stock symbol.

        Routes to the appropriate provider based on market and optional
        source preference. Results are cached in Redis.

        Args:
            symbol: Stock symbol (e.g., AAPL, 0700.HK, 600519.SS).
            market: Market override. Auto-detected from symbol if not provided.
            from_date: Start date YYYY-MM-DD (Finnhub only).
            to_date: End date YYYY-MM-DD (Finnhub only).
            source: Preferred source (finnhub, yfinance, akshare, auto).
                Defaults to market-appropriate provider.

        Returns:
            List of dicts matching NewsArticle model.
        """
        if not market:
            market = detect_market(symbol)

        # Check cache
        cache_key = f"news:company:{symbol.upper()}:{source or 'auto'}"
        cached = await cache_get(cache_key)
        if cached is not None:
            logger.debug("Cache hit for news: %s", symbol)
            return cached

        articles: List[Dict[str, Any]] = []

        if market == US:
            articles = await self._fetch_us_news(symbol, from_date, to_date, source)
        elif market == METAL:
            articles = await self._yfinance.get_news_by_ticker(symbol, news_count=20)
        elif market == HK:
            articles = await self._fetch_hk_news(symbol)
        else:
            # SH or SZ (A-shares)
            articles = await self._akshare.get_news_cn(symbol)

        if articles:
            await cache_set(cache_key, articles, ttl=_cache_ttl())

        return articles

    async def _fetch_us_news(
        self,
        symbol: str,
        from_date: Optional[str],
        to_date: Optional[str],
        source: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Fetch US stock news with provider selection and fallback."""
        if source == "finnhub":
            return await self._finnhub.get_company_news(symbol, from_date, to_date)

        # Default: YFinance primary, Finnhub fallback
        articles = await self._yfinance.get_news(symbol, news_count=20)
        if not articles:
            logger.info(
                "YFinance returned no news for %s, falling back to Finnhub", symbol,
            )
            articles = await self._finnhub.get_company_news(symbol, from_date, to_date)
        return articles

    async def _fetch_hk_news(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch HK stock news with YFinance Ticker primary, Finnhub fallback."""
        articles = await self._yfinance.get_news_by_ticker(symbol, news_count=20)
        if not articles:
            logger.info(
                "YFinance Ticker returned no HK news for %s, trying Finnhub", symbol,
            )
            # Finnhub uses symbols without .HK suffix
            articles = await self._finnhub.get_company_news(
                symbol.replace(".HK", ""),
            )
        return articles

    async def get_general_news(
        self,
        category: str = "general",
    ) -> List[Dict[str, Any]]:
        """Get general market news from Finnhub.

        Args:
            category: News category (general, forex, crypto, merger).

        Returns:
            List of dicts matching NewsArticle model.
        """
        cache_key = f"news:general:{category}"
        cached = await cache_get(cache_key)
        if cached is not None:
            logger.debug("Cache hit for general news: %s", category)
            return cached

        articles = await self._finnhub.get_general_news(category)

        if articles:
            await cache_set(cache_key, articles, ttl=_cache_ttl())

        return articles

    async def get_trending_cn_news(self) -> List[Dict[str, Any]]:
        """Get trending Chinese A-share market news from AKShare.

        Returns:
            List of dicts matching NewsArticle model.
        """
        cache_key = "news:trending:cn"
        cached = await cache_get(cache_key)
        if cached is not None:
            logger.debug("Cache hit for trending CN news")
            return cached

        articles = await self._akshare.get_trending_news_cn()

        if articles:
            await cache_set(cache_key, articles, ttl=_cache_ttl())

        return articles
