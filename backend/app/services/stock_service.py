"""Multi-source stock data service with fallback support."""

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.data_aggregator import DataAggregator, DataType, get_data_aggregator
from app.services.stock_types import (
    Market,
    DataSource,
    HistoryInterval,
    HistoryPeriod,
    StockQuote,
    StockInfo,
    StockFinancials,
    OHLCVBar,
    StockHistory,
    SearchResult,
    PRECIOUS_METALS,
    METAL_KEYWORDS,
    is_precious_metal,
    search_metals,
    detect_market,
    normalize_symbol,
)

__all__ = [
    # Types re-exported from stock_types for backward compatibility
    "Market",
    "DataSource",
    "HistoryInterval",
    "HistoryPeriod",
    "StockQuote",
    "StockInfo",
    "StockFinancials",
    "OHLCVBar",
    "StockHistory",
    "SearchResult",
    "PRECIOUS_METALS",
    "METAL_KEYWORDS",
    "is_precious_metal",
    "search_metals",
    "detect_market",
    "normalize_symbol",
    # Service
    "StockService",
    "get_stock_service",
    "cleanup_stock_service",
]

logger = logging.getLogger(__name__)


class StockService:
    """
    Multi-source stock data service.

    Uses ProviderRouter for automatic provider selection and fallback.

    Data source strategy:
    - US stocks: yfinance (primary)
    - HK stocks: AKShare (primary), yfinance (fallback)
    - A-shares: AKShare (primary), Tushare (fallback), yfinance (fallback)
    - Precious metals: yfinance only
    """

    def __init__(self, aggregator: Optional[DataAggregator] = None):
        self._aggregator = aggregator
        self._router = None

    async def _get_aggregator(self) -> DataAggregator:
        """Get data aggregator, initialize if needed."""
        if self._aggregator is None:
            self._aggregator = await get_data_aggregator()
        return self._aggregator

    async def _get_router(self):
        """Get provider router, initialize if needed."""
        if self._router is None:
            from app.services.providers import get_provider_router
            self._router = await get_provider_router()
        return self._router

    async def get_quote(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get real-time quote for a stock.

        Args:
            symbol: Stock symbol (AAPL, 0700.HK, 600519.SS, etc.)
            force_refresh: Force fetch from source, skip cache

        Returns:
            Quote data as dict or None if unavailable
        """
        market = detect_market(symbol)
        aggregator = await self._get_aggregator()
        router = await self._get_router()

        async def fetch_quote() -> Optional[Dict[str, Any]]:
            quote = await router.get_quote(symbol, market)
            return quote.to_dict() if quote else None

        return await aggregator.get_data(
            symbol=symbol,
            data_type=DataType.QUOTE,
            fetch_func=fetch_quote,
            force_refresh=force_refresh,
        )

    async def get_history(
        self,
        symbol: str,
        period: HistoryPeriod = HistoryPeriod.ONE_YEAR,
        interval: HistoryInterval = HistoryInterval.DAILY,
        force_refresh: bool = False,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get historical OHLCV data via the canonical disk cache.

        Args:
            symbol: Stock symbol
            period: Time period (1mo, 3mo, 6mo, 1y, 2y, 5y, max) - ignored when start/end provided
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)
            force_refresh: Force fetch from source (currently unused; canonical cache uses TTL)
            start: Optional start date/datetime (e.g. '2025-03-01' or '2025-03-01T09:30:00')
            end: Optional end date/datetime (e.g. '2025-03-15' or '2025-03-15T15:00:00')

        Returns:
            Historical data as dict or None if unavailable
        """
        from app.services.canonical_cache_service import get_canonical_cache_service

        market = detect_market(symbol)
        canonical = await get_canonical_cache_service()

        # Calculate the requested day span
        _PERIOD_DAYS_MAP = {
            "1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180,
            "1y": 365, "2y": 730, "5y": 1825, "max": 99999,
        }

        if start and end:
            try:
                start_dt = datetime.fromisoformat(
                    start.replace("T", " ").split("+")[0].split("Z")[0]
                )
                end_dt = datetime.fromisoformat(
                    end.replace("T", " ").split("+")[0].split("Z")[0]
                )
                period_days = max((end_dt - start_dt).days, 1)
            except (ValueError, TypeError):
                period_days = _PERIOD_DAYS_MAP.get(period.value, 365)
        else:
            period_days = _PERIOD_DAYS_MAP.get(period.value, 365)

        bars = await canonical.get_history(
            symbol=symbol,
            interval=interval.value,
            period_days=period_days,
            market=market,
            start=start,
            end=end,
        )

        if not bars:
            return None

        return {
            "symbol": symbol,
            "interval": interval.value,
            "bars": bars,
            "market": market.value,
            "source": "canonical_cache",
        }

    async def get_info(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get company/commodity information.

        Args:
            symbol: Stock or commodity symbol
            force_refresh: Force fetch from source

        Returns:
            Info as dict or None if unavailable
        """
        market = detect_market(symbol)
        aggregator = await self._get_aggregator()
        router = await self._get_router()

        async def fetch_info() -> Optional[Dict[str, Any]]:
            # Handle precious metals with static metadata
            if market == Market.METAL:
                metal_info = PRECIOUS_METALS.get(symbol.upper())
                if metal_info:
                    logger.info(f"Returning static info for precious metal: {symbol}")
                    return {
                        "symbol": symbol.upper(),
                        "name": metal_info["name"],
                        "name_zh": metal_info["name_zh"],
                        "description": f"{metal_info['name']} ({metal_info['name_zh']}) futures contract traded on {metal_info['exchange']}. Unit: {metal_info['unit']}.",
                        "sector": "Commodities",
                        "industry": "Precious Metals",
                        "website": None,
                        "employees": None,
                        "market_cap": None,
                        "currency": metal_info["currency"],
                        "exchange": metal_info["exchange"],
                        "market": market.value,
                        "source": "static",
                        "unit": metal_info["unit"],
                    }
                return None

            info = await router.get_info(symbol, market)
            return info.to_dict() if info else None

        return await aggregator.get_data(
            symbol=symbol,
            data_type=DataType.INFO,
            fetch_func=fetch_info,
            force_refresh=force_refresh,
        )

    async def get_financials(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get financial metrics.

        Args:
            symbol: Stock symbol
            force_refresh: Force fetch from source

        Returns:
            Financial data as dict or None if unavailable.
            Returns None for precious metals (no fundamental data).
        """
        market = detect_market(symbol)

        # Precious metals don't have traditional financial metrics
        if market == Market.METAL:
            logger.debug(f"Skipping financials for precious metal: {symbol}")
            return None

        aggregator = await self._get_aggregator()
        router = await self._get_router()

        async def fetch_financials() -> Optional[Dict[str, Any]]:
            financials = await router.get_financials(symbol, market)
            return financials.to_dict() if financials else None

        return await aggregator.get_data(
            symbol=symbol,
            data_type=DataType.FINANCIAL,
            fetch_func=fetch_financials,
            force_refresh=force_refresh,
        )

    async def search(
        self,
        query: str,
        markets: Optional[List[Market]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for stocks and precious metals across markets.

        Args:
            query: Search query (symbol or name)
            markets: Markets to search (default: all including METAL)

        Returns:
            List of search results
        """
        if not query or len(query) < 1:
            return []

        if markets is None:
            markets = [Market.US, Market.HK, Market.SH, Market.SZ, Market.METAL]

        aggregator = await self._get_aggregator()
        router = await self._get_router()

        # Use cache for search results
        cache_key = hashlib.md5(f"{query}:{','.join(m.value for m in markets)}".encode()).hexdigest()[:12]

        async def fetch_search() -> List[Dict[str, Any]]:
            results = await router.search(query, markets)
            # Filter by requested markets
            filtered = [r for r in results if r.market in markets]
            return [r.to_dict() for r in filtered[:50]]

        return await aggregator.get_data(
            symbol=cache_key,
            data_type=DataType.SEARCH,
            fetch_func=fetch_search,
        ) or []

    async def get_batch_quotes(
        self,
        symbols: List[str],
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Get quotes for multiple symbols efficiently.

        Args:
            symbols: List of stock symbols

        Returns:
            Dict mapping symbol to quote data
        """
        aggregator = await self._get_aggregator()

        async def fetch_single_quote(symbol: str) -> Optional[Dict[str, Any]]:
            return await self.get_quote(symbol)

        return await aggregator.get_batch_data(
            symbols=symbols,
            data_type=DataType.QUOTE,
            fetch_func=fetch_single_quote,
        )


# Singleton instance
_stock_service: Optional[StockService] = None
_stock_service_lock = asyncio.Lock()


async def get_stock_service() -> StockService:
    """Get singleton stock service instance."""
    global _stock_service
    if _stock_service is None:
        async with _stock_service_lock:
            if _stock_service is None:  # double-check after acquiring lock
                _stock_service = StockService()
    return _stock_service


async def cleanup_stock_service() -> None:
    """Cleanup stock service resources."""
    global _stock_service
    if _stock_service is not None:
        _stock_service = None
