"""RAG subsystem: modular chunking, embedding, search, and storage.

Public API:
    IndexService     -- high-level facade for ingest and search operations
    SearchResult     -- result dataclass
    get_index_service / reset_index_service -- singleton management
    get_embedding_config_from_db / get_embedding_model_from_db -- config helpers (re-export)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag.protocols import SearchResult, TextChunker, Embedder, SearchBackend, EmbeddingStore
from app.services.rag.chunking import LangChainRecursiveChunker
from app.services.rag.embedding import GatewayEmbedder, get_embedding_config_from_db, get_embedding_model_from_db
from app.services.rag.search import PgVectorSearch, PgTrigramSearch
from app.services.rag.postprocessing import RRFPostProcessor, FreshnessDecayPostProcessor, ModelMismatchWarner
from app.services.rag.storage import PgEmbeddingStore

logger = logging.getLogger(__name__)

# Re-export for consumer convenience
__all__ = [
    "IndexService",
    "SearchResult",
    "get_index_service",
    "reset_index_service",
    "get_embedding_config_from_db",
    "get_embedding_model_from_db",
]


class IndexService:
    """Facade composing all RAG components.

    Replaces the combined EmbeddingService + RAGService interface.
    Components are injected via constructor for testability and future swaps.
    """

    def __init__(
        self,
        chunker: TextChunker | None = None,
        embedder: Embedder | None = None,
        store: EmbeddingStore | None = None,
        search_backends: Dict[str, SearchBackend] | None = None,
    ):
        self.chunker = chunker or LangChainRecursiveChunker()
        self.embedder = embedder or GatewayEmbedder()
        self.store = store or PgEmbeddingStore()
        self.search_backends = search_backends or {
            "vector": PgVectorSearch(),
            "keyword": PgTrigramSearch(),
        }

    # --- Write path ---

    def chunk_text(self, text: str, max_chars: int = 1500, overlap_chars: int = 150) -> List[str]:
        return self.chunker.chunk(text, max_chars=max_chars, overlap_chars=overlap_chars)

    async def generate_embedding(self, text, *, model, api_key=None, base_url=None):
        return await self.embedder.embed_one(text, model=model, api_key=api_key, base_url=base_url)

    async def generate_embeddings_batch(self, texts, *, model, api_key=None, base_url=None):
        return await self.embedder.embed_batch(texts, model=model, api_key=api_key, base_url=base_url)

    async def store_embedding(self, db, *, source_type, source_id, chunk_text, embedding, symbol=None, chunk_index=0, token_count=None, model=None):
        return await self.store.store(db, source_type=source_type, source_id=source_id, chunk_text=chunk_text, embedding=embedding, symbol=symbol, chunk_index=chunk_index, token_count=token_count, model=model)

    async def delete_embeddings(self, db, source_type, source_id) -> int:
        return await self.store.delete(db, source_type=source_type, source_id=source_id)

    # --- Read path ---

    async def search(
        self,
        db: AsyncSession,
        query_embedding: List[float],
        query_text: str,
        *,
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 5,
        vector_weight: float = 0.7,
        embedding_model: Optional[str] = None,
    ) -> List[SearchResult]:
        """Hybrid search: run backends, fuse with RRF, apply post-processing."""
        # 1. Run each search backend (2x candidates for better RRF)
        ranked_lists: Dict[str, List[SearchResult]] = {}

        if query_embedding and "vector" in self.search_backends:
            ranked_lists["vector"] = await self.search_backends["vector"].search(
                db, query_embedding=query_embedding, symbol=symbol,
                source_type=source_type, top_k=top_k * 2,
            )

        if query_text and "keyword" in self.search_backends:
            ranked_lists["keyword"] = await self.search_backends["keyword"].search(
                db, query_text=query_text, symbol=symbol,
                source_type=source_type, top_k=top_k * 2,
            )

        if not ranked_lists:
            return []

        # 2. RRF fusion
        keyword_weight = 1.0 - vector_weight
        weights = {"vector": vector_weight, "keyword": keyword_weight}
        rrf = RRFPostProcessor(weights={k: weights.get(k, 0.5) for k in ranked_lists})
        combined = rrf.fuse(ranked_lists, top_k=top_k * 2)

        # 3. Post-processing pipeline
        pipeline = [
            FreshnessDecayPostProcessor(),
            ModelMismatchWarner(query_model=embedding_model),
        ]
        for processor in pipeline:
            combined = processor.process(combined, top_k=top_k)

        logger.info(
            "Hybrid search: %s -> %d combined (freshness applied)",
            {k: len(v) for k, v in ranked_lists.items()},
            len(combined),
        )
        return combined[:top_k]

    async def vector_search_only(
        self,
        db: AsyncSession,
        query_embedding: List[float],
        *,
        symbol: Optional[str] = None,
        source_type: Optional[str] = None,
        top_k: int = 5,
    ) -> List[SearchResult]:
        backend = self.search_backends.get("vector")
        if not backend:
            return []
        return await backend.search(
            db, query_embedding=query_embedding, symbol=symbol,
            source_type=source_type, top_k=top_k,
        )


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_index_service: Optional[IndexService] = None


def get_index_service() -> IndexService:
    """Get singleton IndexService instance."""
    global _index_service
    if _index_service is None:
        _index_service = IndexService()
    return _index_service


def reset_index_service() -> None:
    """Reset the singleton (for Celery worker lifecycle)."""
    global _index_service
    _index_service = None
