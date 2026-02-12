"""Health check endpoint."""
import logging

from fastapi import APIRouter

from app.context import QlibContext

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Health check with Redis connectivity verification."""
    redis_ok = False
    try:
        from app.config import get_settings
        import redis.asyncio as aioredis
        settings = get_settings()
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await r.ping()
        await r.aclose()
        redis_ok = True
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)

    status = "healthy" if redis_ok else "degraded"

    return {
        "status": status,
        "service": "qlib-service",
        "qlib_initialized": QlibContext.is_initialized(),
        "qlib_region": QlibContext.get_current_region(),
        "redis": "ok" if redis_ok else "error",
    }
