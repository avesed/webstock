"""Skill: chunk, embed, and store a document in the vector knowledge base."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class EmbedDocumentSkill(BaseSkill):
    """Chunk text, generate embeddings, and store in the document_embeddings table.

    Wraps the embedding pipeline: ``EmbeddingService.chunk_text`` ->
    ``EmbeddingService.generate_embeddings_batch`` -> ``RagService.store_embedding``.

    Uses a PostgreSQL advisory lock to prevent concurrent re-embedding of the
    same (source_type, source_id) pair.  Existing embeddings for the same
    source are replaced atomically.

    Requires a ``db`` (AsyncSession) kwarg injected by the caller for embedding
    config resolution and vector storage.

    Designed to be called by LangGraph news pipeline nodes and embedding tasks.
    """

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="embed_document",
            description=(
                "Chunk text content, generate vector embeddings, and store in the "
                "knowledge base for RAG retrieval. Replaces existing embeddings for "
                "the same source. Requires a db session injected by the caller."
            ),
            category="knowledge",
            parameters=[
                SkillParameter(
                    name="source_type",
                    type="string",
                    description="Type of source document: 'analysis', 'news', or 'report'.",
                    required=True,
                    enum=["analysis", "news", "report"],
                ),
                SkillParameter(
                    name="source_id",
                    type="string",
                    description="Unique ID of the source document (typically a UUID string).",
                    required=True,
                ),
                SkillParameter(
                    name="content",
                    type="string",
                    description="Full text content to chunk and embed.",
                    required=True,
                ),
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Optional stock symbol to associate with the embeddings.",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        # db is injected by the caller, not exposed as a SkillParameter
        db = kwargs.get("db")
        if db is None:
            return SkillResult(
                success=False,
                error="db (AsyncSession) must be provided by the caller",
            )

        source_type = kwargs.get("source_type")
        source_id = kwargs.get("source_id")
        content = kwargs.get("content")
        symbol = kwargs.get("symbol")

        if not source_type:
            return SkillResult(success=False, error="source_type parameter is required")
        if not source_id:
            return SkillResult(success=False, error="source_id parameter is required")
        if not content or not content.strip():
            return SkillResult(
                success=True,
                data={"status": "skipped", "reason": "empty_content"},
                metadata={"source_type": source_type, "source_id": source_id},
            )

        from sqlalchemy import text as sql_text

        from app.services.embedding_service import (
            get_embedding_config_from_db,
            get_embedding_service,
        )
        from app.services.rag_service import get_rag_service

        embedding_service = get_embedding_service()
        rag_service = get_rag_service()

        # Chunk text
        chunks = embedding_service.chunk_text(content)
        if not chunks:
            return SkillResult(
                success=True,
                data={"status": "skipped", "reason": "no_chunks"},
                metadata={"source_type": source_type, "source_id": source_id},
            )

        # Resolve embedding config from DB
        try:
            embed_config = await get_embedding_config_from_db(db)
        except ValueError as e:
            return SkillResult(
                success=False,
                error=f"Embedding config error: {e}",
                metadata={"source_type": source_type, "source_id": source_id},
            )

        # Generate embeddings in batch
        embeddings = await embedding_service.generate_embeddings_batch(
            chunks,
            model=embed_config.model,
            api_key=embed_config.api_key,
            base_url=embed_config.base_url,
        )

        # Filter to valid (non-None) pairs
        valid_pairs = [
            (i, chunk, emb)
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
            if emb is not None
        ]

        if not valid_pairs:
            return SkillResult(
                success=False,
                error="All embeddings failed to generate",
                metadata={
                    "source_type": source_type,
                    "source_id": source_id,
                    "chunks_total": len(chunks),
                },
            )

        # Advisory lock to serialise concurrent re-embed of same document
        lock_key = int.from_bytes(
            hashlib.md5(f"{source_type}:{source_id}".encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )

        stored_count = 0
        await db.execute(sql_text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})

        try:
            # Delete existing embeddings (supports re-embedding)
            await rag_service.delete_embeddings(db, source_type, source_id)

            for i, chunk, embedding in valid_pairs:
                await rag_service.store_embedding(
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
        except Exception as e:
            logger.exception(
                "EmbedDocumentSkill DB error for %s/%s: %s",
                source_type, source_id, e,
            )
            await db.rollback()
            return SkillResult(
                success=False,
                error=f"Failed to store embeddings: {e}",
                metadata={
                    "source_type": source_type,
                    "source_id": source_id,
                    "chunks_total": len(chunks),
                    "chunks_generated": len(valid_pairs),
                },
            )
        finally:
            try:
                if db.in_transaction():
                    await db.rollback()
                await db.execute(
                    sql_text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}
                )
            except Exception as unlock_err:
                logger.warning(
                    "Failed to release advisory lock %d: %s", lock_key, unlock_err
                )

        return SkillResult(
            success=True,
            data={
                "status": "success",
                "chunks_total": len(chunks),
                "chunks_stored": stored_count,
            },
            metadata={
                "source_type": source_type,
                "source_id": source_id,
                "symbol": symbol,
                "model": embed_config.model,
            },
        )
