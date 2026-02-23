"""Celery tasks module."""

import asyncio
import logging

from worker.celery_app import celery_app
from worker.tasks.key_rotation import (
    auto_rotate_jwt_keys,
    cleanup_old_jwt_keys,
    verify_jwt_key_rotation,
)

logger = logging.getLogger(__name__)


@celery_app.task
def cleanup_expired_tokens():
    """Cleanup expired tokens from Redis."""
    # This is a placeholder task
    # Implement actual token cleanup logic when needed
    return {"status": "completed", "message": "Token cleanup executed"}


@celery_app.task
def send_email_notification(email: str, subject: str, body: str):
    """Send email notification task."""
    # Placeholder for email sending logic
    return {"status": "sent", "email": email}


@celery_app.task(name="worker.tasks.cleanup_stuck_discussions")
def cleanup_stuck_discussions():
    """Mark discussion sessions stuck in active statuses as failed.

    Handles edge cases where the background task worker crashed (SIGKILL/OOM)
    and couldn't clean up the session status in PostgreSQL.

    Cutoffs:
    - "pending" sessions older than 10 minutes (never transitioned to discussing)
    - "discussing"/"synthesizing" sessions older than 30 minutes
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_cleanup_stuck_discussions_async())
        finally:
            loop.close()
    except Exception as e:
        logger.exception("Discussion cleanup task failed: %s", e)
        raise


async def _cleanup_stuck_discussions_async():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import or_, update

    from app.db.task_session import get_task_session
    from app.models.discussion import DiscussionSession

    now = datetime.now(timezone.utc)
    pending_cutoff = now - timedelta(minutes=10)
    discussing_cutoff = now - timedelta(minutes=30)

    async with get_task_session() as db:
        result = await db.execute(
            update(DiscussionSession)
            .where(
                or_(
                    # Pending sessions that never started (10 min cutoff)
                    (
                        DiscussionSession.status == "pending"
                    ) & (
                        DiscussionSession.updated_at < pending_cutoff
                    ),
                    # Discussing/synthesizing sessions (30 min cutoff)
                    (
                        DiscussionSession.status.in_(["discussing", "synthesizing"])
                    ) & (
                        DiscussionSession.updated_at < discussing_cutoff
                    ),
                )
            )
            .values(status="failed", error="Session orphaned (worker timeout)")
        )
        await db.commit()
        count = result.rowcount

    if count > 0:
        logger.warning(
            "Discussion cleanup: marked %d orphaned sessions as failed (pending_cutoff=%s, discussing_cutoff=%s)",
            count, pending_cutoff.isoformat(), discussing_cutoff.isoformat(),
        )

    return {"cleaned": count}


# Export key rotation tasks
__all__ = [
    "cleanup_expired_tokens",
    "send_email_notification",
    "cleanup_stuck_discussions",
    "auto_rotate_jwt_keys",
    "cleanup_old_jwt_keys",
    "verify_jwt_key_rotation",
]
