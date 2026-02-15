"""DEPRECATED: Use app.services.rag instead.

This module re-exports symbols for backward compatibility.
All new code should import from app.services.rag.
"""

import warnings
from typing import Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag import get_index_service
from app.services.rag.protocols import SearchResult

# Re-export SearchResult for backward compatibility
__all__ = [
    "SearchResult",
    "get_rag_service",
    "RAGService",
]


class RAGService:
    """Backward-compatible wrapper around IndexService.

    DEPRECATED: Use get_index_service() from app.services.rag instead.
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
        embedding_model: Optional[str] = None,
    ) -> List[SearchResult]:
        return await get_index_service().search(
            db, query_embedding, query_text,
            symbol=symbol, source_type=source_type, top_k=top_k,
            vector_weight=vector_weight, embedding_model=embedding_model,
        )

    async def vector_search_only(
        self,
        db: AsyncSession,
        query_embedding: List[float],
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 5,
    ) -> List[SearchResult]:
        return await get_index_service().vector_search_only(
            db, query_embedding, symbol=symbol,
            source_type=source_type, top_k=top_k,
        )

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
        model: Optional[str] = None,
    ) -> Any:
        return await get_index_service().store_embedding(
            db, source_type=source_type, source_id=source_id,
            chunk_text=chunk_text, embedding=embedding, symbol=symbol,
            chunk_index=chunk_index, token_count=token_count, model=model,
        )

    async def delete_embeddings(
        self,
        db: AsyncSession,
        source_type: str,
        source_id: str,
    ) -> int:
        return await get_index_service().delete_embeddings(
            db, source_type, source_id,
        )


# Singleton (deprecated)
_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    """Get singleton RAGService instance.

    DEPRECATED: Use get_index_service() from app.services.rag instead.
    """
    global _rag_service
    warnings.warn(
        "get_rag_service() is deprecated. "
        "Use get_index_service() from app.services.rag instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
