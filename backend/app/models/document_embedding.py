"""Document embedding model for RAG (Retrieval Augmented Generation)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Hardcoded: changing dimensions requires a DB migration + full re-embedding
EMBEDDING_DIMENSIONS = 1536


class DocumentEmbedding(Base):
    """
    Stores document embeddings for vector similarity search.

    Each row represents a chunk of text from an analysis report, news article,
    or other document, along with its vector embedding for retrieval.
    """

    __tablename__ = "document_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Source document reference
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Type of source: 'analysis', 'news', 'report'",
    )

    source_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="ID of the source document",
    )

    # Associated stock (optional, for filtering)
    symbol: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )

    # Text content
    chunk_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Index of this chunk within the source document",
    )

    # Vector embedding
    embedding: Mapped[list] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS),
        nullable=False,
    )

    # Metadata
    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="unknown",
    )

    token_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # HNSW index for vector similarity search
    __table_args__ = (
        Index(
            "ix_document_embeddings_embedding_hnsw",
            embedding,
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_document_embeddings_source",
            source_type,
            source_id,
        ),
        # GIN trigram index for keyword search via pg_trgm similarity()
        Index(
            "ix_document_embeddings_chunk_text_trgm",
            chunk_text,
            postgresql_using="gin",
            postgresql_ops={"chunk_text": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentEmbedding(id={self.id}, source_type={self.source_type}, "
            f"symbol={self.symbol}, chunk_index={self.chunk_index})>"
        )
