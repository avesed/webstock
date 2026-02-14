"""Pipeline event SQLAlchemy model for tracing news processing pipeline."""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PipelineEvent(Base):
    """
    Traces individual node executions across the news processing pipeline.

    Records timing, status, and metadata for each step in the 3-layer
    pipeline (Layer 1: discovery, Layer 1.5: fetch, Layer 2: LangGraph).
    """

    __tablename__ = "pipeline_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    news_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        comment="Associated news article ID (no FK for decoupling)",
    )

    layer: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="Pipeline layer: 1, 1.5, 2",
    )

    node: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Node name: initial_filter, fetch, read_file, deep_filter, embed, update_db",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Execution status: success, error, skip",
    )

    duration_ms: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="Execution duration in milliseconds",
    )

    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="Additional context (filter decisions, token counts, etc.)",
    )

    error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if status is error",
    )

    cache_metadata: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Prompt cache hit rate and token statistics",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_pipeline_events_news_id_created", "news_id", "created_at"),
        Index("ix_pipeline_events_layer_node_created", "layer", "node", "created_at"),
        Index("ix_pipeline_events_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<PipelineEvent(id={self.id}, news_id={self.news_id}, "
            f"layer={self.layer}, node={self.node}, status={self.status})>"
        )
