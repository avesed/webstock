"""Provider router with market-based routing and fallback chains."""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar

from app.services.providers.base import DataProvider
from app.services.stock_service import (
    HistoryInterval,
    HistoryPeriod,
    Market,
    SearchResult,
    StockFinancials,
    StockHistory,
    StockInfo,
    StockQuote,
    detect_market,
    search_metals,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ProviderRouter:
    """
    Routes data requests to appropriate providers with fallback support.

    Market routing strategy:
    - US stocks: yfinance primary, Tiingo fallback (if available)
    - METAL: yfinance only
    - HK: AKShare primary, yfinance fallback
    - A-shares (SH/SZ): AKShare primary, Tushare fallback (if available), yfinance fallback
    """

    def __init__(
        self,
        yfinance: DataProvider,
        akshare: DataProvider,
        tushare: Optional[DataProvider] = None,
        tiingo: Optional[DataProvider] = None,
    ):
        self._yfinance = yfinance
        self._akshare = akshare
        self._tushare = tushare
        self._tiingo = tiingo

        # Build routing table: Market -> List[Provider] (in priority order)
        tushare_list = [tushare] if tushare and tushare.is_available() else []
        tiingo_list = [tiingo] if tiingo and tiingo.is_available() else []

        self._routing: Dict[Market, List[DataProvider]] = {
            Market.US: [yfinance] + tiingo_list,  # Tiingo as US fallback
            Market.METAL: [yfinance],
            Market.HK: [akshare, yfinance],
            Market.SH: [akshare] + tushare_list + [yfinance],
            Market.SZ: [akshare] + tushare_list + [yfinance],
        }

    def get_providers(self, market: Market) -> List[DataProvider]:
        """Get ordered list of providers for a market."""
        return self._routing.get(market, [self._yfinance])

    async def _try_providers(
        self,
        market: Market,
        operation: str,
        func: Callable[[DataProvider], T],
    ) -> Optional[T]:
        """
        Try providers in order until one succeeds.

        Args:
            market: Target market
            operation: Operation name for logging
            func: Async function that takes a provider and returns result

        Returns:
            Result from first successful provider, or None
        """
        providers = self.get_providers(market)

        for i, provider in enumerate(providers):
            try:
                result = await func(provider)
                if result is not None:
                    if i > 0:
                        logger.info(
                            f"{operation}: Fallback to {provider.source.value} succeeded"
                        )
                    return result
                # Result was None, try next provider
                logger.debug(
                    f"{operation}: {provider.source.value} returned None, trying next"
                )
            except Exception as e:
                logger.warning(f"{operation}: {provider.source.value} failed: {e}")
                continue

        return None

    # === Core Routing Methods ===

    async def get_quote(
        self,
        symbol: str,
        market: Optional[Market] = None,
    ) -> Optional[StockQuote]:
        """Get quote with automatic fallback."""
        if market is None:
            market = detect_market(symbol)

        return await self._try_providers(
            market,
            f"get_quote({symbol})",
            lambda p: p.get_quote(symbol, market),
        )

    async def get_history(
        self,
        symbol: str,
        period: HistoryPeriod,
        interval: HistoryInterval,
        market: Optional[Market] = None,
    ) -> Optional[StockHistory]:
        """Get history with automatic fallback."""
        if market is None:
            market = detect_market(symbol)

        return await self._try_providers(
            market,
            f"get_history({symbol})",
            lambda p: p.get_history(symbol, market, period, interval),
        )

    async def get_info(
        self,
        symbol: str,
        market: Optional[Market] = None,
    ) -> Optional[StockInfo]:
        """Get info with automatic fallback."""
        if market is None:
            market = detect_market(symbol)

        return await self._try_providers(
            market,
            f"get_info({symbol})",
            lambda p: p.get_info(symbol, market),
        )

    async def get_financials(
        self,
        symbol: str,
        market: Optional[Market] = None,
    ) -> Optional[StockFinancials]:
        """Get financials with automatic fallback."""
        if market is None:
            market = detect_market(symbol)

        return await self._try_providers(
            market,
            f"get_financials({symbol})",
            lambda p: p.get_financials(symbol, market),
        )

    async def search(
        self,
        query: str,
        markets: Optional[List[Market]] = None,
    ) -> List[SearchResult]:
        """
        Search across markets with deduplication.

        Metal search is handled specially and always included first.
        """
        if markets is None:
            markets = list(Market)

        results: List[SearchResult] = []

        # Metal search first (special handling)
        if Market.METAL in markets:
            metal_results = search_metals(query)
            results.extend(metal_results)

        # Parallel search across providers
        tasks = []
        if Market.US in markets:
            tasks.append(self._yfinance.search(query, {Market.US}))
        if Market.HK in markets:
            tasks.append(self._akshare.search(query, {Market.HK}))
        if Market.SH in markets or Market.SZ in markets:
            tasks.append(self._akshare.search(query, {Market.SH, Market.SZ}))

        if tasks:
            search_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in search_results:
                if isinstance(result, Exception):
                    logger.error(f"Search error: {result}")
                    continue
                results.extend(result)

        # Deduplicate by symbol (metals added first have priority)
        seen: Set[str] = set()
        unique: List[SearchResult] = []
        for r in results:
            if r.symbol not in seen:
                seen.add(r.symbol)
                unique.append(r)

        return unique[:50]

    # === Direct Provider Access for Extended Features ===

    @property
    def yfinance(self) -> DataProvider:
        """Direct access to yfinance provider for extended features."""
        return self._yfinance

    @property
    def akshare(self) -> DataProvider:
        """Direct access to akshare provider for extended features."""
        return self._akshare

    @property
    def tushare(self) -> Optional[DataProvider]:
        """Direct access to tushare provider (may be None)."""
        return self._tushare

    @property
    def tiingo(self) -> Optional[DataProvider]:
        """Direct access to tiingo provider (may be None)."""
        return self._tiingo

    # === Convenience Methods (combining data from multiple providers) ===

    async def get_market_context(self) -> Dict[str, Any]:
        """
        Get aggregated market context for sentiment analysis.

        Combines:
        - Major market indices (from yfinance)
        - Northbound capital flow summary (from akshare)

        Returns:
            Dict with market overview data
        """
        from datetime import datetime

        # Fetch all data in parallel
        indices_task = self._yfinance.get_all_market_indices(period="5d")
        northbound_task = self._akshare.get_northbound_flow("北向资金", days=10)

        indices, northbound = await asyncio.gather(
            indices_task,
            northbound_task,
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(indices, Exception):
            logger.error(f"Error fetching indices: {indices}")
            indices = {}
        if isinstance(northbound, Exception):
            logger.error(f"Error fetching northbound: {northbound}")
            northbound = None

        # Build northbound summary
        northbound_summary = None
        if northbound and northbound.get("flows"):
            flows = northbound["flows"]
            valid_flows = [f for f in flows if f.get("net_buy") is not None]
            if valid_flows:
                latest = valid_flows[-1]
                total_5d = sum(
                    f["net_buy"] for f in valid_flows[-5:] if f.get("net_buy")
                )
                northbound_summary = {
                    "latest_date": latest.get("date"),
                    "latest_net_buy": latest.get("net_buy"),
                    "last_5d_net_buy": round(total_5d, 2),
                    "cumulative_net_buy": latest.get("cumulative_net_buy"),
                    "data_cutoff_notice": northbound.get("data_cutoff_notice"),
                }

        return {
            "sp500": indices.get("sp500") if indices else None,
            "hang_seng": indices.get("hang_seng") if indices else None,
            "shanghai_composite": indices.get("shanghai") if indices else None,
            "shenzhen_component": indices.get("shenzhen") if indices else None,
            "northbound_summary": northbound_summary,
            "fetched_at": datetime.utcnow().isoformat(),
            "source": "mixed",
        }


# Singleton instance management
_router: Optional[ProviderRouter] = None
_router_lock = asyncio.Lock()


async def get_provider_router() -> ProviderRouter:
    """Get singleton ProviderRouter instance."""
    global _router
    if _router is None:
        async with _router_lock:
            if _router is None:
                from app.services.providers.yfinance import YFinanceProvider
                from app.services.providers.akshare import AKShareProvider
                from app.services.providers.tushare import TushareProvider
                from app.services.providers.tiingo import TiingoProvider

                yfinance = YFinanceProvider()
                akshare = AKShareProvider()
                tushare = TushareProvider() if TushareProvider.is_available() else None
                tiingo = TiingoProvider() if TiingoProvider.is_available() else None

                _router = ProviderRouter(yfinance, akshare, tushare, tiingo)

                providers = ["yfinance", "akshare"]
                if tushare:
                    providers.append("tushare")
                if tiingo:
                    providers.append("tiingo")
                logger.info(f"ProviderRouter initialized: {', '.join(providers)}")
    return _router
