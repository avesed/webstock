"""Token bucket rate limiter for API rate limiting.

NOTE: This implementation uses Redis for state storage but the acquire operation
is not fully atomic across multiple instances. For single-instance deployments,
the local asyncio lock provides sufficient protection. For high-concurrency
multi-instance deployments, consider using the atomic Lua script version
(acquire_atomic method) which guarantees atomicity at the Redis level.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.db.redis import get_redis

logger = logging.getLogger(__name__)

# Lua script for atomic token bucket operations
# This ensures atomicity even in distributed/multi-instance deployments
TOKEN_BUCKET_LUA_SCRIPT = """
local tokens_key = KEYS[1]
local time_key = KEYS[2]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local tokens_needed = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

-- Get current state
local tokens = tonumber(redis.call('get', tokens_key)) or max_tokens
local last_refill = tonumber(redis.call('get', time_key)) or now

-- Calculate refill
local elapsed = now - last_refill
local new_tokens = math.min(tokens + (elapsed * refill_rate), max_tokens)

-- Try to acquire tokens
if new_tokens >= tokens_needed then
    new_tokens = new_tokens - tokens_needed
    redis.call('setex', tokens_key, ttl, tostring(new_tokens))
    redis.call('setex', time_key, ttl, tostring(now))
    return 1  -- Success
else
    redis.call('setex', tokens_key, ttl, tostring(new_tokens))
    redis.call('setex', time_key, ttl, tostring(now))
    return 0  -- Rate limited
end
"""


@dataclass
class TokenBucketConfig:
    """Configuration for token bucket rate limiter."""

    max_tokens: int  # Maximum tokens in bucket (burst capacity)
    refill_rate: float  # Tokens added per second
    key_prefix: str = "token_bucket"


class TokenBucket:
    """
    Token bucket rate limiter using Redis for distributed rate limiting.

    The token bucket algorithm allows for burst traffic while maintaining
    an average rate limit. Tokens are added to the bucket at a constant rate
    (refill_rate) up to a maximum (max_tokens). Each request consumes one token.

    This implementation uses Redis for state storage, making it suitable for
    distributed systems where multiple instances need to share rate limit state.

    Usage:
        # 50 requests per minute = 50/60 tokens per second
        bucket = TokenBucket(
            name="openai",
            config=TokenBucketConfig(
                max_tokens=50,
                refill_rate=50/60,  # ~0.833 tokens per second
            )
        )

        if await bucket.acquire():
            # Proceed with API call
            pass
        else:
            # Rate limited
            pass
    """

    def __init__(
        self,
        name: str,
        config: TokenBucketConfig,
    ):
        self.name = name
        self.config = config
        self._key = f"{config.key_prefix}:{name}"
        self._lock = asyncio.Lock()

    def _get_redis_key(self, suffix: str) -> str:
        """Get Redis key for a specific value."""
        return f"{self._key}:{suffix}"

    async def _get_bucket_state(self) -> tuple[float, float]:
        """
        Get current bucket state from Redis.

        Returns:
            tuple: (tokens, last_refill_time)
        """
        redis = await get_redis()

        tokens_key = self._get_redis_key("tokens")
        time_key = self._get_redis_key("last_refill")

        pipe = redis.pipeline()
        pipe.get(tokens_key)
        pipe.get(time_key)
        results = await pipe.execute()

        tokens = float(results[0]) if results[0] else self.config.max_tokens
        last_refill = float(results[1]) if results[1] else time.time()

        return tokens, last_refill

    async def _set_bucket_state(self, tokens: float, last_refill: float) -> None:
        """Save bucket state to Redis."""
        redis = await get_redis()

        tokens_key = self._get_redis_key("tokens")
        time_key = self._get_redis_key("last_refill")

        # Set with TTL to auto-cleanup inactive buckets (1 hour)
        pipe = redis.pipeline()
        pipe.setex(tokens_key, 3600, str(tokens))
        pipe.setex(time_key, 3600, str(last_refill))
        await pipe.execute()

    async def _refill(self, tokens: float, last_refill: float) -> float:
        """
        Calculate new token count after refill.

        Args:
            tokens: Current token count
            last_refill: Timestamp of last refill

        Returns:
            New token count (capped at max_tokens)
        """
        now = time.time()
        elapsed = now - last_refill
        new_tokens = tokens + (elapsed * self.config.refill_rate)
        return min(new_tokens, self.config.max_tokens)

    async def acquire(self, tokens_needed: int = 1, use_atomic: bool = False) -> bool:
        """
        Attempt to acquire tokens from the bucket.

        Args:
            tokens_needed: Number of tokens to acquire (default: 1)
            use_atomic: Use Redis Lua script for atomic operation (recommended
                        for multi-instance deployments)

        Returns:
            True if tokens were acquired, False if rate limited
        """
        if use_atomic:
            return await self._acquire_atomic(tokens_needed)

        async with self._lock:
            current_tokens, last_refill = await self._get_bucket_state()
            now = time.time()

            # Refill tokens based on elapsed time
            current_tokens = await self._refill(current_tokens, last_refill)

            if current_tokens >= tokens_needed:
                # Consume tokens
                new_tokens = current_tokens - tokens_needed
                await self._set_bucket_state(new_tokens, now)
                logger.debug(
                    f"Token bucket '{self.name}': acquired {tokens_needed} tokens, "
                    f"{new_tokens:.2f} remaining"
                )
                return True
            else:
                # Not enough tokens - update refill time but don't consume
                await self._set_bucket_state(current_tokens, now)
                logger.debug(
                    f"Token bucket '{self.name}': rate limited, "
                    f"only {current_tokens:.2f} tokens available"
                )
                return False

    async def _acquire_atomic(self, tokens_needed: int = 1) -> bool:
        """
        Atomically acquire tokens using Redis Lua script.

        This method is recommended for multi-instance deployments where
        the standard acquire method may have race conditions.

        Args:
            tokens_needed: Number of tokens to acquire

        Returns:
            True if tokens were acquired, False if rate limited
        """
        redis = await get_redis()

        tokens_key = self._get_redis_key("tokens")
        time_key = self._get_redis_key("last_refill")
        now = time.time()
        ttl = 3600  # 1 hour TTL for keys

        try:
            result = await redis.eval(
                TOKEN_BUCKET_LUA_SCRIPT,
                2,  # Number of keys
                tokens_key,
                time_key,
                str(self.config.max_tokens),
                str(self.config.refill_rate),
                str(tokens_needed),
                str(now),
                str(ttl),
            )

            acquired = bool(result)
            if acquired:
                logger.debug(
                    f"Token bucket '{self.name}': atomically acquired {tokens_needed} tokens"
                )
            else:
                logger.debug(
                    f"Token bucket '{self.name}': rate limited (atomic)"
                )
            return acquired

        except Exception as e:
            logger.error(f"Token bucket atomic acquire error: {e}")
            # Fall back to non-atomic acquire on error
            return await self.acquire(tokens_needed, use_atomic=False)

    async def wait_and_acquire(
        self,
        tokens_needed: int = 1,
        timeout: float = 30.0,
    ) -> bool:
        """
        Wait for tokens to become available and acquire them.

        Args:
            tokens_needed: Number of tokens to acquire
            timeout: Maximum time to wait in seconds

        Returns:
            True if tokens were acquired, False if timed out
        """
        start_time = time.time()

        while True:
            if await self.acquire(tokens_needed):
                return True

            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.warning(
                    f"Token bucket '{self.name}': timed out waiting for tokens"
                )
                return False

            # Calculate wait time until next token is available
            wait_time = min(1.0 / self.config.refill_rate, timeout - elapsed)
            await asyncio.sleep(wait_time)

    async def get_available_tokens(self) -> float:
        """Get the current number of available tokens."""
        current_tokens, last_refill = await self._get_bucket_state()
        return await self._refill(current_tokens, last_refill)

    async def get_wait_time(self, tokens_needed: int = 1) -> float:
        """
        Calculate estimated wait time for tokens to become available.

        Args:
            tokens_needed: Number of tokens needed

        Returns:
            Estimated wait time in seconds (0 if tokens available)
        """
        available = await self.get_available_tokens()
        if available >= tokens_needed:
            return 0.0

        tokens_needed_to_wait = tokens_needed - available
        return tokens_needed_to_wait / self.config.refill_rate

    async def reset(self) -> None:
        """Reset bucket to full capacity."""
        async with self._lock:
            await self._set_bucket_state(self.config.max_tokens, time.time())
            logger.info(f"Token bucket '{self.name}' reset to full capacity")

    def get_status(self) -> dict:
        """Get bucket status (synchronous version for debugging)."""
        return {
            "name": self.name,
            "max_tokens": self.config.max_tokens,
            "refill_rate": self.config.refill_rate,
            "key_prefix": self.config.key_prefix,
        }


# Global token bucket registry
_token_buckets: dict[str, TokenBucket] = {}
_registry_lock = asyncio.Lock()


async def get_token_bucket(
    name: str,
    config: Optional[TokenBucketConfig] = None,
) -> TokenBucket:
    """
    Get or create a token bucket by name.

    Args:
        name: Unique identifier for the token bucket
        config: Configuration (required when creating new bucket)

    Returns:
        TokenBucket instance

    Raises:
        ValueError: If config not provided for new bucket
    """
    async with _registry_lock:
        if name not in _token_buckets:
            if config is None:
                raise ValueError(
                    f"Config required when creating new token bucket '{name}'"
                )
            _token_buckets[name] = TokenBucket(name, config)
        return _token_buckets[name]


async def get_openai_rate_limiter() -> TokenBucket:
    """
    Get the global OpenAI API rate limiter.

    Default: 200 requests per minute across all features.
    """
    from app.config import settings

    return await get_token_bucket(
        "openai",
        TokenBucketConfig(
            max_tokens=settings.OPENAI_RATE_LIMIT,
            refill_rate=settings.OPENAI_RATE_LIMIT / 60.0,  # tokens per second
            key_prefix="token_bucket:api",
        ),
    )


async def get_analysis_rate_limiter() -> TokenBucket:
    """Get rate limiter for AI analysis agents."""
    from app.config import settings
    return await get_token_bucket(
        "openai:analysis",
        TokenBucketConfig(
            max_tokens=settings.OPENAI_RATE_LIMIT_ANALYSIS,
            refill_rate=settings.OPENAI_RATE_LIMIT_ANALYSIS / 60.0,
            key_prefix="token_bucket:api",
        ),
    )


async def get_chat_rate_limiter() -> TokenBucket:
    """Get rate limiter for AI chat conversations."""
    from app.config import settings
    return await get_token_bucket(
        "openai:chat",
        TokenBucketConfig(
            max_tokens=settings.OPENAI_RATE_LIMIT_CHAT,
            refill_rate=settings.OPENAI_RATE_LIMIT_CHAT / 60.0,
            key_prefix="token_bucket:api",
        ),
    )


async def get_embedding_rate_limiter() -> TokenBucket:
    """Get rate limiter for embedding generation."""
    from app.config import settings
    return await get_token_bucket(
        "openai:embedding",
        TokenBucketConfig(
            max_tokens=settings.OPENAI_RATE_LIMIT_EMBEDDING,
            refill_rate=settings.OPENAI_RATE_LIMIT_EMBEDDING / 60.0,
            key_prefix="token_bucket:api",
        ),
    )


async def get_background_rate_limiter() -> TokenBucket:
    """Get rate limiter for background tasks."""
    from app.config import settings
    return await get_token_bucket(
        "openai:background",
        TokenBucketConfig(
            max_tokens=settings.OPENAI_RATE_LIMIT_BACKGROUND,
            refill_rate=settings.OPENAI_RATE_LIMIT_BACKGROUND / 60.0,
            key_prefix="token_bucket:api",
        ),
    )


async def get_user_chat_rate_limiter(user_id: int) -> TokenBucket:
    """Get per-user rate limiter for chat messages."""
    from app.config import settings
    return await get_token_bucket(
        f"user:{user_id}:chat",
        TokenBucketConfig(
            max_tokens=settings.AI_CHAT_RATE_LIMIT,
            refill_rate=settings.AI_CHAT_RATE_LIMIT / 60.0,
            key_prefix="token_bucket:user",
        ),
    )


async def reset_all_token_buckets() -> None:
    """Reset all token buckets to full capacity."""
    async with _registry_lock:
        for bucket in _token_buckets.values():
            await bucket.reset()
