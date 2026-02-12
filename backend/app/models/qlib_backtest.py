"""Qlib backtest SQLAlchemy model for quantitative backtesting."""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class BacktestStatus(str, Enum):
    """Backtest execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QlibBacktest(Base):
    """
    Stores backtest configurations and results from the Qlib
    quantitative analysis service.

    Tracks strategy parameters, execution config, progress,
    and final results including equity curves and risk metrics.
    """

    __tablename__ = "qlib_backtests"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="Target market: US, HK, CN, etc.",
    )

    symbols: Mapped[Optional[list]] = mapped_column(
        ARRAY(Text),
        nullable=True,
        comment="Stock pool for backtesting",
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    strategy_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Strategy type: topk, signal, long_short",
    )

    strategy_config: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Strategy-specific parameters",
    )

    execution_config: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Execution parameters: slippage, commission, limit_threshold",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=BacktestStatus.PENDING.value,
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Execution progress 0-100",
    )

    qlib_task_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Task ID from the qlib-service",
    )

    results: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Equity curve and risk metrics",
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

    def __repr__(self) -> str:
        return (
            f"<QlibBacktest(id={self.id}, name={self.name}, "
            f"status={self.status}, market={self.market})>"
        )
