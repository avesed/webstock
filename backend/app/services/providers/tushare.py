"""Tushare data provider for A-shares (fallback)."""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional, Set

from app.services.providers.base import DataProvider
from app.services.stock_service import (
    DataSource,
    HistoryInterval,
    HistoryPeriod,
    Market,
    SearchResult,
    StockHistory,
    StockQuote,
    normalize_symbol,
)

logger = logging.getLogger(__name__)

# Thread pool for synchronous tushare calls
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()

EXTERNAL_API_TIMEOUT = 30  # seconds


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


class TushareProvider(DataProvider):
    """
    Tushare data provider for A-shares.

    This is a fallback provider that only activates if TUSHARE_TOKEN
    environment variable is configured. It provides quote data for
    A-shares when AKShare fails.

    Limitations:
    - Requires API token (paid service)
    - Only implements get_quote (no history, info, financials)
    - Daily data only (no intraday)
    """

    _token: Optional[str] = None

    def __init__(self):
        # Check token on initialization
        if TushareProvider._token is None:
            TushareProvider._token = os.environ.get("TUSHARE_TOKEN", "")

    @property
    def source(self) -> DataSource:
        return DataSource.TUSHARE

    @property
    def supported_markets(self) -> Set[Market]:
        return {Market.SH, Market.SZ}

    @classmethod
    def is_available(cls) -> bool:
        """Check if Tushare API key is available."""
        if cls._token is None:
            cls._token = os.environ.get("TUSHARE_TOKEN", "")
        return bool(cls._token)

    async def get_quote(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockQuote]:
        """Get quote from Tushare - skip if no API key."""
        if not self.is_available():
            logger.debug("Tushare API key not configured, skipping")
            return None

        if market not in (Market.SH, Market.SZ):
            return None

        try:
            import tushare as ts

            ts.set_token(self._token)
            pro = ts.pro_api()

            code = normalize_symbol(symbol, market)
            ts_code = f"{code}.{'SH' if market == Market.SH else 'SZ'}"

            def fetch():
                df = pro.daily(
                    ts_code=ts_code,
                    start_date=(datetime.now() - timedelta(days=5)).strftime("%Y%m%d"),
                )
                if df is None or df.empty:
                    return None
                return df.iloc[0].to_dict()

            data = await run_in_executor(fetch)
            if not data:
                return None

            price = float(data.get("close", 0))
            prev_close = float(data.get("pre_close", price))
            change = price - prev_close
            change_pct = float(data.get("pct_chg", 0))

            return StockQuote(
                symbol=symbol,
                name=None,  # Tushare daily doesn't include name
                price=price,
                change=round(change, 4),
                change_percent=round(change_pct, 2),
                volume=int(data.get("vol", 0) * 100),  # Tushare uses lots
                market_cap=None,
                day_high=float(data.get("high", 0)) if data.get("high") else None,
                day_low=float(data.get("low", 0)) if data.get("low") else None,
                open=float(data.get("open", 0)) if data.get("open") else None,
                previous_close=prev_close,
                timestamp=datetime.utcnow(),
                market=market,
                source=DataSource.TUSHARE,
            )
        except Exception as e:
            logger.error(f"Tushare quote error for {symbol}: {e}")
            return None

    async def get_history(
        self,
        symbol: str,
        market: Market,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """History not implemented for Tushare fallback."""
        return None

    async def search(
        self,
        query: str,
        markets: Optional[Set[Market]] = None,
    ) -> List[SearchResult]:
        """Search not implemented for Tushare fallback."""
        return []
