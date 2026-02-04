"""Celery tasks module."""

from worker.celery_app import celery_app
from worker.tasks.key_rotation import (
    auto_rotate_jwt_keys,
    cleanup_old_jwt_keys,
    verify_jwt_key_rotation,
)


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


# Export key rotation tasks
__all__ = [
    "cleanup_expired_tokens",
    "send_email_notification",
    "auto_rotate_jwt_keys",
    "cleanup_old_jwt_keys",
    "verify_jwt_key_rotation",
]
