"""Celery application configuration."""

import logging
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

logger = logging.getLogger(__name__)

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
        "worker.tasks.rss_monitor",
        "worker.tasks.daily_bar_tasks",
        "worker.tasks.qlib_sync",
        "worker.tasks.stock_profile_tasks",
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
        "worker.tasks.full_content_tasks.cleanup_old_usage_records": {"queue": "default"},
        # Daily bar tasks: dedicated queue so they don't block main worker slots
        "worker.tasks.daily_bar_tasks.collect_market_daily_bars": {"queue": "daily_bars"},
        "worker.tasks.daily_bar_tasks.rebuild_market_daily_bars": {"queue": "daily_bars"},
        "worker.tasks.qlib_sync.sync_qlib_market": {"queue": "default"},
    },

    # Broker transport — Redis visibility timeout
    # CRITICAL: With task_acks_late=True, Redis re-delivers unacknowledged
    # messages after visibility_timeout.  Default is 3600s (1 hour), but
    # daily bar tasks can run for 8+ hours.  Without this, tasks running
    # longer than 1 hour get duplicated every hour, causing the "progress
    # bar completes then resets to 0" loop on the admin dashboard.
    broker_transport_options={
        "visibility_timeout": 43200,  # 12h — exceeds longest task time_limit (8h)
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
        "cleanup-old-usage-records": {
            "task": "worker.tasks.full_content_tasks.cleanup_old_usage_records",
            "schedule": crontab(hour=5, minute=0),  # Daily at 5:00 AM UTC
        },
        "monitor-rss-feeds": {
            "task": "worker.tasks.rss_monitor.monitor_rss_feeds",
            "schedule": crontab(minute="*/5"),
        },
        "collect-daily-bars-cn": {
            "task": "worker.tasks.daily_bar_tasks.collect_market_daily_bars",
            "schedule": crontab(hour=8, minute=0),  # 08:00 UTC, A-share close + 1h
            "args": ["cn"],
        },
        "collect-daily-bars-hk": {
            "task": "worker.tasks.daily_bar_tasks.collect_market_daily_bars",
            "schedule": crontab(hour=9, minute=0),  # 09:00 UTC, HK close + 1h
            "args": ["hk"],
        },
        "collect-daily-bars-us": {
            "task": "worker.tasks.daily_bar_tasks.collect_market_daily_bars",
            "schedule": crontab(hour=22, minute=0),  # 22:00 UTC, US close + 1h
            "args": ["us"],
        },
        "collect-daily-bars-metal": {
            "task": "worker.tasks.daily_bar_tasks.collect_market_daily_bars",
            "schedule": crontab(hour=22, minute=30),  # 22:30 UTC, CME close
            "args": ["metal"],
        },
        "sync-concept-boards": {
            "task": "worker.tasks.stock_profile_tasks.sync_concept_boards",
            "schedule": crontab(hour=6, minute=0, day_of_week="1-6"),  # Mon-Sat 6:00 AM UTC (skip Sunday)
        },
        "build-stock-knowledge-base": {
            "task": "worker.tasks.stock_profile_tasks.build_stock_knowledge_base",
            "schedule": crontab(day_of_week=0, hour=6, minute=0),  # Weekly Sunday 6:00 AM UTC
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

# ---------------------------------------------------------------------------
# Startup self-check: trigger knowledge base build if empty
# ---------------------------------------------------------------------------

@worker_ready.connect
def _check_stock_knowledge_base(sender, **kwargs):
    """Auto-trigger stock profile knowledge base build on first deploy.

    The weekly beat task (Sunday 6 AM) handles routine rebuilds, but on a
    fresh deployment there are zero embeddings and the first Sunday may be
    days away.  This signal fires once per worker startup and dispatches the
    build task if no stock_profile embeddings exist yet.
    """
    import redis as redis_lib

    try:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis_lib.from_url(redis_url, decode_responses=True)

        # Check the cached embedding counter first (fast, no DB query)
        raw = r.get("kb:counters:embeddings:stock_profile")
        if raw:
            import json
            counter = json.loads(raw)
            if counter.get("count", 0) > 0:
                logger.info(
                    "[KBCheck] stock_profile has %d embeddings (Redis cache), skipping auto-build",
                    counter["count"],
                )
                return

        # Counter missing or zero — check DB to be sure
        # Use a sync connection to avoid event loop issues in signal handler
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return
        # Convert asyncpg URL to psycopg2-compatible: strip +asyncpg dialect
        # and remove asyncpg-specific query params (e.g. ?ssl=disable)
        sync_url = db_url.replace("+asyncpg", "").split("?")[0]

        from sqlalchemy import create_engine, text
        engine = create_engine(sync_url, pool_pre_ping=True)
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM document_embeddings WHERE source_type = 'stock_profile'")
            ).scalar() or 0
        engine.dispose()

        if count > 0:
            logger.info(
                "[KBCheck] stock_profile has %d embeddings, skipping auto-build", count,
            )
            return

        # No embeddings — trigger build (with a short delay to let worker fully initialize)
        logger.warning(
            "[KBCheck] Zero stock_profile embeddings detected, "
            "auto-triggering build_stock_knowledge_base"
        )
        from worker.tasks.stock_profile_tasks import build_stock_knowledge_base
        build_stock_knowledge_base.apply_async(countdown=30)

    except Exception as e:
        logger.warning("[KBCheck] Startup check failed (non-fatal): %s", e)


if __name__ == "__main__":
    celery_app.start()
