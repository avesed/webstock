"""Redis-based sliding window rate limiter."""

import time
from typing import Optional

from fastapi import HTTPException, Request, status

from app.config import settings
from app.db.redis import get_redis


class RateLimiter:
    """Sliding window rate limiter using Redis sorted sets."""

    def __init__(
        self,
        max_requests: int = settings.RATE_LIMIT_REQUESTS,
        window_seconds: int = settings.RATE_LIMIT_WINDOW_SECONDS,
        key_prefix: str = "rate_limit",
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _get_key(self, identifier: str) -> str:
        """Generate Redis key for the identifier."""
        return f"{self.key_prefix}:{identifier}"

    async def is_allowed(self, identifier: str) -> tuple[bool, int, int]:
        """
        Check if request is allowed under rate limit.

        Returns:
            tuple: (is_allowed, remaining_requests, retry_after_seconds)
        """
        redis = await get_redis()
        key = self._get_key(identifier)
        now = time.time()
        window_start = now - self.window_seconds

        # Use pipeline for atomic operations
        pipe = redis.pipeline()

        # Remove old entries outside the window
        pipe.zremrangebyscore(key, 0, window_start)

        # Count current requests in window
        pipe.zcard(key)

        # Add current request
        pipe.zadd(key, {str(now): now})

        # Set expiry on the key
        pipe.expire(key, self.window_seconds)

        results = await pipe.execute()
        request_count = results[1]

        if request_count >= self.max_requests:
            # Get oldest request timestamp to calculate retry-after
            oldest = await redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(oldest[0][1] + self.window_seconds - now) + 1
            else:
                retry_after = self.window_seconds

            return False, 0, retry_after

        remaining = self.max_requests - request_count - 1
        return True, remaining, 0

    async def check(self, identifier: str) -> None:
        """
        Check rate limit and raise exception if exceeded.

        Raises:
            HTTPException: 429 Too Many Requests if rate limit exceeded
        """
        is_allowed, remaining, retry_after = await self.is_allowed(identifier)

        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                    "Retry-After": str(retry_after),
                },
            )


class RateLimitMiddleware:
    """Rate limiting middleware for FastAPI."""

    def __init__(
        self,
        max_requests: int = settings.RATE_LIMIT_REQUESTS,
        window_seconds: int = settings.RATE_LIMIT_WINDOW_SECONDS,
    ):
        self.limiter = RateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

    async def __call__(self, request: Request) -> None:
        """Apply rate limiting based on client IP."""
        # Get client IP (considering proxy headers)
        client_ip = self._get_client_ip(request)
        await self.limiter.check(client_ip)

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, considering proxies."""
        # Check X-Forwarded-For header first (for proxied requests)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP in the chain (original client)
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct client IP
        if request.client:
            return request.client.host

        return "unknown"


def rate_limit(
    max_requests: Optional[int] = None,
    window_seconds: Optional[int] = None,
    key_prefix: str = "rate_limit",
):
    """
    Dependency factory for route-specific rate limiting.

    Usage:
        @router.get("/endpoint")
        async def endpoint(
            _: None = Depends(rate_limit(max_requests=10, window_seconds=60))
        ):
            ...
    """
    limiter = RateLimiter(
        max_requests=max_requests or settings.RATE_LIMIT_REQUESTS,
        window_seconds=window_seconds or settings.RATE_LIMIT_WINDOW_SECONDS,
        key_prefix=key_prefix,
    )

    async def dependency(request: Request) -> None:
        # Get client IP
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        else:
            client_ip = "unknown"

        # Include route path so each endpoint has its own counter
        route_path = request.scope.get("path", "")
        identifier = f"{client_ip}:{route_path}"

        await limiter.check(identifier)

    return dependency
