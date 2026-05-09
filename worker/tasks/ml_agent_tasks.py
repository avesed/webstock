"""Celery tasks for ML Agent polling and maintenance.

- run_ml_agent_session: Runs the initial agent loop (profile + LLM + training)
  asynchronously, dispatched by the API endpoint.
- poll_ml_agent_tasks: Polls data-processor for completed training tasks
  and resumes suspended ML Agent sessions (every 30s).
- cleanup_stuck_ml_agents: Marks suspended backtests older than 2h as failed
  and NULLs agent_conversation on old completed/failed backtests (daily).
"""

import logging

from worker.celery_app import celery_app
from worker.task_helpers import run_async_task

logger = logging.getLogger(__name__)


@celery_app.task(
    name="worker.tasks.ml_agent_tasks.run_ml_agent_session",
    bind=True,
    max_retries=0,
    soft_time_limit=900,
    time_limit=960,
)
def run_ml_agent_session(
    self,
    backtest_id: str,
    market: str,
    cutoff_date: str,
    validation_days: int,
    forward_days: int,
    max_iterations: int,
    user_id: int,
):
    """Run the initial ML Agent session (profile + LLM + submit training).

    Dispatched by the API endpoint so the HTTP request returns immediately.
    """
    run_async_task(
        _run_agent_session,
        backtest_id,
        market,
        cutoff_date,
        validation_days,
        forward_days,
        max_iterations,
        user_id,
    )


async def _run_agent_session(
    backtest_id: str,
    market: str,
    cutoff_date: str,
    validation_days: int,
    forward_days: int,
    max_iterations: int,
    user_id: int,
):
    """Async implementation of the initial agent session."""
    from datetime import date as date_type

    from app.db.task_session import get_task_session
    from app.services.ml_agent_service import ml_agent_service

    async with get_task_session() as db:
        try:
            result = await ml_agent_service.run_agent_session(
                backtest_id=backtest_id,
                market=market,
                cutoff_date=date_type.fromisoformat(cutoff_date),
                validation_days=validation_days,
                forward_days=forward_days,
                max_iterations=max_iterations,
                user_id=user_id,
                db=db,
            )
            logger.info(
                "ML Agent session completed: backtest=%s, status=%s",
                backtest_id,
                result.get("status"),
            )
        except Exception as e:
            logger.error(
                "ML Agent session failed for backtest %s: %s",
                backtest_id,
                e,
                exc_info=True,
            )
            await _mark_failed(backtest_id, f"Agent session failed: {e}")


@celery_app.task(
    name="worker.tasks.ml_agent_tasks.poll_ml_agent_tasks",
    bind=True,
    max_retries=0,
)
def poll_ml_agent_tasks(self):
    """Poll for completed ML training tasks and resume agent sessions.

    Runs every 30s via beat. Scans Redis for ml_agent:pending:* keys,
    checks data-processor task status, and resumes agent on completion.
    """
    run_async_task(_poll_pending_tasks)


async def _poll_pending_tasks():
    """Async implementation of the polling logic."""
    from app.db.redis import get_redis

    try:
        redis = await get_redis()
    except Exception as e:
        logger.warning("ML Agent poll: Redis unavailable: %s", e)
        return

    # Scan for pending tasks using SCAN (non-blocking iteration)
    pending_keys: list[str] = []
    async for key in redis.scan_iter(match="ml_agent:pending:*", count=100):
        # RedisConnectionManager uses decode_responses=True, so keys are str
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        pending_keys.append(key)

    if not pending_keys:
        return  # Nothing to poll

    logger.info("ML Agent poll: found %d pending tasks", len(pending_keys))

    from app.services.alphaforge_client import (
        AlphaForgeServiceError,
        get_alphaforge_client,
    )

    try:
        client = await get_alphaforge_client()
    except Exception as e:
        logger.warning("ML Agent poll: AlphaForgeClient unavailable: %s", e)
        return

    for key in pending_keys:
        # key format: ml_agent:pending:{backtest_id}
        backtest_id = key.split(":")[-1]
        task_id = await redis.get(key)
        if isinstance(task_id, bytes):
            task_id = task_id.decode("utf-8")

        if not task_id:
            # Key exists but empty -- clean up
            await redis.delete(key)
            continue

        try:
            # Query data-processor for task status
            task_status = await client.ml_get_training_task(task_id)
            status = task_status.get("status", "unknown")

            if status == "completed":
                logger.info(
                    "ML Agent poll: training completed, resuming backtest=%s task=%s",
                    backtest_id,
                    task_id,
                )
                # Delete key BEFORE resuming to prevent concurrent poll
                # from picking up the same backtest
                await redis.delete(key)
                await _resume_agent(backtest_id, task_status)

            elif status == "failed":
                error = task_status.get("error", "Training failed")
                logger.warning(
                    "ML Agent poll: training failed, marking backtest=%s: %s",
                    backtest_id,
                    error,
                )
                await redis.delete(key)
                await _mark_failed(backtest_id, f"Training failed: {error}")

            elif status in ("submitted", "training"):
                # Still running — refresh TTL so long training jobs
                # don't lose their pending key
                await redis.expire(key, 7200)
                logger.debug(
                    "ML Agent poll: task %s still %s for backtest %s",
                    task_id,
                    status,
                    backtest_id,
                )
            else:
                logger.warning(
                    "ML Agent poll: unknown status '%s' for task %s",
                    status,
                    task_id,
                )

        except AlphaForgeServiceError as e:
            # If task not found (404), data-processor likely restarted
            # and lost in-memory state. Mark the backtest as failed.
            if "404" in str(e) or "not found" in str(e).lower():
                logger.warning(
                    "ML Agent poll: task %s not found (data-processor may have restarted), "
                    "marking backtest %s as failed",
                    task_id,
                    backtest_id,
                )
                await redis.delete(key)
                await _mark_failed(
                    backtest_id,
                    f"Training task {task_id} lost (data-processor restart?)",
                )
            else:
                logger.warning(
                    "ML Agent poll: data-processor error for task %s: %s",
                    task_id,
                    e,
                )
        except Exception as e:
            logger.error(
                "ML Agent poll: unexpected error for backtest %s: %s",
                backtest_id,
                e,
                exc_info=True,
            )


async def _resume_agent(backtest_id: str, training_result: dict):
    """Resume the ML Agent session with training results."""
    from app.db.task_session import get_task_session
    from app.services.ml_agent_service import ml_agent_service

    async with get_task_session() as db:
        try:
            result = await ml_agent_service.resume_session(
                backtest_id=backtest_id,
                training_result=training_result,
                db=db,
            )
            logger.info(
                "ML Agent resumed: backtest=%s, result=%s",
                backtest_id,
                result.get("status"),
            )
        except Exception as e:
            logger.error(
                "ML Agent resume failed for backtest %s: %s",
                backtest_id,
                e,
                exc_info=True,
            )
            # Mark as failed so it does not get polled again
            await _mark_failed(backtest_id, f"Resume failed: {e}")


async def _mark_failed(backtest_id: str, error: str):
    """Mark a backtest as failed."""
    from app.db.task_session import get_task_session
    from app.services.ml_agent_service import MLAgentService

    async with get_task_session() as db:
        try:
            await MLAgentService._update_backtest_status(
                db, backtest_id, "failed", error=error
            )
        except Exception as e:
            logger.error(
                "Failed to mark backtest %s as failed: %s",
                backtest_id,
                e,
            )


# ---------------------------------------------------------------------------
# Auto-tune task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="worker.tasks.ml_agent_tasks.check_auto_tune",
    bind=True,
    max_retries=0,
    soft_time_limit=120,
    time_limit=180,
)
def check_auto_tune(self):
    """Check if auto-tune is due for any market and dispatch ML agent sessions.

    Runs daily at 2:00 AM UTC via beat.  Reads auto_tune_enabled,
    auto_tune_interval_days, and auto_tune_max_iterations from
    system_settings.  For each market where the interval has elapsed,
    creates an MLBacktest record and dispatches run_ml_agent_session.
    """
    run_async_task(_check_auto_tune)


async def _check_auto_tune():
    """Async implementation of auto-tune check."""
    from datetime import date as date_type, timedelta

    from sqlalchemy import text

    from app.db.redis import get_redis
    from app.db.task_session import get_task_session

    # Read settings from DB
    async with get_task_session() as db:
        row = await db.execute(
            text(
                "SELECT auto_tune_enabled, auto_tune_interval_days, "
                "auto_tune_max_iterations, prediction_enabled "
                "FROM system_settings WHERE id = 1"
            )
        )
        settings = row.mappings().first()
        if not settings:
            logger.debug("Auto-tune: no system_settings row")
            return

        if not settings["auto_tune_enabled"]:
            return

        if not settings["prediction_enabled"]:
            logger.debug("Auto-tune: prediction is disabled, skipping")
            return

        interval_days = int(settings["auto_tune_interval_days"])
        max_iterations = int(settings["auto_tune_max_iterations"])

    # Check Redis for last run date per market
    try:
        redis = await get_redis()
    except Exception as e:
        logger.warning("Auto-tune: Redis unavailable: %s", e)
        return

    today = date_type.today()

    for market in ("cn", "us", "hk"):
        key = f"auto_tune:last:{market}"
        last_run_raw = await redis.get(key)

        if last_run_raw:
            if isinstance(last_run_raw, bytes):
                last_run_raw = last_run_raw.decode("utf-8")
            try:
                last_date = date_type.fromisoformat(last_run_raw)
                if (today - last_date).days < interval_days:
                    continue
            except ValueError:
                pass  # Malformed date, proceed with tuning

        logger.info(
            "Auto-tune triggered for %s (interval=%d days, max_iterations=%d)",
            market,
            interval_days,
            max_iterations,
        )

        try:
            await _dispatch_auto_tune(market, max_iterations, today)
            # Update last-run timestamp in Redis (90-day TTL as safety)
            await redis.set(key, today.isoformat(), ex=86400 * 90)
        except Exception as e:
            logger.error(
                "Auto-tune dispatch failed for %s: %s",
                market,
                e,
                exc_info=True,
            )


async def _dispatch_auto_tune(
    market: str,
    max_iterations: int,
    today,
):
    """Create an MLBacktest record and dispatch agent session for auto-tune."""
    from datetime import timedelta

    from app.db.task_session import get_task_session
    from app.services.ml_agent_service import ml_agent_service

    # Use sensible defaults for auto-tune backtests:
    # cutoff_date = today (use latest available data)
    # validation_days = 60 (standard OOS window)
    # forward_days = 5 (default prediction horizon)
    cutoff_date = today
    validation_days = 60
    forward_days = 5

    async with get_task_session() as db:
        backtest_id = await ml_agent_service.create_session(
            market=market,
            cutoff_date=cutoff_date,
            validation_days=validation_days,
            forward_days=forward_days,
            max_iterations=max_iterations,
            db=db,
        )

    # Dispatch as Celery task so it runs async
    # Use a system user_id of 0 for automated sessions
    run_ml_agent_session.apply_async(
        args=[
            backtest_id,
            market,
            cutoff_date.isoformat(),
            validation_days,
            forward_days,
            max_iterations,
            0,  # user_id=0 for automated sessions
        ],
        countdown=5,  # Small delay to let DB commit propagate
    )

    logger.info(
        "Auto-tune dispatched: market=%s, backtest=%s, max_iterations=%d",
        market,
        backtest_id,
        max_iterations,
    )


# ---------------------------------------------------------------------------
# Cleanup task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="worker.tasks.ml_agent_tasks.cleanup_stuck_ml_agents",
    bind=True,
    max_retries=0,
)
def cleanup_stuck_ml_agents(self):
    """Cleanup stuck suspended backtests and stale agent conversation data.

    1. Marks backtests stuck in 'suspended' for >2 hours as failed.
    2. NULLs out agent_conversation on completed/failed backtests older than 7 days.
    """
    run_async_task(_cleanup_ml_agents)


async def _cleanup_ml_agents():
    """Async cleanup implementation."""
    from sqlalchemy import text

    from app.db.task_session import get_task_session

    async with get_task_session() as db:
        # 1. Mark stuck suspended backtests as failed
        result = await db.execute(
            text("""
                UPDATE ml_backtests
                SET status = 'failed',
                    completed_at = NOW()
                WHERE status = 'suspended'
                  AND created_at < NOW() - INTERVAL '2 hours'
            """)
        )
        stuck_count = result.rowcount
        if stuck_count:
            logger.warning(
                "ML Agent cleanup: marked %d stuck suspended backtests as failed",
                stuck_count,
            )

        # 2. NULL out agent_conversation on old terminal backtests
        result = await db.execute(
            text("""
                UPDATE ml_backtests
                SET agent_conversation = NULL
                WHERE status IN ('completed', 'failed')
                  AND completed_at < NOW() - INTERVAL '7 days'
                  AND agent_conversation IS NOT NULL
            """)
        )
        cleaned_count = result.rowcount
        if cleaned_count:
            logger.info(
                "ML Agent cleanup: cleared agent_conversation from %d old backtests",
                cleaned_count,
            )

        await db.commit()
