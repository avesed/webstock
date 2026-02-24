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
COUNTER_KEY_STOCK_PROFILE = "kb:counters:stock_profile:{market}"
STOCK_PROFILE_MARKETS = ("cn", "us", "hk")

# Per-market stock profile lock/progress/queued keys
_SP_MARKETS = ("cn", "us", "hk")
_SP_LOCK_KEY_TEMPLATE = "kb:stock_profile:{market}:lock"
_SP_PROGRESS_KEY_TEMPLATE = "kb:stock_profile:{market}:progress"
_SP_QUEUED_KEY_TEMPLATE = "kb:stock_profile:{market}:queued"
_SP_QUEUED_TTL = 14400

CLEARABLE_SOURCE_TYPES = {"news", "analysis", "report"}
REBUILDABLE_SOURCE_TYPES = {
    "stock_profile", "stock_profile_sync", "news", "analysis", "report",
}
RETRYABLE_SOURCE_TYPES = {"news", "report"}

# Lock key pattern matching daily_bar_tasks.py
_LOCK_KEY_TEMPLATE = "kb:daily_bars:{market}:lock"


# ---------------------------------------------------------------------------
# Redis lock helpers (async — check / force-release from API layer)
# ---------------------------------------------------------------------------


async def _check_market_lock(market: str) -> Optional[int]:
    """Check if a market's daily bar lock is held.

    Returns remaining TTL in seconds if locked, None if unlocked.
    """
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        ttl = await redis.ttl(_LOCK_KEY_TEMPLATE.format(market=market))
        return ttl if ttl > 0 else None
    except Exception:
        return None


_QUEUED_KEY_TEMPLATE = "kb:daily_bars:{market}:queued"
_QUEUED_TTL = 28800  # same as lock TTL — cleared when task acquires lock


async def _mark_market_queued(market: str) -> None:
    """Set a Redis flag indicating this market's task is queued but not yet running."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        await redis.set(_QUEUED_KEY_TEMPLATE.format(market=market), "1", ex=_QUEUED_TTL)
    except Exception:
        pass


async def _check_market_queued(market: str) -> bool:
    """Check if a market has a pending queued task."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        return await redis.exists(_QUEUED_KEY_TEMPLATE.format(market=market)) > 0
    except Exception:
        return False


async def _get_running_markets(exclude: str = "") -> List[str]:
    """Return list of markets that currently have an active lock."""
    running = []
    for m in _MARKETS_SORTED:
        if m == exclude:
            continue
        ttl = await _check_market_lock(m)
        if ttl is not None:
            running.append(m)
    return running


async def _force_release_market_lock(market: str) -> Optional[str]:
    """Revoke the running Celery task and release the Redis lock.

    The lock value stores the Celery task ID, so we can revoke the task
    before deleting the lock to prevent duplicate collection runs.

    Returns the revoked task ID if a lock was released, None otherwise.
    """
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        key = _LOCK_KEY_TEMPLATE.format(market=market)

        # Read the task ID stored as lock value
        task_id = await redis.get(key)

        # Delete the lock
        deleted = await redis.delete(key)
        if not deleted:
            return None

        # Also clear the progress key so UI resets
        await redis.delete(f"kb:daily_bars:{market}:progress")

        # Revoke the Celery task to prevent it from continuing
        if task_id:
            try:
                from worker.celery_app import celery_app
                celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
                logger.info("Revoked Celery task %s for market=%s", task_id, market)
            except Exception as e:
                logger.warning("Failed to revoke task %s: %s", task_id, e)

        return task_id
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stock profile per-market lock helpers (mirror daily bars helpers)
# ---------------------------------------------------------------------------


async def _check_sp_market_lock(market: str) -> Optional[int]:
    """Check stock profile per-market lock TTL. Returns seconds remaining or None."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        ttl = await redis.ttl(_SP_LOCK_KEY_TEMPLATE.format(market=market))
        return ttl if ttl > 0 else None
    except Exception:
        return None


async def _mark_sp_market_queued(market: str) -> None:
    """Set a Redis flag indicating this market's stock profile task is queued."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        await redis.set(
            _SP_QUEUED_KEY_TEMPLATE.format(market=market), "1", ex=_SP_QUEUED_TTL,
        )
    except Exception:
        pass


async def _check_sp_market_queued(market: str) -> bool:
    """Check if a market has a pending queued stock profile task."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        return bool(await redis.exists(_SP_QUEUED_KEY_TEMPLATE.format(market=market)))
    except Exception:
        return False


async def _get_running_sp_markets(exclude: str = "") -> List[str]:
    """Return list of stock profile markets that currently hold a lock."""
    running = []
    for m in _SP_MARKETS:
        if m == exclude:
            continue
        ttl = await _check_sp_market_lock(m)
        if ttl is not None:
            running.append(m)
    return running


async def _force_release_sp_market_lock(market: str) -> Optional[str]:
    """Force-release stock profile lock and revoke the holding Celery task. Returns revoked task_id."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        lock_key = _SP_LOCK_KEY_TEMPLATE.format(market=market)
        task_id = await redis.get(lock_key)

        await redis.delete(lock_key)
        await redis.delete(_SP_PROGRESS_KEY_TEMPLATE.format(market=market))
        await redis.delete(_SP_QUEUED_KEY_TEMPLATE.format(market=market))

        if task_id and task_id != "1":
            try:
                from worker.celery_app import celery_app as _celery
                _celery.control.revoke(task_id, terminate=True, signal="SIGTERM")
            except Exception as e:
                logger.warning("Failed to revoke stock profile task %s: %s", task_id, e)
        return task_id
    except Exception as e:
        logger.error("Failed to force-release stock profile lock for %s: %s", market, e)
        return None


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


async def rebuild_stock_profile_market_counters(db: AsyncSession) -> Dict[str, dict]:
    """Query per-market stock profile embedding counts and write to Redis.

    Classifies stock_profile embeddings by market using source_id (symbol) suffix:
    .SS/.SZ = cn, .HK = hk, everything else = us.
    """
    rows = await db.execute(text(
        "SELECT "
        "  CASE "
        "    WHEN source_id LIKE '%%.SS' OR source_id LIKE '%%.SZ' THEN 'cn' "
        "    WHEN source_id LIKE '%%.HK' THEN 'hk' "
        "    ELSE 'us' "
        "  END as market, "
        "  COUNT(*) as count, "
        "  MAX(created_at) as last_updated "
        "FROM document_embeddings "
        "WHERE source_type = 'stock_profile' "
        "GROUP BY 1"
    ))

    # Get latest model (same for all markets)
    model_row = await db.execute(text(
        "SELECT model FROM document_embeddings "
        "WHERE source_type = 'stock_profile' ORDER BY created_at DESC LIMIT 1"
    ))
    model_result = model_row.first()
    model = model_result.model if model_result else None

    result: Dict[str, dict] = {}
    for r in rows:
        counter = {
            "count": r.count,
            "lastUpdated": r.last_updated.isoformat() if r.last_updated else None,
            "model": model,
        }
        result[r.market] = counter
        await write_counter(COUNTER_KEY_STOCK_PROFILE.format(market=r.market), counter)

    # Ensure all 3 markets are present
    for m in STOCK_PROFILE_MARKETS:
        if m not in result:
            result[m] = {"count": 0, "lastUpdated": None, "model": model}
            await write_counter(COUNTER_KEY_STOCK_PROFILE.format(market=m), result[m])

    # Also update the aggregate counter for backward compat
    total_count = sum(v["count"] for v in result.values())
    latest_updated = max(
        (v["lastUpdated"] for v in result.values() if v["lastUpdated"]),
        default=None,
    )
    aggregate = {"count": total_count, "lastUpdated": latest_updated, "model": model}
    await write_counter(COUNTER_KEY_EMBEDDING.format(source_type="stock_profile"), aggregate)

    return result


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
    sp_market_keys = [COUNTER_KEY_STOCK_PROFILE.format(market=m) for m in STOCK_PROFILE_MARKETS]
    all_keys = daily_bars_keys + [sp_key] + sp_market_keys
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

    # ── 3a. stock_profile aggregate from Redis counter ──
    sp_raw = raw_values[len(daily_bars_keys)]
    if sp_raw:
        stock_profile_stats = json.loads(sp_raw)
    else:
        stock_profile_stats = await rebuild_embedding_counter(db, "stock_profile")

    # ── 3b. Per-market stock profile from Redis (lazy fallback) ──
    sp_market_offset = len(daily_bars_keys) + 1
    sp_market_raw = raw_values[sp_market_offset : sp_market_offset + len(STOCK_PROFILE_MARKETS)]
    sp_market_stats: Dict[str, dict] = {}
    need_rebuild = False
    for i, m in enumerate(STOCK_PROFILE_MARKETS):
        raw = sp_market_raw[i]
        if raw:
            sp_market_stats[m] = json.loads(raw)
        else:
            need_rebuild = True
            break

    if need_rebuild:
        sp_market_stats = await rebuild_stock_profile_market_counters(db)

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
    embeddings: Dict[str, Any] = {
        "stock_profile": {
            "total": stock_profile_stats,
            **{m: sp_market_stats.get(m, {"count": 0, "lastUpdated": None, "model": None})
               for m in STOCK_PROFILE_MARKETS},
        }
    }
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

    # ── 8. Lock + queue status per market ──
    locks: Dict[str, Any] = {}
    for market in _MARKETS_SORTED:
        ttl = await _check_market_lock(market)
        queued = await _check_market_queued(market)
        if ttl:
            locks[market] = {"locked": True, "ttlSeconds": ttl}
        elif queued:
            locks[market] = {"queued": True}
        else:
            locks[market] = None

    # ── 8b. Stock profile per-market locks ──
    sp_locks: Dict[str, Any] = {}
    for market in _SP_MARKETS:
        ttl = await _check_sp_market_lock(market)
        queued = await _check_sp_market_queued(market)
        if ttl is not None:
            sp_locks[market] = {"locked": True, "ttlSeconds": ttl}
        elif queued:
            sp_locks[market] = {"queued": True}
        else:
            sp_locks[market] = None

    # ── 9. Stock list stats ──
    stock_list_stats = None
    try:
        from app.services.stock_list_service import get_stock_list_service
        svc = await get_stock_list_service()
        # Force version check (skip cooldown) so admin panel always shows latest
        await svc.check_for_updates(force=True)
        sl = svc.get_stats()
        # Derive lastUpdated from version string (format: YYYYMMDDHHmmSS)
        sl_last_updated = None
        if sl.get("version"):
            try:
                from datetime import datetime, timezone
                dt = datetime.strptime(sl["version"], "%Y%m%d%H%M%S").replace(
                    tzinfo=timezone.utc,
                )
                sl_last_updated = dt.isoformat()
            except (ValueError, TypeError):
                pass
        stock_list_stats = {
            "totalCount": sl["stock_count"],
            "version": sl["version"],
            "lastUpdated": sl_last_updated,
            "marketCounts": sl["market_counts"],
        }
    except Exception as e:
        logger.warning("Failed to get stock list stats: %s", e)

    return {
        "embeddings": embeddings,
        "dailyBars": daily_bars,
        "stockList": stock_list_stats,
        "progress": progress,
        "locks": locks,
        "stockProfileLocks": sp_locks,
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

    # Pre-check lock to give immediate feedback instead of silent no-op
    lock_ttl = await _check_market_lock(market)
    if lock_ttl is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Market {market} has a task already running (lock TTL: {lock_ttl}s). "
                   f"Wait for it to finish or force-unlock via the admin panel.",
        )

    from worker.tasks.daily_bar_tasks import collect_market_daily_bars
    result = collect_market_daily_bars.delay(market)

    running = await _get_running_markets(exclude=market)
    msg = f"Daily bar collection started for market={market}"
    resp: Dict[str, Any] = {"message": msg, "taskId": result.id}
    if running:
        await _mark_market_queued(market)
        resp["queued"] = True
        resp["runningMarkets"] = running
        resp["message"] = msg + f" (queued behind: {', '.join(running)})"
    return resp


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
    """Trigger daily bar collection for all markets (chained sequentially)."""

    from celery import chain as celery_chain

    from worker.tasks.daily_bar_tasks import collect_market_daily_bars

    markets_to_run: List[str] = []
    skipped: List[str] = []
    for market in sorted(VALID_MARKETS):
        lock_ttl = await _check_market_lock(market)
        if lock_ttl is not None:
            skipped.append(market)
        else:
            markets_to_run.append(market)

    if not markets_to_run:
        raise HTTPException(
            status_code=409,
            detail=f"All markets are locked: {', '.join(skipped)}. "
                   f"Wait for running tasks to finish or force-unlock.",
        )

    # Chain tasks sequentially: with dedicated concurrency=1 worker,
    # parallel dispatch would just queue them anyway. Chain makes the
    # order explicit and avoids lock contention between tasks.
    task_chain = celery_chain(
        *(collect_market_daily_bars.si(m) for m in markets_to_run)
    )
    result = task_chain.apply_async()

    msg = f"Daily bar collection chained for {', '.join(markets_to_run)}"
    if skipped:
        msg += f" (skipped locked: {', '.join(skipped)})"

    return {
        "message": msg,
        "taskId": result.id,
        "markets": markets_to_run,
        "skipped": skipped,
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

    # Pre-check lock to give immediate feedback instead of silent no-op
    lock_ttl = await _check_market_lock(market)
    if lock_ttl is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Market {market} has a task already running (lock TTL: {lock_ttl}s). "
                   f"Wait for it to finish or force-unlock via the admin panel.",
        )

    logger.info("Admin %s requested daily bar rebuild for market=%s", current_user.email, market)

    from worker.tasks.daily_bar_tasks import rebuild_market_daily_bars
    result = rebuild_market_daily_bars.delay(market)

    running = await _get_running_markets(exclude=market)
    msg = f"Daily bar rebuild started for market={market}"
    resp: Dict[str, Any] = {"message": msg, "taskId": result.id}
    if running:
        await _mark_market_queued(market)
        resp["queued"] = True
        resp["runningMarkets"] = running
        resp["message"] = msg + f" (queued behind: {', '.join(running)})"
    return resp


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
    """Trigger a full rebuild for all markets (chained sequentially)."""

    logger.info("Admin %s requested daily bar rebuild for all markets", current_user.email)

    from celery import chain as celery_chain

    from worker.tasks.daily_bar_tasks import rebuild_market_daily_bars

    markets_to_run: List[str] = []
    skipped: List[str] = []
    for market in sorted(VALID_MARKETS):
        lock_ttl = await _check_market_lock(market)
        if lock_ttl is not None:
            skipped.append(market)
        else:
            markets_to_run.append(market)

    if not markets_to_run:
        raise HTTPException(
            status_code=409,
            detail=f"All markets are locked: {', '.join(skipped)}. "
                   f"Wait for running tasks to finish or force-unlock.",
        )

    task_chain = celery_chain(
        *(rebuild_market_daily_bars.si(m) for m in markets_to_run)
    )
    result = task_chain.apply_async()

    msg = f"Daily bar rebuild chained for {', '.join(markets_to_run)}"
    if skipped:
        msg += f" (skipped locked: {', '.join(skipped)})"

    return {
        "message": msg,
        "taskId": result.id,
        "markets": markets_to_run,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# POST /knowledge-base/daily-bars/{market}/unlock
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/daily-bars/{market}/unlock",
    summary="Force-unlock a market's daily bar lock",
    description="Remove a stale Redis lock for the specified market. Use when a previous task crashed.",
)
async def unlock_daily_bars(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Force-release a stale per-market lock and revoke the running task."""

    if market not in VALID_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}",
        )

    revoked_task_id = await _force_release_market_lock(market)

    # Also unlock on data-service side
    try:
        from app.services.data_service_client import get_data_service_client
        client = await get_data_service_client()
        await client.unlock_daily_bars(market)
    except Exception as e:
        logger.warning("Failed to unlock data-service for %s: %s", market, e)

    if revoked_task_id:
        logger.warning(
            "Admin %s force-released daily bar lock for market=%s, revoked task=%s",
            current_user.email, market, revoked_task_id,
        )
        return {"message": f"Lock released and task terminated for market={market}"}
    else:
        return {"message": f"No lock held for market={market}"}


# ---------------------------------------------------------------------------
# POST /knowledge-base/daily-bars/{market}/sync-qlib
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/daily-bars/{market}/sync-qlib",
    summary="Trigger Qlib data sync for a market",
    description="Trigger non-blocking Qlib .bin file sync for the specified market via qlib-service.",
)
async def sync_qlib(
    market: str,
    full: bool = False,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger Qlib sync for a single market (non-blocking)."""

    if market not in VALID_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}",
        )

    mode = "full rebuild" if full else "incremental"
    logger.info("Admin %s triggered Qlib sync for market=%s (%s)", current_user.email, market, mode)

    try:
        from app.services.qlib_client import get_qlib_client
        client = await get_qlib_client()
        result = await client.trigger_sync(market=market, update_only=not full)
        return result
    except Exception as e:
        from app.services.qlib_client import QlibServiceError
        if isinstance(e, QlibServiceError) and e.status_code == 409:
            raise HTTPException(
                status_code=409,
                detail=f"Qlib sync already running for market={market}",
            )
        logger.error("Failed to trigger Qlib sync for %s: %s", market, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger Qlib sync: {e}")


# ---------------------------------------------------------------------------
# GET /knowledge-base/qlib-sync/progress
# ---------------------------------------------------------------------------


@router.get(
    "/knowledge-base/qlib-sync/progress",
    summary="Get Qlib sync progress",
    description="Get current Qlib sync progress for all markets from qlib-service.",
)
async def get_qlib_sync_progress(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Get Qlib sync progress for all markets."""

    try:
        from app.services.qlib_client import get_qlib_client
        client = await get_qlib_client()
        return await client.get_sync_progress()
    except Exception as e:
        logger.error("Failed to get Qlib sync progress: %s", e, exc_info=True)
        # Return empty progress on error rather than 500
        return {"markets": {"us": None, "hk": None, "cn": None, "metal": None}}


# ---------------------------------------------------------------------------
# POST /knowledge-base/stock-profiles/{market}/collect
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/stock-profiles/{market}/collect",
    summary="Collect stock profiles for a single market",
)
async def collect_stock_profiles(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger stock profile collection for a single market."""

    if market not in _SP_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market: {market}. Must be one of: {', '.join(_SP_MARKETS)}",
        )

    lock_ttl = await _check_sp_market_lock(market)
    if lock_ttl is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Stock profile collection for {market} is already running (lock TTL: {lock_ttl}s)",
        )

    from worker.tasks.stock_profile_tasks import collect_market_profiles
    result = collect_market_profiles.delay(market)

    running = await _get_running_sp_markets(exclude=market)
    msg = f"Stock profile collection started for market={market}"
    resp: Dict[str, Any] = {"message": msg, "taskId": result.id}
    if running:
        await _mark_sp_market_queued(market)
        resp["queued"] = True
        resp["runningMarkets"] = running
        resp["message"] = msg + f" (queued behind: {', '.join(running)})"

    logger.info(
        "Admin %s triggered stock profile collect: market=%s, taskId=%s",
        current_user.email, market, result.id,
    )
    return resp


# ---------------------------------------------------------------------------
# POST /knowledge-base/stock-profiles/collect-all
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/stock-profiles/collect-all",
    summary="Collect stock profiles for all markets sequentially",
)
async def collect_all_stock_profiles(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger stock profile collection for all markets (chained sequentially)."""

    from celery import chain as celery_chain
    from worker.tasks.stock_profile_tasks import collect_market_profiles

    markets_to_run: List[str] = []
    skipped: List[str] = []
    for market in _SP_MARKETS:
        lock_ttl = await _check_sp_market_lock(market)
        if lock_ttl is not None:
            skipped.append(market)
        else:
            markets_to_run.append(market)

    if not markets_to_run:
        raise HTTPException(
            status_code=409,
            detail=f"All stock profile markets are locked: {', '.join(skipped)}",
        )

    task_chain = celery_chain(
        *(collect_market_profiles.si(m) for m in markets_to_run)
    )
    result = task_chain.apply_async()

    # Mark non-first markets as queued
    for m in markets_to_run[1:]:
        await _mark_sp_market_queued(m)

    logger.info(
        "Admin %s triggered stock profile collect-all: markets=%s, skipped=%s, chainId=%s",
        current_user.email, markets_to_run, skipped, result.id,
    )
    return {
        "message": f"Stock profile collection chain started for {', '.join(markets_to_run)}",
        "taskId": result.id,
        "markets": markets_to_run,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# POST /knowledge-base/stock-profiles/{market}/unlock
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/stock-profiles/{market}/unlock",
    summary="Force-unlock a stock profile market lock",
)
async def unlock_stock_profiles(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Force-release a stale stock profile per-market lock and revoke the running task."""

    if market not in _SP_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market: {market}. Must be one of: {', '.join(_SP_MARKETS)}",
        )

    revoked_task_id = await _force_release_sp_market_lock(market)
    if revoked_task_id:
        logger.warning(
            "Admin %s force-released stock profile lock: market=%s, revoked_task=%s",
            current_user.email, market, revoked_task_id,
        )
        return {"message": f"Lock released and task terminated for market={market}"}
    return {"message": f"No lock held for market={market}"}


# ---------------------------------------------------------------------------
# POST /knowledge-base/stock-list/update
# ---------------------------------------------------------------------------


@router.post(
    "/knowledge-base/stock-list/update",
    summary="Update stock list",
    description="Dispatch a Celery task to refresh the stock list from Finnhub and AKShare.",
)
async def trigger_stock_list_update(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger stock list update task."""
    logger.info("Admin %s triggered stock list update", current_user.email)
    from worker.tasks.stock_list_tasks import update_stock_list
    result = update_stock_list.delay()
    return {"message": "Stock list update started", "taskId": result.id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_all_progress() -> Dict[str, Any]:
    """Read all progress keys from Redis."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()

        # Stock profile progress (legacy aggregate)
        raw = await redis.get("kb:stock_profile:progress")
        stock_profile_progress = json.loads(raw) if raw else None

        # Per-market stock profile progress
        sp_progress: Dict[str, Any] = {}
        for market in _SP_MARKETS:
            raw = await redis.get(_SP_PROGRESS_KEY_TEMPLATE.format(market=market))
            sp_progress[market] = json.loads(raw) if raw else None

        # Daily bars progress per market
        daily_bars_progress: Dict[str, Any] = {}
        for market in VALID_MARKETS:
            raw = await redis.get(f"kb:daily_bars:{market}:progress")
            daily_bars_progress[market] = json.loads(raw) if raw else None

        return {
            "stockProfile": stock_profile_progress,
            "stockProfiles": sp_progress,
            "dailyBars": daily_bars_progress,
        }
    except Exception as e:
        logger.warning("Failed to read progress from Redis: %s", e)
        return {
            "stockProfile": None,
            "stockProfiles": {m: None for m in _SP_MARKETS},
            "dailyBars": {m: None for m in VALID_MARKETS},
        }
