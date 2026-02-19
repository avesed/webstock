"""Health check endpoint for data-service."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from app.config import get_settings
from app.core.cache import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(response: Response):
    """Health check with Redis connectivity and provider key status.

    Returns HTTP 503 when degraded so that Docker health checks
    (``curl -f``) correctly report the container as unhealthy and
    dependent services (``app``) wait before starting.
    """
    settings = get_settings()
    checks: dict = {"status": "healthy", "service": "data-service"}

    # Check Redis
    try:
        r = await get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)
        checks["redis"] = f"error: {e}"
        checks["status"] = "degraded"

    # Report which API keys are configured (DB + env, never expose actual keys)
    from app.core.api_keys import get_api_key
    checks["providers"] = {
        "finnhub": bool(get_api_key("finnhub")),
        "tushare": bool(get_api_key("tushare")),
        "tiingo": bool(get_api_key("tiingo")),
        "tavily": bool(get_api_key("tavily")),
        "polygon": bool(get_api_key("polygon")),
        "yfinance": True,  # No key needed
        "akshare": True,  # No key needed
    }

    # Return 503 when degraded so Docker/Compose health checks fail correctly
    if checks["status"] == "degraded":
        response.status_code = 503

    return checks
