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

    # Report which API keys are configured (never expose the actual keys)
    checks["providers"] = {
        "finnhub": bool(settings.FINNHUB_API_KEY),
        "tushare": bool(settings.TUSHARE_TOKEN),
        "tiingo": bool(settings.TIINGO_API_KEY),
        "tavily": bool(settings.TAVILY_API_KEY),
        "polygon": bool(settings.POLYGON_API_KEY),
        "yfinance": True,  # No key needed
        "akshare": True,  # No key needed
    }

    # Return 503 when degraded so Docker/Compose health checks fail correctly
    if checks["status"] == "degraded":
        response.status_code = 503

    return checks
