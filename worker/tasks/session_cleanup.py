"""Cleanup old analysis and discussion sessions (90-day retention)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)

SESSION_RETENTION_DAYS = 90


@celery_app.task(name="worker.tasks.session_cleanup.cleanup_old_sessions")
def cleanup_old_sessions():
    """Remove analysis and discussion sessions older than 90 days."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_cleanup_sessions_async())
        finally:
            loop.close()
    except Exception as e:
        logger.exception("Session cleanup task failed: %s", e)
        raise


async def _cleanup_sessions_async() -> Dict[str, Any]:
    """Async implementation: delete old analysis + discussion sessions."""
    from sqlalchemy import delete

    from app.db.task_session import get_task_session
    from app.models.analysis_session import AnalysisSession
    from app.models.discussion import DiscussionSession

    cutoff = datetime.now(timezone.utc) - timedelta(days=SESSION_RETENTION_DAYS)

    stats: Dict[str, Any] = {
        "retention_days": SESSION_RETENTION_DAYS,
        "analysis_deleted": 0,
        "discussion_deleted": 0,
    }

    try:
        async with get_task_session() as db:
            # Delete old analysis sessions (only terminal statuses)
            result = await db.execute(
                delete(AnalysisSession).where(
                    AnalysisSession.created_at < cutoff,
                    AnalysisSession.status.in_(["completed", "failed"]),
                )
            )
            stats["analysis_deleted"] = result.rowcount

            # Delete old discussion sessions (only terminal statuses).
            # discussion_messages cascade-deleted via FK ondelete=CASCADE.
            result = await db.execute(
                delete(DiscussionSession).where(
                    DiscussionSession.created_at < cutoff,
                    DiscussionSession.status.in_(["completed", "failed"]),
                )
            )
            stats["discussion_deleted"] = result.rowcount

            await db.commit()

        total = stats["analysis_deleted"] + stats["discussion_deleted"]
        if total > 0:
            logger.info(
                "会话清理：删除%d条分析会话、%d条讨论会话（>%d天）",
                stats["analysis_deleted"],
                stats["discussion_deleted"],
                SESSION_RETENTION_DAYS,
            )

    except Exception as e:
        logger.exception("Error in session cleanup: %s", e)
        raise

    return stats
