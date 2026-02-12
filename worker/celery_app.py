"""Celery application configuration."""

import os

from celery import Celery
from celery.schedules import crontab

# Redis configuration from environment variables
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

# Create Celery app
celery_app = Celery(
    "webstock",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "worker.tasks",
        "worker.tasks.news_monitor",
        "worker.tasks.price_monitor",
        "worker.tasks.report_generator",
        "worker.tasks.key_rotation",
        "worker.tasks.embedding_tasks",
        "worker.tasks.full_content_tasks",
        "worker.tasks.stock_list_tasks",
        "worker.tasks.backtest_cleanup",
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

    # Task routing
    # - scraping queue: I/O-bound tasks (HTTP fetching with controlled concurrency)
    # - default queue: LLM-bound tasks (can safely scale concurrency)
    task_routes={
        "worker.tasks.full_content_tasks.batch_fetch_content": {"queue": "scraping"},
        "worker.tasks.full_content_tasks.process_news_article": {"queue": "default"},
        "worker.tasks.full_content_tasks.cleanup_expired_news": {"queue": "default"},
        "worker.tasks.full_content_tasks.cleanup_pipeline_events": {"queue": "default"},
    },

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
        "cleanup-news-content": {
            "task": "worker.tasks.full_content_tasks.cleanup_expired_news",
            "schedule": crontab(hour=4, minute=0),  # Daily at 4:00 AM
        },
        "cleanup-pipeline-events": {
            "task": "worker.tasks.full_content_tasks.cleanup_pipeline_events",
            "schedule": crontab(hour=4, minute=30),  # Daily at 4:30 AM (after news cleanup)
        },
        "update-stock-list": {
            "task": "worker.tasks.stock_list_tasks.update_stock_list",
            "schedule": crontab(hour=5, minute=30),  # Daily at 5:30 AM UTC
        },
        "cleanup-old-backtests": {
            "task": "worker.tasks.backtest_cleanup.cleanup_old_backtests",
            "schedule": crontab(hour=5, minute=15),  # Daily at 5:15 AM UTC
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
