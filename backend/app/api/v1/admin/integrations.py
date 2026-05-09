"""Admin integration management for NewsForge, StockPulse, and AlphaForge connections."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.database import get_db
from app.schemas.base import CamelModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - Integrations"])


class IntegrationConfigResponse(BaseModel):
    newsforge_url: str = ""
    newsforge_api_key_set: bool = False
    newsforge_proxy_enabled: bool = True


class IntegrationConfigUpdate(BaseModel):
    newsforge_url: str | None = None
    newsforge_api_key: str | None = None


class IntegrationStatsResponse(BaseModel):
    total_pushed: int = 0
    total_duplicates: int = 0
    total_errors: int = 0
    last_push_at: str | None = None
    last_sync_at: str | None = None


class StockPulseConfigResponse(BaseModel):
    stockpulse_url: str = ""
    stockpulse_api_key_set: bool = False


class StockPulseConfigUpdate(BaseModel):
    stockpulse_url: str | None = None
    stockpulse_api_key: str | None = None


class StockPulseProviderHealth(CamelModel):
    """One row in the StockPulse health-summary providers table.

    StockPulse emits camelCase keys (``healthStatus``, ``lastCheck``,
    ``errorMessage``); ``CamelModel`` (``populate_by_name=True``) accepts
    either snake_case or camelCase on input and serialises camelCase out
    to the WebStock admin UI.
    """

    name: str
    enabled: bool = False
    health_status: str = "unknown"
    last_check: str | None = None
    error_message: str | None = None


class StockPulseMarketStatus(CamelModel):
    """One row in the StockPulse health-summary markets table."""

    market: str
    last_collection_at: str | None = None
    total_bars: int = 0
    total_symbols: int = 0


class StockPulseHealthResponse(CamelModel):
    """Aggregated StockPulse health snapshot proxied to the admin UI."""

    connected: bool = False
    status: str = ""
    service: str = "stockpulse"
    redis: str = "unknown"
    database: str = "unknown"
    executor: str = "unknown"
    providers: list[StockPulseProviderHealth] = []
    markets: list[StockPulseMarketStatus] = []
    error: str | None = None


# Helper: Read/write settings from system_settings table (key-value via JSONB)
# NewsForge integration settings are stored in a JSONB column or as
# individual env-based config values. We use a dedicated DB table for
# admin-overridable integration config.
_SETTING_KEYS = {
    "newsforge_url": "integration.newsforge.url",
    "newsforge_api_key": "integration.newsforge.api_key",
    "newsforge_push_enabled": "integration.newsforge.push_enabled",
    "newsforge_proxy_enabled": "integration.newsforge.proxy_enabled",
    "newsforge_webhook_secret": "integration.newsforge.webhook_secret",
    "stockpulse_url": "integration.stockpulse.url",
    "stockpulse_api_key": "integration.stockpulse.api_key",
    "alphaforge_url": "integration.alphaforge.url",
    "alphaforge_api_key": "integration.alphaforge.api_key",
}


async def _get_setting(db: AsyncSession, key: str) -> str | None:
    """Read a setting from integration_settings table, falling back to env."""
    from sqlalchemy import text

    result = await db.execute(
        text("SELECT value FROM integration_settings WHERE key = :key"),
        {"key": key},
    )
    row = result.first()
    if row:
        return row[0]

    # Fall back to environment config
    from app.config import settings

    env_map = {
        "integration.newsforge.url": settings.NEWSFORGE_URL,
        "integration.newsforge.api_key": settings.NEWSFORGE_API_KEY,
        "integration.newsforge.webhook_secret": settings.NEWSFORGE_WEBHOOK_SECRET,
        "integration.stockpulse.url": settings.STOCKPULSE_URL,
        "integration.stockpulse.api_key": settings.STOCKPULSE_API_KEY,
        "integration.alphaforge.url": settings.ALPHAFORGE_URL,
        "integration.alphaforge.api_key": settings.ALPHAFORGE_API_KEY,
    }
    return env_map.get(key) or None


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Upsert a setting in integration_settings table."""
    from sqlalchemy import text

    await db.execute(
        text(
            "INSERT INTO integration_settings (key, value) VALUES (:key, :value) "
            "ON CONFLICT (key) DO UPDATE SET value = :value"
        ),
        {"key": key, "value": value},
    )


@router.get("/integrations/newsforge", response_model=IntegrationConfigResponse)
async def get_newsforge_config(db: AsyncSession = Depends(get_db)):
    """Get NewsForge integration configuration."""
    url = await _get_setting(db, _SETTING_KEYS["newsforge_url"]) or ""
    api_key = await _get_setting(db, _SETTING_KEYS["newsforge_api_key"])

    return IntegrationConfigResponse(
        newsforge_url=url,
        newsforge_api_key_set=bool(api_key),
        newsforge_proxy_enabled=True,
    )


@router.patch("/integrations/newsforge", response_model=IntegrationConfigResponse)
async def update_newsforge_config(
    body: IntegrationConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update NewsForge integration configuration (URL and API key only)."""
    if body.newsforge_url is not None:
        await _set_setting(db, _SETTING_KEYS["newsforge_url"], body.newsforge_url)
    if body.newsforge_api_key is not None:
        await _set_setting(
            db, _SETTING_KEYS["newsforge_api_key"], body.newsforge_api_key
        )

    await db.commit()

    logger.info("NewsForge integration config updated")

    return await get_newsforge_config(db)


@router.post("/integrations/newsforge/test")
async def test_newsforge_connection(db: AsyncSession = Depends(get_db)):
    """Test connection to NewsForge API."""
    url = await _get_setting(db, _SETTING_KEYS["newsforge_url"])
    api_key = await _get_setting(db, _SETTING_KEYS["newsforge_api_key"])

    if not url or not api_key:
        return {"connected": False, "error": "URL or API key not configured"}

    import httpx

    try:
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/api/internal/articles/recent",
                headers=headers,
                params={"limit": 1},
            )
            resp.raise_for_status()
            return {"connected": True}
    except Exception as e:
        logger.warning("NewsForge connection test failed: %s", e)
        return {"connected": False, "error": str(e)[:200]}


@router.get("/integrations/newsforge/stats", response_model=IntegrationStatsResponse)
async def get_newsforge_stats():
    """Get NewsForge push/sync statistics."""
    try:
        import redis.asyncio as aioredis

        from app.config import settings

        # data-service uses Redis DB 5
        redis_url = settings.REDIS_URL
        base_url = redis_url.rsplit("/", 1)[0]
        ds_redis_url = f"{base_url}/5"

        r = aioredis.from_url(ds_redis_url, decode_responses=True)
        try:
            stats = await r.hgetall("ds:newsforge:push_stats")
            return IntegrationStatsResponse(
                total_pushed=int(stats.get("total_pushed", 0)),
                total_duplicates=int(stats.get("total_duplicates", 0)),
                total_errors=int(stats.get("total_errors", 0)),
                last_push_at=stats.get("last_push_at"),
            )
        finally:
            await r.aclose()
    except Exception:
        logger.warning("Failed to fetch NewsForge stats from Redis", exc_info=True)
        return IntegrationStatsResponse()


# ---------------------------------------------------------------------------
# StockPulse integration endpoints
# ---------------------------------------------------------------------------


async def _resolved_stockpulse_creds(db: AsyncSession) -> tuple[str, str]:
    """Read effective StockPulse URL + API key (DB override → env fallback)."""
    url = (await _get_setting(db, _SETTING_KEYS["stockpulse_url"])) or ""
    api_key = (await _get_setting(db, _SETTING_KEYS["stockpulse_api_key"])) or ""
    return url.rstrip("/"), api_key


@router.get("/integrations/stockpulse", response_model=StockPulseConfigResponse)
async def get_stockpulse_config(db: AsyncSession = Depends(get_db)):
    """Get StockPulse integration configuration."""
    url = (await _get_setting(db, _SETTING_KEYS["stockpulse_url"])) or ""
    api_key = await _get_setting(db, _SETTING_KEYS["stockpulse_api_key"])

    return StockPulseConfigResponse(
        stockpulse_url=url,
        stockpulse_api_key_set=bool(api_key),
    )


@router.patch("/integrations/stockpulse", response_model=StockPulseConfigResponse)
async def update_stockpulse_config(
    body: StockPulseConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update StockPulse integration configuration.

    Accepts a partial update; only the fields that are explicitly provided
    are written. After committing, the singleton ``StockPulseClient`` is
    reset so the next request picks up the new URL / API key.
    """
    if body.stockpulse_url is not None:
        await _set_setting(
            db, _SETTING_KEYS["stockpulse_url"], body.stockpulse_url,
        )
    if body.stockpulse_api_key is not None:
        await _set_setting(
            db, _SETTING_KEYS["stockpulse_api_key"], body.stockpulse_api_key,
        )

    await db.commit()

    # Force the singleton client to be re-created so the new URL/key take
    # effect on the next request without a process restart.
    from app.services.stockpulse_client import (
        close_stockpulse_client,
        reset_stockpulse_client,
    )
    try:
        await close_stockpulse_client()
    except Exception:
        logger.warning("Failed to close StockPulse client during reset", exc_info=True)
    reset_stockpulse_client()

    logger.info("StockPulse integration config updated")

    return await get_stockpulse_config(db)


@router.post("/integrations/stockpulse/test")
async def test_stockpulse_connection(db: AsyncSession = Depends(get_db)):
    """Test connection to StockPulse using an authenticated probe.

    Calls ``GET /api/v1/data/health/summary`` with the configured
    ``X-API-Key`` so the test exercises the actual auth gate the real data
    calls will hit.  A 401/403 response is reported as
    ``{"connected": False, "error": "Invalid API key"}`` so the admin UI
    can flag bad credentials.  Connection-level failures return the raw
    exception message.  On success the response includes a
    ``latency_ms`` field measured from the start of the HTTP request.
    """
    url, api_key = await _resolved_stockpulse_creds(db)

    if not url or not api_key:
        return {"connected": False, "error": "URL or API key not configured"}

    import httpx

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url}/api/v1/data/health/summary", headers=headers,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code in (401, 403):
            logger.warning(
                "StockPulse auth check failed (HTTP %d) — invalid API key",
                resp.status_code,
            )
            return {"connected": False, "error": "Invalid API key"}
        resp.raise_for_status()
        return {"connected": True, "latency_ms": latency_ms}
    except httpx.HTTPStatusError as e:
        logger.warning(
            "StockPulse connection test HTTP error: %d %s",
            e.response.status_code, e.response.reason_phrase,
        )
        return {
            "connected": False,
            "error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
        }
    except Exception as e:
        logger.warning("StockPulse connection test failed: %s", e)
        return {"connected": False, "error": str(e)[:200]}


@router.get(
    "/integrations/stockpulse/health", response_model=StockPulseHealthResponse,
)
async def get_stockpulse_health(db: AsyncSession = Depends(get_db)):
    """Proxy StockPulse's health summary endpoint for the admin dashboard.

    Calls StockPulse ``GET /api/v1/data/health/summary`` (X-API-Key
    protected, 60s Redis cache) and reshapes the response into the
    ``StockPulseHealthResponse`` envelope. On any failure returns
    ``connected=False`` with an ``error`` message — never raises 5xx so the
    admin UI can render a degraded card instead of a fatal error.
    """
    url, api_key = await _resolved_stockpulse_creds(db)
    if not url or not api_key:
        return StockPulseHealthResponse(
            connected=False,
            error="URL or API key not configured",
        )

    import httpx

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{url}/api/v1/data/health/summary",
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as e:
        logger.warning("StockPulse health summary fetch failed: %s", e)
        return StockPulseHealthResponse(
            connected=False, error=str(e)[:200],
        )

    # Unwrap ApiResponse envelope if present
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        payload: dict[str, Any] = body["data"]
    elif isinstance(body, dict):
        payload = body
    else:
        return StockPulseHealthResponse(
            connected=False,
            error="Unexpected health summary payload shape",
        )

    raw_providers = payload.get("providers") or []
    raw_markets = payload.get("markets") or []

    providers: list[StockPulseProviderHealth] = []
    if isinstance(raw_providers, list):
        for item in raw_providers:
            if not isinstance(item, dict):
                continue
            try:
                providers.append(StockPulseProviderHealth.model_validate(item))
            except Exception as exc:  # noqa: BLE001 — log + skip bad row
                logger.debug(
                    "Skipping malformed StockPulse provider row: %s (%s)",
                    item, exc,
                )

    markets: list[StockPulseMarketStatus] = []
    if isinstance(raw_markets, list):
        for item in raw_markets:
            if not isinstance(item, dict):
                continue
            try:
                markets.append(StockPulseMarketStatus.model_validate(item))
            except Exception as exc:  # noqa: BLE001 — log + skip bad row
                logger.debug(
                    "Skipping malformed StockPulse market row: %s (%s)",
                    item, exc,
                )

    return StockPulseHealthResponse(
        connected=True,
        status=str(payload.get("status") or ""),
        service=str(payload.get("service") or "stockpulse"),
        redis=str(payload.get("redis") or "unknown"),
        database=str(payload.get("database") or "unknown"),
        executor=str(payload.get("executor") or "unknown"),
        providers=providers,
        markets=markets,
    )


# ---------------------------------------------------------------------------
# AlphaForge integration endpoints
# ---------------------------------------------------------------------------


class AlphaForgeConfigResponse(BaseModel):
    alphaforge_url: str = ""
    alphaforge_api_key_set: bool = False


class AlphaForgeConfigUpdate(BaseModel):
    alphaforge_url: str | None = None
    alphaforge_api_key: str | None = None


async def _resolved_alphaforge_creds(db: AsyncSession) -> tuple[str, str]:
    """Read effective AlphaForge URL + API key (DB override -> env fallback)."""
    url = (await _get_setting(db, _SETTING_KEYS["alphaforge_url"])) or ""
    api_key = (await _get_setting(db, _SETTING_KEYS["alphaforge_api_key"])) or ""
    return url.rstrip("/"), api_key


@router.get("/integrations/alphaforge", response_model=AlphaForgeConfigResponse)
async def get_alphaforge_config(db: AsyncSession = Depends(get_db)):
    """Get AlphaForge integration configuration."""
    url = (await _get_setting(db, _SETTING_KEYS["alphaforge_url"])) or ""
    api_key = await _get_setting(db, _SETTING_KEYS["alphaforge_api_key"])

    return AlphaForgeConfigResponse(
        alphaforge_url=url,
        alphaforge_api_key_set=bool(api_key),
    )


@router.patch("/integrations/alphaforge", response_model=AlphaForgeConfigResponse)
async def update_alphaforge_config(
    body: AlphaForgeConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update AlphaForge integration configuration.

    Accepts a partial update; only the fields that are explicitly provided
    are written. After committing, the singleton ``AlphaForgeClient`` is
    reset so the next request picks up the new URL / API key.
    """
    if body.alphaforge_url is not None:
        await _set_setting(
            db, _SETTING_KEYS["alphaforge_url"], body.alphaforge_url,
        )
    if body.alphaforge_api_key is not None:
        await _set_setting(
            db, _SETTING_KEYS["alphaforge_api_key"], body.alphaforge_api_key,
        )

    await db.commit()

    # Force the singleton client to be re-created so the new URL/key take
    # effect on the next request without a process restart.
    from app.services.alphaforge_client import (
        close_alphaforge_client,
        reset_alphaforge_client,
    )
    try:
        await close_alphaforge_client()
    except Exception:
        logger.warning("Failed to close AlphaForge client during reset", exc_info=True)
    reset_alphaforge_client()

    logger.info("AlphaForge integration config updated")

    return await get_alphaforge_config(db)


@router.post("/integrations/alphaforge/test")
async def test_alphaforge_connection(db: AsyncSession = Depends(get_db)):
    """Test connection to AlphaForge using an authenticated probe.

    Calls ``GET {url}/health`` with the configured ``X-API-Key`` header.
    A 401/403 response is reported as ``{"connected": False, "error":
    "Invalid API key"}`` so the admin UI can flag bad credentials.
    Connection-level failures return the raw exception message.  On
    success the response includes a ``latency_ms`` field measured from
    the start of the HTTP request.
    """
    url, api_key = await _resolved_alphaforge_creds(db)

    if not url or not api_key:
        return {"connected": False, "error": "URL or API key not configured"}

    import httpx

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url}/health", headers=headers)
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code in (401, 403):
            logger.warning(
                "AlphaForge auth check failed (HTTP %d) -- invalid API key",
                resp.status_code,
            )
            return {"connected": False, "error": "Invalid API key"}
        resp.raise_for_status()
        return {"connected": True, "latency_ms": latency_ms}
    except httpx.HTTPStatusError as e:
        logger.warning(
            "AlphaForge connection test HTTP error: %d %s",
            e.response.status_code, e.response.reason_phrase,
        )
        return {
            "connected": False,
            "error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
        }
    except Exception as e:
        logger.warning("AlphaForge connection test failed: %s", e)
        return {"connected": False, "error": str(e)[:200]}
