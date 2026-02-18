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
_PROGRESS_TTL = 600  # 10 minutes — auto-expires if task crashes
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


def _acquire_daily_bar_lock_sync(market: str) -> Optional[str]:
    """Try to acquire the per-market collection lock.

    Uses SET NX with a unique owner token (UUID) so only the holder can
    release it (CAS pattern).

    Returns:
        Owner token string if lock acquired, None if already held by another task.
    """
    try:
        r = _get_sync_redis()
        owner = str(uuid.uuid4())
        acquired = r.set(
            _LOCK_KEY_TEMPLATE.format(market=market),
            owner,
            nx=True,
            ex=_LOCK_TTL,
        )
        return owner if acquired else None
    except Exception:
        # Redis unavailable — generate owner token and allow task to proceed
        return str(uuid.uuid4())


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


def _invalidate_daily_bars_stats_cache():
    """Delete the admin stats cache so the next request fetches fresh counts."""
    try:
        r = _get_sync_redis()
        r.delete("kb:stats:daily_bars")
    except Exception:
        pass


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
    logger.info("Starting daily bar collection for market=%s", market)

    owner = _acquire_daily_bar_lock_sync(market)
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

        logger.info("Resolved %d symbols for market=%s", len(symbols), market)
        _update_daily_bar_progress_sync(market, 0, len(symbols), 0)

        async def _on_progress(completed: int, total: int, with_data: int, errors: int):
            _update_daily_bar_progress_sync(market, completed, total, with_data)

        async with get_task_session() as db:
            service = DailyBarService()
            result = await service.collect_market(
                db, market, symbols, on_progress=_on_progress,
            )
            return result

    try:
        result = run_async_task(_collect)
        logger.info(
            "Daily bar collection result for market=%s: symbols=%d, new_bars=%d, errors=%d",
            market,
            result.get("symbol_count", 0),
            result.get("new_bars", 0),
            len(result.get("errors", [])),
        )

        # Invalidate admin stats cache — counts changed after a successful run
        _invalidate_daily_bars_stats_cache()

        # Chain-trigger Qlib sync if we have symbols (even if no new bars,
        # qlib-service may need to catch up from its last sync point)
        if result.get("symbol_count", 0) > 0:
            from worker.tasks.qlib_sync import sync_qlib_market

            sync_qlib_market.delay(market)
            logger.info("Triggered Qlib sync for market=%s", market)

        return result
    except Exception as exc:
        logger.error(
            "Daily bar collection failed for market=%s: %s", market, exc
        )
        raise self.retry(exc=exc)
    finally:
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
