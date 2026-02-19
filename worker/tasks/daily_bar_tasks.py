"""Daily bar collection tasks -- fetch OHLCV data from providers and store in PostgreSQL.

Each market has its own Celery Beat schedule aligned to market close times.
On successful collection, triggers qlib-service sync via chain task.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from celery import shared_task

from worker.task_helpers import run_async_task

logger = logging.getLogger(__name__)

# Redis progress key pattern for admin dashboard
_PROGRESS_KEY_TEMPLATE = "kb:daily_bars:{market}:progress"
_PROGRESS_TTL = 3600  # 1 hour — auto-expires if task crashes without cleanup
_LOCK_KEY_TEMPLATE = "kb:daily_bars:{market}:lock"
_LOCK_TTL = 28800  # matches task time_limit — auto-releases if task crashes

# Lua script for atomic CAS lock release: only delete if current value matches owner
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

# Module-level Redis connection — reused across calls within one task
_redis_conn = None


def _get_sync_redis():
    """Get or create a module-level sync Redis connection."""
    global _redis_conn
    if _redis_conn is None:
        import redis as redis_lib
        from app.config import settings
        _redis_conn = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
    return _redis_conn


def _update_daily_bar_progress_sync(market: str, symbols_done: int, symbols_total: int, new_bars: int):
    """Write daily bar collection progress to Redis (sync, for use in async callback)."""
    try:
        r = _get_sync_redis()
        pct = int(symbols_done * 100 / symbols_total) if symbols_total > 0 else 0
        progress = {
            "symbolsDone": symbols_done,
            "symbolsTotal": symbols_total,
            "newBars": new_bars,
            "percent": pct,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        key = _PROGRESS_KEY_TEMPLATE.format(market=market)
        r.set(key, json.dumps(progress), ex=_PROGRESS_TTL)
    except Exception:
        pass  # Non-critical


def _clear_daily_bar_progress_sync(market: str):
    """Clear the progress key from Redis (sync, called in task finally)."""
    try:
        r = _get_sync_redis()
        r.delete(_PROGRESS_KEY_TEMPLATE.format(market=market))
    except Exception:
        pass


def _acquire_daily_bar_lock_sync(market: str, task_id: Optional[str] = None) -> Optional[str]:
    """Try to acquire the per-market collection lock.

    Uses SET NX with the Celery task ID as the owner token so that:
    1. Only the holder can release it (CAS pattern).
    2. The admin force-unlock endpoint can revoke the running task by reading
       the lock value.

    Args:
        market: Market code.
        task_id: Celery task ID (self.request.id). Falls back to UUID if None.

    Returns:
        Owner token string if lock acquired, None if already held by another task.
    """
    try:
        r = _get_sync_redis()
        owner = task_id or str(uuid.uuid4())
        acquired = r.set(
            _LOCK_KEY_TEMPLATE.format(market=market),
            owner,
            nx=True,
            ex=_LOCK_TTL,
        )
        if acquired:
            # Clear queued flag — task is now running
            r.delete(f"kb:daily_bars:{market}:queued")
        return owner if acquired else None
    except Exception:
        # Redis unavailable — generate owner token and allow task to proceed
        return task_id or str(uuid.uuid4())


def _release_daily_bar_lock_sync(market: str, owner: str):
    """Release the per-market collection lock only if we still own it (CAS).

    Uses a Lua script to atomically check owner and delete — prevents one
    task from accidentally releasing another task's lock.
    """
    try:
        r = _get_sync_redis()
        r.eval(_RELEASE_LOCK_LUA, 1, _LOCK_KEY_TEMPLATE.format(market=market), owner)
    except Exception:
        pass


def _rebuild_daily_bars_counter_sync(market: str):
    """Sync version of counter rebuild for use in finally blocks.

    Uses psycopg2 (sync) + sync redis to update the cached counter.
    This ensures the counter is always refreshed even if the async event
    loop has already been torn down (e.g., after a crash in run_async_task).
    """
    try:
        import psycopg2
        from app.config import settings

        # Convert asyncpg URL to psycopg2 format
        db_url = str(settings.DATABASE_URL).replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        conn = psycopg2.connect(db_url)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(date), MAX(date) "
                "FROM stock_daily_bars WHERE market = %s",
                (market,),
            )
            count, symbol_count, first_date, last_date = cur.fetchone()
        finally:
            conn.close()

        counter = {
            "count": count,
            "symbolCount": symbol_count,
            "firstDate": first_date.isoformat() if first_date else None,
            "lastDate": last_date.isoformat() if last_date else None,
        }

        r = _get_sync_redis()
        from app.api.v1.admin.knowledge_base import COUNTER_KEY_DAILY_BARS
        r.set(COUNTER_KEY_DAILY_BARS.format(market=market), json.dumps(counter))
        logger.debug(
            "Sync counter rebuild for market=%s: %d bars, %d symbols",
            market, count, symbol_count,
        )
    except Exception as e:
        logger.warning("Failed to rebuild counter sync for %s: %s", market, e)


async def _rebuild_daily_bars_counter_async(market: str):
    """Query per-market stats from DB and write to Redis counter.

    Called inside the task's async context (within run_async_task) after
    collection completes, so both async DB and async Redis are available.
    The stats endpoint reads this counter instead of running expensive queries.
    """
    try:
        from sqlalchemy import text as sa_text

        from app.db.redis import get_redis
        from app.db.task_session import get_task_session

        async with get_task_session() as db:
            row = await db.execute(sa_text(
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

        from app.api.v1.admin.knowledge_base import COUNTER_KEY_DAILY_BARS
        redis = await get_redis()
        await redis.set(
            COUNTER_KEY_DAILY_BARS.format(market=market),
            json.dumps(counter),
        )
        logger.debug(
            "Rebuilt daily bars counter for market=%s: %d bars, %d symbols",
            market, r.count, r.symbol_count,
        )
    except Exception as e:
        logger.warning("Failed to rebuild daily bars counter for %s: %s", market, e)


@shared_task(
    bind=True,
    name="worker.tasks.daily_bar_tasks.collect_market_daily_bars",
    max_retries=2,
    default_retry_delay=300,
    time_limit=28800,     # 8 h — covers full bootstrap of ~12K US symbols (~5-6 h)
    soft_time_limit=28740,
)
def collect_market_daily_bars(self, market: str):
    """Collect daily bars for a single market and write to PostgreSQL.

    On success, chain-triggers Qlib sync for the same market.

    Args:
        market: Market code (us, hk, cn, metal).
    """
    logger.info("日线数据：开始采集%s市场", market)

    owner = _acquire_daily_bar_lock_sync(market, task_id=self.request.id)
    if owner is None:
        logger.warning(
            "Daily bar collection for market=%s already running, skipping duplicate task",
            market,
        )
        return {"symbol_count": 0, "new_bars": 0, "errors": ["Already running"]}

    async def _collect():
        from app.db.task_session import get_task_session
        from app.services.daily_bar_service import DailyBarService

        symbols = await _get_symbols_for_market(market)
        if not symbols:
            logger.warning("No symbols found for market=%s, skipping", market)
            return {"symbol_count": 0, "new_bars": 0, "errors": ["No symbols"]}

        logger.debug("Resolved %d symbols for market=%s", len(symbols), market)
        _update_daily_bar_progress_sync(market, 0, len(symbols), 0)

        async def _on_progress(completed: int, total: int, with_data: int, errors: int):
            _update_daily_bar_progress_sync(market, completed, total, with_data)

        async with get_task_session() as db:
            service = DailyBarService()
            result = await service.collect_market(
                db, market, symbols, on_progress=_on_progress,
            )

        # Rebuild counter BEFORE clearing progress so UI sees updated counts
        await _rebuild_daily_bars_counter_async(market)
        return result

    try:
        result = run_async_task(_collect)
        logger.info(
            "日线数据：%s市场完成 %d只股票 新增%d条 %d错误",
            market,
            result.get("symbol_count", 0),
            result.get("new_bars", 0),
            len(result.get("errors", [])),
        )

        # Chain-trigger Qlib sync if we have symbols (even if no new bars,
        # qlib-service may need to catch up from its last sync point)
        if result.get("symbol_count", 0) > 0:
            from worker.tasks.qlib_sync import sync_qlib_market

            sync_qlib_market.delay(market)
            logger.debug("Triggered Qlib sync for market=%s", market)

        return result
    except Exception as exc:
        logger.exception(
            "Daily bar collection failed for market=%s: %s", market, exc
        )
        raise self.retry(exc=exc)
    finally:
        # Refresh counter from DB so partial inserts are visible even on crash
        _rebuild_daily_bars_counter_sync(market)
        _clear_daily_bar_progress_sync(market)
        _release_daily_bar_lock_sync(market, owner)


@shared_task(
    bind=True,
    name="worker.tasks.daily_bar_tasks.rebuild_market_daily_bars",
    max_retries=0,
    time_limit=28800,
    soft_time_limit=28740,
)
def rebuild_market_daily_bars(self, market: str):
    """Delete all daily bars for a market, then re-collect from scratch.

    This is a destructive operation — all existing bars for the market are
    deleted before re-collection begins.  Uses the same per-market Redis lock
    as collect_market_daily_bars to prevent concurrent runs.
    """
    logger.info("日线重建：开始%s市场", market)

    owner = _acquire_daily_bar_lock_sync(market, task_id=self.request.id)
    if owner is None:
        logger.warning(
            "Daily bar task for market=%s already running, cannot rebuild",
            market,
        )
        return {"symbol_count": 0, "new_bars": 0, "errors": ["Already running"]}

    async def _rebuild():
        from app.db.task_session import get_task_session
        from app.services.daily_bar_service import DailyBarService

        service = DailyBarService()

        # Phase 1: Delete existing bars
        async with get_task_session() as db:
            deleted = await service.delete_market_bars(db, market)
            logger.debug("Rebuild phase 1 complete: deleted %d bars for market=%s", deleted, market)

        # Write zero counter so UI immediately reflects the deletion
        await _rebuild_daily_bars_counter_async(market)

        # Phase 2: Re-collect from scratch
        symbols = await _get_symbols_for_market(market)
        if not symbols:
            logger.warning("No symbols found for market=%s, skipping rebuild", market)
            return {"symbol_count": 0, "new_bars": 0, "deleted": deleted, "errors": ["No symbols"]}

        logger.debug("Rebuild phase 2: collecting %d symbols for market=%s", len(symbols), market)
        _update_daily_bar_progress_sync(market, 0, len(symbols), 0)

        async def _on_progress(completed: int, total: int, with_data: int, errors: int):
            _update_daily_bar_progress_sync(market, completed, total, with_data)

        async with get_task_session() as db:
            result = await service.collect_market(
                db, market, symbols, on_progress=_on_progress,
            )

        # Rebuild counter with final counts BEFORE progress is cleared
        await _rebuild_daily_bars_counter_async(market)
        result["deleted"] = deleted
        return result

    try:
        result = run_async_task(_rebuild)
        logger.info(
            "日线重建：%s市场完成 删除%d 股票%d 新增%d 错误%d",
            market,
            result.get("deleted", 0),
            result.get("symbol_count", 0),
            result.get("new_bars", 0),
            len(result.get("errors", [])),
        )

        if result.get("symbol_count", 0) > 0:
            from worker.tasks.qlib_sync import sync_qlib_market

            sync_qlib_market.delay(market)
            logger.debug("Triggered Qlib sync for market=%s after rebuild", market)

        return result
    except Exception as exc:
        logger.exception("Daily bar rebuild failed for market=%s: %s", market, exc)
        raise
    finally:
        # Always refresh the counter so a Phase-1-zero doesn't persist on crash.
        # This runs even if Phase 2 crashes after Phase 1 set the counter to 0.
        _rebuild_daily_bars_counter_sync(market)
        _clear_daily_bar_progress_sync(market)
        _release_daily_bar_lock_sync(market, owner)


async def _get_symbols_for_market(market: str) -> list[str]:
    """Get symbol list for a market.

    Uses the same logic as the internal API endpoint.
    """
    if market == "us":
        from app.api.v1.internal import _get_us_symbols

        return await _get_us_symbols()

    elif market == "hk":
        from app.services.hsi_constituents import get_hsi_constituents

        return await get_hsi_constituents()

    elif market == "cn":
        from app.api.v1.internal import _get_cn_symbols

        return await _get_cn_symbols()

    elif market == "metal":
        return ["GC=F", "SI=F", "PL=F", "PA=F"]

    else:
        logger.error("Unknown market: %s", market)
        return []
