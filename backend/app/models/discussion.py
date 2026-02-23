"""SQLAlchemy models for Discussion Group sessions and messages."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.chat import Conversation
    from app.models.user import User


class DiscussionSession(Base):
    """A multi-agent discussion group session for a stock symbol."""

    __tablename__ = "discussion_sessions"

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
        default="pending",
    )

    config: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    synthesis_report: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    compact_context: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    discussion_rounds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    max_rounds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
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

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    messages: Mapped[List["DiscussionMessage"]] = relationship(
        "DiscussionMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="DiscussionMessage.created_at",
        lazy="noload",
    )

    user: Mapped["User"] = relationship("User", lazy="joined")

    conversation: Mapped[Optional["Conversation"]] = relationship(
        "Conversation",
        back_populates="discussion_session",
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DiscussionSession(id={self.id}, symbol={self.symbol!r}, "
            f"status={self.status!r}, rounds={self.discussion_rounds})>"
        )


class DiscussionMessage(Base):
    """A single message within a discussion session (agent statement, moderator guidance, etc.)."""

    __tablename__ = "discussion_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("discussion_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    round: Mapped[int] = mapped_column(
        "round",  # Explicitly quoted — "round" is a PostgreSQL reserved word
        Integer,
        nullable=False,
        default=0,
    )

    agent_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    structured_data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    tool_calls: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
    )

    token_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    latency_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    session: Mapped["DiscussionSession"] = relationship(
        "DiscussionSession",
        back_populates="messages",
    )

    def __repr__(self) -> str:
        return (
            f"<DiscussionMessage(id={self.id}, round={self.round}, "
            f"agent_type={self.agent_type!r})>"
        )
