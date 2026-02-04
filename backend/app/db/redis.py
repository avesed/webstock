"""Redis async connection using a proper connection pool pattern."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import redis.asyncio as redis
from redis.asyncio import ConnectionPool, Redis

from app.config import settings


class RedisConnectionManager:
    """
    Manages Redis connection pool with proper lifecycle handling.

    This class encapsulates the Redis connection pool to avoid mutable global state
    and provides proper initialization and cleanup methods.
    """

    def __init__(self) -> None:
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[Redis] = None
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Initialize the Redis connection pool."""
        if self._initialized:
            return

        self._pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_POOL_SIZE,
            decode_responses=True,
        )
        self._client = Redis(connection_pool=self._pool)

        # Test connection
        await self._client.ping()
        self._initialized = True

    async def get_client(self) -> Redis:
        """
        Get the Redis client instance.

        Automatically initializes if not already done.
        """
        if not self._initialized:
            await self.initialize()

        if self._client is None:
            raise RuntimeError("Redis client not initialized")

        return self._client

    async def close(self) -> None:
        """Close the Redis connection pool and cleanup resources."""
        if self._client is not None:
            await self._client.close()
            self._client = None

        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None

        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Check if the connection manager is initialized."""
        return self._initialized


# Singleton instance of the connection manager
_redis_manager = RedisConnectionManager()


async def init_redis() -> Redis:
    """Initialize Redis connection pool and return the client."""
    await _redis_manager.initialize()
    return await _redis_manager.get_client()


async def get_redis() -> Redis:
    """Get Redis client instance (initializes if needed)."""
    return await _redis_manager.get_client()


async def close_redis() -> None:
    """Close Redis connection pool."""
    await _redis_manager.close()


@asynccontextmanager
async def redis_context() -> AsyncGenerator[Redis, None]:
    """
    Context manager for Redis operations.

    Ensures proper connection handling for one-off operations.
    """
    client = await get_redis()
    try:
        yield client
    finally:
        # Connection is returned to pool automatically
        pass
