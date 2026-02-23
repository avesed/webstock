"""Daily bar collection tasks -- thin proxies delegating to data-service.

Data-service (Phase 7) now owns daily bar collection and DB persistence.
These Celery tasks exist solely for admin UI backward compatibility: the
admin panel dispatches tasks and reads progress from Redis DB 0.

The proxy pattern:
1. Acquire Redis lock (DB 0) for admin UI status
2. Call data-service collection endpoint via HTTP
3. Poll data-service progress every 5 seconds
4. Mirror progress to Redis DB 0 keys for admin UI
5. Rebuild counter from DB when complete
6. Release lock
"""

import asyncio
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
_PROGRESS_TTL = 3600  # 1 hour -- auto-expires if task crashes without cleanup
_LOCK_KEY_TEMPLATE = "kb:daily_bars:{market}:lock"
_LOCK_TTL = 28800  # matches task time_limit -- auto-releases if task crashes

# Lua script for atomic CAS lock release: only delete if current value matches owner
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

# Module-level Redis connection -- reused across calls within one task
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
            # Clear queued flag -- task is now running
            r.delete(f"kb:daily_bars:{market}:queued")
        return owner if acquired else None
    except Exception:
        # Redis unavailable -- generate owner token and allow task to proceed
        return task_id or str(uuid.uuid4())


def _release_daily_bar_lock_sync(market: str, owner: str):
    """Release the per-market collection lock only if we still own it (CAS).

    Uses a Lua script to atomically check owner and delete -- prevents one
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


async def _poll_data_service_progress(market: str, operation: str = "collect"):
    """Poll data-service for collection progress and mirror to Redis DB 0.

    The data-service progress endpoint returns::

        {"market": "us", "progress": {nested dict} or null, "task_running": true/false}

    When collection completes, the progress Redis key is deleted, so the
    inner ``progress`` becomes ``null`` and ``task_running`` becomes ``false``.

    Args:
        market: Market code (us, hk, cn, metal).
        operation: 'collect' or 'rebuild'.

    Returns:
        Final result dict from data-service or a fallback summary.
    """
    from app.services.data_service_client import get_data_service_client

    client = await get_data_service_client()

    max_polls = 5760  # 5760 * 5s = 8 hours (matches task time_limit)
    last_task_running = None

    for _ in range(max_polls):
        await asyncio.sleep(5)

        resp = await client.get_daily_bar_progress(market)
        if resp is None:
            # Data-service unreachable; keep polling
            continue

        task_running = resp.get("task_running", False)
        last_task_running = task_running
        inner = resp.get("progress") or {}

        # Mirror progress to Redis DB 0 for admin UI
        symbols_done = inner.get("symbolsDone", inner.get("symbols_done", 0))
        symbols_total = inner.get("symbolsTotal", inner.get("symbols_total", 0))
        new_bars = inner.get("newBars", inner.get("new_bars", 0))
        _update_daily_bar_progress_sync(market, symbols_done, symbols_total, new_bars)

        if not task_running:
            # Task finished: inner progress may be null (cleared on completion)
            return {
                "symbolsDone": symbols_done,
                "symbolsTotal": symbols_total,
                "newBars": new_bars,
            }

    logger.warning("Polling timed out for %s %s (last_task_running=%s)", operation, market, last_task_running)
    return {"status": "timeout", "symbol_count": 0, "new_bars": 0, "errors": ["Polling timed out"]}


@shared_task(
    bind=True,
    name="worker.tasks.daily_bar_tasks.collect_market_daily_bars",
    max_retries=2,
    default_retry_delay=300,
    time_limit=28800,     # 8 h -- matches data-service collection timeout
    soft_time_limit=28740,
)
def collect_market_daily_bars(self, market: str):
    """Trigger daily bar collection on data-service and mirror progress.

    This is a thin proxy: the actual collection happens in data-service.
    On success, chain-triggers Qlib sync for the same market.

    Args:
        market: Market code (us, hk, cn, metal).
    """
    logger.info("日线数据：触发data-service采集%s市场", market)

    owner = _acquire_daily_bar_lock_sync(market, task_id=self.request.id)
    if owner is None:
        logger.warning(
            "Daily bar collection for market=%s already running, skipping duplicate task",
            market,
        )
        return {"symbol_count": 0, "new_bars": 0, "errors": ["Already running"]}

    async def _collect():
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()

        # Trigger collection on data-service
        trigger_result = await client.trigger_daily_bar_collection(market)
        if trigger_result is None:
            logger.error("Failed to trigger daily bar collection on data-service for market=%s", market)
            return {"symbol_count": 0, "new_bars": 0, "errors": ["data-service trigger failed"]}

        logger.info("data-service collection triggered for market=%s: %s", market, trigger_result.get("status", "unknown"))

        # Poll for progress
        result = await _poll_data_service_progress(market, "collect")

        # Rebuild counter from DB
        await _rebuild_daily_bars_counter_async(market)

        return {
            "symbol_count": result.get("symbolCount", result.get("symbol_count", 0)),
            "new_bars": result.get("newBars", result.get("new_bars", 0)),
            "errors": result.get("errors", []),
        }

    try:
        result = run_async_task(_collect)
        logger.info(
            "日线数据：%s市场完成 %d只股票 新增%d条 %d错误",
            market,
            result.get("symbol_count", 0),
            result.get("new_bars", 0),
            len(result.get("errors", [])),
        )

        # Chain-trigger Qlib sync if we have symbols
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
    """Trigger daily bar rebuild on data-service (delete + re-collect).

    This is a thin proxy: the actual rebuild happens in data-service.
    Uses the same per-market Redis lock as collect_market_daily_bars
    to prevent concurrent runs.
    """
    logger.info("日线重建：触发data-service重建%s市场", market)

    owner = _acquire_daily_bar_lock_sync(market, task_id=self.request.id)
    if owner is None:
        logger.warning(
            "Daily bar task for market=%s already running, cannot rebuild",
            market,
        )
        return {"symbol_count": 0, "new_bars": 0, "errors": ["Already running"]}

    async def _rebuild():
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()

        # Trigger rebuild on data-service
        trigger_result = await client.trigger_daily_bar_rebuild(market)
        if trigger_result is None:
            logger.error("Failed to trigger daily bar rebuild on data-service for market=%s", market)
            return {"symbol_count": 0, "new_bars": 0, "deleted": 0, "errors": ["data-service trigger failed"]}

        logger.info("data-service rebuild triggered for market=%s: %s", market, trigger_result.get("status", "unknown"))

        # Poll for progress
        result = await _poll_data_service_progress(market, "rebuild")

        # Rebuild counter from DB
        await _rebuild_daily_bars_counter_async(market)

        return {
            "symbol_count": result.get("symbolCount", result.get("symbol_count", 0)),
            "new_bars": result.get("newBars", result.get("new_bars", 0)),
            "deleted": result.get("deleted", 0),
            "errors": result.get("errors", []),
        }

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
        _rebuild_daily_bars_counter_sync(market)
        _clear_daily_bar_progress_sync(market)
        _release_daily_bar_lock_sync(market, owner)
