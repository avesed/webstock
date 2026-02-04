"""Price Alert and Push Subscription SQLAlchemy models."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AlertConditionType(str, Enum):
    """Alert condition type enumeration."""

    ABOVE = "above"
    BELOW = "below"
    CHANGE_PERCENT = "change_percent"


class PriceAlert(Base):
    """Price alert model for user-defined stock price notifications."""

    __tablename__ = "price_alerts"

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

    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    condition_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    threshold: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=8),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_triggered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    triggered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Optional note for the alert
    note: Mapped[Optional[str]] = mapped_column(
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

    def __repr__(self) -> str:
        return (
            f"<PriceAlert(id={self.id}, symbol={self.symbol}, "
            f"condition={self.condition_type}, threshold={self.threshold})>"
        )


class PushSubscription(Base):
    """Web Push subscription model for browser push notifications."""

    __tablename__ = "push_subscriptions"

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

    # Push subscription endpoint URL
    endpoint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )

    # VAPID key for encryption
    p256dh_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Authentication secret
    auth_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # User agent info for debugging
    user_agent: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<PushSubscription(id={self.id}, user_id={self.user_id})>"
