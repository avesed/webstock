"""Health check endpoints for monitoring and orchestration."""

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.db.redis import get_redis

router = APIRouter(prefix="/health", tags=["Health"])


class HealthStatus(BaseModel):
    """Health check response model."""

    status: str
    version: str
    timestamp: str
    uptime_seconds: float | None = None


class ReadinessStatus(BaseModel):
    """Readiness check response model."""

    status: str
    checks: dict[str, Any]
    timestamp: str


class LivenessStatus(BaseModel):
    """Liveness check response model."""

    status: str
    timestamp: str


# Track application start time for uptime calculation
_start_time: float | None = None


def get_start_time() -> float:
    """Get or initialize application start time."""
    global _start_time
    if _start_time is None:
        _start_time = time.time()
    return _start_time


@router.get(
    "",
    response_model=HealthStatus,
    summary="Basic health check",
    description="Returns basic health status of the API service.",
)
async def health_check() -> HealthStatus:
    """
    Basic health check endpoint.

    Returns the current health status of the API.
    This endpoint is lightweight and does not check external dependencies.
    """
    start_time = get_start_time()
    uptime = time.time() - start_time

    return HealthStatus(
        status="healthy",
        version=settings.APP_VERSION,
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=round(uptime, 2),
    )


@router.get(
    "/ready",
    response_model=ReadinessStatus,
    summary="Readiness check",
    description="Checks if the application is ready to accept traffic (DB + Redis).",
    responses={
        200: {"description": "Application is ready"},
        503: {"description": "Application is not ready"},
    },
)
async def readiness_check(
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Readiness check endpoint.

    Verifies that all required dependencies (database, Redis) are available
    and the application is ready to handle requests.

    Used by Kubernetes/Docker for readiness probes.
    """
    checks: dict[str, Any] = {}
    is_ready = True

    # Check database connection
    try:
        db_start = time.time()
        result = await db.execute(text("SELECT 1"))
        result.fetchone()
        db_latency = round((time.time() - db_start) * 1000, 2)
        checks["database"] = {
            "status": "healthy",
            "latency_ms": db_latency,
        }
    except Exception as e:
        checks["database"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        is_ready = False

    # Check Redis connection
    try:
        redis_client = await get_redis()
        redis_start = time.time()
        await redis_client.ping()
        redis_latency = round((time.time() - redis_start) * 1000, 2)
        checks["redis"] = {
            "status": "healthy",
            "latency_ms": redis_latency,
        }
    except Exception as e:
        checks["redis"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        is_ready = False

    response_data = ReadinessStatus(
        status="ready" if is_ready else "not_ready",
        checks=checks,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        content=response_data.model_dump(),
        status_code=status_code,
    )


@router.get(
    "/live",
    response_model=LivenessStatus,
    summary="Liveness check",
    description="Checks if the application process is alive.",
)
async def liveness_check() -> LivenessStatus:
    """
    Liveness check endpoint.

    Simple check to verify the application process is running.
    Used by Kubernetes/Docker for liveness probes.

    This endpoint should always return 200 if the process is alive.
    """
    return LivenessStatus(
        status="alive",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
