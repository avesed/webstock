"""RSS feed monitoring Celery task.

Periodically polls all enabled RSS feeds via RSSHub, deduplicates
against existing News records, runs initial filtering, and dispatches
articles into the existing 3-layer news pipeline.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from worker.celery_app import celery_app
from worker.db_utils import get_task_session
from worker.tasks.news_monitor import run_async_task, _run_initial_filter_if_enabled

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
    4. Run initial filter (if two-phase enabled)
    5. Dispatch to Layer 1.5 (batch_fetch_content) or Layer 2 (process_news_article)

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
    from app.models.rss_feed import RssFeed

    logger.info("Starting RSS monitor task")
    await _update_rss_progress("init", "Initializing RSS monitor...", 0)

    stats = {
        "feeds_polled": 0,
        "total_new": 0,
        "fulltext_dispatched": 0,
        "standard_dispatched": 0,
        "filter_skipped": 0,
        "errors": 0,
        "two_phase_enabled": False,
    }

    try:
        async with get_task_session() as db:
            # Load system settings for feature flags
            settings_service = SettingsService()
            system_settings = await settings_service.get_system_settings(db)
            use_two_phase = system_settings.use_two_phase_filter
            stats["two_phase_enabled"] = use_two_phase

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
            # This ensures poll progress is visible even if the initial
            # filter (LLM-based, slow) times out later.
            await db.commit()

            if stats["feeds_polled"] == 0:
                logger.info("RSS monitor: no feeds due for polling")
                await _finish_rss_progress(stats)
                return stats

            # Collect all new articles from all feeds
            all_fulltext_articles: List = []
            all_standard_articles: List = []

            for feed_result in poll_result.get("feed_results", []):
                all_fulltext_articles.extend(
                    feed_result.get("fulltext_articles", [])
                )
                all_standard_articles.extend(
                    feed_result.get("standard_articles", [])
                )
                stats["total_new"] += feed_result.get("new_count", 0)

            # Run initial filter on standard articles if two-phase is enabled
            await _update_rss_progress(
                "filtering",
                f"Filtering {len(all_standard_articles)} articles...",
                40,
            )

            if use_two_phase and all_standard_articles:
                # Truncate summaries for initial filter — RSS feeds often
                # put full article text in <description>, yielding ~2000-char
                # "summaries".  The initial filter only needs a brief snippet
                # (title + summary) for quick screening; sending full text
                # bloats token count and causes timeouts at scale (120+ articles).
                FILTER_SUMMARY_LIMIT = 300
                articles_for_filter = [
                    {
                        "url": a.url,
                        "headline": a.title,
                        "summary": (a.summary or "")[:FILTER_SUMMARY_LIMIT],
                    }
                    for a in all_standard_articles
                ]

                try:
                    filter_results, _ = await _run_initial_filter_if_enabled(
                        db, system_settings, articles_for_filter
                    )
                except Exception as e:
                    logger.warning(
                        "RSS initial filter failed, proceeding without filter: %s", e
                    )
                    filter_results = {}

                # Apply filter decisions to articles
                from app.services.two_phase_filter_service import get_two_phase_filter_service
                filter_service = get_two_phase_filter_service()

                filtered_standard = []
                for article in all_standard_articles:
                    filter_result = filter_results.get(article.url, {})
                    decision = filter_result.get("decision", "uncertain")

                    if decision == "skip":
                        stats["filter_skipped"] += 1
                        # Mark as skipped in DB
                        article.filter_status = "skipped"
                        continue

                    article.filter_status = filter_service.map_initial_decision_to_status(
                        decision
                    ).value
                    filtered_standard.append(article)

                all_standard_articles = filtered_standard

            # Commit filter decisions (News records were already committed
            # in the early commit above; this saves filter_status updates)
            await _update_rss_progress(
                "saving",
                f"Saving filter results for {stats['total_new']} articles...",
                70,
            )
            await db.commit()

            # Refresh article IDs after commit
            for article in all_fulltext_articles + all_standard_articles:
                try:
                    await db.refresh(article)
                except Exception as e:
                    logger.warning("Failed to refresh article: %s", e)

            # Dispatch fulltext articles directly to Layer 2 (skip Layer 1.5)
            await _update_rss_progress(
                "dispatch", "Dispatching articles to pipeline...", 85
            )

            from worker.tasks.full_content_tasks import (
                batch_fetch_content,
                process_news_article,
                BATCH_CHUNK_SIZE,
            )

            for news_obj in all_fulltext_articles:
                try:
                    if news_obj.id and news_obj.content_file_path:
                        process_news_article.delay(
                            str(news_obj.id),
                            news_obj.url,
                            market=news_obj.market or "US",
                            symbol=news_obj.symbol or "",
                            title=news_obj.title or "",
                            summary=news_obj.summary or "",
                            published_at=news_obj.published_at.isoformat() if news_obj.published_at else None,
                            use_two_phase=use_two_phase,
                            source=news_obj.source or "",
                            file_path=news_obj.content_file_path,
                        )
                        stats["fulltext_dispatched"] += 1
                except Exception as e:
                    logger.warning(
                        "Failed to dispatch fulltext article: %s", e
                    )

            # Dispatch standard articles to Layer 1.5 (batch fetch)
            batch = []
            for news_obj in all_standard_articles:
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
                            "use_two_phase": use_two_phase,
                            "content_source": "trafilatura",
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
        "RSS monitor completed: feeds=%d, new=%d, fulltext=%d, standard=%d, "
        "skipped=%d, errors=%d",
        stats["feeds_polled"],
        stats["total_new"],
        stats["fulltext_dispatched"],
        stats["standard_dispatched"],
        stats["filter_skipped"],
        stats["errors"],
    )

    return stats
