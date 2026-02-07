"""Celery tasks for news full content fetching, filtering, and embedding.

This module implements the full news content pipeline:
1. fetch_news_content - Fetches full article text using FullContentService
2. fetch_batch_content - Batch fetch multiple URLs in parallel
3. evaluate_news_relevance - Uses LLM to filter irrelevant news
4. embed_news_full_content - Generates embeddings for full text
5. cleanup_expired_news - Periodic cleanup of old news content
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from worker.celery_app import celery_app

# Use Celery-safe database utilities (avoids event loop conflicts)
from worker.db_utils import get_task_session, setup_task_ai_context

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def fetch_news_content(
    self,
    news_id: str,
    url: str,
    market: str,
    symbol: str,
    user_id: Optional[int] = None,
):
    """
    Fetch full content for a single news article.

    Workflow:
    1. Use FullContentService to scrape article content
    2. Save content to JSON file using NewsStorageService
    3. Update News record with file path and status
    4. Dispatch evaluate_news_relevance task

    Args:
        news_id: UUID of the news article
        url: Article URL to fetch
        market: Market identifier (US, HK, SH, SZ)
        symbol: Stock symbol
        user_id: Optional user ID for personalized settings
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _fetch_news_content_async(news_id, url, market, symbol, user_id)
            )
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception("fetch_news_content failed for news_id=%s: %s", news_id, e)
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


async def _fetch_news_content_async(
    news_id: str,
    url: str,
    market: str,
    symbol: str,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Async implementation of fetch_news_content."""
    from sqlalchemy import select

    from app.models.news import News, ContentStatus
    from app.models.user_settings import UserSettings
    from app.services.full_content_service import (
        get_full_content_service,
        ContentSource,
    )
    from app.services.news_storage_service import get_news_storage_service

    logger.info("Fetching content for news_id=%s, url=%s", news_id, url[:100])

    async with get_task_session() as db:
        # Get news record
        query = select(News).where(News.id == uuid.UUID(news_id))
        result = await db.execute(query)
        news = result.scalar_one_or_none()

        if not news:
            logger.warning("News record not found: %s", news_id)
            return {"status": "error", "reason": "not_found"}

        # Skip if already processed
        if news.content_status in [
            ContentStatus.FETCHED.value,
            ContentStatus.EMBEDDED.value,
        ]:
            logger.info("News already processed: %s (status=%s)", news_id, news.content_status)
            return {"status": "skipped", "reason": "already_processed"}

        # Get user settings for content source preference
        content_source = ContentSource.SCRAPER
        polygon_api_key = None

        if user_id:
            settings_query = select(UserSettings).where(UserSettings.user_id == user_id)
            settings_result = await db.execute(settings_query)
            user_settings = settings_result.scalar_one_or_none()

            if user_settings:
                if user_settings.full_content_source == "polygon":
                    content_source = ContentSource.POLYGON
                polygon_api_key = user_settings.polygon_api_key

        # Fetch content
        content_service = get_full_content_service(
            default_source=content_source,
            polygon_api_key=polygon_api_key,
        )

        # Detect language from market
        language = "zh" if market in ["SH", "SZ"] else "en"

        fetch_result = await content_service.fetch_with_fallback(
            url=url,
            primary_source=content_source,
            language=language,
            polygon_api_key=polygon_api_key,
        )

        now = datetime.now(timezone.utc)

        if not fetch_result.success:
            # Update status to failed or blocked
            if "blocked" in (fetch_result.error or "").lower():
                news.content_status = ContentStatus.BLOCKED.value
            else:
                news.content_status = ContentStatus.FAILED.value
            news.content_error = fetch_result.error
            news.content_fetched_at = now
            await db.commit()

            logger.warning(
                "Failed to fetch content for news_id=%s: %s",
                news_id,
                fetch_result.error,
            )
            return {
                "status": "failed",
                "news_id": news_id,
                "error": fetch_result.error,
            }

        # Determine content status
        if fetch_result.is_partial:
            content_status = ContentStatus.PARTIAL.value
        else:
            content_status = ContentStatus.FETCHED.value

        # Prepare content for storage
        content_data = {
            "url": url,
            "title": news.title,
            "full_text": fetch_result.full_text,
            "authors": fetch_result.authors,
            "keywords": fetch_result.keywords,
            "top_image": fetch_result.top_image,
            "language": fetch_result.language,
            "word_count": fetch_result.word_count,
            "fetched_at": now.isoformat(),
            "source": fetch_result.source.value if fetch_result.source else None,
            "metadata": fetch_result.metadata,
        }

        # Save to JSON file
        storage_service = get_news_storage_service()
        try:
            file_path = storage_service.save_content(
                news_id=uuid.UUID(news_id),
                symbol=symbol,
                content=content_data,
                published_at=news.published_at,
            )
        except IOError as e:
            logger.error("Failed to save content file for news_id=%s: %s", news_id, e)
            news.content_status = ContentStatus.FAILED.value
            news.content_error = f"Storage error: {str(e)[:200]}"
            news.content_fetched_at = now
            await db.commit()
            return {"status": "failed", "news_id": news_id, "error": str(e)}

        # Update news record
        news.content_file_path = file_path
        news.content_status = content_status
        news.content_fetched_at = now
        news.content_error = None
        news.language = fetch_result.language
        news.authors = fetch_result.authors
        news.keywords = fetch_result.keywords
        news.top_image = fetch_result.top_image

        await db.commit()

        logger.info(
            "Fetched content for news_id=%s: %d words, status=%s",
            news_id,
            fetch_result.word_count,
            content_status,
        )

        # Dispatch evaluation task
        evaluate_news_relevance.delay(news_id, user_id)

        return {
            "status": "success",
            "news_id": news_id,
            "word_count": fetch_result.word_count,
            "content_status": content_status,
            "file_path": file_path,
        }


@celery_app.task(bind=True, max_retries=2)
def fetch_batch_content(self, news_items: List[Dict[str, Any]]):
    """
    Batch fetch content for multiple news articles.

    Fetches 10-20 URLs in parallel using asyncio.gather.

    Args:
        news_items: List of dicts with keys: news_id, url, market, symbol, user_id
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_fetch_batch_content_async(news_items))
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception("fetch_batch_content failed: %s", e)
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _fetch_batch_content_async(
    news_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Async implementation of batch content fetching."""
    if not news_items:
        return {"status": "skipped", "reason": "empty_batch"}

    # Limit batch size
    batch = news_items[:20]

    logger.info("Starting batch fetch for %d news items", len(batch))

    # Create tasks for parallel execution
    tasks = []
    for item in batch:
        task = _fetch_news_content_async(
            news_id=item["news_id"],
            url=item["url"],
            market=item.get("market", "US"),
            symbol=item.get("symbol", "UNKNOWN"),
            user_id=item.get("user_id"),
        )
        tasks.append(task)

    # Execute in parallel with timeout
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Summarize results
    success_count = 0
    failed_count = 0
    for result in results:
        if isinstance(result, Exception):
            failed_count += 1
            logger.error("Batch item failed: %s", result)
        elif isinstance(result, dict) and result.get("status") == "success":
            success_count += 1
        else:
            failed_count += 1

    logger.info(
        "Batch fetch completed: %d success, %d failed out of %d",
        success_count,
        failed_count,
        len(batch),
    )

    return {
        "status": "completed",
        "total": len(batch),
        "success": success_count,
        "failed": failed_count,
    }


@celery_app.task(bind=True, max_retries=2)
def evaluate_news_relevance(self, news_id: str, user_id: Optional[int] = None):
    """
    Evaluate news relevance using LLM.

    Workflow:
    1. Load news record and full content from JSON file
    2. Get user's filter model preference
    3. Call NewsFilterService.evaluate_relevance
    4. If DELETE: delete JSON file and mark news as deleted
    5. If KEEP: dispatch embed_news_full_content task

    Args:
        news_id: UUID of the news article
        user_id: Optional user ID for personalized settings
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _evaluate_news_relevance_async(news_id, user_id)
            )
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception("evaluate_news_relevance failed for news_id=%s: %s", news_id, e)
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


async def _evaluate_news_relevance_async(
    news_id: str,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Async implementation of news relevance evaluation."""
    from sqlalchemy import select

    from app.models.news import News, ContentStatus
    from app.models.user_settings import UserSettings
    from app.services.news_filter_service import get_news_filter_service
    from app.services.news_storage_service import get_news_storage_service

    logger.info("Evaluating relevance for news_id=%s", news_id)

    async with get_task_session() as db:
        # Get news record
        query = select(News).where(News.id == uuid.UUID(news_id))
        result = await db.execute(query)
        news = result.scalar_one_or_none()

        if not news:
            logger.warning("News record not found: %s", news_id)
            return {"status": "error", "reason": "not_found"}

        # Skip if no content file
        if not news.content_file_path:
            logger.info("No content file for news_id=%s, skipping evaluation", news_id)
            # Still dispatch embedding for title/summary
            embed_news_full_content.delay(news_id, user_id)
            return {"status": "skipped", "reason": "no_content_file"}

        # Get user's filter model preference
        filter_model = "gpt-4o-mini"  # Default
        if user_id:
            settings_query = select(UserSettings).where(UserSettings.user_id == user_id)
            settings_result = await db.execute(settings_query)
            user_settings = settings_result.scalar_one_or_none()
            if user_settings and user_settings.news_filter_model:
                filter_model = user_settings.news_filter_model

        # Read full content from JSON file
        storage_service = get_news_storage_service()
        content_data = storage_service.read_content(news.content_file_path)

        full_text = None
        if content_data:
            full_text = content_data.get("full_text")

        # Evaluate relevance (set up AI context for LLM call)
        async with setup_task_ai_context():
            filter_service = get_news_filter_service(model=filter_model)
            should_keep = await filter_service.evaluate_relevance(
                title=news.title,
                summary=news.summary,
                full_text=full_text,
                source=news.source,
                symbol=news.symbol,
                model=filter_model,
            )

        if not should_keep:
            # Delete JSON file and mark as deleted
            logger.info("Filtering out irrelevant news_id=%s", news_id)

            if news.content_file_path:
                storage_service.delete_content(news.content_file_path)

            news.content_status = ContentStatus.DELETED.value
            news.content_file_path = None
            await db.commit()

            return {
                "status": "deleted",
                "news_id": news_id,
                "reason": "filtered_by_llm",
            }

        # Article is relevant, dispatch embedding task
        logger.info("Keeping relevant news_id=%s, dispatching embedding", news_id)
        embed_news_full_content.delay(news_id, user_id)

        return {
            "status": "kept",
            "news_id": news_id,
        }


@celery_app.task(bind=True, max_retries=3)
def embed_news_full_content(self, news_id: str, user_id: Optional[int] = None):
    """
    Generate embeddings for news full content.

    Uses the full text from JSON file for embedding, falling back to
    title + summary if no full content is available.

    Args:
        news_id: UUID of the news article
        user_id: Optional user ID for personalized settings
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _embed_news_full_content_async(news_id, user_id)
            )
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception("embed_news_full_content failed for news_id=%s: %s", news_id, e)
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


async def _embed_news_full_content_async(
    news_id: str,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Async implementation of news embedding."""
    import hashlib

    from sqlalchemy import select, text

    from app.models.news import News, ContentStatus
    from app.models.user_settings import UserSettings
    from app.services.news_storage_service import get_news_storage_service
    from app.services.embedding_service import get_embedding_service
    from app.services.rag_service import get_rag_service

    logger.info("Embedding news_id=%s", news_id)

    embedding_service = get_embedding_service()
    rag_service = get_rag_service()
    storage_service = get_news_storage_service()

    async with get_task_session() as db:
        # Get news record
        query = select(News).where(News.id == uuid.UUID(news_id))
        result = await db.execute(query)
        news = result.scalar_one_or_none()

        if not news:
            logger.warning("News record not found: %s", news_id)
            return {"status": "error", "reason": "not_found"}

        # Skip if already embedded or deleted
        if news.content_status == ContentStatus.EMBEDDED.value:
            logger.info("News already embedded: %s", news_id)
            return {"status": "skipped", "reason": "already_embedded"}
        if news.content_status == ContentStatus.DELETED.value:
            logger.info("News is deleted: %s", news_id)
            return {"status": "skipped", "reason": "deleted"}

        # Build content for embedding
        content_parts = []

        # Add title
        if news.title:
            content_parts.append(news.title)

        # Try to get full text from JSON file
        full_text = None
        if news.content_file_path:
            content_data = storage_service.read_content(news.content_file_path)
            if content_data:
                full_text = content_data.get("full_text")

        if full_text:
            content_parts.append(full_text)
        elif news.summary:
            # Fall back to summary
            content_parts.append(news.summary)

        content = "\n\n".join(content_parts)

        if not content.strip():
            logger.warning("Empty content for embedding: %s", news_id)
            return {"status": "skipped", "reason": "empty_content"}

        # Chunk the text
        chunks = embedding_service.chunk_text(content)
        logger.info(
            "Chunked news_id=%s into %d chunks (total %d chars)",
            news_id,
            len(chunks),
            len(content),
        )

        if not chunks:
            return {"status": "skipped", "reason": "no_chunks"}

        # Generate embeddings (set up AI context for API call)
        async with setup_task_ai_context():
            embeddings = await embedding_service.generate_embeddings_batch(chunks)

        # Check for valid embeddings
        valid_pairs = [
            (i, chunk, emb)
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
            if emb is not None
        ]

        if not valid_pairs:
            logger.error("All embeddings failed for news_id=%s", news_id)
            return {
                "status": "error",
                "reason": "all_embeddings_failed",
                "chunks_total": len(chunks),
            }

        # Use advisory lock to prevent concurrent embedding
        lock_key = int.from_bytes(
            hashlib.md5(f"news:{news_id}".encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )

        await db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})

        try:
            # Delete existing embeddings
            await rag_service.delete_embeddings(db, "news", news_id)

            # Store new embeddings
            stored_count = 0
            for i, chunk, embedding in valid_pairs:
                await rag_service.store_embedding(
                    db=db,
                    source_type="news",
                    source_id=news_id,
                    chunk_text=chunk,
                    embedding=embedding,
                    symbol=news.symbol,
                    chunk_index=i,
                )
                stored_count += 1

            # Update news status
            news.content_status = ContentStatus.EMBEDDED.value
            await db.commit()

        finally:
            try:
                if db.in_transaction():
                    await db.rollback()
                await db.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}
                )
            except Exception as unlock_err:
                logger.warning(
                    "Failed to release advisory lock %d: %s", lock_key, unlock_err
                )

        logger.info(
            "Embedded news_id=%s: %d/%d chunks stored",
            news_id,
            stored_count,
            len(chunks),
        )

        return {
            "status": "success",
            "news_id": news_id,
            "chunks_total": len(chunks),
            "chunks_stored": stored_count,
        }


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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_cleanup_expired_news_async())
            return result
        finally:
            loop.close()
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
