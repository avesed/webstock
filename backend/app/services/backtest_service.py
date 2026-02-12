"""Backtest management service.

Manages backtest records in PostgreSQL and proxies execution to the
qlib-service microservice. Provides a complete lifecycle:
  create -> poll progress -> complete/fail/cancel -> delete

All queries enforce user ownership via user_id filtering.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qlib_backtest import BacktestStatus, QlibBacktest
from app.schemas.qlib import BacktestCreateRequest
from app.services.qlib_client import QlibServiceError, get_qlib_client

logger = logging.getLogger(__name__)

# User backtest quotas
MAX_CONCURRENT_BACKTESTS = 1
MAX_DAILY_BACKTESTS = 10


class BacktestManagementService:
    """Manages backtest records in PostgreSQL + proxies to qlib-service."""

    @staticmethod
    async def create_backtest(
        db: AsyncSession,
        user_id: int,
        request: BacktestCreateRequest,
    ) -> QlibBacktest:
        """Create DB record, forward to qlib-service, store task_id.

        Flow:
        1. Check user quotas (concurrent and daily limits)
        2. Create a local QlibBacktest row (status=pending)
        3. Call qlib-service POST /backtests
        4. Store the returned qlib task_id
        5. Return the enriched record

        If the qlib-service call fails, the record stays in FAILED state
        so the user can see the error and retry.

        Raises:
            ValueError: If user exceeds concurrent or daily backtest quotas
        """
        # 1. Check concurrent backtest quota
        concurrent_count_result = await db.execute(
            select(func.count())
            .select_from(QlibBacktest)
            .where(
                QlibBacktest.user_id == user_id,
                QlibBacktest.status.in_([
                    BacktestStatus.PENDING.value,
                    BacktestStatus.RUNNING.value,
                ]),
            )
        )
        concurrent_count = concurrent_count_result.scalar() or 0

        if concurrent_count >= MAX_CONCURRENT_BACKTESTS:
            raise ValueError(
                f"Maximum {MAX_CONCURRENT_BACKTESTS} concurrent backtest allowed. "
                "Please wait for your current backtest to complete."
            )

        # 2. Check daily backtest quota
        # Note: counts ALL backtests in 24h window regardless of status.
        # This is intentional — failed/cancelled backtests still consume the
        # daily quota to prevent retry-spam abuse.
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
        daily_count_result = await db.execute(
            select(func.count())
            .select_from(QlibBacktest)
            .where(
                QlibBacktest.user_id == user_id,
                QlibBacktest.created_at >= twenty_four_hours_ago,
            )
        )
        daily_count = daily_count_result.scalar() or 0

        if daily_count >= MAX_DAILY_BACKTESTS:
            raise ValueError(
                f"Daily backtest limit ({MAX_DAILY_BACKTESTS}) reached. "
                "Please try again later."
            )

        # 3. Create local record
        backtest = QlibBacktest(
            name=request.name,
            user_id=user_id,
            market=request.market.value,
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            strategy_type=request.strategy_type.value,
            strategy_config=request.strategy_config or {},
            execution_config=request.execution_config or {},
            status=BacktestStatus.PENDING.value,
            progress=0,
        )
        db.add(backtest)
        await db.flush()  # Populate id before qlib-service call

        # 4. Forward to qlib-service
        try:
            client = await get_qlib_client()
            config: Dict[str, Any] = {
                "name": request.name,
                "market": request.market.value,
                "symbols": request.symbols,
                "start_date": str(request.start_date),
                "end_date": str(request.end_date),
                "strategy_type": request.strategy_type.value,
                "strategy_config": request.strategy_config or {},
                "execution_config": request.execution_config or {},
            }
            result = await client.create_backtest(config)

            # 5. Store qlib task_id
            backtest.qlib_task_id = result.get("task_id") or result.get("id")
            backtest.status = BacktestStatus.RUNNING.value
            logger.info(
                "Backtest %s created, qlib_task_id=%s",
                backtest.id, backtest.qlib_task_id,
            )

        except QlibServiceError as e:
            logger.error("qlib-service create_backtest failed: %s", e)
            backtest.status = BacktestStatus.FAILED.value
            backtest.error_message = str(e)

        except Exception as e:
            logger.exception("Unexpected error creating backtest: %s", e)
            backtest.status = BacktestStatus.FAILED.value
            backtest.error_message = f"Unexpected error: {e}"

        await db.commit()
        await db.refresh(backtest)
        return backtest

    @staticmethod
    async def get_backtest(
        db: AsyncSession,
        user_id: int,
        backtest_id: str,
    ) -> Optional[QlibBacktest]:
        """Get backtest from DB. If running, poll qlib-service for progress.

        When the qlib-service reports completion, results are cached
        in the DB so subsequent reads are instant.
        """
        result = await db.execute(
            select(QlibBacktest).where(
                QlibBacktest.id == backtest_id,
                QlibBacktest.user_id == user_id,
            )
        )
        backtest = result.scalar_one_or_none()
        if backtest is None:
            return None

        # If running and we have a qlib task_id, poll for updates
        if (
            backtest.status in (BacktestStatus.PENDING.value, BacktestStatus.RUNNING.value)
            and backtest.qlib_task_id
        ):
            await BacktestManagementService._sync_from_qlib(db, backtest)

        return backtest

    @staticmethod
    async def list_backtests(
        db: AsyncSession,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[QlibBacktest], int]:
        """List user's backtests ordered by created_at desc.

        Returns (items, total_count) for pagination.
        """
        # Count
        count_result = await db.execute(
            select(func.count()).select_from(QlibBacktest).where(
                QlibBacktest.user_id == user_id,
            )
        )
        total = count_result.scalar() or 0

        # Items
        result = await db.execute(
            select(QlibBacktest)
            .where(QlibBacktest.user_id == user_id)
            .order_by(QlibBacktest.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())

        return items, total

    @staticmethod
    async def cancel_backtest(
        db: AsyncSession,
        user_id: int,
        backtest_id: str,
    ) -> Optional[QlibBacktest]:
        """Cancel running backtest via qlib-service + update DB.

        Returns None if backtest not found. Raises ValueError if
        backtest is not in a cancellable state.
        """
        result = await db.execute(
            select(QlibBacktest).where(
                QlibBacktest.id == backtest_id,
                QlibBacktest.user_id == user_id,
            )
        )
        backtest = result.scalar_one_or_none()
        if backtest is None:
            return None

        if backtest.status not in (BacktestStatus.PENDING.value, BacktestStatus.RUNNING.value):
            raise ValueError(
                f"Cannot cancel backtest in '{backtest.status}' state"
            )

        # Cancel in qlib-service if we have a task id
        if backtest.qlib_task_id:
            try:
                client = await get_qlib_client()
                await client.cancel_backtest(backtest.qlib_task_id)
            except QlibServiceError as e:
                logger.warning(
                    "qlib-service cancel failed for task %s: %s",
                    backtest.qlib_task_id, e,
                )
                # Continue -- still mark as cancelled locally

        backtest.status = BacktestStatus.CANCELLED.value
        backtest.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(backtest)

        logger.info("Backtest %s cancelled", backtest_id)
        return backtest

    @staticmethod
    async def delete_backtest(
        db: AsyncSession,
        user_id: int,
        backtest_id: str,
    ) -> bool:
        """Delete backtest record (+ cancel if running).

        Returns True if deleted, False if not found.
        """
        result = await db.execute(
            select(QlibBacktest).where(
                QlibBacktest.id == backtest_id,
                QlibBacktest.user_id == user_id,
            )
        )
        backtest = result.scalar_one_or_none()
        if backtest is None:
            return False

        # Best-effort cancel if still running
        if (
            backtest.status in (BacktestStatus.PENDING.value, BacktestStatus.RUNNING.value)
            and backtest.qlib_task_id
        ):
            try:
                client = await get_qlib_client()
                await client.cancel_backtest(backtest.qlib_task_id)
            except Exception as e:
                logger.warning(
                    "qlib-service cancel on delete failed for task %s: %s",
                    backtest.qlib_task_id, e,
                )

        await db.delete(backtest)
        await db.commit()

        logger.info("Backtest %s deleted", backtest_id)
        return True

    @staticmethod
    async def _sync_from_qlib(
        db: AsyncSession,
        backtest: QlibBacktest,
    ) -> None:
        """Poll qlib-service and update local record.

        This is called internally when fetching a backtest that is
        still in a running/pending state.  Any qlib-service errors
        are logged but do not raise -- the caller gets the last
        known local state instead.
        """
        try:
            client = await get_qlib_client()
            remote = await client.get_backtest(backtest.qlib_task_id)
        except QlibServiceError as e:
            if e.status_code == 404:
                # Task lost on qlib-service restart — mark as failed
                logger.warning(
                    "Backtest %s: qlib-service task %s not found (404). "
                    "Marking as FAILED (likely lost on service restart).",
                    backtest.id, backtest.qlib_task_id,
                )
                backtest.status = BacktestStatus.FAILED.value
                backtest.error_message = (
                    "Backtest task lost on qlib-service restart. "
                    "Please create a new backtest."
                )
                backtest.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await db.refresh(backtest)
            else:
                logger.warning(
                    "qlib-service poll failed for task %s: %s",
                    backtest.qlib_task_id, e,
                )
            return
        except Exception as e:
            logger.error(
                "Unexpected error polling qlib-service for task %s: %s",
                backtest.qlib_task_id, e,
            )
            return

        remote_status = remote.get("status", "").lower()
        remote_progress = remote.get("progress", backtest.progress)

        # Map remote status to local enum
        status_map = {
            "pending": BacktestStatus.PENDING.value,
            "running": BacktestStatus.RUNNING.value,
            "completed": BacktestStatus.COMPLETED.value,
            "failed": BacktestStatus.FAILED.value,
            "cancelled": BacktestStatus.CANCELLED.value,
        }
        mapped_status = status_map.get(remote_status, backtest.status)

        changed = False

        if mapped_status != backtest.status:
            backtest.status = mapped_status
            changed = True

        if remote_progress != backtest.progress:
            backtest.progress = remote_progress
            changed = True

        # Cache results on completion
        if mapped_status == BacktestStatus.COMPLETED.value and remote.get("results"):
            backtest.results = remote["results"]
            backtest.progress = 100
            backtest.completed_at = datetime.now(timezone.utc)
            changed = True

        # Store error on failure
        if mapped_status == BacktestStatus.FAILED.value and remote.get("error"):
            backtest.error_message = remote["error"]
            backtest.completed_at = datetime.now(timezone.utc)
            changed = True

        if changed:
            await db.commit()
            await db.refresh(backtest)
            logger.info(
                "Backtest %s synced: status=%s progress=%d",
                backtest.id, backtest.status, backtest.progress,
            )
