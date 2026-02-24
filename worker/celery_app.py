"""Celery application configuration."""

import logging
import os
import sys

from celery import Celery
from celery.schedules import crontab
from celery.signals import after_setup_logger, after_setup_task_logger, worker_ready

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
        "worker.tasks.session_cleanup",
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
    # - default queue: lightweight tasks that must stay responsive
    # NOTE: process_news_article, analyze_important_news, retry_score_articles
    # are now dispatched to the standalone asyncio news-consumer via Redis LIST
    # (see worker/news_queue.py). Celery task definitions are kept for admin
    # manual trigger / fallback use.
    task_routes={
        "worker.tasks.full_content_tasks.batch_fetch_content": {"queue": "scraping"},
        "worker.tasks.full_content_tasks.cleanup_expired_news": {"queue": "default"},
        "worker.tasks.full_content_tasks.cleanup_pipeline_events": {"queue": "default"},
        "worker.tasks.full_content_tasks.cleanup_old_usage_records": {"queue": "default"},
        # Daily bar tasks: thin proxies that delegate to data-service.
        # Kept on dedicated queue for admin UI backward compatibility.
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
        "cleanup-stuck-discussions": {
            "task": "worker.tasks.cleanup_stuck_discussions",
            "schedule": crontab(minute="*/10"),  # Every 10 minutes
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
        # "update-stock-list": Migrated to data-service APScheduler (Phase 7).
        # Admin UI can still trigger via stock_list_tasks.update_stock_list.
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
        # Daily bar collection migrated to data-service APScheduler (Phase 7).
        # Beat entries for collect-daily-bars-{cn,hk,us,metal} removed.
        # Admin UI still dispatches tasks via daily_bar_tasks (thin proxies).
        "sync-concept-boards": {
            "task": "worker.tasks.stock_profile_tasks.sync_concept_boards",
            "schedule": crontab(hour=6, minute=0, day_of_week="1-6"),  # Mon-Sat 6:00 AM UTC (skip Sunday)
        },
        "build-stock-knowledge-base": {
            "task": "worker.tasks.stock_profile_tasks.build_stock_knowledge_base",
            "schedule": crontab(day_of_week=0, hour=6, minute=0),  # Weekly Sunday 6:00 AM UTC
        },
        "cleanup-old-sessions": {
            "task": "worker.tasks.session_cleanup.cleanup_old_sessions",
            "schedule": crontab(hour=5, minute=30),  # Daily at 5:30 AM UTC
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


@after_setup_logger.connect
def _configure_celery_logger(logger, loglevel, **kwargs):
    """Override Celery's default log format for Docker stdout."""
    from worker.log_config import LOG_FORMAT, LOG_DATEFMT, RequestIdFilter
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    logger.addHandler(handler)
    logger.setLevel(loglevel)
    # Suppress noisy third-party loggers
    for name in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles", "multipart", "openai._base_client"):
        logging.getLogger(name).setLevel(logging.WARNING)


@after_setup_task_logger.connect
def _configure_task_logger(logger, loglevel, **kwargs):
    """Override Celery's task logger format for Docker stdout."""
    from worker.log_config import LOG_FORMAT, LOG_DATEFMT, RequestIdFilter
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    logger.addHandler(handler)
    logger.setLevel(loglevel)


# ---------------------------------------------------------------------------
# Request ID propagation: web → Celery task → log output
# ---------------------------------------------------------------------------

from celery.signals import before_task_publish, task_prerun, task_postrun


@before_task_publish.connect
def _inject_request_id(headers=None, **kwargs):
    """Propagate request ID from web process to task headers."""
    if headers is not None:
        from app.core.request_id import get_request_id
        rid = get_request_id()
        if rid:
            headers["x_request_id"] = rid


@task_prerun.connect
def _extract_request_id(task=None, **kwargs):
    """Extract request ID from task headers into context var."""
    from app.core.request_id import request_id_var, generate_request_id
    rid = getattr(task.request, "x_request_id", None)
    if not rid:
        # Beat-scheduled tasks or tasks without a request ID get a fresh one
        rid = generate_request_id()
    request_id_var.set(rid)


@task_postrun.connect
def _clear_request_id(**kwargs):
    """Clear request ID after task completes."""
    from app.core.request_id import request_id_var
    request_id_var.set(None)


if __name__ == "__main__":
    celery_app.start()
