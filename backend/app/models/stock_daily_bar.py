"""StockDailyBar SQLAlchemy model for persistent daily OHLCV data."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class StockDailyBar(Base):
    """Persistent daily OHLCV bar for all markets.

    Serves as the single source of truth for daily price data.
    Used by:
    - Internal API for qlib-service data sync
    - Future features (portfolio analytics, backtesting, etc.)
    """

    __tablename__ = "stock_daily_bars"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    symbol: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="WebStock format: AAPL, 0700.HK, 600000.SS"
    )

    market: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="us, hk, cn, metal"
    )

    date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="Trading date"
    )

    open: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
    )

    high: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
    )

    low: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
    )

    close: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
    )

    volume: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    data_source: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="yfinance, akshare, etc."
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_daily_bars_symbol_date"),
        Index("ix_daily_bars_market_date", "market", "date"),
        # NOTE: No separate (symbol, date) index -- the unique constraint
        # already creates one.  A duplicate wastes disk and slows writes.
    )

    def __repr__(self) -> str:
        return f"<StockDailyBar(symbol={self.symbol}, date={self.date}, close={self.close})>"
