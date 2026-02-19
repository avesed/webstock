"""Celery tasks for generating document embeddings.

These tasks are dispatched asynchronously after content is created or updated
(e.g. analysis reports, news articles) to make them searchable via RAG.
Each task chunks the input text, calls the embedding API, and stores the
resulting vectors in the document_embeddings table.
"""

import logging
from typing import Any, Dict, List

from worker.celery_app import celery_app

# Use Celery-safe database utilities (avoids event loop conflicts)
from app.db.task_session import get_task_session

logger = logging.getLogger(__name__)

# Redis lock keys to prevent concurrent rebuild/retry tasks
_LOCK_KEY_PREFIX = "kb:embedding_task_lock:"
_LOCK_TTL = {
    "rebuild_news": 7200,
    "rebuild_report": 3600,
    "retry_news": 3600,
    "retry_report": 3600,
}


def _acquire_embedding_lock(task_name: str) -> bool:
    """Try to acquire a Redis lock (sync, for use inside Celery tasks)."""
    import redis as redis_lib
    from app.config import settings

    try:
        r = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
        ttl = _LOCK_TTL.get(task_name, 3600)
        return bool(r.set(f"{_LOCK_KEY_PREFIX}{task_name}", "1", nx=True, ex=ttl))
    except Exception as e:
        logger.warning("[EmbeddingTask] Redis lock check failed for %s: %s", task_name, e)
        return True  # fail-open: proceed if Redis is down


def _release_embedding_lock(task_name: str) -> None:
    """Release a Redis lock (sync)."""
    import redis as redis_lib
    from app.config import settings

    try:
        r = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
        r.delete(f"{_LOCK_KEY_PREFIX}{task_name}")
    except Exception:
        pass


def _reset_singletons() -> None:
    """Reset singleton clients after each Celery task event loop closes."""
    try:
        from app.core.llm import reset_llm_gateway
        reset_llm_gateway()
    except Exception as e:
        logger.warning("Failed to reset LLM gateway in embedding task: %s", e)
    try:
        from app.services.rag import reset_index_service
        reset_index_service()
    except Exception as e:
        logger.warning("Failed to reset IndexService in embedding task: %s", e)


@celery_app.task(bind=True, max_retries=3)
def embed_analysis_report(self, report_data: Dict[str, Any]):
    """
    Generate embeddings for an AI analysis report.

    Called after an analysis is completed to make it searchable via RAG.

    Args:
        report_data: {
            "source_id": str,
            "symbol": str,
            "agent_type": str,
            "content": str,
        }
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _embed_document_async(
                    source_type="analysis",
                    source_id=report_data["source_id"],
                    content=report_data["content"],
                    symbol=report_data.get("symbol"),
                )
            )
            return result
        finally:
            loop.close()
            _reset_singletons()
    except Exception as e:
        logger.exception("Embedding task failed for analysis report: %s", e)
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def embed_news_article(self, news_id: str, content: str, symbol: str = None):
    """
    Generate embeddings for a news article.

    Args:
        news_id: UUID of the news article
        content: Text content to embed (title + summary)
        symbol: Associated stock symbol
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _embed_document_async(
                    source_type="news",
                    source_id=news_id,
                    content=content,
                    symbol=symbol,
                )
            )
            return result
        finally:
            loop.close()
            _reset_singletons()
    except Exception as e:
        logger.exception("Embedding task failed for news %s: %s", news_id, e)
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def embed_report(self, report_id: str, content: str, symbol: str = None):
    """
    Generate embeddings for a generated report.

    Args:
        report_id: UUID of the report
        content: Full report text
        symbol: Associated stock symbol (if report is symbol-specific)
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _embed_document_async(
                    source_type="report",
                    source_id=report_id,
                    content=content,
                    symbol=symbol,
                )
            )
            return result
        finally:
            loop.close()
            _reset_singletons()
    except Exception as e:
        logger.exception("Embedding task failed for report %s: %s", report_id, e)
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


# ---------------------------------------------------------------------------
# Retry & rebuild tasks
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=1, time_limit=3600, soft_time_limit=3500)
def retry_failed_news_embeddings(self):
    """
    Retry embedding for all news articles whose content_status is 'embedding_failed'.

    For each failed article, constructs embed text preferring detailed_summary
    (with title prefix), falling back to title + summary, then dispatches an
    individual embed_news_article task.

    Returns:
        dict with "dispatched" count.
    """
    if not _acquire_embedding_lock("retry_news"):
        logger.info("[RetryFailedNews] Already running, skipping")
        return {"skipped": True, "reason": "already_running"}

    from worker.task_helpers import run_async_task

    async def _retry():
        from sqlalchemy import text
        async with get_task_session() as db:
            rows = await db.execute(text(
                "SELECT id, symbol, title, summary, detailed_summary "
                "FROM news WHERE content_status = 'embedding_failed'"
            ))
            articles = rows.fetchall()

        dispatched = 0
        for row in articles:
            news_id = str(row.id)
            # Prefer detailed_summary, fallback to title + summary
            parts = []
            if row.title:
                parts.append(row.title)
            if row.detailed_summary:
                parts.append(row.detailed_summary)
            elif row.summary:
                parts.append(row.summary)
            content = "\n\n".join(parts)
            if not content.strip():
                continue
            embed_news_article.delay(news_id, content, row.symbol)
            dispatched += 1

        logger.info("嵌入重试：news 派发%d个任务", dispatched)
        return {"dispatched": dispatched}

    try:
        return run_async_task(_retry)
    except Exception as e:
        logger.exception("retry_failed_news_embeddings failed: %s", e)
        raise self.retry(exc=e, countdown=60)
    finally:
        _release_embedding_lock("retry_news")


@celery_app.task(bind=True, max_retries=1, time_limit=3600, soft_time_limit=3500)
def retry_failed_report_embeddings(self):
    """
    Retry embedding for completed reports that are missing from document_embeddings.

    Queries reports with status='completed' that have no corresponding entry in
    document_embeddings, extracts text from the JSONB content field, and dispatches
    individual embed_report tasks.

    Returns:
        dict with "dispatched" count.
    """
    if not _acquire_embedding_lock("retry_report"):
        logger.info("[RetryFailedReports] Already running, skipping")
        return {"skipped": True, "reason": "already_running"}

    from worker.task_helpers import run_async_task

    async def _retry():
        import json
        from sqlalchemy import text
        async with get_task_session() as db:
            rows = await db.execute(text(
                "SELECT r.id, r.title, r.content FROM reports r "
                "WHERE r.status = 'completed' "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM document_embeddings de "
                "  WHERE de.source_type = 'report' AND de.source_id = r.id::text"
                ")"
            ))
            reports = rows.fetchall()

        dispatched = 0
        skipped = 0
        for row in reports:
            report_id = str(row.id)
            try:
                content_data = row.content
                if isinstance(content_data, str):
                    content_data = json.loads(content_data)
                # Extract text from the content dict
                text_parts = [row.title] if row.title else []
                if isinstance(content_data, dict):
                    for key, val in content_data.items():
                        if isinstance(val, str) and len(val) > 20:
                            text_parts.append(val)
                content_text = "\n\n".join(text_parts)
                if not content_text.strip():
                    skipped += 1
                    continue
                embed_report.delay(report_id, content_text, None)
                dispatched += 1
            except (json.JSONDecodeError, TypeError) as e:
                skipped += 1
                logger.warning(
                    "[RetryFailedReports] Skipping report %s: malformed content: %s",
                    report_id, e,
                )

        logger.info("嵌入重试：report 派发%d个任务", dispatched)
        return {"dispatched": dispatched, "skipped": skipped}

    try:
        return run_async_task(_retry)
    except Exception as e:
        logger.exception("retry_failed_report_embeddings failed: %s", e)
        raise self.retry(exc=e, countdown=60)
    finally:
        _release_embedding_lock("retry_report")


@celery_app.task(bind=True, max_retries=1, time_limit=7200, soft_time_limit=7100)
def rebuild_news_embeddings(self):
    """
    Delete all news embeddings and re-embed all news with useful content.

    Steps:
    1. DELETE all document_embeddings where source_type='news'
    2. SELECT all news with content_status IN ('fetched', 'embedded', 'embedding_failed')
    3. For each, construct content and dispatch embed_news_article task

    Returns:
        dict with "deleted" and "dispatched" counts.
    """
    if not _acquire_embedding_lock("rebuild_news"):
        logger.info("[RebuildNewsEmbeddings] Already running, skipping")
        return {"skipped": True, "reason": "already_running"}

    from worker.task_helpers import run_async_task

    async def _rebuild():
        from sqlalchemy import text
        async with get_task_session() as db:
            result = await db.execute(text(
                "DELETE FROM document_embeddings WHERE source_type = 'news'"
            ))
            deleted = result.rowcount
            await db.commit()
            logger.info("嵌入重建：news 删除%d条", deleted)

        async with get_task_session() as db:
            rows = await db.execute(text(
                "SELECT id, symbol, title, summary, detailed_summary "
                "FROM news WHERE content_status IN ('fetched', 'embedded', 'embedding_failed')"
            ))
            articles = rows.fetchall()

        dispatched = 0
        for row in articles:
            news_id = str(row.id)
            parts = []
            if row.title:
                parts.append(row.title)
            if row.detailed_summary:
                parts.append(row.detailed_summary)
            elif row.summary:
                parts.append(row.summary)
            content = "\n\n".join(parts)
            if not content.strip():
                continue
            embed_news_article.delay(news_id, content, row.symbol)
            dispatched += 1

        logger.info("嵌入重建：news 派发%d个任务", dispatched)
        return {"deleted": deleted, "dispatched": dispatched}

    try:
        return run_async_task(_rebuild)
    except Exception as e:
        logger.exception("rebuild_news_embeddings failed: %s", e)
        raise self.retry(exc=e, countdown=60)
    finally:
        _release_embedding_lock("rebuild_news")


@celery_app.task(bind=True, max_retries=1, time_limit=3600, soft_time_limit=3500)
def rebuild_report_embeddings(self):
    """
    Delete all report embeddings and re-embed all completed reports.

    Steps:
    1. DELETE all document_embeddings where source_type='report'
    2. SELECT all reports with status='completed'
    3. For each, extract text from JSONB content and dispatch embed_report task

    Returns:
        dict with "deleted" and "dispatched" counts.
    """
    if not _acquire_embedding_lock("rebuild_report"):
        logger.info("[RebuildReportEmbeddings] Already running, skipping")
        return {"skipped": True, "reason": "already_running"}

    from worker.task_helpers import run_async_task

    async def _rebuild():
        import json
        from sqlalchemy import text
        async with get_task_session() as db:
            result = await db.execute(text(
                "DELETE FROM document_embeddings WHERE source_type = 'report'"
            ))
            deleted = result.rowcount
            await db.commit()
            logger.info("嵌入重建：report 删除%d条", deleted)

        async with get_task_session() as db:
            rows = await db.execute(text(
                "SELECT id, title, content FROM reports WHERE status = 'completed'"
            ))
            reports = rows.fetchall()

        dispatched = 0
        skipped = 0
        for row in reports:
            report_id = str(row.id)
            try:
                content_data = row.content
                if isinstance(content_data, str):
                    content_data = json.loads(content_data)
                text_parts = [row.title] if row.title else []
                if isinstance(content_data, dict):
                    for key, val in content_data.items():
                        if isinstance(val, str) and len(val) > 20:
                            text_parts.append(val)
                content_text = "\n\n".join(text_parts)
                if not content_text.strip():
                    skipped += 1
                    continue
                embed_report.delay(report_id, content_text, None)
                dispatched += 1
            except (json.JSONDecodeError, TypeError) as e:
                skipped += 1
                logger.warning(
                    "[RebuildReportEmbeddings] Skipping report %s: malformed content: %s",
                    report_id, e,
                )

        logger.info("嵌入重建：report 派发%d个任务", dispatched)
        return {"deleted": deleted, "dispatched": dispatched, "skipped": skipped}

    try:
        return run_async_task(_rebuild)
    except Exception as e:
        logger.exception("rebuild_report_embeddings failed: %s", e)
        raise self.retry(exc=e, countdown=60)
    finally:
        _release_embedding_lock("rebuild_report")


# ---------------------------------------------------------------------------
# Shared async implementation
# ---------------------------------------------------------------------------


async def _embed_document_async(
    source_type: str,
    source_id: str,
    content: str,
    symbol: str = None,
) -> Dict[str, Any]:
    """
    Async implementation: chunk text, generate embeddings, store in DB.

    Steps:
    1. Validate content is non-empty
    2. Chunk the text via IndexService
    3. Generate embeddings in batch (respects rate limits)
    4. Acquire advisory lock to prevent concurrent re-embedding of same doc
    5. Delete existing embeddings only if new embeddings were generated
    6. Store new embeddings in document_embeddings table
    """
    import hashlib

    from sqlalchemy import text

    from app.services.rag import get_index_service

    index_service = get_index_service()

    if not content or not content.strip():
        logger.warning("Empty content for embedding: %s/%s", source_type, source_id)
        return {"status": "skipped", "reason": "empty_content"}

    # Chunk the text into embedding-sized pieces
    chunks = index_service.chunk_text(content)
    logger.debug(
        "Chunked %s/%s into %d chunks (total %d chars)",
        source_type,
        source_id,
        len(chunks),
        len(content),
    )

    if not chunks:
        return {"status": "skipped", "reason": "no_chunks"}

    # Read embedding config (model + provider credentials) from DB
    from app.services.rag.embedding import get_embedding_config_from_db
    async with get_task_session() as tmp_db:
        embed_config = await get_embedding_config_from_db(tmp_db)

    # Generate embeddings in batch (handles rate limiting internally)
    embeddings = await index_service.generate_embeddings_batch(
        chunks, model=embed_config.model,
        api_key=embed_config.api_key, base_url=embed_config.base_url,
    )

    # P0-4: Check that at least one embedding succeeded before replacing old data
    valid_pairs = [
        (i, chunk, emb)
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
        if emb is not None
    ]
    if not valid_pairs:
        logger.error(
            "All embeddings failed for %s/%s (%d chunks). "
            "Keeping existing embeddings intact.",
            source_type,
            source_id,
            len(chunks),
        )
        return {
            "status": "error",
            "reason": "all_embeddings_failed",
            "chunks_total": len(chunks),
        }

    # P0-3: Use PostgreSQL advisory lock to serialise concurrent re-embed
    # of the same (source_type, source_id) pair.
    lock_key = int.from_bytes(
        hashlib.md5(f"{source_type}:{source_id}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )

    stored_count = 0
    async with get_task_session() as db:
        # Acquire session-level advisory lock (released on session close)
        await db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})

        try:
            # Delete existing embeddings for this source (supports re-embedding)
            await index_service.delete_embeddings(db, source_type, source_id)

            for i, chunk, embedding in valid_pairs:
                await index_service.store_embedding(
                    db=db,
                    source_type=source_type,
                    source_id=source_id,
                    chunk_text=chunk,
                    embedding=embedding,
                    symbol=symbol,
                    chunk_index=i,
                    model=embed_config.model,
                )
                stored_count += 1

            await db.commit()
        finally:
            # Explicitly release the advisory lock.
            # If the transaction is in an aborted state (commit failed),
            # rollback first so the unlock SQL can execute.
            try:
                if db.in_transaction():
                    await db.rollback()
                await db.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}
                )
            except Exception as unlock_err:
                # Lock is released automatically when the session closes,
                # so log but do not mask the original exception.
                logger.warning(
                    "Failed to explicitly release advisory lock %d: %s",
                    lock_key,
                    unlock_err,
                )

    failed_count = len(chunks) - stored_count
    if failed_count > 0:
        logger.warning(
            "Embedded %s/%s: %d/%d chunks stored (%d failed)",
            source_type,
            source_id,
            stored_count,
            len(chunks),
            failed_count,
        )
    else:
        logger.debug(
            "Embedded %s/%s: %d/%d chunks stored",
            source_type,
            source_id,
            stored_count,
            len(chunks),
        )

    # If this is a news embedding (possibly retried), update content_status
    if source_type == "news" and stored_count > 0:
        try:
            async with get_task_session() as status_db:
                await status_db.execute(
                    text(
                        "UPDATE news SET content_status = 'embedded' "
                        "WHERE id = :nid AND content_status = 'embedding_failed'"
                    ),
                    {"nid": source_id},
                )
                await status_db.commit()
        except Exception as status_err:
            logger.warning(
                "Failed to update content_status for %s: %s",
                source_id, status_err,
            )

    return {
        "status": "success",
        "source_type": source_type,
        "source_id": source_id,
        "chunks_total": len(chunks),
        "chunks_stored": stored_count,
    }
