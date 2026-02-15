"""Protocols and shared types for the RAG subsystem."""

from __future__ import annotations

import typing
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

if typing.TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class SearchResult:
    """A single search result with relevance score."""

    chunk_text: str
    source_type: str
    source_id: str
    symbol: Optional[str]
    score: float
    chunk_index: int = 0
    created_at: Optional[datetime] = None
    model: Optional[str] = None

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


@runtime_checkable
class TextChunker(Protocol):
    """Split text into chunks suitable for embedding."""
    def chunk(self, text: str, *, max_chars: int = 1500, overlap_chars: int = 150) -> List[str]: ...


@runtime_checkable
class Embedder(Protocol):
    """Generate vector embeddings for text."""
    async def embed_one(self, text: str, *, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> Optional[List[float]]: ...
    async def embed_batch(self, texts: List[str], *, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> List[Optional[List[float]]]: ...


@runtime_checkable
class SearchBackend(Protocol):
    """Execute a single search strategy."""
    async def search(self, db: "AsyncSession", *, query_embedding: Optional[List[float]] = None, query_text: Optional[str] = None, symbol: Optional[str] = None, source_type: Optional[str] = None, top_k: int = 10) -> List[SearchResult]: ...


@runtime_checkable
class SearchPostProcessor(Protocol):
    """Transform/re-rank search results. Composes into a pipeline."""
    def process(self, results: List[SearchResult], *, top_k: int = 5) -> List[SearchResult]: ...


@runtime_checkable
class EmbeddingStore(Protocol):
    """Persist and delete document embeddings."""
    async def store(self, db: "AsyncSession", *, source_type: str, source_id: str, chunk_text: str, embedding: List[float], symbol: Optional[str] = None, chunk_index: int = 0, token_count: Optional[int] = None, model: Optional[str] = None) -> Any: ...
    async def delete(self, db: "AsyncSession", *, source_type: str, source_id: str) -> int: ...
