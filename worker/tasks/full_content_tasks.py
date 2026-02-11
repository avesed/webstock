"""Celery tasks for news full content fetching, filtering, and embedding.

3-layer news pipeline architecture:
  Layer 1   - news_monitor (discovery + initial filter, every 15min)
  Layer 1.5 - batch_fetch_content (HTTP fetch with Semaphore(3) + 1.0s delay)
  Layer 2   - process_news_article (LangGraph: read_file -> filter -> embed -> update_db)
  Cleanup   - cleanup_expired_news (periodic, daily at 4:00 AM)
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, TypeVar

from worker.celery_app import celery_app

# Use Celery-safe database utilities (avoids event loop conflicts)
from worker.db_utils import get_task_session

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async_task(coro_func: Callable[..., T], *args, **kwargs) -> T:
    """
    Run an async function in a new event loop, properly cleaning up afterwards.

    This helper ensures all singleton async clients are reset after each task
    to avoid "Event loop is closed" errors when tasks reuse singleton clients
    that were bound to different (now closed) event loops.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_func(*args, **kwargs))
    finally:
        loop.close()
        # Reset all singleton clients that may have bound to this event loop
        try:
            from app.core.llm import reset_llm_gateway
            reset_llm_gateway()
        except Exception as e:
            logger.warning("Failed to reset LLM gateway: %s", e)
        try:
            from app.db.redis import reset_redis
            reset_redis()
        except Exception as e:
            logger.warning("Failed to reset Redis client: %s", e)


@celery_app.task(bind=True, max_retries=3)
def process_news_article(
    self,
    news_id: str,
    url: str,
    market: str = "US",
    symbol: str = "",
    title: str = "",
    summary: str = "",
    published_at: str = None,
    use_two_phase: bool = False,
    source: str = "",
    file_path: str = None,
):
    """
    Process a single news article through the LangGraph pipeline (Layer 2).

    Content is pre-fetched by Layer 1.5 (batch_fetch_content). This task
    reads the content from file, runs LLM filtering, and embeds for RAG.

    Pipeline: read_file -> deep_filter/single_filter -> embed -> update_db

    Args:
        news_id: UUID of the news article
        url: Article URL (for reference/logging)
        market: Market identifier (US, HK, SH, SZ)
        symbol: Stock symbol
        title: Article title
        summary: Article summary
        published_at: ISO 8601 publish date
        use_two_phase: Whether to use two-phase filtering
        source: News source name (e.g. 'reuters', 'eastmoney')
        file_path: Path to pre-fetched content JSON file (set by Layer 1.5)
    """
    try:
        return run_async_task(
            _process_news_article_async,
            news_id, url, market, symbol, title, summary,
            published_at, use_two_phase, source, file_path,
        )
    except Exception as e:
        logger.exception("process_news_article failed for news_id=%s: %s", news_id, e)
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


async def _process_news_article_async(
    news_id: str,
    url: str,
    market: str,
    symbol: str,
    title: str,
    summary: str,
    published_at: str,
    use_two_phase: bool,
    source: str,
    file_path: str,
) -> Dict[str, Any]:
    """Async implementation: run the LangGraph news pipeline (Layer 2)."""
    from app.agents.langgraph.workflows.news_pipeline import run_news_pipeline

    logger.info(
        "Layer 2 starting: news_id=%s, file_path=%s, two_phase=%s",
        news_id, file_path, use_two_phase,
    )

    final_state = await run_news_pipeline(
        news_id=news_id,
        url=url,
        market=market,
        symbol=symbol,
        title=title,
        summary=summary,
        published_at=published_at,
        use_two_phase=use_two_phase,
        source=source,
        file_path=file_path,
    )

    result = {
        "status": final_state.get("final_status", "unknown"),
        "news_id": news_id,
        "chunks_stored": final_state.get("chunks_stored", 0),
        "filter_decision": final_state.get("filter_decision", "pending"),
        "error": final_state.get("error"),
    }

    logger.info(
        "Layer 2 completed: news_id=%s, status=%s, filter=%s, chunks=%d",
        news_id, result["status"], result["filter_decision"], result["chunks_stored"],
    )

    return result


BATCH_CHUNK_SIZE = 30  # Max articles per batch task


@celery_app.task(bind=True, max_retries=2)
def batch_fetch_content(self, articles: List[Dict[str, Any]]):
    """Batch fetch content for news articles with controlled concurrency.

    Layer 1.5: Bridges news discovery (Layer 1) and LLM processing (Layer 2).
    Uses asyncio.Semaphore to limit concurrent HTTP fetches to 3, with 1.0s
    delay between fetches for rate limit protection.

    Args:
        articles: List of dicts with keys: news_id, url, market, symbol,
                  title, summary, source, published_at, use_two_phase,
                  content_source
    """
    try:
        return run_async_task(_batch_fetch_content_async, articles)
    except Exception as e:
        logger.exception("batch_fetch_content failed: %s", e)
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _batch_fetch_content_async(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Async implementation of batch content fetching with controlled concurrency."""
    if not articles:
        logger.info("batch_fetch: empty batch, skipping")
        return {"status": "skipped", "reason": "empty_batch"}

    sem = asyncio.Semaphore(3)
    FETCH_DELAY = 1.0

    async def fetch_one(article: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            result = await _fetch_single_article(article)
            await asyncio.sleep(FETCH_DELAY)
            return result

    logger.info(
        "Starting batch fetch for %d articles (semaphore=3, delay=%.1fs)",
        len(articles), FETCH_DELAY,
    )

    results = await asyncio.gather(
        *[fetch_one(a) for a in articles],
        return_exceptions=True,
    )

    # Dispatch Layer 2 for successful fetches
    success_count = 0
    failed_count = 0
    dispatched_count = 0

    for article, result in zip(articles, results):
        if isinstance(result, Exception):
            failed_count += 1
            logger.error(
                "batch_fetch: exception for news_id=%s: %s",
                article.get("news_id"), result,
            )
            continue

        if not isinstance(result, dict):
            failed_count += 1
            continue

        if result.get("success"):
            success_count += 1
            try:
                process_news_article.delay(
                    news_id=article["news_id"],
                    url=article["url"],
                    source=article.get("source", ""),
                    file_path=result.get("file_path"),
                    market=article.get("market", "US"),
                    symbol=article.get("symbol", ""),
                    title=article.get("title", ""),
                    summary=article.get("summary", ""),
                    published_at=article.get("published_at"),
                    use_two_phase=article.get("use_two_phase", False),
                )
                dispatched_count += 1
            except Exception as e:
                logger.error(
                    "batch_fetch: failed to dispatch Layer 2 for news_id=%s: %s",
                    article.get("news_id"), e,
                )
        else:
            failed_count += 1

    logger.info(
        "Batch fetch completed: total=%d, success=%d, failed=%d, dispatched=%d",
        len(articles), success_count, failed_count, dispatched_count,
    )

    return {
        "status": "completed",
        "total": len(articles),
        "success": success_count,
        "failed": failed_count,
        "dispatched": dispatched_count,
    }


async def _fetch_single_article(article: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch content for a single article and update DB.

    Uses FetchFullContentSkill for fetching, then updates the News record
    with content status and file path.

    Returns:
        Dict with 'success', 'file_path', and optional 'error' keys.
    """
    from app.skills.registry import get_skill_registry
    from app.models.news import News, ContentStatus
    from sqlalchemy import select

    news_id = article["news_id"]
    url = article["url"]

    # Get the fetch skill
    registry = get_skill_registry()
    skill = registry.get("fetch_full_content")
    if skill is None:
        logger.error("fetch_full_content skill not found in registry")
        return {"success": False, "error": "fetch_full_content skill not found"}

    t0 = time.monotonic()

    # Execute fetch
    result = await skill.safe_execute(
        timeout=60.0,
        url=url,
        news_id=news_id,
        symbol=article.get("symbol", ""),
        market=article.get("market", "US"),
        content_source=article.get("content_source", "scraper"),
        polygon_api_key=article.get("polygon_api_key"),
        published_at=article.get("published_at"),
        title=article.get("title", ""),
    )

    # Update DB with result
    try:
        async with get_task_session() as db:
            query = select(News).where(News.id == uuid.UUID(news_id))
            res = await db.execute(query)
            news = res.scalar_one_or_none()
            if not news:
                logger.warning("_fetch_single_article: news record not found: %s", news_id)
                return {"success": False, "error": "news record not found"}

            now = datetime.now(timezone.utc)

            if not result.success:
                error_msg = result.error or "Unknown fetch error"
                if "blocked" in error_msg.lower():
                    news.content_status = ContentStatus.BLOCKED.value
                else:
                    news.content_status = ContentStatus.FAILED.value
                news.content_error = error_msg[:500]
                news.content_fetched_at = now

                # Record pipeline trace event for fetch failure
                elapsed = (time.monotonic() - t0) * 1000
                try:
                    from app.services.pipeline_trace_service import PipelineTraceService
                    await PipelineTraceService.record_event(
                        db, news_id=news_id, layer="1.5", node="fetch",
                        status="error", duration_ms=elapsed,
                        metadata={"content_status": str(news.content_status)},
                        error=error_msg[:200] if error_msg else None,
                    )
                except Exception as trace_err:
                    logger.debug("Trace recording failed for fetch error: %s", trace_err)

                await db.commit()

                logger.warning(
                    "batch_fetch: fetch failed for news_id=%s: %s",
                    news_id, error_msg[:100],
                )
                return {"success": False, "error": error_msg}

            data = result.data

            # Update News record with fetched content metadata
            if data.get("is_partial"):
                news.content_status = ContentStatus.PARTIAL.value
            else:
                news.content_status = ContentStatus.FETCHED.value
            news.content_file_path = data.get("file_path")
            news.content_fetched_at = now
            news.content_error = None
            news.language = data.get("language")
            news.authors = data.get("authors")
            news.keywords = data.get("keywords")

            # Record pipeline trace event for fetch success
            elapsed = (time.monotonic() - t0) * 1000
            try:
                from app.services.pipeline_trace_service import PipelineTraceService
                await PipelineTraceService.record_event(
                    db, news_id=news_id, layer="1.5", node="fetch",
                    status="success", duration_ms=elapsed,
                    metadata={
                        "word_count": data.get("word_count", 0),
                        "language": data.get("language"),
                        "content_status": str(news.content_status),
                    },
                )
            except Exception as trace_err:
                logger.debug("Trace recording failed for fetch success: %s", trace_err)

            await db.commit()

            logger.info(
                "batch_fetch: fetched news_id=%s, %d words, status=%s",
                news_id, data.get("word_count", 0), news.content_status,
            )

            return {
                "success": True,
                "file_path": data.get("file_path"),
                "word_count": data.get("word_count", 0),
            }

    except Exception as e:
        logger.error(
            "batch_fetch: DB update failed for news_id=%s: %s", news_id, e,
        )
        return {"success": False, "error": f"DB update failed: {e}"}


@celery_app.task
def cleanup_expired_news():
    """
    Cleanup expired news content based on system default retention period.

    Since News is a global shared resource (not per-user), we use the system
    default retention period from config.NEWS_RETENTION_DAYS_DEFAULT.

    User settings for news_retention_days are reserved for future features
    like personalized news feed filtering, not for data cleanup.

    Runs daily to:
    1. Find news older than system retention period
    2. Delete JSON files and associated embeddings
    3. Update news records
    """
    try:
        return run_async_task(_cleanup_expired_news_async)
    except Exception as e:
        logger.exception("cleanup_expired_news failed: %s", e)
        raise


async def _cleanup_expired_news_async() -> Dict[str, Any]:
    """Async implementation of news cleanup."""
    from sqlalchemy import select, and_

    from app.config import settings
    from app.models.news import News, ContentStatus
    from app.services.news_storage_service import get_news_storage_service
    from app.services.rag_service import get_rag_service

    # Use system default retention period (News is global, not per-user)
    retention_days = settings.NEWS_RETENTION_DAYS_DEFAULT
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

    logger.info(
        "Starting expired news cleanup: retention=%d days, cutoff=%s",
        retention_days,
        cutoff_date.isoformat(),
    )

    storage_service = get_news_storage_service()
    rag_service = get_rag_service()

    stats = {
        "retention_days": retention_days,
        "files_deleted": 0,
        "embeddings_deleted": 0,
        "news_updated": 0,
        "errors": 0,
    }

    async with get_task_session() as db:
        # Find expired news with content files (process in batches)
        news_query = select(News).where(
            and_(
                News.published_at < cutoff_date,
                News.content_file_path.isnot(None),
                News.content_status.notin_([
                    ContentStatus.DELETED.value,
                    ContentStatus.PENDING.value,
                ]),
            )
        ).limit(1000)

        news_result = await db.execute(news_query)
        expired_news = news_result.scalars().all()

        logger.info("Found %d expired news articles to clean up", len(expired_news))

        for news in expired_news:
            try:
                # Delete JSON file
                if news.content_file_path:
                    if storage_service.delete_content(news.content_file_path):
                        stats["files_deleted"] += 1
                        logger.debug("Deleted content file: %s", news.content_file_path)

                # Delete embeddings
                deleted_count = await rag_service.delete_embeddings(
                    db, "news", str(news.id)
                )
                stats["embeddings_deleted"] += deleted_count

                # Update news record (keep basic metadata, clear content)
                news.content_file_path = None
                news.content_status = ContentStatus.DELETED.value
                stats["news_updated"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    "Error cleaning up news_id=%s: %s", news.id, e
                )

        await db.commit()

    # Also run file-based cleanup for orphaned files
    try:
        orphan_deleted = storage_service.cleanup_old_files(days=retention_days)
        stats["orphan_files_deleted"] = orphan_deleted
        if orphan_deleted > 0:
            logger.info("Cleaned up %d orphan content files", orphan_deleted)
    except Exception as e:
        logger.error("Error cleaning up orphan files: %s", e)

    logger.info(
        "Expired news cleanup completed: files=%d, embeddings=%d, news=%d, errors=%d",
        stats["files_deleted"],
        stats["embeddings_deleted"],
        stats["news_updated"],
        stats["errors"],
    )

    return stats


@celery_app.task(name="worker.tasks.full_content_tasks.cleanup_pipeline_events")
def cleanup_pipeline_events():
    """Clean up old pipeline trace events."""
    try:
        return run_async_task(_cleanup_pipeline_events_async)
    except Exception as e:
        logger.exception("cleanup_pipeline_events failed: %s", e)
        raise


async def _cleanup_pipeline_events_async() -> Dict[str, Any]:
    """Async implementation of pipeline events cleanup."""
    from app.services.pipeline_trace_service import PipelineTraceService

    async with get_task_session() as db:
        deleted = await PipelineTraceService.cleanup_old_events(db, retention_days=7)
        await db.commit()

    logger.info("Pipeline events cleanup: deleted %d old events", deleted)
    return {"deleted": deleted}
