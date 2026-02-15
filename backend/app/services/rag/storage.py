"""Embedding storage via PostgreSQL + pgvector."""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_embedding import DocumentEmbedding

logger = logging.getLogger(__name__)


class PgEmbeddingStore:
    """Store and delete document embeddings in PostgreSQL."""

    async def store(
        self,
        db: AsyncSession,
        *,
        source_type: str,
        source_id: str,
        chunk_text: str,
        embedding: List[float],
        symbol: Optional[str] = None,
        chunk_index: int = 0,
        token_count: Optional[int] = None,
        model: Optional[str] = None,
    ) -> DocumentEmbedding:
        """Store a document embedding in the database."""
        doc = DocumentEmbedding(
            source_type=source_type,
            source_id=source_id,
            symbol=symbol,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            embedding=embedding,
            model=model or "unknown",
            token_count=token_count,
        )
        db.add(doc)
        await db.flush()
        logger.debug(
            "Stored embedding: source=%s/%s, symbol=%s, chunk=%d",
            source_type,
            source_id,
            symbol,
            chunk_index,
        )
        return doc

    async def delete(
        self,
        db: AsyncSession,
        *,
        source_type: str,
        source_id: str,
    ) -> int:
        """Delete all embeddings for a source document.

        Used when re-embedding a document to replace stale chunks.
        """
        result = await db.execute(
            text(
                "DELETE FROM document_embeddings "
                "WHERE source_type = :source_type AND source_id = :source_id"
            ),
            {"source_type": source_type, "source_id": source_id},
        )
        count = result.rowcount
        if count > 0:
            logger.info(
                "Deleted %d embeddings for %s/%s", count, source_type, source_id
            )
        return count
