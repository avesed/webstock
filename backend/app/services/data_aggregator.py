"""Data aggregation layer with request merging and cache coordination."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Generic, Optional, Set, TypeVar

from app.services.cache_service import (
    CachePrefix,
    CacheService,
    CacheTTL,
    get_cache_service,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DataType(str, Enum):
    """Types of stock data requests."""

    QUOTE = "quote"
    HISTORY = "history"
    INFO = "info"
    FINANCIAL = "financial"
    SEARCH = "search"


@dataclass
class PendingRequest:
    """Tracks a pending data request with its waiters."""

    symbol: str
    data_type: DataType
    params_hash: str
    future: asyncio.Future
    created_at: datetime = field(default_factory=datetime.utcnow)
    waiter_count: int = 1


class RequestMerger:
    """
    Merges identical concurrent requests to prevent duplicate API calls.

    When multiple requests for the same symbol/data come in simultaneously,
    only one actual API call is made and the result is shared with all waiters.
    """

    def __init__(self):
        self._pending: Dict[str, PendingRequest] = {}
        self._lock = asyncio.Lock()

    def _build_request_key(
        self,
        symbol: str,
        data_type: DataType,
        params_hash: str = "",
    ) -> str:
        """Build unique key for request deduplication."""
        return f"{data_type.value}:{symbol}:{params_hash}"

    async def get_or_create_request(
        self,
        symbol: str,
        data_type: DataType,
        params_hash: str = "",
    ) -> tuple[asyncio.Future, bool]:
        """
        Get existing pending request or create new one.

        Args:
            symbol: Stock symbol
            data_type: Type of data being requested
            params_hash: Hash of additional parameters for uniqueness

        Returns:
            Tuple of (future to await, is_creator: bool)
            is_creator is True if this is the first request that should do the work
        """
        key = self._build_request_key(symbol, data_type, params_hash)

        async with self._lock:
            if key in self._pending:
                # Join existing request
                pending = self._pending[key]
                pending.waiter_count += 1
                logger.debug(
                    f"Request merged: {key} (waiters: {pending.waiter_count})"
                )
                return pending.future, False

            # Create new request
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            pending = PendingRequest(
                symbol=symbol,
                data_type=data_type,
                params_hash=params_hash,
                future=future,
            )
            self._pending[key] = pending
            logger.debug(f"New request created: {key}")
            return future, True

    async def complete_request(
        self,
        symbol: str,
        data_type: DataType,
        result: Any,
        params_hash: str = "",
        error: Optional[Exception] = None,
    ) -> None:
        """
        Complete a pending request with result or error.

        Args:
            symbol: Stock symbol
            data_type: Type of data
            result: Result data (if successful)
            params_hash: Hash of additional parameters
            error: Exception if request failed
        """
        key = self._build_request_key(symbol, data_type, params_hash)

        async with self._lock:
            pending = self._pending.pop(key, None)
            if pending is None:
                logger.warning(f"No pending request found for: {key}")
                return

            if not pending.future.done():
                if error:
                    pending.future.set_exception(error)
                else:
                    pending.future.set_result(result)

            logger.debug(
                f"Request completed: {key} (served {pending.waiter_count} waiters)"
            )


class DataAggregator:
    """
    High-level data aggregation layer that coordinates:
    - Cache-first retrieval strategy
    - Request merging for concurrent requests
    - Distributed locking for cache population
    - Stale data fallback on errors
    """

    # Mapping of data types to cache configurations
    CACHE_CONFIG: Dict[DataType, tuple[CachePrefix, CacheTTL]] = {
        DataType.QUOTE: (CachePrefix.QUOTE, CacheTTL.REALTIME_QUOTE),
        DataType.HISTORY: (CachePrefix.HISTORY, CacheTTL.DAILY_HISTORY),
        DataType.INFO: (CachePrefix.INFO, CacheTTL.COMPANY_INFO),
        DataType.FINANCIAL: (CachePrefix.FINANCIAL, CacheTTL.FINANCIAL_DATA),
        DataType.SEARCH: (CachePrefix.SEARCH, CacheTTL.STOCK_SEARCH),
    }

    def __init__(self, cache_service: Optional[CacheService] = None):
        self._cache = cache_service
        self._merger = RequestMerger()

    async def _get_cache(self) -> CacheService:
        """Get cache service, initialize if needed."""
        if self._cache is None:
            self._cache = await get_cache_service()
        return self._cache

    def _build_cache_key(
        self,
        symbol: str,
        data_type: DataType,
        params_hash: str = "",
    ) -> str:
        """Build cache key from symbol and params."""
        if params_hash:
            return f"{symbol}:{params_hash}"
        return symbol

    async def get_data(
        self,
        symbol: str,
        data_type: DataType,
        fetch_func: Callable[[], Awaitable[T]],
        params_hash: str = "",
        force_refresh: bool = False,
    ) -> Optional[T]:
        """
        Get data with full caching and request merging pipeline.

        Flow:
        1. Check cache (unless force_refresh)
        2. Join or create merged request
        3. If creator, fetch data with distributed lock
        4. Cache result and notify all waiters
        5. On error, try stale data

        Args:
            symbol: Stock symbol
            data_type: Type of data to fetch
            fetch_func: Async function that fetches the actual data
            params_hash: Hash of additional parameters for cache key uniqueness
            force_refresh: If True, skip cache and force fetch

        Returns:
            Data if available, None otherwise
        """
        cache = await self._get_cache()
        prefix, ttl = self.CACHE_CONFIG.get(
            data_type,
            (CachePrefix.QUOTE, CacheTTL.REALTIME_QUOTE),
        )
        cache_key = self._build_cache_key(symbol, data_type, params_hash)

        # Step 1: Check cache first (unless force_refresh)
        if not force_refresh:
            cached = await cache.get(prefix, cache_key, allow_stale=False)
            if cached is not None:
                return cached

        # Step 2: Get or join merged request
        future, is_creator = await self._merger.get_or_create_request(
            symbol, data_type, params_hash
        )

        if not is_creator:
            # Wait for creator to complete the request
            try:
                return await future
            except Exception as e:
                logger.error(f"Merged request failed for {symbol}: {e}")
                # Try stale data
                return await cache.get(prefix, cache_key, allow_stale=True)

        # Step 3: We're the creator - fetch with lock
        try:
            data = await cache.get_with_lock(
                prefix=prefix,
                key=cache_key,
                ttl=ttl,
                fetch_func=fetch_func,
            )

            # Complete merged request for all waiters
            await self._merger.complete_request(
                symbol, data_type, data, params_hash
            )
            return data

        except Exception as e:
            logger.error(f"Data fetch error for {symbol}/{data_type}: {e}")

            # Complete with error
            await self._merger.complete_request(
                symbol, data_type, None, params_hash, error=e
            )

            # Try stale data
            stale = await cache.get(prefix, cache_key, allow_stale=True)
            if stale is not None:
                logger.info(f"Returning stale data for {symbol} after error")
                return stale

            raise

    async def get_batch_data(
        self,
        symbols: list[str],
        data_type: DataType,
        fetch_func: Callable[[str], Awaitable[T]],
    ) -> Dict[str, Optional[T]]:
        """
        Get data for multiple symbols with efficient batching.

        First checks cache for all symbols, then fetches missing ones
        in parallel.

        Args:
            symbols: List of stock symbols
            data_type: Type of data to fetch
            fetch_func: Async function that fetches data for a single symbol

        Returns:
            Dict mapping symbol to data (or None if unavailable)
        """
        if not symbols:
            return {}

        cache = await self._get_cache()
        prefix, _ = self.CACHE_CONFIG.get(
            data_type,
            (CachePrefix.QUOTE, CacheTTL.REALTIME_QUOTE),
        )

        # Get all cached values first
        cached = await cache.get_many(prefix, symbols)

        # Find missing symbols
        missing = [s for s in symbols if s not in cached]

        if missing:
            # Fetch missing in parallel
            async def fetch_single(symbol: str) -> tuple[str, Optional[T]]:
                try:
                    data = await self.get_data(
                        symbol=symbol,
                        data_type=data_type,
                        fetch_func=lambda: fetch_func(symbol),
                    )
                    return symbol, data
                except Exception as e:
                    logger.error(f"Batch fetch error for {symbol}: {e}")
                    return symbol, None

            tasks = [fetch_single(s) for s in missing]
            results = await asyncio.gather(*tasks)

            for symbol, data in results:
                cached[symbol] = data

        return cached

    async def invalidate(
        self,
        symbol: str,
        data_type: Optional[DataType] = None,
    ) -> None:
        """
        Invalidate cached data for a symbol.

        Args:
            symbol: Stock symbol
            data_type: Specific data type to invalidate (or all if None)
        """
        cache = await self._get_cache()

        if data_type:
            prefix, _ = self.CACHE_CONFIG[data_type]
            await cache.delete(prefix, symbol)
        else:
            # Invalidate all data types for this symbol
            for dtype in DataType:
                prefix, _ = self.CACHE_CONFIG.get(
                    dtype,
                    (CachePrefix.QUOTE, CacheTTL.REALTIME_QUOTE),
                )
                await cache.delete(prefix, symbol)

        logger.info(f"Cache invalidated for {symbol} ({data_type or 'all'})")


# Singleton instance
_data_aggregator: Optional[DataAggregator] = None
_data_aggregator_lock = asyncio.Lock()


async def get_data_aggregator() -> DataAggregator:
    """Get singleton data aggregator instance."""
    global _data_aggregator
    if _data_aggregator is None:
        async with _data_aggregator_lock:
            if _data_aggregator is None:  # double-check after acquiring lock
                _data_aggregator = DataAggregator()
    return _data_aggregator


async def cleanup_data_aggregator() -> None:
    """Cleanup data aggregator resources."""
    global _data_aggregator
    if _data_aggregator is not None:
        _data_aggregator = None
