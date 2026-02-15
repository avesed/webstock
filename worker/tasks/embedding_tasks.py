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
    logger.info(
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
        logger.info(
            "Embedded %s/%s: %d/%d chunks stored",
            source_type,
            source_id,
            stored_count,
            len(chunks),
        )

    return {
        "status": "success",
        "source_type": source_type,
        "source_id": source_id,
        "chunks_total": len(chunks),
        "chunks_stored": stored_count,
    }
