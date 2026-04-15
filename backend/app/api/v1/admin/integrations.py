"""Admin integration management for NewsForge connection."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - Integrations"])


class IntegrationConfigResponse(BaseModel):
    newsforge_url: str = ""
    newsforge_api_key_set: bool = False
    newsforge_push_enabled: bool = False
    newsforge_proxy_enabled: bool = False
    newsforge_webhook_secret_set: bool = False


class IntegrationConfigUpdate(BaseModel):
    newsforge_url: str | None = None
    newsforge_api_key: str | None = None
    newsforge_push_enabled: bool | None = None
    newsforge_proxy_enabled: bool | None = None
    newsforge_webhook_secret: str | None = None


class IntegrationStatsResponse(BaseModel):
    total_pushed: int = 0
    total_duplicates: int = 0
    total_errors: int = 0
    last_push_at: str | None = None
    last_sync_at: str | None = None


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


@router.get("/integrations/export-news")
async def export_news(
    since: datetime | None = None,
    market: str | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Export news articles as a streaming JSON file download.

    Used for data migration to NewsForge. Only exports basic fields
    (url, title, summary, dates, source) — NOT LLM analysis results.
    NewsForge will reprocess with its own pipeline after import.
    """
    import asyncio
    import json
    from collections.abc import AsyncGenerator
    from datetime import datetime as dt, timezone

    from fastapi.responses import StreamingResponse
    from sqlalchemy import select as sa_select

    from app.models.news import News

    # Build query
    stmt = sa_select(News).order_by(News.published_at.desc())
    if since is not None:
        stmt = stmt.where(News.published_at >= since)
    if market is not None:
        stmt = stmt.where(News.market == market.lower())
    if limit is not None:
        stmt = stmt.limit(limit)

    # Count total (for header)
    from sqlalchemy import func

    count_stmt = sa_select(func.count()).select_from(News)
    if since is not None:
        count_stmt = count_stmt.where(News.published_at >= since)
    if market is not None:
        count_stmt = count_stmt.where(News.market == market.lower())
    if limit is not None:
        count_stmt = count_stmt.limit(limit)

    total_result = await db.execute(
        sa_select(func.count()).select_from(stmt.subquery())
    )
    total_count = total_result.scalar() or 0

    now_str = dt.now(timezone.utc).isoformat()
    date_str = dt.now(timezone.utc).strftime("%Y%m%d")

    async def generate() -> AsyncGenerator[str, None]:
        header = {
            "exported_at": now_str,
            "source": "webstock",
            "count": total_count,
        }
        # Write opening JSON manually for streaming
        yield '{\n'
        yield f'  "exported_at": {json.dumps(now_str)},\n'
        yield f'  "source": "webstock",\n'
        yield f'  "count": {total_count},\n'
        yield '  "articles": [\n'

        BATCH_SIZE = 500
        offset = 0
        first = True

        while True:
            batch_stmt = stmt.offset(offset).limit(BATCH_SIZE)
            result = await db.execute(batch_stmt)
            rows = result.scalars().all()

            if not rows:
                break

            for row in rows:
                article = {
                    "url": row.url,
                    "title": row.title,
                    "summary": row.summary,
                    "published_at": row.published_at.isoformat() if row.published_at else None,
                    "source_name": row.source,
                    "symbol": row.symbol,
                    "market": row.market,
                    "language": row.language,
                }
                prefix = "    " if first else ",\n    "
                first = False
                yield prefix + json.dumps(article, ensure_ascii=False)

            offset += BATCH_SIZE
            if len(rows) < BATCH_SIZE:
                break

        yield '\n  ]\n'
        yield '}\n'

    return StreamingResponse(
        generate(),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="webstock-news-export-{date_str}.json"',
        },
    )


@router.get("/integrations/newsforge", response_model=IntegrationConfigResponse)
async def get_newsforge_config(db: AsyncSession = Depends(get_db)):
    """Get NewsForge integration configuration."""
    url = await _get_setting(db, _SETTING_KEYS["newsforge_url"]) or ""
    api_key = await _get_setting(db, _SETTING_KEYS["newsforge_api_key"])
    enabled = await _get_setting(db, _SETTING_KEYS["newsforge_push_enabled"])
    proxy = await _get_setting(db, _SETTING_KEYS["newsforge_proxy_enabled"])
    secret = await _get_setting(db, _SETTING_KEYS["newsforge_webhook_secret"])

    return IntegrationConfigResponse(
        newsforge_url=url,
        newsforge_api_key_set=bool(api_key),
        newsforge_push_enabled=enabled == "true",
        newsforge_proxy_enabled=proxy == "true",
        newsforge_webhook_secret_set=bool(secret),
    )


@router.patch("/integrations/newsforge", response_model=IntegrationConfigResponse)
async def update_newsforge_config(
    body: IntegrationConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update NewsForge integration configuration."""
    if body.newsforge_url is not None:
        await _set_setting(db, _SETTING_KEYS["newsforge_url"], body.newsforge_url)
    if body.newsforge_api_key is not None:
        await _set_setting(
            db, _SETTING_KEYS["newsforge_api_key"], body.newsforge_api_key
        )
    if body.newsforge_push_enabled is not None:
        await _set_setting(
            db,
            _SETTING_KEYS["newsforge_push_enabled"],
            "true" if body.newsforge_push_enabled else "false",
        )
    if body.newsforge_proxy_enabled is not None:
        await _set_setting(
            db,
            _SETTING_KEYS["newsforge_proxy_enabled"],
            "true" if body.newsforge_proxy_enabled else "false",
        )
    if body.newsforge_webhook_secret is not None:
        await _set_setting(
            db,
            _SETTING_KEYS["newsforge_webhook_secret"],
            body.newsforge_webhook_secret,
        )

    await db.commit()

    # Recompute the effective proxy_enabled flag from the fresh DB state and
    # write it to Redis. Celery workers read Redis synchronously, so this is
    # what makes the PATCH take effect across processes without an
    # asyncio.run() call inside the worker (which previously caused
    # "attached to a different loop" errors).
    from app.services.newsforge_proxy import (
        _compute_proxy_enabled_from_db,
        invalidate_proxy_enabled_cache,
        write_proxy_enabled_to_redis,
    )
    effective = await _compute_proxy_enabled_from_db()
    await write_proxy_enabled_to_redis(effective)
    invalidate_proxy_enabled_cache()

    logger.info(
        "NewsForge integration config updated (proxy_enabled effective=%s)",
        effective,
    )

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
