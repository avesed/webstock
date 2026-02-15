"""Celery task for cleaning up old Qlib backtest records.

Removes completed/failed/cancelled backtests older than the retention period
to prevent unbounded database growth. Does not delete pending/running backtests
regardless of age to avoid disrupting active work.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from worker.celery_app import celery_app
from app.db.task_session import get_task_session

logger = logging.getLogger(__name__)

# Retention period for completed backtests
BACKTEST_RETENTION_DAYS = 90


@celery_app.task(name="worker.tasks.backtest_cleanup.cleanup_old_backtests")
def cleanup_old_backtests():
    """
    Cleanup task to remove old backtest records.

    Removes backtests older than 90 days with terminal status (completed,
    failed, or cancelled). Active backtests (pending/running) are never
    deleted regardless of age.

    This task should be scheduled to run daily at 5:15 AM.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_cleanup_backtests_async())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception("Backtest cleanup task failed: %s", e)
        raise


async def _cleanup_backtests_async() -> Dict[str, Any]:
    """Async implementation of backtest cleanup."""
    from sqlalchemy import delete

    from app.models.qlib_backtest import QlibBacktest, BacktestStatus

    logger.info("Starting backtest cleanup task")

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=BACKTEST_RETENTION_DAYS)

    try:
        async with get_task_session() as db:
            # Delete old backtests with terminal status only
            # Do NOT delete pending/running backtests regardless of age
            query = delete(QlibBacktest).where(
                QlibBacktest.created_at < cutoff_date,
                QlibBacktest.status.in_([
                    BacktestStatus.COMPLETED.value,
                    BacktestStatus.FAILED.value,
                    BacktestStatus.CANCELLED.value,
                ]),
            )
            result = await db.execute(query)
            await db.commit()

            deleted_count = result.rowcount

            logger.info(
                "Cleaned up %d old backtests (retention: %d days, cutoff: %s)",
                deleted_count, BACKTEST_RETENTION_DAYS, cutoff_date.isoformat(),
            )

            return {
                "deleted_count": deleted_count,
                "retention_days": BACKTEST_RETENTION_DAYS,
                "cutoff_date": cutoff_date.isoformat(),
            }

    except Exception as e:
        logger.exception("Error in backtest cleanup: %s", e)
        raise
