"""Health check endpoints for data-processor.

Provides /health (basic liveness with component status) and
/health/ready (full readiness including DB pool and Redis).
"""

import logging

from fastapi import APIRouter

from app.context import QlibContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Health check with Redis, asyncpg, and Qlib status.

    Returns 200 even when degraded so Docker healthcheck passes
    during initial data sync. The 'status' field indicates actual health.
    """
    redis_ok = False
    db_ok = False
    prediction_enabled = False

    # Check Redis connectivity
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

    # Check asyncpg pool via settings cache
    try:
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if pool:
            async with pool.acquire(timeout=5) as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True

        # Also check prediction config
        config = await settings_cache.get_config()
        prediction_enabled = config.llm.enabled
    except Exception as e:
        logger.warning("DB health check failed: %s", e)

    # Determine overall status
    if redis_ok and db_ok:
        status = "healthy"
    elif redis_ok or db_ok:
        status = "degraded"
    else:
        status = "unhealthy"

    return {
        "status": status,
        "service": "data-processor",
        "qlib_initialized": QlibContext.is_initialized(),
        "qlib_region": QlibContext.get_current_region(),
        "redis": "ok" if redis_ok else "error",
        "database": "ok" if db_ok else "error",
        "prediction_enabled": prediction_enabled,
    }


@router.get("/health/ready")
async def health_ready():
    """Readiness check -- stricter than /health.

    Returns 503 if critical dependencies (DB, Redis) are not available.
    Used by orchestrators to gate traffic routing.
    """
    errors = []

    # Check Redis
    try:
        from app.config import get_settings
        import redis.asyncio as aioredis

        settings = get_settings()
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await r.ping()
        await r.aclose()
    except Exception as e:
        errors.append(f"redis: {e}")

    # Check asyncpg pool
    try:
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if pool:
            async with pool.acquire(timeout=5) as conn:
                await conn.fetchval("SELECT 1")
        else:
            errors.append("database: pool not initialized")
    except Exception as e:
        errors.append(f"database: {e}")

    if errors:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "service": "data-processor",
                "errors": errors,
            },
        )

    return {
        "status": "ready",
        "service": "data-processor",
    }
