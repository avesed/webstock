"""Celery application configuration."""

import os

from celery import Celery
from celery.schedules import crontab

# Redis URL - use localhost for all-in-one, redis for docker-compose
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")

# Create Celery app
celery_app = Celery(
    "webstock",
    broker=f"redis://{REDIS_HOST}:6379/1",
    backend=f"redis://{REDIS_HOST}:6379/2",
    include=[
        "worker.tasks",
        "worker.tasks.news_monitor",
        "worker.tasks.price_monitor",
        "worker.tasks.report_generator",
        "worker.tasks.key_rotation",
        "worker.tasks.embedding_tasks",
    ],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=300,  # 5 minutes
    task_soft_time_limit=240,  # 4 minutes

    # Worker settings
    worker_prefetch_multiplier=1,
    worker_concurrency=4,

    # Result backend settings
    result_expires=3600,  # 1 hour

    # Beat schedule for periodic tasks
    beat_schedule={
        "cleanup-expired-tokens": {
            "task": "worker.tasks.cleanup_expired_tokens",
            "schedule": 3600.0,  # Every hour
        },
        "monitor-news": {
            "task": "worker.tasks.news_monitor.monitor_news",
            "schedule": crontab(minute="*/15"),  # Every 15 minutes
        },
        "monitor-prices": {
            "task": "worker.tasks.price_monitor.monitor_prices",
            "schedule": crontab(minute="*"),  # Every minute
        },
        "cleanup-old-alerts": {
            "task": "worker.tasks.price_monitor.cleanup_old_triggered_alerts",
            "schedule": crontab(hour=3, minute=0),  # Daily at 3:00 AM
        },
        "cleanup-subscriptions": {
            "task": "worker.tasks.price_monitor.cleanup_inactive_subscriptions",
            "schedule": crontab(day_of_week=0, hour=4, minute=0),  # Weekly on Sunday at 4:00 AM
        },
        "check-scheduled-reports": {
            "task": "worker.tasks.report_generator.check_scheduled_reports",
            "schedule": crontab(minute="*"),  # Every minute
        },
        "cleanup-old-reports": {
            "task": "worker.tasks.report_generator.cleanup_old_reports",
            "schedule": crontab(hour=5, minute=0),  # Daily at 5:00 AM
        },
        # JWT Key Rotation - DISABLED by default
        # Manual rotation recommended: python worker/scripts/manage_keys.py rotate
        # Then restart: docker-compose restart backend
        # "rotate-jwt-keys": {
        #     "task": "worker.tasks.key_rotation.auto_rotate_jwt_keys",
        #     "schedule": 21600.0,  # Every 6 hours
        # },
    },
)

if __name__ == "__main__":
    celery_app.start()
