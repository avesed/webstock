"""RAG (Retrieval Augmented Generation) service with hybrid search."""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document_embedding import DocumentEmbedding

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result with relevance score."""

    chunk_text: str
    source_type: str
    source_id: str
    symbol: Optional[str]
    score: float
    chunk_index: int = 0

    @property
    def dedup_key(self) -> str:
        """Stable dedup key using source identity + chunk position."""
        return f"{self.source_type}:{self.source_id}:{self.chunk_index}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.chunk_text,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "symbol": self.symbol,
            "score": round(self.score, 4),
        }


class RAGService:
    """
    Hybrid search service combining vector similarity and keyword matching.

    Search strategy:
    1. Vector similarity search (pgvector cosine distance)
    2. Keyword search (pg_trgm trigram similarity)
    3. Reciprocal Rank Fusion (RRF) to combine results
    """

    async def search(
        self,
        db: AsyncSession,
        query_embedding: List[float],
        query_text: str,
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 5,
        vector_weight: float = 0.7,
    ) -> List[SearchResult]:
        """
        Hybrid search combining vector similarity and keyword matching.

        Args:
            db: Database session
            query_embedding: Embedding vector for the query
            query_text: Raw query text for keyword matching
            symbol: Optional filter by stock symbol
            source_type: Optional filter by source type
            top_k: Number of results to return
            vector_weight: Weight for vector results in RRF (keyword weight = 1 - vector_weight)

        Returns:
            List of SearchResults ordered by combined relevance
        """
        # Run both searches, fetching 2x candidates to give RRF more to work with
        vector_results = await self._vector_search(
            db, query_embedding, symbol, source_type, top_k=top_k * 2
        )
        keyword_results = await self._keyword_search(
            db, query_text, symbol, source_type, top_k=top_k * 2
        )

        # Combine with Reciprocal Rank Fusion
        combined = self._rrf_combine(
            vector_results, keyword_results, vector_weight, top_k
        )

        logger.info(
            "Hybrid search: %d vector + %d keyword -> %d combined results",
            len(vector_results),
            len(keyword_results),
            len(combined),
        )
        return combined

    async def vector_search_only(
        self,
        db: AsyncSession,
        query_embedding: List[float],
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 5,
    ) -> List[SearchResult]:
        """Pure vector similarity search."""
        return await self._vector_search(
            db, query_embedding, symbol, source_type, top_k
        )

    async def _vector_search(
        self,
        db: AsyncSession,
        query_embedding: List[float],
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        """
        Vector similarity search using pgvector cosine distance.

        Uses the <=> operator (cosine distance) and converts to similarity
        via 1 - distance. Results are ordered by ascending distance (most
        similar first).
        """
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
                   chunk_index,
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
            )
            for row in rows
        ]

    async def _keyword_search(
        self,
        db: AsyncSession,
        query_text: str,
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        """
        Keyword search using pg_trgm trigram similarity.

        Requires the pg_trgm extension (initialized in database.init_db).
        Falls back gracefully if the extension is unavailable or the query
        produces no matches above the 0.1 similarity threshold.
        """
        conditions = ["similarity(chunk_text, :query) > 0.1"]
        params: Dict[str, Any] = {"query": query_text, "limit": top_k}

        if symbol:
            conditions.append("symbol = :symbol")
            params["symbol"] = symbol
        if source_type:
            conditions.append("source_type = :source_type")
            params["source_type"] = source_type

        where_clause = "WHERE " + " AND ".join(conditions)

        query = text(f"""
            SELECT id, chunk_text, source_type, source_id, symbol,
                   chunk_index,
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
            )
            for row in rows
        ]

    def _rrf_combine(
        self,
        vector_results: List[SearchResult],
        keyword_results: List[SearchResult],
        vector_weight: float,
        top_k: int,
    ) -> List[SearchResult]:
        """
        Reciprocal Rank Fusion to combine vector and keyword results.

        RRF score = weight / (k + rank), where k=60 is a smoothing constant
        that prevents top-ranked items from dominating excessively.

        References:
            Cormack, Clarke, Buettcher (2009) - Reciprocal Rank Fusion
        """
        k = 60  # RRF smoothing constant
        scores: Dict[str, float] = {}
        result_map: Dict[str, SearchResult] = {}
        keyword_weight = 1.0 - vector_weight

        # Score vector results by rank position
        for rank, result in enumerate(vector_results):
            key = result.dedup_key
            rrf_score = vector_weight / (k + rank + 1)
            scores[key] = scores.get(key, 0.0) + rrf_score
            result_map[key] = result

        # Score keyword results by rank position
        for rank, result in enumerate(keyword_results):
            key = result.dedup_key
            rrf_score = keyword_weight / (k + rank + 1)
            scores[key] = scores.get(key, 0.0) + rrf_score
            if key not in result_map:
                result_map[key] = result

        # Sort by combined RRF score descending
        sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)

        results: List[SearchResult] = []
        for key in sorted_keys[:top_k]:
            result = result_map[key]
            result.score = scores[key]
            results.append(result)

        return results

    async def store_embedding(
        self,
        db: AsyncSession,
        source_type: str,
        source_id: str,
        chunk_text: str,
        embedding: List[float],
        symbol: Optional[str] = None,
        chunk_index: int = 0,
        token_count: Optional[int] = None,
    ) -> DocumentEmbedding:
        """Store a document embedding in the database."""
        doc = DocumentEmbedding(
            source_type=source_type,
            source_id=source_id,
            symbol=symbol,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            embedding=embedding,
            model=settings.OPENAI_EMBEDDING_MODEL,
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

    async def delete_embeddings(
        self,
        db: AsyncSession,
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


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    """Get singleton RAGService instance."""
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
