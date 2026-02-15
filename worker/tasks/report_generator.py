"""Report generation Celery tasks."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from worker.celery_app import celery_app

# Use Celery-safe database utilities (avoids event loop conflicts)
from app.db.task_session import get_task_session

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def check_scheduled_reports(self):
    """
    Periodic task to check if any reports need generating.

    Runs every minute to:
    1. Get all active schedules
    2. Check if current time matches any schedule
    3. Create pending reports for matching schedules
    4. Dispatch generate_report tasks

    This task is registered with Celery Beat schedule.
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_check_scheduled_reports_async())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Check scheduled reports task failed: {e}")
        # Retry with backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _check_scheduled_reports_async() -> Dict[str, Any]:
    """Async implementation of checking scheduled reports."""
    from app.models.report import ReportFormat
    from app.services.report_service import ReportGenerator, ReportService

    logger.info("Checking for scheduled reports")

    stats = {
        "schedules_checked": 0,
        "reports_created": 0,
        "errors": 0,
    }

    try:
        async with get_task_session() as db:
            service = ReportService(db)
            generator = ReportGenerator(db)

            # Get due schedules
            due_schedules = await service.get_due_schedules()
            stats["schedules_checked"] = len(due_schedules)

            if not due_schedules:
                logger.info("No scheduled reports due")
                return stats

            logger.info(f"Found {len(due_schedules)} due schedules")

            for schedule in due_schedules:
                try:
                    # Get symbols for this schedule
                    symbols = await generator.get_symbols_for_schedule(schedule)

                    if not symbols:
                        logger.warning(
                            f"No symbols for schedule {schedule.id}, skipping"
                        )
                        continue

                    # Create report
                    title = (
                        f"{schedule.name} - "
                        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
                    )
                    report = await service.create_report(
                        user_id=schedule.user_id,
                        title=title,
                        schedule_id=schedule.id,
                        format=ReportFormat.JSON.value,
                    )

                    # Mark schedule as run
                    await service.mark_schedule_run(schedule)

                    # Cleanup old reports for user
                    await service.cleanup_old_reports(schedule.user_id)

                    # Dispatch generation task
                    generate_report.delay(str(report.id))

                    stats["reports_created"] += 1
                    logger.info(
                        f"Created report {report.id} for schedule {schedule.id}"
                    )

                except Exception as e:
                    logger.exception(
                        f"Error processing schedule {schedule.id}: {e}"
                    )
                    stats["errors"] += 1

            logger.info(
                f"Scheduled reports check completed: "
                f"{stats['reports_created']} reports created"
            )

    except Exception as e:
        logger.exception(f"Error in check scheduled reports: {e}")
        raise

    return stats


@celery_app.task(bind=True, max_retries=3)
def generate_report(self, report_id: str):
    """
    Generate a single report.

    Steps:
    1. Mark report as "generating"
    2. Fetch all required data (stock prices, technical indicators, news)
    3. Generate content using AI
    4. Save report content
    5. Mark as "completed" or "failed"

    Args:
        report_id: UUID of the report to generate
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_generate_report_async(report_id))
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Generate report task failed for {report_id}: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _generate_report_async(report_id: str) -> Dict[str, Any]:
    """Async implementation of report generation."""
    from app.models.report import Report, ReportSchedule, ReportStatus
    from app.services.report_service import ReportGenerator, ReportService
    from sqlalchemy import select

    logger.info(f"Starting report generation for {report_id}")

    try:
        async with get_task_session() as db:
            service = ReportService(db)
            generator = ReportGenerator(db)

            # Get report
            query = select(Report).where(Report.id == report_id)
            result = await db.execute(query)
            report = result.scalar_one_or_none()

            if not report:
                logger.error(f"Report {report_id} not found")
                return {"status": "error", "message": "Report not found"}

            if report.status not in (
                ReportStatus.PENDING.value,
                ReportStatus.GENERATING.value,
            ):
                logger.warning(
                    f"Report {report_id} already processed "
                    f"(status: {report.status})"
                )
                return {
                    "status": "skipped",
                    "message": f"Report already {report.status}",
                }

            # Get schedule for configuration
            schedule = None
            include_portfolio = False
            include_news = True
            symbols = []

            if report.schedule_id:
                schedule_query = select(ReportSchedule).where(
                    ReportSchedule.id == report.schedule_id
                )
                schedule_result = await db.execute(schedule_query)
                schedule = schedule_result.scalar_one_or_none()

                if schedule:
                    include_portfolio = schedule.include_portfolio
                    include_news = schedule.include_news
                    symbols = await generator.get_symbols_for_schedule(schedule)

            if not symbols:
                # No symbols from schedule, try to get from user's default watchlist
                from app.models.watchlist import Watchlist
                from sqlalchemy.orm import selectinload
                from sqlalchemy import and_

                watchlist_query = (
                    select(Watchlist)
                    .where(
                        and_(
                            Watchlist.user_id == report.user_id,
                            Watchlist.is_default == True,
                        )
                    )
                    .options(selectinload(Watchlist.items))
                )
                watchlist_result = await db.execute(watchlist_query)
                watchlist = watchlist_result.scalar_one_or_none()

                if watchlist and watchlist.items:
                    symbols = [item.symbol for item in watchlist.items]

            if not symbols:
                logger.warning(f"No symbols for report {report_id}")
                report.status = ReportStatus.FAILED.value
                report.error_message = "No symbols to include in report"
                await db.commit()
                return {
                    "status": "failed",
                    "message": "No symbols to include in report",
                }

            # Generate the report
            report = await generator.generate_report(
                report=report,
                symbols=symbols,
                include_portfolio=include_portfolio,
                include_news=include_news,
            )

            logger.info(
                f"Report {report_id} generation completed "
                f"(status: {report.status})"
            )

            # Dispatch embedding for RAG search
            if report.status == ReportStatus.COMPLETED.value and report.content:
                try:
                    from worker.tasks.embedding_tasks import embed_report
                    embed_text = _extract_report_text(report.content)
                    if embed_text:
                        embed_report.delay(
                            str(report.id),
                            embed_text,
                            symbols[0] if len(symbols) == 1 else None,
                        )
                        logger.info("Queued embedding for report %s", report_id)
                except Exception as e:
                    logger.warning("Failed to dispatch report embedding: %s", e)

            return {
                "status": report.status,
                "report_id": report.id,
                "symbols_count": len(symbols),
            }

    except Exception as e:
        logger.exception(f"Error generating report {report_id}: {e}")

        # Try to mark report as failed
        try:
            async with get_task_session() as db:
                from sqlalchemy import update

                await db.execute(
                    update(Report)
                    .where(Report.id == report_id)
                    .values(
                        status=ReportStatus.FAILED.value,
                        error_message=str(e)[:500],
                    )
                )
                await db.commit()
        except Exception as update_error:
            logger.error(f"Failed to update report status: {update_error}")

        raise


def _extract_report_text(content) -> str:
    """Extract plain text from JSON report content for embedding."""
    import json

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content[:50000] if content else ""

    parts: list[str] = []

    def _walk(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, str):
            stripped = obj.strip()
            if stripped:
                parts.append(stripped)
        elif isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(key, str) and key.strip():
                    parts.append(f"\n{key}:")
                _walk(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)

    _walk(content)
    text = "\n".join(parts)
    return text[:50000]


@celery_app.task
def cleanup_old_reports():
    """
    Cleanup task to remove old reports.

    Keeps only the most recent MAX_REPORTS_PER_USER (30) reports per user.
    This task should be scheduled to run daily.
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_cleanup_old_reports_async())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Report cleanup task failed: {e}")
        raise


async def _cleanup_old_reports_async() -> Dict[str, Any]:
    """Async implementation of report cleanup."""
    from app.models.report import Report
    from app.models.user import User
    from app.services.report_service import ReportService
    from sqlalchemy import select, func

    logger.info("Starting report cleanup task")

    total_deleted = 0

    try:
        async with get_task_session() as db:
            service = ReportService(db)

            # Get all users with reports
            user_query = (
                select(Report.user_id)
                .group_by(Report.user_id)
                .having(func.count(Report.id) > 30)  # Only users exceeding limit
            )
            result = await db.execute(user_query)
            user_ids = [row[0] for row in result.fetchall()]

            for user_id in user_ids:
                try:
                    deleted = await service.cleanup_old_reports(user_id)
                    total_deleted += deleted
                except Exception as e:
                    logger.warning(
                        f"Error cleaning up reports for user {user_id}: {e}"
                    )

            logger.info(f"Report cleanup completed: {total_deleted} reports deleted")

            return {
                "users_processed": len(user_ids),
                "reports_deleted": total_deleted,
            }

    except Exception as e:
        logger.exception(f"Error in report cleanup: {e}")
        raise
