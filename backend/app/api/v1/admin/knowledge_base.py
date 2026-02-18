"""Admin knowledge base management endpoints.

Provides stats, rebuild, retry, and clear operations for all 5 knowledge bases:
- Embeddings: stock_profile, news, analysis, report
- Daily bars: cn, us, hk, metal
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

# Redis cache for the slow daily-bars stats query (COUNT DISTINCT on 8M+ rows)
_DAILY_BARS_STATS_CACHE_KEY = "kb:stats:daily_bars"
_DAILY_BARS_STATS_TTL = 60  # seconds; data only changes when collection tasks run
CLEARABLE_SOURCE_TYPES = {"news", "analysis", "report"}
REBUILDABLE_SOURCE_TYPES = {
    "stock_profile", "stock_profile_sync", "news", "analysis", "report",
}
RETRYABLE_SOURCE_TYPES = {"news", "report"}


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
    """Aggregate stats from document_embeddings, stock_daily_bars, news, reports, and Redis progress keys."""

    # -- Embedding stats by source_type --
    embedding_rows = await db.execute(text("""
        SELECT source_type, COUNT(*) as count,
               MAX(created_at) as last_updated,
               (SELECT model FROM document_embeddings de2
                WHERE de2.source_type = de.source_type
                ORDER BY de2.created_at DESC LIMIT 1) as model
        FROM document_embeddings de
        GROUP BY source_type
    """))
    embedding_stats_raw = {
        row.source_type: {
            "count": row.count,
            "lastUpdated": row.last_updated.isoformat() if row.last_updated else None,
            "model": row.model,
        }
        for row in embedding_rows
    }

    # -- Failed news count (embedding_failed) --
    news_failed_row = await db.execute(
        text("SELECT COUNT(*) as cnt FROM news WHERE content_status = 'embedding_failed'")
    )
    news_failed_count = news_failed_row.scalar() or 0

    # -- Failed report count (completed reports without embeddings) --
    report_failed_row = await db.execute(text("""
        SELECT COUNT(*) as cnt FROM reports r
        WHERE r.status = 'completed'
        AND NOT EXISTS (
            SELECT 1 FROM document_embeddings de
            WHERE de.source_type = 'report' AND de.source_id = r.id::text
        )
    """))
    report_failed_count = report_failed_row.scalar() or 0

    # Build embeddings response with defaults for missing source types
    embeddings: Dict[str, Any] = {}
    for src_type in ("stock_profile", "news", "analysis", "report"):
        base = embedding_stats_raw.get(src_type, {
            "count": 0,
            "lastUpdated": None,
            "model": None,
        })
        if src_type == "news":
            base["failedCount"] = news_failed_count
        elif src_type == "report":
            base["failedCount"] = report_failed_count
        embeddings[src_type] = base

    # -- Daily bar stats by market (cached: COUNT DISTINCT on 8M+ rows is 2-17s) --
    daily_bars: Dict[str, Any] = {}
    try:
        from app.db.redis import get_redis
        _redis = await get_redis()
        _cached = await _redis.get(_DAILY_BARS_STATS_CACHE_KEY)
        if _cached:
            daily_bars = json.loads(_cached)
    except Exception as _e:
        logger.debug("Daily bars stats cache read failed: %s", _e)

    if not daily_bars:
        bar_rows = await db.execute(text("""
            SELECT market, COUNT(*) as count,
                   MAX(date) as last_date, MIN(date) as first_date,
                   COUNT(DISTINCT symbol) as symbol_count
            FROM stock_daily_bars
            GROUP BY market
        """))
        for row in bar_rows:
            daily_bars[row.market] = {
                "count": row.count,
                "symbolCount": row.symbol_count,
                "firstDate": row.first_date.isoformat() if row.first_date else None,
                "lastDate": row.last_date.isoformat() if row.last_date else None,
            }
        # Fill missing markets with zeros
        for market in VALID_MARKETS:
            if market not in daily_bars:
                daily_bars[market] = {
                    "count": 0,
                    "symbolCount": 0,
                    "firstDate": None,
                    "lastDate": None,
                }
        # Store in Redis for subsequent requests
        try:
            from app.db.redis import get_redis
            _redis = await get_redis()
            await _redis.set(
                _DAILY_BARS_STATS_CACHE_KEY,
                json.dumps(daily_bars),
                ex=_DAILY_BARS_STATS_TTL,
            )
        except Exception as _e:
            logger.debug("Daily bars stats cache write failed: %s", _e)

    # -- Progress from Redis --
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
