"""Search backend implementations for pgvector and pg_trgm."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag.protocols import SearchResult

logger = logging.getLogger(__name__)


class PgVectorSearch:
    """Cosine similarity search via pgvector <=> operator."""

    async def search(
        self,
        db: AsyncSession,
        *,
        query_embedding: Optional[List[float]] = None,
        query_text: Optional[str] = None,
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        if query_embedding is None:
            return []

        # Serialize embedding to pgvector literal format
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        conditions: List[str] = []
        params: Dict[str, Any] = {"embedding": embedding_str, "limit": top_k}

        if symbol:
            conditions.append("symbol = :symbol")
            params["symbol"] = symbol
        if source_type:
            conditions.append("source_type = :source_type")
            params["source_type"] = source_type

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = text(f"""
            SELECT id, chunk_text, source_type, source_id, symbol,
                   chunk_index, created_at, model,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM document_embeddings
            {where_clause}
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)

        try:
            result = await db.execute(query, params)
            rows = result.fetchall()
        except Exception as e:
            logger.error("Vector search failed: %s", e)
            return []

        return [
            SearchResult(
                chunk_text=row.chunk_text,
                source_type=row.source_type,
                source_id=row.source_id,
                symbol=row.symbol,
                score=float(row.similarity),
                chunk_index=row.chunk_index,
                created_at=row.created_at,
                model=row.model,
            )
            for row in rows
        ]


class PgTrigramSearch:
    """Trigram similarity search via pg_trgm extension."""

    def __init__(self, threshold: float = 0.1):
        self.threshold = threshold

    async def search(
        self,
        db: AsyncSession,
        *,
        query_embedding: Optional[List[float]] = None,
        query_text: Optional[str] = None,
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        if not query_text:
            return []

        conditions = ["similarity(chunk_text, :query) > :threshold"]
        params: Dict[str, Any] = {"query": query_text, "limit": top_k, "threshold": self.threshold}

        if symbol:
            conditions.append("symbol = :symbol")
            params["symbol"] = symbol
        if source_type:
            conditions.append("source_type = :source_type")
            params["source_type"] = source_type

        where_clause = "WHERE " + " AND ".join(conditions)

        query = text(f"""
            SELECT id, chunk_text, source_type, source_id, symbol,
                   chunk_index, created_at, model,
                   similarity(chunk_text, :query) AS sim_score
            FROM document_embeddings
            {where_clause}
            ORDER BY sim_score DESC
            LIMIT :limit
        """)

        try:
            result = await db.execute(query, params)
            rows = result.fetchall()
        except Exception as e:
            logger.warning(
                "Keyword search failed (pg_trgm may not be available): %s", e
            )
            return []

        return [
            SearchResult(
                chunk_text=row.chunk_text,
                source_type=row.source_type,
                source_id=row.source_id,
                symbol=row.symbol,
                score=float(row.sim_score),
                chunk_index=row.chunk_index,
                created_at=row.created_at,
                model=row.model,
            )
            for row in rows
        ]
