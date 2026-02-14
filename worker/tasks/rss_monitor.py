"""RSS feed monitoring Celery task.

Periodically polls all enabled RSS feeds via RSSHub, deduplicates
against existing News records, runs Layer 1 scoring (if LLM pipeline
enabled), and dispatches articles into the 3-layer news pipeline.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from worker.celery_app import celery_app
from worker.db_utils import get_task_session
from worker.tasks.news_monitor import (
    run_async_task,
    _run_layer1_scoring_if_enabled,
    _build_score_details,
)

logger = logging.getLogger(__name__)

# Redis keys for RSS monitor progress tracking
RSS_MONITOR_STATUS_KEY = "rss:monitor:status"
RSS_MONITOR_PROGRESS_KEY = "rss:monitor:progress"
RSS_MONITOR_LAST_RUN_KEY = "rss:monitor:last_run"


async def _update_rss_progress(stage: str, message: str, percent: int = 0):
    """Update RSS monitor progress in Redis for the admin dashboard."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        progress = {
            "stage": stage,
            "message": message,
            "percent": percent,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis.set(RSS_MONITOR_PROGRESS_KEY, json.dumps(progress), ex=600)
        await redis.set(RSS_MONITOR_STATUS_KEY, "running", ex=600)
    except Exception:
        pass  # Non-critical


async def _finish_rss_progress(stats: Dict[str, Any]):
    """Mark RSS monitor task as complete in Redis."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        now = datetime.now(timezone.utc).isoformat()
        await redis.set(RSS_MONITOR_STATUS_KEY, "idle", ex=1800)
        await redis.set(RSS_MONITOR_LAST_RUN_KEY, json.dumps({
            "finished_at": now,
            "stats": stats,
        }), ex=1800)
        await redis.delete(RSS_MONITOR_PROGRESS_KEY)
    except Exception:
        pass


@celery_app.task(bind=True, max_retries=3)
def monitor_rss_feeds(self):
    """
    Periodic task to poll all due RSS feeds.

    Runs every 5 minutes to:
    1. Find all enabled feeds whose poll interval has elapsed
    2. Fetch articles from RSSHub
    3. Deduplicate against existing News records
    4. Run Layer 1 scoring (if LLM pipeline enabled)
    5. Dispatch to Layer 1.5 (batch_fetch_content) for non-discarded articles

    Uses global Celery time limits (4min soft / 5min hard).
    """
    try:
        return run_async_task(_monitor_rss_feeds_async)
    except ConnectionError as e:
        # RSSHub unavailable - skip entire run, don't retry
        logger.warning("RSSHub unavailable, skipping RSS monitor run: %s", e)
        return {"status": "skipped", "reason": "rsshub_unavailable", "error": str(e)}
    except Exception as e:
        logger.exception("RSS monitor task failed: %s", e)
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _monitor_rss_feeds_async() -> Dict[str, Any]:
    """Async implementation of RSS feed monitoring."""
    from sqlalchemy import select

    from app.services.rss_service import get_rss_service
    from app.services.settings_service import SettingsService
    from app.models.news import News, FilterStatus
    from app.models.rss_feed import RssFeed

    logger.info("Starting RSS monitor task")
    await _update_rss_progress("init", "Initializing RSS monitor...", 0)

    stats = {
        "feeds_polled": 0,
        "total_new": 0,
        "standard_dispatched": 0,
        "filter_skipped": 0,
        "errors": 0,
        "llm_pipeline_enabled": False,
        # Layer 1 scoring stats (only when pipeline enabled)
        "layer1_discard": 0,
        "layer1_lightweight": 0,
        "layer1_full_analysis": 0,
        "layer1_critical": 0,
    }

    try:
        async with get_task_session() as db:
            # Load system settings for feature flags
            settings_service = SettingsService()
            system_settings = await settings_service.get_system_settings(db)
            enable_pipeline = system_settings.enable_llm_pipeline
            stats["llm_pipeline_enabled"] = enable_pipeline

            rss_service = get_rss_service()

            await _update_rss_progress(
                "polling", "Polling due RSS feeds...", 10
            )

            # Poll all due feeds (with Semaphore(3) inside)
            poll_result = await rss_service.poll_all_due_feeds(
                db, system_settings
            )

            stats["feeds_polled"] = poll_result.get("polled", 0)
            stats["errors"] = poll_result.get("errors", 0)

            # Early commit: persist feed stats (last_polled_at, article_count,
            # consecutive_errors) and new News records immediately.
            # This ensures poll progress is visible even if the scoring
            # (LLM-based, slow) times out later.
            await db.commit()

            if stats["feeds_polled"] == 0:
                logger.info("RSS monitor: no feeds due for polling")
                await _finish_rss_progress(stats)
                return stats

            # Collect all new articles from all feeds
            # (fulltext_mode is kept in DB but ignored -- all articles go
            # through the same scoring -> batch_fetch_content pipeline)
            all_new_articles: List = []

            for feed_result in poll_result.get("feed_results", []):
                # Combine both fulltext and standard articles into one list
                all_new_articles.extend(
                    feed_result.get("fulltext_articles", [])
                )
                all_new_articles.extend(
                    feed_result.get("standard_articles", [])
                )
                stats["total_new"] += feed_result.get("new_count", 0)

            if not all_new_articles:
                logger.info("RSS monitor: no new articles to process")
                await _finish_rss_progress(stats)
                return stats

            # Run Layer 1 scoring if pipeline is enabled
            await _update_rss_progress(
                "scoring",
                f"Scoring {len(all_new_articles)} articles...",
                40,
            )

            if enable_pipeline and all_new_articles:
                # Format articles for scoring
                FILTER_SUMMARY_LIMIT = 300
                articles_for_scoring = [
                    {
                        "url": a.url,
                        "title": a.title or "",
                        "summary": (a.summary or "")[:FILTER_SUMMARY_LIMIT],
                    }
                    for a in all_new_articles
                ]

                scoring_results = []
                try:
                    scoring_results, _ = await _run_layer1_scoring_if_enabled(
                        db, system_settings, articles_for_scoring
                    )
                except Exception as e:
                    logger.warning("RSS Layer 1 scoring failed: %s", e)

                # Build URL -> scoring result map
                scoring_map = {r.url: r for r in scoring_results}

                # Apply scoring decisions to articles
                dispatching_articles = []
                for article in all_new_articles:
                    scoring = scoring_map.get(article.url)

                    if scoring:
                        if scoring.routing_decision == "discard":
                            article.filter_status = FilterStatus.DISCARDED.value
                            article.content_score = scoring.total_score
                            article.processing_path = "discarded"
                            article.score_details = _build_score_details(scoring)
                            stats["layer1_discard"] += 1
                            if scoring.is_critical:
                                stats["layer1_critical"] += 1
                            continue  # Don't dispatch

                        # Not discarded: mark with scoring data
                        article.filter_status = FilterStatus.INITIAL_USEFUL.value
                        article.content_score = scoring.total_score
                        article.processing_path = scoring.routing_decision
                        article.score_details = _build_score_details(scoring)
                        if scoring.routing_decision == "full_analysis":
                            stats["layer1_full_analysis"] += 1
                        else:
                            stats["layer1_lightweight"] += 1
                        if scoring.is_critical:
                            stats["layer1_critical"] += 1
                    else:
                        # No scoring result (scoring failed) -- default to lightweight
                        article.filter_status = FilterStatus.INITIAL_USEFUL.value
                        article.content_score = 0
                        article.processing_path = "lightweight"

                    dispatching_articles.append(article)

                all_new_articles = dispatching_articles

            elif not enable_pipeline:
                # Pipeline OFF: articles already saved with basic metadata.
                # No scoring, no dispatch. Clear the list so no dispatch happens.
                logger.info(
                    "RSS monitor: pipeline OFF, %d articles saved with basic metadata only",
                    len(all_new_articles),
                )
                all_new_articles = []

            # Commit scoring/filter decisions
            await _update_rss_progress(
                "saving",
                f"Saving results for {stats['total_new']} articles...",
                70,
            )
            await db.commit()

            # Refresh article IDs after commit
            for article in all_new_articles:
                try:
                    await db.refresh(article)
                except Exception as e:
                    logger.warning("Failed to refresh article: %s", e)

            # Dispatch articles to Layer 1.5 (batch fetch)
            await _update_rss_progress(
                "dispatch", "Dispatching articles to pipeline...", 85
            )

            if all_new_articles:
                from worker.tasks.full_content_tasks import (
                    batch_fetch_content,
                    BATCH_CHUNK_SIZE,
                )

                batch = []
                for news_obj in all_new_articles:
                    try:
                        if news_obj.id and news_obj.url:
                            batch.append({
                                "news_id": str(news_obj.id),
                                "url": news_obj.url,
                                "market": news_obj.market or "US",
                                "symbol": news_obj.symbol or "",
                                "title": news_obj.title or "",
                                "summary": news_obj.summary or "",
                                "source": news_obj.source or "",
                                "published_at": news_obj.published_at.isoformat() if news_obj.published_at else None,
                                "content_source": "trafilatura",
                                # Scoring data flows through to Layer 3
                                "content_score": news_obj.content_score or 0,
                                "processing_path": news_obj.processing_path or "lightweight",
                                "score_details": news_obj.score_details,
                            })
                    except Exception as e:
                        logger.warning(
                            "Failed to prepare article for batch fetch: %s", e
                        )

                if batch:
                    for i in range(0, len(batch), BATCH_CHUNK_SIZE):
                        chunk = batch[i:i + BATCH_CHUNK_SIZE]
                        batch_fetch_content.delay(chunk)
                        logger.info(
                            "Dispatched batch_fetch_content: chunk %d/%d (%d articles)",
                            i // BATCH_CHUNK_SIZE + 1,
                            (len(batch) + BATCH_CHUNK_SIZE - 1) // BATCH_CHUNK_SIZE,
                            len(chunk),
                        )

                stats["standard_dispatched"] = len(batch)

    except Exception as e:
        logger.exception("Error in RSS monitor: %s", e)
        raise
    finally:
        await _finish_rss_progress(stats)

    logger.info(
        "RSS monitor completed: feeds=%d, new=%d, dispatched=%d, "
        "discard=%d, lightweight=%d, full=%d, critical=%d, errors=%d, "
        "pipeline=%s",
        stats["feeds_polled"],
        stats["total_new"],
        stats["standard_dispatched"],
        stats["layer1_discard"],
        stats["layer1_lightweight"],
        stats["layer1_full_analysis"],
        stats["layer1_critical"],
        stats["errors"],
        stats["llm_pipeline_enabled"],
    )

    return stats
