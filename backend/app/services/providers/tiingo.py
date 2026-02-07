"""Tiingo data provider for US stocks.

Tiingo provides high-quality financial data including:
- End-of-Day (EOD) stock prices
- Real-time IEX quotes
- Fundamentals (daily metrics, quarterly statements)
- News (handled separately in news_service)

API Documentation: https://www.tiingo.com/documentation/
Python Client: https://tiingo-python.readthedocs.io/
"""

import asyncio
import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

from app.db.redis import get_redis
from app.services.providers.base import DataProvider
from app.services.stock_service import (
    DataSource,
    HistoryInterval,
    HistoryPeriod,
    Market,
    OHLCVBar,
    SearchResult,
    StockFinancials,
    StockHistory,
    StockInfo,
    StockQuote,
)

logger = logging.getLogger(__name__)

# Thread pool for synchronous tiingo calls
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()

EXTERNAL_API_TIMEOUT = 30  # seconds

# Cache TTL configurations (base_seconds, random_range_seconds)
CACHE_TTL = {
    "quote": (60, 30),  # 1min + rand(30s) for real-time quotes
    "history": (300, 60),  # 5min + rand(1min)
    "info": (86400, 3600),  # 24h + rand(1h)
    "financials": (86400, 3600),  # 24h + rand(1h)
    "fundamentals_daily": (3600, 600),  # 1h + rand(10min)
}


async def _get_executor() -> ThreadPoolExecutor:
    """Get thread pool executor, initialize if needed."""
    global _executor
    if _executor is None:
        async with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=4)
    return _executor


async def run_in_executor(func: Callable, *args, **kwargs) -> Any:
    """Run synchronous function in thread pool with timeout."""
    loop = asyncio.get_running_loop()
    executor = await _get_executor()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                executor,
                lambda: func(*args, **kwargs),
            ),
            timeout=EXTERNAL_API_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"Executor timeout after {EXTERNAL_API_TIMEOUT}s for function: {func.__name__}"
        )
        raise


def _get_ttl(data_type: str) -> int:
    """Get TTL with randomization to prevent cache avalanche."""
    base, rand_range = CACHE_TTL.get(data_type, (3600, 300))
    return base + random.randint(0, rand_range)


class TiingoProvider(DataProvider):
    """
    Tiingo data provider for US stocks.

    Primary use cases:
    - Alternative/fallback source for US stock data
    - High-quality EOD historical data
    - Fundamentals data (PE, EPS, market cap, etc.)

    Requires TIINGO_API_KEY environment variable.

    API Endpoints:
    - /tiingo/daily/{ticker} - Metadata
    - /tiingo/daily/{ticker}/prices - Historical prices
    - /iex/{ticker} - Real-time IEX quotes
    - /tiingo/fundamentals/{ticker}/daily - Daily fundamentals
    - /tiingo/fundamentals/{ticker}/statements - Quarterly statements
    """

    _api_key: Optional[str] = None
    _client = None

    def __init__(self):
        self._redis = None
        self._cache_prefix = "tiingo:"
        # Check API key on initialization
        if TiingoProvider._api_key is None:
            TiingoProvider._api_key = os.environ.get("TIINGO_API_KEY", "")

    @property
    def source(self) -> DataSource:
        return DataSource.TIINGO

    @property
    def supported_markets(self) -> Set[Market]:
        # Tiingo primarily supports US stocks
        return {Market.US}

    @classmethod
    def is_available(cls) -> bool:
        """Check if Tiingo API key is available."""
        if cls._api_key is None:
            cls._api_key = os.environ.get("TIINGO_API_KEY", "")
        return bool(cls._api_key)

    def _get_client(self):
        """Get or create Tiingo client."""
        if TiingoProvider._client is None and self.is_available():
            try:
                from tiingo import TiingoClient

                config = {"api_key": self._api_key, "session": True}
                TiingoProvider._client = TiingoClient(config)
            except ImportError:
                logger.warning("tiingo package not installed. Run: pip install tiingo")
                return None
            except Exception as e:
                logger.error(f"Failed to initialize Tiingo client: {e}")
                return None
        return TiingoProvider._client

    async def _get_redis(self):
        """Get Redis client."""
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    async def _get_cached_or_fetch(
        self,
        data_type: str,
        identifier: str,
        fetch_func: Callable,
    ) -> Optional[Dict[str, Any]]:
        """Get data from cache or fetch from source."""
        redis = await self._get_redis()
        cache_key = f"{self._cache_prefix}{data_type}:{identifier}"

        # Try cache first
        try:
            cached_data = await redis.get(cache_key)
            if cached_data:
                logger.debug(f"Cache hit: {cache_key}")
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"Cache read error: {e}")

        # Fetch from source
        try:
            data = await fetch_func()
            if data:
                try:
                    ttl = _get_ttl(data_type)
                    await redis.setex(
                        cache_key,
                        ttl,
                        json.dumps(data, default=str),
                    )
                    logger.debug(f"Cached: {cache_key} (TTL: {ttl}s)")
                except Exception as e:
                    logger.warning(f"Cache write error: {e}")
            return data
        except Exception as e:
            logger.error(f"Fetch error for {data_type}/{identifier}: {e}")
            return None

    # === Core Methods ===

    async def get_quote(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockQuote]:
        """Get real-time quote from Tiingo IEX endpoint."""
        if not self.is_available():
            logger.debug("Tiingo API key not configured, skipping")
            return None

        if market != Market.US:
            return None

        try:
            client = self._get_client()
            if not client:
                return None

            def fetch():
                # Use IEX endpoint for real-time quotes
                try:
                    # get_ticker_price returns latest price data
                    data = client.get_ticker_price(symbol)
                    if not data:
                        return None
                    # Returns list, get most recent
                    return data[-1] if isinstance(data, list) else data
                except Exception as e:
                    logger.warning(f"Tiingo IEX quote error: {e}")
                    return None

            data = await run_in_executor(fetch)
            if not data:
                return None

            # Parse Tiingo response
            price = float(data.get("close") or data.get("adjClose", 0))
            prev_close = float(data.get("prevClose", 0)) if data.get("prevClose") else None
            change = price - prev_close if prev_close else 0
            change_pct = (change / prev_close * 100) if prev_close else 0

            return StockQuote(
                symbol=symbol,
                name=None,  # Tiingo price endpoint doesn't include name
                price=price,
                change=round(change, 4),
                change_percent=round(change_pct, 2),
                volume=int(data.get("volume", 0)),
                market_cap=None,
                day_high=float(data.get("high", 0)) if data.get("high") else None,
                day_low=float(data.get("low", 0)) if data.get("low") else None,
                open=float(data.get("open", 0)) if data.get("open") else None,
                previous_close=prev_close,
                timestamp=datetime.utcnow(),
                market=market,
                source=DataSource.TIINGO,
            )
        except Exception as e:
            logger.error(f"Tiingo quote error for {symbol}: {e}")
            return None

    async def get_history(
        self,
        symbol: str,
        market: Market,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """Get historical data from Tiingo EOD endpoint."""
        if not self.is_available():
            return None

        if market != Market.US:
            return None

        # Tiingo only supports daily data for EOD
        if interval not in (
            HistoryInterval.DAILY,
            HistoryInterval.WEEKLY,
            HistoryInterval.MONTHLY,
        ):
            logger.debug(f"Tiingo doesn't support intraday intervals: {interval}")
            return None

        try:
            client = self._get_client()
            if not client:
                return None

            # Calculate date range
            end_date = datetime.now()
            period_days = {
                HistoryPeriod.ONE_DAY: 1,
                HistoryPeriod.FIVE_DAYS: 5,
                HistoryPeriod.ONE_MONTH: 30,
                HistoryPeriod.THREE_MONTHS: 90,
                HistoryPeriod.SIX_MONTHS: 180,
                HistoryPeriod.ONE_YEAR: 365,
                HistoryPeriod.TWO_YEARS: 730,
                HistoryPeriod.FIVE_YEARS: 1825,
                HistoryPeriod.MAX: 7300,  # 20 years
            }
            start_date = end_date - timedelta(days=period_days.get(period, 365))

            # Map interval to Tiingo frequency
            frequency_map = {
                HistoryInterval.DAILY: "daily",
                HistoryInterval.WEEKLY: "weekly",
                HistoryInterval.MONTHLY: "monthly",
            }
            frequency = frequency_map.get(interval, "daily")

            def fetch():
                data = client.get_ticker_price(
                    symbol,
                    startDate=start_date.strftime("%Y-%m-%d"),
                    endDate=end_date.strftime("%Y-%m-%d"),
                    frequency=frequency,
                )
                return data

            data = await run_in_executor(fetch)
            if not data:
                return None

            bars = []
            for row in data:
                date_str = row.get("date", "")
                if isinstance(date_str, str):
                    # Parse ISO format date
                    date_val = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00").split("T")[0]
                    )
                else:
                    date_val = date_str

                bars.append(
                    OHLCVBar(
                        date=date_val,
                        open=round(float(row.get("adjOpen") or row.get("open", 0)), 4),
                        high=round(float(row.get("adjHigh") or row.get("high", 0)), 4),
                        low=round(float(row.get("adjLow") or row.get("low", 0)), 4),
                        close=round(float(row.get("adjClose") or row.get("close", 0)), 4),
                        volume=int(row.get("adjVolume") or row.get("volume", 0)),
                    )
                )

            return StockHistory(
                symbol=symbol,
                interval=interval,
                bars=bars,
                market=market,
                source=DataSource.TIINGO,
            )
        except Exception as e:
            logger.error(f"Tiingo history error for {symbol}: {e}")
            return None

    async def search(
        self,
        query: str,
        markets: Optional[Set[Market]] = None,
    ) -> List[SearchResult]:
        """Search for tickers (limited - Tiingo doesn't have a search API)."""
        # Tiingo doesn't have a search endpoint
        # We can only validate if a ticker exists
        if not self.is_available():
            return []

        try:
            client = self._get_client()
            if not client:
                return []

            def fetch():
                try:
                    metadata = client.get_ticker_metadata(query.upper())
                    if metadata:
                        return [metadata]
                except Exception:
                    pass
                return []

            results = await run_in_executor(fetch)
            return [
                SearchResult(
                    symbol=r.get("ticker", query.upper()),
                    name=r.get("name", ""),
                    exchange=r.get("exchangeCode", ""),
                    market=Market.US,
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"Tiingo search error for {query}: {e}")
            return []

    # === Optional Methods ===

    async def get_info(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockInfo]:
        """Get company info from Tiingo metadata endpoint."""
        if not self.is_available() or market != Market.US:
            return None

        async def fetch():
            client = self._get_client()
            if not client:
                return None

            def _fetch_sync():
                try:
                    metadata = client.get_ticker_metadata(symbol)
                    if not metadata:
                        return None
                    return metadata
                except Exception as e:
                    logger.warning(f"Tiingo metadata error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        data = await self._get_cached_or_fetch("info", symbol, fetch)
        if not data:
            return None

        return StockInfo(
            symbol=symbol,
            name=data.get("name", ""),
            description=data.get("description"),
            sector=None,  # Tiingo metadata doesn't include sector
            industry=None,
            website=None,
            employees=None,
            market_cap=None,
            currency="USD",
            exchange=data.get("exchangeCode", ""),
            market=market,
            source=DataSource.TIINGO,
        )

    async def get_financials(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockFinancials]:
        """Get financial data from Tiingo fundamentals endpoint."""
        if not self.is_available() or market != Market.US:
            return None

        async def fetch():
            client = self._get_client()
            if not client:
                return None

            def _fetch_sync():
                try:
                    # Get daily fundamentals metrics
                    fundamentals = client.get_fundamentals_daily(symbol)
                    if not fundamentals:
                        return None
                    # Returns list, get most recent
                    return fundamentals[-1] if isinstance(fundamentals, list) else fundamentals
                except Exception as e:
                    logger.warning(f"Tiingo fundamentals error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        data = await self._get_cached_or_fetch("financials", symbol, fetch)
        if not data:
            return None

        return StockFinancials(
            symbol=symbol,
            pe_ratio=float(data.get("peRatio", 0)) if data.get("peRatio") else None,
            forward_pe=None,
            eps=float(data.get("epsTTM", 0)) if data.get("epsTTM") else None,
            dividend_yield=(
                float(data.get("divYield", 0)) if data.get("divYield") else None
            ),
            dividend_rate=None,
            book_value=(
                float(data.get("bookVal", 0)) if data.get("bookVal") else None
            ),
            price_to_book=(
                float(data.get("pbRatio", 0)) if data.get("pbRatio") else None
            ),
            revenue=(
                float(data.get("revenue", 0)) if data.get("revenue") else None
            ),
            revenue_growth=None,
            net_income=(
                float(data.get("netIncome", 0)) if data.get("netIncome") else None
            ),
            profit_margin=(
                float(data.get("profitMargin", 0)) if data.get("profitMargin") else None
            ),
            gross_margin=(
                float(data.get("grossMargin", 0)) if data.get("grossMargin") else None
            ),
            operating_margin=(
                float(data.get("opMargin", 0)) if data.get("opMargin") else None
            ),
            roe=float(data.get("roe", 0)) if data.get("roe") else None,
            roa=float(data.get("roa", 0)) if data.get("roa") else None,
            debt_to_equity=(
                float(data.get("debtEquity", 0)) if data.get("debtEquity") else None
            ),
            current_ratio=(
                float(data.get("currentRatio", 0)) if data.get("currentRatio") else None
            ),
            eps_growth=None,
            payout_ratio=None,
            market=market,
            source=DataSource.TIINGO,
        )

    # === Extended Methods ===

    async def get_fundamentals_statements(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get quarterly financial statements from Tiingo."""
        if not self.is_available():
            return None

        async def fetch():
            client = self._get_client()
            if not client:
                return None

            def _fetch_sync():
                try:
                    statements = client.get_fundamentals_statements(symbol)
                    if not statements:
                        return None

                    # Get most recent quarter
                    if isinstance(statements, list) and statements:
                        latest = statements[-1]
                        return {
                            "symbol": symbol,
                            "date": latest.get("date"),
                            "quarter": latest.get("quarter"),
                            "year": latest.get("year"),
                            "revenue": latest.get("revenue"),
                            "gross_profit": latest.get("grossProfit"),
                            "operating_income": latest.get("operatingIncome"),
                            "net_income": latest.get("netIncome"),
                            "eps": latest.get("eps"),
                            "total_assets": latest.get("totalAssets"),
                            "total_liabilities": latest.get("totalLiabilities"),
                            "shareholders_equity": latest.get("shareholdersEquity"),
                            "cash_and_equivalents": latest.get("cashAndEquiv"),
                            "total_debt": latest.get("totalDebt"),
                            "source": "tiingo",
                        }
                    return None
                except Exception as e:
                    logger.warning(f"Tiingo statements error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch("fundamentals_statements", symbol, fetch)
