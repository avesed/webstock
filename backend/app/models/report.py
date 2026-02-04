"""Report Schedule and Report SQLAlchemy models."""

from datetime import datetime, time, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ReportFrequency(str, Enum):
    """Report frequency enumeration."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReportFormat(str, Enum):
    """Report format enumeration."""

    JSON = "json"
    HTML = "html"


class ReportStatus(str, Enum):
    """Report status enumeration."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportSchedule(Base):
    """Report schedule model for scheduled report generation."""

    __tablename__ = "report_schedules"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    frequency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Time of day to generate (stored as UTC)
    time_of_day: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )

    # Day of week for weekly schedules (0=Monday, 6=Sunday)
    day_of_week: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Day of month for monthly schedules (1-31)
    day_of_month: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Stock symbols to include (empty list means all watchlist)
    symbols: Mapped[list] = mapped_column(
        ARRAY(String(20)),
        nullable=False,
        default=list,
    )

    include_portfolio: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    include_news: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
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

    # Relationships
    reports: Mapped[list["Report"]] = relationship(
        "Report",
        back_populates="schedule",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<ReportSchedule(id={self.id}, name={self.name}, "
            f"frequency={self.frequency})>"
        )


class Report(Base):
    """Generated report model."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    schedule_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("report_schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Structured report content
    content: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )

    format: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReportFormat.JSON.value,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReportStatus.PENDING.value,
    )

    error_message: Mapped[Optional[str]] = mapped_column(
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
    schedule: Mapped[Optional["ReportSchedule"]] = relationship(
        "ReportSchedule",
        back_populates="reports",
    )

    def __repr__(self) -> str:
        return (
            f"<Report(id={self.id}, title={self.title}, "
            f"status={self.status})>"
        )
