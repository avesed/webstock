"""SQLAlchemy model for Analysis Session persistence."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class AnalysisSession(Base):
    """A persisted AI analysis session for a stock symbol."""

    __tablename__ = "analysis_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    language: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="zh",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="running",
    )

    agent_results: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
    )

    synthesis_content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    clarification_rounds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    total_latency_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<AnalysisSession(id={self.id}, symbol={self.symbol!r}, "
            f"status={self.status!r})>"
        )
