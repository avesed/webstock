"""Admin knowledge base management endpoints.

Provides stats, rebuild, retry, and clear operations for all 5 knowledge bases:
- Embeddings: stock_profile, news, analysis, report
- Daily bars: cn, us, hk, metal

Performance: Stats endpoint uses Redis counters for expensive aggregations
(daily_bars 8M+ rows, stock_profile embeddings) updated by Celery tasks on
completion.  Lightweight SQL is used for smaller tables (news/analysis/report
embeddings, failed counts).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - Knowledge Base"])

VALID_MARKETS = {"cn", "us", "hk", "metal"}
_MARKETS_SORTED = sorted(VALID_MARKETS)

# Redis counter keys (no TTL — maintained by Celery tasks after batch completion)
COUNTER_KEY_DAILY_BARS = "kb:counters:daily_bars:{market}"
COUNTER_KEY_EMBEDDING = "kb:counters:embeddings:{source_type}"

CLEARABLE_SOURCE_TYPES = {"news", "analysis", "report"}
REBUILDABLE_SOURCE_TYPES = {
    "stock_profile", "stock_profile_sync", "news", "analysis", "report",
}
RETRYABLE_SOURCE_TYPES = {"news", "report"}


# ---------------------------------------------------------------------------
# Redis counter helpers (async — used by stats endpoint and task async code)
# ---------------------------------------------------------------------------


async def _redis_mget(keys: List[str]) -> List[Optional[str]]:
    """Read multiple keys from Redis via MGET. Returns raw strings or None."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        return await redis.mget(keys)
    except Exception as e:
        logger.debug("Redis MGET failed: %s", e)
        return [None] * len(keys)


async def write_counter(key: str, data: dict) -> None:
    """Write a counter to Redis (no TTL — persists until next task update)."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        await redis.set(key, json.dumps(data))
    except Exception as e:
        logger.debug("Redis counter write failed for %s: %s", key, e)


async def rebuild_daily_bars_counter(db: AsyncSession, market: str) -> dict:
    """Run per-market COUNT query and write result to Redis. Returns counter dict."""
    row = await db.execute(text(
        "SELECT COUNT(*) as count, COUNT(DISTINCT symbol) as symbol_count, "
        "MIN(date) as first_date, MAX(date) as last_date "
        "FROM stock_daily_bars WHERE market = :market"
    ), {"market": market})
    r = row.one()
    counter = {
        "count": r.count,
        "symbolCount": r.symbol_count,
        "firstDate": r.first_date.isoformat() if r.first_date else None,
        "lastDate": r.last_date.isoformat() if r.last_date else None,
    }
    await write_counter(COUNTER_KEY_DAILY_BARS.format(market=market), counter)
    return counter


async def rebuild_embedding_counter(db: AsyncSession, source_type: str) -> dict:
    """Run per-source_type COUNT query and write result to Redis. Returns counter dict."""
    row = await db.execute(text(
        "SELECT COUNT(*) as count, MAX(created_at) as last_updated "
        "FROM document_embeddings WHERE source_type = :st"
    ), {"st": source_type})
    r = row.one()
    # Latest model (fast — source_type index narrows scan, LIMIT 1)
    model_row = await db.execute(text(
        "SELECT model FROM document_embeddings "
        "WHERE source_type = :st ORDER BY created_at DESC LIMIT 1"
    ), {"st": source_type})
    model_result = model_row.first()
    counter = {
        "count": r.count,
        "lastUpdated": r.last_updated.isoformat() if r.last_updated else None,
        "model": model_result.model if model_result else None,
    }
    await write_counter(COUNTER_KEY_EMBEDDING.format(source_type=source_type), counter)
    return counter


# ---------------------------------------------------------------------------
# GET /knowledge-base/stats
# ---------------------------------------------------------------------------


@router.get(
    "/knowledge-base/stats",
    summary="Get knowledge base statistics",
    description="Returns embedding counts, daily bar stats, and task progress for all knowledge bases.",
)
async def get_knowledge_base_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Fast stats: Redis counters for daily_bars + stock_profile,
    lightweight SQL for news/analysis/report embeddings."""

    # ── 1. Read cached counters from Redis (single MGET) ──
    daily_bars_keys = [COUNTER_KEY_DAILY_BARS.format(market=m) for m in _MARKETS_SORTED]
    sp_key = COUNTER_KEY_EMBEDDING.format(source_type="stock_profile")
    all_keys = daily_bars_keys + [sp_key]
    raw_values = await _redis_mget(all_keys)

    # ── 2. Daily bars from Redis counters (lazy fallback per market) ──
    daily_bars: Dict[str, Any] = {}
    for i, market in enumerate(_MARKETS_SORTED):
        raw = raw_values[i]
        if raw:
            daily_bars[market] = json.loads(raw)
        else:
            # Counter missing (Redis restart / first deploy) — rebuild from DB
            daily_bars[market] = await rebuild_daily_bars_counter(db, market)

    # ── 3. stock_profile from Redis counter ──
    sp_raw = raw_values[len(daily_bars_keys)]
    if sp_raw:
        stock_profile_stats = json.loads(sp_raw)
    else:
        stock_profile_stats = await rebuild_embedding_counter(db, "stock_profile")

    # ── 4. Lightweight SQL for news/analysis/report embeddings ──
    emb_rows = await db.execute(text(
        "SELECT source_type, COUNT(*) as count, MAX(created_at) as last_updated "
        "FROM document_embeddings "
        "WHERE source_type IN ('news', 'analysis', 'report') "
        "GROUP BY source_type"
    ))
    emb_stats: Dict[str, dict] = {}
    for row in emb_rows:
        emb_stats[row.source_type] = {
            "count": row.count,
            "lastUpdated": row.last_updated.isoformat() if row.last_updated else None,
        }

    # Latest model per source_type (DISTINCT ON — one index scan per type)
    model_rows = await db.execute(text(
        "SELECT DISTINCT ON (source_type) source_type, model "
        "FROM document_embeddings "
        "WHERE source_type IN ('news', 'analysis', 'report') "
        "ORDER BY source_type, created_at DESC"
    ))
    model_map = {row.source_type: row.model for row in model_rows}

    # ── 5. Failed counts (unchanged — already fast) ──
    news_failed_count = (await db.execute(
        text("SELECT COUNT(*) FROM news WHERE content_status = 'embedding_failed'")
    )).scalar() or 0

    report_failed_count = (await db.execute(text(
        "SELECT COUNT(*) FROM reports r "
        "WHERE r.status = 'completed' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM document_embeddings de "
        "  WHERE de.source_type = 'report' AND de.source_id = r.id::text"
        ")"
    ))).scalar() or 0

    # ── 6. Assemble embeddings response ──
    embeddings: Dict[str, Any] = {"stock_profile": stock_profile_stats}
    for src_type in ("news", "analysis", "report"):
        base = emb_stats.get(src_type, {"count": 0, "lastUpdated": None})
        base["model"] = model_map.get(src_type)
        if src_type == "news":
            base["failedCount"] = news_failed_count
        elif src_type == "report":
            base["failedCount"] = report_failed_count
        embeddings[src_type] = base

    # ── 7. Progress from Redis (unchanged) ──
    progress = await _get_all_progress()

    return {
        "embeddings": embeddings,
        "dailyBars": daily_bars,
        "progress": progress,
    }


# ---------------------------------------------------------------------------
# POST /knowledge-base/{source_type}/rebuild
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/{source_type}/rebuild",
    summary="Rebuild knowledge base",
    description="Dispatch a Celery task to rebuild embeddings for the given source type.",
)
async def rebuild_knowledge_base(
    source_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger rebuild for a specific embedding source type."""

    if source_type not in REBUILDABLE_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source type '{source_type}'. Must be one of: {', '.join(sorted(REBUILDABLE_SOURCE_TYPES))}",
        )

    if source_type == "stock_profile":
        from worker.tasks.stock_profile_tasks import build_stock_knowledge_base
        result = build_stock_knowledge_base.delay()
        return {
            "message": "Stock profile knowledge base rebuild started",
            "taskId": result.id,
        }

    if source_type == "stock_profile_sync":
        from worker.tasks.stock_profile_tasks import sync_concept_boards
        result = sync_concept_boards.delay()
        return {
            "message": "Concept board sync started",
            "taskId": result.id,
        }

    if source_type == "news":
        from worker.tasks.embedding_tasks import rebuild_news_embeddings
        result = rebuild_news_embeddings.delay()
        return {
            "message": "News embedding rebuild started",
            "taskId": result.id,
        }

    if source_type == "analysis":
        # Analysis embeddings cannot be recovered (no stored content to re-embed),
        # so just delete them directly.
        try:
            delete_result = await db.execute(
                text("DELETE FROM document_embeddings WHERE source_type = 'analysis'")
            )
            await db.commit()
            deleted = delete_result.rowcount
        except Exception as e:
            logger.error("Failed to clear analysis embeddings: %s", e)
            raise HTTPException(status_code=500, detail="Failed to clear analysis embeddings")
        logger.info(
            "Admin %s cleared %d analysis embeddings",
            current_user.email, deleted,
        )
        return {
            "message": f"Deleted {deleted} analysis embeddings (content cannot be re-embedded)",
            "taskId": None,
        }

    if source_type == "report":
        from worker.tasks.embedding_tasks import rebuild_report_embeddings
        result = rebuild_report_embeddings.delay()
        return {
            "message": "Report embedding rebuild started",
            "taskId": result.id,
        }

    # Unreachable due to validation above, but satisfies type checker
    raise HTTPException(status_code=400, detail="Unknown source type")


# ---------------------------------------------------------------------------
# POST /knowledge-base/{source_type}/retry-failed
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/{source_type}/retry-failed",
    summary="Retry failed embeddings",
    description="Re-process items that failed embedding for news or report source types.",
)
async def retry_failed_embeddings(
    source_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Retry embedding for items that previously failed."""

    if source_type not in RETRYABLE_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Retry is only supported for: {', '.join(sorted(RETRYABLE_SOURCE_TYPES))}",
        )

    if source_type == "news":
        # Count failed news for response
        row = await db.execute(
            text("SELECT COUNT(*) as cnt FROM news WHERE content_status = 'embedding_failed'")
        )
        failed_count = row.scalar() or 0

        from worker.tasks.embedding_tasks import retry_failed_news_embeddings
        result = retry_failed_news_embeddings.delay()
        return {
            "message": f"Retrying {failed_count} failed news embeddings",
            "taskId": result.id,
            "failedCount": failed_count,
        }

    if source_type == "report":
        # Count completed reports without embeddings
        row = await db.execute(text("""
            SELECT COUNT(*) as cnt FROM reports r
            WHERE r.status = 'completed'
            AND NOT EXISTS (
                SELECT 1 FROM document_embeddings de
                WHERE de.source_type = 'report' AND de.source_id = r.id::text
            )
        """))
        failed_count = row.scalar() or 0

        from worker.tasks.embedding_tasks import retry_failed_report_embeddings
        result = retry_failed_report_embeddings.delay()
        return {
            "message": f"Retrying {failed_count} failed report embeddings",
            "taskId": result.id,
            "failedCount": failed_count,
        }

    raise HTTPException(status_code=400, detail="Unknown source type")


# ---------------------------------------------------------------------------
# POST /knowledge-base/{source_type}/clear
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/{source_type}/clear",
    summary="Clear embeddings for a source type",
    description="Delete all embeddings for a given source type. Not allowed for stock_profile.",
)
async def clear_embeddings(
    source_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Delete all embeddings for a given source type."""

    if source_type not in CLEARABLE_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Clear is only supported for: {', '.join(sorted(CLEARABLE_SOURCE_TYPES))}. "
                   f"Use rebuild for stock_profile instead.",
        )

    result = await db.execute(
        text("DELETE FROM document_embeddings WHERE source_type = :src_type"),
        {"src_type": source_type},
    )
    await db.commit()
    deleted = result.rowcount

    logger.info(
        "Admin %s cleared %d embeddings for source_type=%s",
        current_user.email, deleted, source_type,
    )

    return {
        "message": f"Deleted {deleted} {source_type} embeddings",
        "deleted": deleted,
    }


# ---------------------------------------------------------------------------
# POST /knowledge-base/daily-bars/{market}/collect
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/daily-bars/{market}/collect",
    summary="Collect daily bars for a market",
    description="Dispatch a Celery task to collect daily OHLCV bars for the specified market.",
)
async def collect_daily_bars(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger daily bar collection for a single market."""

    if market not in VALID_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}",
        )

    from worker.tasks.daily_bar_tasks import collect_market_daily_bars
    result = collect_market_daily_bars.delay(market)

    return {
        "message": f"Daily bar collection started for market={market}",
        "taskId": result.id,
    }


# ---------------------------------------------------------------------------
# POST /knowledge-base/daily-bars/collect-all
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/daily-bars/collect-all",
    summary="Collect daily bars for all markets",
    description="Dispatch Celery tasks to collect daily OHLCV bars for all 4 markets.",
)
async def collect_all_daily_bars(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger daily bar collection for all markets."""

    from worker.tasks.daily_bar_tasks import collect_market_daily_bars

    task_ids: Dict[str, str] = {}
    for market in sorted(VALID_MARKETS):
        result = collect_market_daily_bars.delay(market)
        task_ids[market] = result.id

    return {
        "message": "Daily bar collection started for all markets",
        "taskIds": task_ids,
    }


# ---------------------------------------------------------------------------
# POST /knowledge-base/daily-bars/{market}/rebuild
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/daily-bars/{market}/rebuild",
    summary="Rebuild daily bars for a market",
    description="Delete all existing daily bars for the market, then re-collect from scratch.",
)
async def rebuild_daily_bars(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger a full rebuild (delete + re-collect) for a single market."""

    if market not in VALID_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}",
        )

    logger.info("Admin %s requested daily bar rebuild for market=%s", current_user.email, market)

    from worker.tasks.daily_bar_tasks import rebuild_market_daily_bars
    result = rebuild_market_daily_bars.delay(market)

    return {
        "message": f"Daily bar rebuild started for market={market}",
        "taskId": result.id,
    }


# ---------------------------------------------------------------------------
# POST /knowledge-base/daily-bars/rebuild-all
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/daily-bars/rebuild-all",
    summary="Rebuild daily bars for all markets",
    description="Delete and re-collect daily OHLCV bars for all 4 markets.",
)
async def rebuild_all_daily_bars(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger a full rebuild for all markets."""

    logger.info("Admin %s requested daily bar rebuild for all markets", current_user.email)

    from worker.tasks.daily_bar_tasks import rebuild_market_daily_bars

    task_ids: Dict[str, str] = {}
    for market in sorted(VALID_MARKETS):
        result = rebuild_market_daily_bars.delay(market)
        task_ids[market] = result.id

    return {
        "message": "Daily bar rebuild started for all markets",
        "taskIds": task_ids,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_all_progress() -> Dict[str, Any]:
    """Read all progress keys from Redis."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()

        # Stock profile progress
        raw = await redis.get("kb:stock_profile:progress")
        stock_profile_progress = json.loads(raw) if raw else None

        # Daily bars progress per market
        daily_bars_progress: Dict[str, Any] = {}
        for market in VALID_MARKETS:
            raw = await redis.get(f"kb:daily_bars:{market}:progress")
            daily_bars_progress[market] = json.loads(raw) if raw else None

        return {
            "stockProfile": stock_profile_progress,
            "dailyBars": daily_bars_progress,
        }
    except Exception as e:
        logger.warning("Failed to read progress from Redis: %s", e)
        return {
            "stockProfile": None,
            "dailyBars": {m: None for m in VALID_MARKETS},
        }
