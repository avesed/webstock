"""APScheduler-based background scheduler for data-service.

Uses Redis-backed leader election so that only ONE uvicorn worker runs
scheduled jobs when multiple workers are deployed.  The leader checks
a Redis key with SET NX + TTL, and renews it on a heartbeat interval.

Daily bar collection jobs:
- collect_cn:        cron hour=8,  minute=0  (UTC, after CN market close)
- collect_hk:        cron hour=9,  minute=0  (UTC, after HK market close)
- collect_us:        cron hour=22, minute=0  (UTC, after US market close)
- collect_metal:     cron hour=22, minute=30 (UTC, metals daily summary)

Stock list job:
- update_stock_list: cron hour=5, minute=30 (UTC, daily stock list rebuild)

Stock profile collection jobs:
- build_stock_kb:     cron sun  hour=6 (UTC, weekly full KB rebuild)
- sync_concept_boards: cron mon-sat hour=6 (UTC, daily concept sync)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.cache import get_redis

logger = logging.getLogger(__name__)

# Leader election settings
_LEADER_KEY = "ds:scheduler:leader"
_LEADER_TTL = 60       # seconds — key expires if leader crashes
_HEARTBEAT_INTERVAL = 30  # seconds — renew before TTL expires

# Unique ID for this worker instance
_INSTANCE_ID = str(uuid.uuid4())

# Module-level state
_scheduler: Optional[AsyncIOScheduler] = None
_is_leader = False


async def start_scheduler() -> None:
    """Attempt to acquire leadership and start the scheduler if successful.

    Called during application startup (FastAPI lifespan).  If another
    worker is already the leader, this is a no-op and the worker runs
    without scheduled jobs.
    """
    global _scheduler, _is_leader

    acquired = await _try_acquire_leadership()
    if not acquired:
        logger.info(
            "Scheduler: another worker is leader, skipping (instance=%s)",
            _INSTANCE_ID[:8],
        )
        return

    _is_leader = True
    logger.info(
        "Scheduler: acquired leadership (instance=%s)", _INSTANCE_ID[:8],
    )

    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Register collection jobs
    _scheduler.add_job(
        _run_collection,
        CronTrigger(hour=8, minute=0),
        args=["cn"],
        id="collect_cn",
        name="Collect CN daily bars",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_collection,
        CronTrigger(hour=9, minute=0),
        args=["hk"],
        id="collect_hk",
        name="Collect HK daily bars",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_collection,
        CronTrigger(hour=22, minute=0),
        args=["us"],
        id="collect_us",
        name="Collect US daily bars",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_collection,
        CronTrigger(hour=22, minute=30),
        args=["metal"],
        id="collect_metal",
        name="Collect Metal daily bars",
        replace_existing=True,
    )

    # Stock list update (daily, same schedule as backend Celery beat)
    _scheduler.add_job(
        _run_stock_list_update,
        CronTrigger(hour=5, minute=30),
        id="update_stock_list",
        name="Update stock list",
        replace_existing=True,
    )

    # Stock profile collection (weekly Sunday 6 AM UTC -- matches Celery beat)
    _scheduler.add_job(
        _run_profile_collection_all,
        CronTrigger(day_of_week="sun", hour=6, minute=0),
        id="build_stock_kb",
        name="Build stock knowledge base (all markets)",
        replace_existing=True,
    )

    # Concept board sync (daily Mon-Sat 6 AM UTC -- matches Celery beat)
    _scheduler.add_job(
        _run_concept_sync,
        CronTrigger(day_of_week="mon-sat", hour=6, minute=0),
        id="sync_concept_boards",
        name="Sync concept boards",
        replace_existing=True,
    )

    # Leadership heartbeat
    _scheduler.add_job(
        _heartbeat,
        IntervalTrigger(seconds=_HEARTBEAT_INTERVAL),
        id="leader_heartbeat",
        name="Leader heartbeat",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started with 4 collection + 1 stock list + 2 profile jobs + heartbeat",
    )


async def stop_scheduler() -> None:
    """Stop the scheduler and release leadership.

    Called during application shutdown (FastAPI lifespan).
    """
    global _scheduler, _is_leader

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")

    if _is_leader:
        await _release_leadership()
        _is_leader = False
        logger.info("Leadership released (instance=%s)", _INSTANCE_ID[:8])


# ---------------------------------------------------------------------------
# Job wrappers
# ---------------------------------------------------------------------------


async def _run_collection(market: str) -> None:
    """Scheduled job: run daily bar collection for a market.

    Checks leadership flag before running (guards against stale scheduler).
    Leadership verification and shutdown are handled exclusively by _heartbeat().
    """
    if not _is_leader:
        logger.debug("Scheduler: not leader, skipping collection for %s", market)
        return

    logger.info("Scheduler: starting collection for market=%s", market)
    try:
        from app.services import collection_service

        result = await collection_service.collect_market(market)
        logger.info(
            "Scheduler: collection for %s complete — symbols=%d, new_bars=%d, errors=%d",
            market,
            result.get("symbol_count", 0),
            result.get("new_bars", 0),
            len(result.get("errors", [])),
        )
    except Exception as exc:
        logger.exception(
            "Scheduler: collection for %s failed: %s", market, exc,
        )


async def _run_stock_list_update() -> None:
    """Scheduled job: build the full stock list and save to disk.

    Checks leadership flag before running (guards against stale scheduler).
    Leadership verification and shutdown are handled exclusively by _heartbeat().
    """
    if not _is_leader:
        logger.debug("Scheduler: not leader, skipping stock list update")
        return

    logger.info("Scheduler: starting stock list update")
    try:
        from app.services import stock_list_persistence

        result = await stock_list_persistence.build_and_save_stock_list()
        logger.info(
            "Scheduler: stock list update complete -- total=%d, by_market=%s",
            result.get("total_stocks", 0),
            result.get("by_market", {}),
        )
    except Exception as exc:
        logger.exception(
            "Scheduler: stock list update failed: %s", exc,
        )


async def _run_profile_collection_all() -> None:
    """Scheduled job: collect profiles for all markets (CN, US, HK).

    Runs markets sequentially to avoid overwhelming external APIs.
    Checks leadership flag before running (guards against stale scheduler).
    Leadership verification and shutdown are handled exclusively by _heartbeat().
    """
    if not _is_leader:
        logger.debug("Scheduler: not leader, skipping profile collection")
        return

    logger.info("Scheduler: starting stock profile collection for all markets")
    try:
        from app.services import profile_collection_service

        for market in ("cn", "us", "hk"):
            logger.info("Scheduler: collecting profiles for market=%s", market)
            result = await profile_collection_service.collect_market_profiles(market)
            collected = result.get("collected", 0)
            elapsed = result.get("elapsed_seconds", 0)
            error = result.get("error")
            if error:
                logger.warning(
                    "Scheduler: profile collection for %s had error: %s "
                    "(collected=%d, %.0fs)",
                    market, error, collected, elapsed,
                )
            else:
                logger.info(
                    "Scheduler: profile collection for %s complete -- "
                    "collected=%d, %.0fs",
                    market, collected, elapsed,
                )

        logger.info("Scheduler: stock profile collection for all markets complete")
    except Exception as exc:
        logger.exception(
            "Scheduler: stock profile collection failed: %s", exc,
        )


async def _run_concept_sync() -> None:
    """Scheduled job: daily concept board mapping sync.

    Collects the concept mapping and saves to disk. The backend's
    Celery task handles the diff-based embedding update.
    Checks leadership flag before running (guards against stale scheduler).
    Leadership verification and shutdown are handled exclusively by _heartbeat().
    """
    if not _is_leader:
        logger.debug("Scheduler: not leader, skipping concept sync")
        return

    logger.info("Scheduler: starting concept board sync")
    try:
        from app.services import profile_collection_service

        result = await profile_collection_service.collect_cn_concept_mapping()
        stock_count = result.get("stock_count", 0)
        elapsed = result.get("elapsed_seconds", 0)
        logger.info(
            "Scheduler: concept board sync complete -- %d stocks in %.0fs",
            stock_count, elapsed,
        )

        # Publish signal so backend can react
        try:
            r = await get_redis()
            payload = json.dumps({
                "stock_count": stock_count,
                "elapsed_seconds": elapsed,
            })
            await r.publish("concept_sync:complete", payload)
        except Exception:
            pass  # Non-critical

    except Exception as exc:
        logger.exception(
            "Scheduler: concept board sync failed: %s", exc,
        )


_RENEW_LEADER_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""


async def _heartbeat() -> None:
    """Atomically check ownership and renew the leader key TTL.

    Uses a Lua CAS script to prevent the race where another instance
    steals the key between our GET and EXPIRE calls.  If the key is
    no longer ours, stop the scheduler gracefully.
    """
    global _is_leader

    try:
        r = await get_redis()
        renewed = await r.eval(
            _RENEW_LEADER_LUA, 1, _LEADER_KEY, _INSTANCE_ID, _LEADER_TTL,
        )
        if renewed:
            logger.debug("Scheduler: heartbeat OK (instance=%s)", _INSTANCE_ID[:8])
            return
    except Exception as exc:
        logger.warning("Scheduler: heartbeat failed: %s", exc)
        # Fail-open on transient error: assume still leader
        return

    # Lua returned 0 -- key is gone or owned by another instance
    logger.warning(
        "Scheduler: leadership lost during heartbeat, stopping (instance=%s)",
        _INSTANCE_ID[:8],
    )
    _is_leader = False
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Leadership helpers
# ---------------------------------------------------------------------------


async def _try_acquire_leadership() -> bool:
    """Try to acquire the leader key via SET NX.

    Returns True if this instance is now the leader.
    """
    try:
        r = await get_redis()
        acquired = await r.set(
            _LEADER_KEY, _INSTANCE_ID, nx=True, ex=_LEADER_TTL,
        )
        return bool(acquired)
    except Exception as exc:
        logger.warning("Failed to acquire leadership: %s", exc)
        return False


async def _verify_leadership() -> bool:
    """Check if this instance is still the leader.

    Returns True on Redis errors (fail-open) to prevent premature
    scheduler shutdown on transient network blips.
    """
    try:
        r = await get_redis()
        current = await r.get(_LEADER_KEY)
        return current == _INSTANCE_ID
    except Exception as exc:
        logger.warning("Failed to verify leadership: %s", exc)
        return True  # Assume still leader on transient error


async def _release_leadership() -> None:
    """Release the leader key (only if we own it)."""
    try:
        r = await get_redis()
        lua = (
            'if redis.call("get", KEYS[1]) == ARGV[1] then '
            'return redis.call("del", KEYS[1]) end return 0'
        )
        await r.eval(lua, 1, _LEADER_KEY, _INSTANCE_ID)
    except Exception as exc:
        logger.warning("Failed to release leadership: %s", exc)
