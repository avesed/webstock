"""Redis caching service with TTL randomization and distributed locking."""

import asyncio
import json
import logging
import random
import uuid
from datetime import timedelta
from enum import Enum
from typing import Any, Optional, TypeVar, Callable, Awaitable

from redis.asyncio import Redis

from app.db.redis import get_redis

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CacheTTL(Enum):
    """Cache TTL configurations with base values and randomization ranges."""

    REALTIME_QUOTE = (30, 30)  # 30-60 seconds
    COMPANY_INFO = (3600, 600)  # 1 hour + rand(10min)
    FINANCIAL_DATA = (86400, 3600)  # 24 hours + rand(1h)
    STOCK_SEARCH = (600, 60)  # 10 minutes + rand(60s)

    @property
    def base_ttl(self) -> int:
        return self.value[0]

    @property
    def rand_range(self) -> int:
        return self.value[1]

    def get_ttl(self) -> int:
        """Get TTL with randomization to prevent cache avalanche."""
        return self.base_ttl + random.randint(0, self.rand_range)


class CachePrefix(str, Enum):
    """Cache key prefixes for different data types."""

    QUOTE = "stock:quote:"
    HISTORY = "stock:history:"
    INFO = "stock:info:"
    FINANCIAL = "stock:financial:"
    SEARCH = "stock:search:"
    LOCK = "lock:"
    STALE = "stale:"


class CacheService:
    """
    Redis caching service with:
    - TTL randomization to prevent cache avalanche
    - Distributed locking to prevent cache stampede
    - Stale data fallback for degraded mode
    """

    def __init__(self, redis_client: Optional[Redis] = None):
        self._redis: Optional[Redis] = redis_client
        self._lock_timeout = 10  # seconds
        self._lock_retry_interval = 0.1  # seconds

    async def _get_redis(self) -> Redis:
        """Get Redis client, initialize if needed."""
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    def _build_key(self, prefix: CachePrefix, key: str) -> str:
        """Build cache key with prefix."""
        return f"{prefix.value}{key}"

    async def get(
        self,
        prefix: CachePrefix,
        key: str,
        allow_stale: bool = True,
    ) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            prefix: Cache key prefix
            key: Cache key
            allow_stale: If True, return stale data when main cache misses

        Returns:
            Cached value or None if not found
        """
        redis = await self._get_redis()
        cache_key = self._build_key(prefix, key)

        try:
            data = await redis.get(cache_key)
            if data:
                logger.debug(f"Cache hit: {cache_key}")
                return json.loads(data)

            # Try stale data if main cache misses
            if allow_stale:
                stale_key = f"{CachePrefix.STALE.value}{cache_key}"
                stale_data = await redis.get(stale_key)
                if stale_data:
                    logger.info(f"Returning stale data for: {cache_key}")
                    return json.loads(stale_data)

            logger.debug(f"Cache miss: {cache_key}")
            return None
        except Exception as e:
            logger.error(f"Cache get error for {cache_key}: {e}")
            return None

    async def set(
        self,
        prefix: CachePrefix,
        key: str,
        value: Any,
        ttl: CacheTTL,
        store_stale: bool = True,
    ) -> bool:
        """
        Set value in cache with TTL.

        Args:
            prefix: Cache key prefix
            key: Cache key
            value: Value to cache
            ttl: TTL configuration
            store_stale: If True, also store a stale copy with longer TTL

        Returns:
            True if successful, False otherwise
        """
        redis = await self._get_redis()
        cache_key = self._build_key(prefix, key)

        try:
            json_data = json.dumps(value, default=str)
            actual_ttl = ttl.get_ttl()

            # Set main cache
            await redis.setex(cache_key, actual_ttl, json_data)
            logger.debug(f"Cache set: {cache_key} (TTL: {actual_ttl}s)")

            # Store stale copy with 5x TTL for fallback
            if store_stale:
                stale_key = f"{CachePrefix.STALE.value}{cache_key}"
                stale_ttl = actual_ttl * 5
                await redis.setex(stale_key, stale_ttl, json_data)

            return True
        except Exception as e:
            logger.error(f"Cache set error for {cache_key}: {e}")
            return False

    async def delete(self, prefix: CachePrefix, key: str) -> bool:
        """Delete value from cache."""
        redis = await self._get_redis()
        cache_key = self._build_key(prefix, key)

        try:
            await redis.delete(cache_key)
            # Also delete stale copy
            stale_key = f"{CachePrefix.STALE.value}{cache_key}"
            await redis.delete(stale_key)
            logger.debug(f"Cache deleted: {cache_key}")
            return True
        except Exception as e:
            logger.error(f"Cache delete error for {cache_key}: {e}")
            return False

    async def acquire_lock(
        self,
        key: str,
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        """
        Acquire distributed lock using Redis SETNX with unique token.

        Args:
            key: Lock key (without prefix)
            timeout: Lock timeout in seconds (default: 10s)

        Returns:
            Lock token string if acquired, None otherwise.
            The token must be passed to release_lock() for safe release.
        """
        redis = await self._get_redis()
        lock_key = self._build_key(CachePrefix.LOCK, key)
        lock_timeout = timeout or self._lock_timeout
        token = uuid.uuid4().hex

        try:
            # SETNX with expiration to prevent deadlocks
            acquired = await redis.set(
                lock_key,
                token,
                nx=True,
                ex=lock_timeout,
            )
            if acquired:
                logger.debug(f"Lock acquired: {lock_key}")
                return token
            return None
        except Exception as e:
            logger.error(f"Lock acquire error for {lock_key}: {e}")
            return None

    # Lua script for atomic compare-and-delete (safe lock release)
    _RELEASE_LOCK_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    async def release_lock(self, key: str, token: Optional[str] = None) -> bool:
        """Release distributed lock. Uses Lua CAS when token is provided."""
        redis = await self._get_redis()
        lock_key = self._build_key(CachePrefix.LOCK, key)

        try:
            if token:
                # Atomic compare-and-delete: only release if we still own it
                result = await redis.eval(
                    self._RELEASE_LOCK_SCRIPT, 1, lock_key, token
                )
                released = bool(result)
                if not released:
                    logger.warning(f"Lock already expired or stolen: {lock_key}")
                else:
                    logger.debug(f"Lock released: {lock_key}")
                return released
            else:
                # Legacy fallback: unconditional delete
                await redis.delete(lock_key)
                logger.debug(f"Lock released (legacy): {lock_key}")
                return True
        except Exception as e:
            logger.error(f"Lock release error for {lock_key}: {e}")
            return False

    async def get_with_lock(
        self,
        prefix: CachePrefix,
        key: str,
        ttl: CacheTTL,
        fetch_func: Callable[[], Awaitable[T]],
        max_retries: int = 5,
    ) -> Optional[T]:
        """
        Get from cache or fetch with distributed lock to prevent stampede.

        This implements the cache-aside pattern with:
        1. Check cache first
        2. If miss, try to acquire lock
        3. If lock acquired, fetch data and cache it
        4. If lock not acquired, wait and retry cache check
        5. Fall back to stale data if all else fails

        Args:
            prefix: Cache key prefix
            key: Cache key
            ttl: TTL configuration
            fetch_func: Async function to fetch data on cache miss
            max_retries: Max retries while waiting for lock holder

        Returns:
            Cached or freshly fetched value
        """
        # First, try to get from cache
        cached = await self.get(prefix, key, allow_stale=False)
        if cached is not None:
            return cached

        lock_key = f"{prefix.value}{key}"

        for attempt in range(max_retries):
            # Try to acquire lock
            lock_token = await self.acquire_lock(lock_key)
            if lock_token:
                try:
                    # Double-check cache after acquiring lock
                    cached = await self.get(prefix, key, allow_stale=False)
                    if cached is not None:
                        return cached

                    # Fetch fresh data
                    logger.info(f"Fetching data for: {key}")
                    data = await fetch_func()

                    if data is not None:
                        await self.set(prefix, key, data, ttl)

                    return data
                except Exception as e:
                    logger.error(f"Fetch error for {key}: {e}")
                    # Return stale data on error
                    stale = await self.get(prefix, key, allow_stale=True)
                    return stale
                finally:
                    await self.release_lock(lock_key, lock_token)
            else:
                # Wait for lock holder to populate cache
                await asyncio.sleep(self._lock_retry_interval * (attempt + 1))

                # Check if cache was populated
                cached = await self.get(prefix, key, allow_stale=False)
                if cached is not None:
                    return cached

        # All retries exhausted, return stale data if available
        logger.warning(f"Lock contention timeout for: {key}, returning stale data")
        return await self.get(prefix, key, allow_stale=True)

    async def get_many(
        self,
        prefix: CachePrefix,
        keys: list[str],
    ) -> dict[str, Any]:
        """
        Get multiple values from cache.

        Args:
            prefix: Cache key prefix
            keys: List of cache keys

        Returns:
            Dict of key -> value for found items
        """
        if not keys:
            return {}

        redis = await self._get_redis()
        cache_keys = [self._build_key(prefix, k) for k in keys]

        try:
            values = await redis.mget(cache_keys)
            result = {}
            for key, value in zip(keys, values):
                if value:
                    result[key] = json.loads(value)
            return result
        except Exception as e:
            logger.error(f"Cache mget error: {e}")
            return {}

    async def invalidate_pattern(self, pattern: str) -> int:
        """
        Invalidate all keys matching pattern.

        Args:
            pattern: Redis key pattern (e.g., "stock:quote:*")

        Returns:
            Number of keys deleted
        """
        redis = await self._get_redis()
        count = 0

        try:
            cursor = None
            while cursor != 0:
                cursor, keys = await redis.scan(
                    cursor=cursor or 0,
                    match=pattern,
                    count=100,
                )
                if keys:
                    await redis.delete(*keys)
                    count += len(keys)
            logger.info(f"Invalidated {count} keys matching: {pattern}")
            return count
        except Exception as e:
            logger.error(f"Pattern invalidation error for {pattern}: {e}")
            return count


# Singleton instance
_cache_service: Optional[CacheService] = None
_cache_service_lock = asyncio.Lock()


async def get_cache_service() -> CacheService:
    """Get singleton cache service instance."""
    global _cache_service
    if _cache_service is None:
        async with _cache_service_lock:
            if _cache_service is None:  # double-check after acquiring lock
                _cache_service = CacheService()
    return _cache_service


async def cleanup_cache_service() -> None:
    """Cleanup cache service resources."""
    global _cache_service
    if _cache_service is not None:
        # Close Redis connection if we own it
        if _cache_service._redis is not None:
            try:
                await _cache_service._redis.close()
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")
        _cache_service = None
