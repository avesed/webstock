"""DEPRECATED: Use app.services.rag instead.

This module re-exports symbols for backward compatibility.
All new code should import from app.services.rag.
"""

import warnings
from typing import List, Optional

from app.services.rag import get_index_service
from app.services.rag.embedding import (
    get_embedding_config_from_db,
    get_embedding_model_from_db,
)

# Re-export config helpers (these are the most commonly imported symbols)
__all__ = [
    "get_embedding_config_from_db",
    "get_embedding_model_from_db",
    "get_embedding_service",
    "EmbeddingService",
]


class EmbeddingService:
    """Backward-compatible wrapper around IndexService.

    DEPRECATED: Use get_index_service() from app.services.rag instead.
    """

    def chunk_text(
        self,
        text: str,
        max_chars: int = 1500,
        overlap_chars: int = 150,
    ) -> List[str]:
        return get_index_service().chunk_text(text, max_chars=max_chars, overlap_chars=overlap_chars)

    async def generate_embedding(
        self, text: str, model: Optional[str] = None,
        *, api_key: Optional[str] = None, base_url: Optional[str] = None,
    ) -> Optional[List[float]]:
        return await get_index_service().generate_embedding(
            text, model=model, api_key=api_key, base_url=base_url,
        )

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        model: Optional[str] = None,
        *, api_key: Optional[str] = None, base_url: Optional[str] = None,
    ) -> List[Optional[List[float]]]:
        return await get_index_service().generate_embeddings_batch(
            texts, model=model, api_key=api_key, base_url=base_url,
        )


# Singleton (deprecated)
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get singleton EmbeddingService instance.

    DEPRECATED: Use get_index_service() from app.services.rag instead.
    """
    global _embedding_service
    warnings.warn(
        "get_embedding_service() is deprecated. "
        "Use get_index_service() from app.services.rag instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
