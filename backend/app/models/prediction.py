"""SQLAlchemy models for prediction infrastructure.

Tables:
- prediction_universes: Stock pool definitions (CSI300, S&P500, HSI, custom)
- prediction_models: Trained model metadata and metrics
- stock_predictions: Per-symbol prediction scores and rankings
- stock_fundamentals: Financial fundamental data for factor construction
- discovered_factors: LLM-discovered alpha factor expressions
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PredictionUniverse(Base):
    """Stock universe definition for prediction pipelines.

    Defines a pool of symbols to predict on, either by index membership
    (e.g. CSI300, S&P500) or by explicit symbol list.
    """

    __tablename__ = "prediction_universes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("uuid_generate_v4()"),
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Universe name, e.g. CSI300, S&P500",
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="Target market: us, hk, cn",
    )

    universe_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Type: index, custom",
    )

    index_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Index code for index-based universes, e.g. 000300, SPX",
    )

    symbols: Mapped[Optional[list]] = mapped_column(
        ARRAY(Text),
        nullable=True,
        comment="Explicit symbol list for custom universes",
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether this is the default universe for its market",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("name", "market", name="uq_prediction_universes_name_market"),
    )

    def __repr__(self) -> str:
        return (
            f"<PredictionUniverse(id={self.id}, name={self.name!r}, "
            f"market={self.market!r}, type={self.universe_type!r})>"
        )


class PredictionModel(Base):
    """Trained prediction model metadata and evaluation metrics.

    Stores information about each model training run, including
    date ranges, feature configuration, and IC/ICIR/NDCG metrics.
    """

    __tablename__ = "prediction_models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("uuid_generate_v4()"),
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="Target market: us, hk, cn",
    )

    model_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Date the model was trained for",
    )

    train_start: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )

    train_end: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )

    val_start: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )

    val_end: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )

    forward_days: Mapped[Optional[int]] = mapped_column(
        Integer,
        default=5,
        comment="Prediction horizon in trading days",
    )

    feature_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    symbol_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    feature_sources: Mapped[Optional[list]] = mapped_column(
        ARRAY(Text),
        default=lambda: ["alpha158"],
        server_default="{alpha158}",
        comment="Feature set names used for training",
    )

    ic: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 6),
        nullable=True,
        comment="Information Coefficient",
    )

    icir: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 6),
        nullable=True,
        comment="IC Information Ratio",
    )

    ndcg: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 6),
        nullable=True,
        comment="Normalized Discounted Cumulative Gain",
    )

    model_path: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="File system path to serialized model",
    )

    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="Additional training metadata",
    )

    quality_passed: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        default=True,
        server_default=sa.text("true"),
        comment="Whether model passed quality gate (IC/ICIR thresholds)",
    )

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "market", "model_date", "forward_days",
            name="uq_prediction_models_market_date_fwd",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PredictionModel(id={self.id}, market={self.market!r}, "
            f"date={self.model_date}, ic={self.ic})>"
        )


class StockPrediction(Base):
    """Per-symbol prediction score for a given date and model.

    Contains the predicted score, percentile rank, direction,
    and (optionally) the realized actual return for backtesting.
    """

    __tablename__ = "stock_predictions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("uuid_generate_v4()"),
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    prediction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    model_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prediction_models.id", ondelete="SET NULL"),
        nullable=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    predicted_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 8),
        nullable=True,
        comment="Raw model prediction score",
    )

    percentile_rank: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="Cross-sectional percentile rank (0-1)",
    )

    predicted_direction: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        comment="up, down, or sideways",
    )

    actual_return: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 8),
        nullable=True,
        comment="Realized return over forward_days (filled after the fact)",
    )

    forward_days: Mapped[Optional[int]] = mapped_column(
        Integer,
        default=5,
    )

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "market", "prediction_date", "symbol", "forward_days",
            name="uq_stock_predictions_market_date_sym_fwd",
        ),
        Index("ix_pred_market_date", "market", sa.text("prediction_date DESC")),
    )

    def __repr__(self) -> str:
        return (
            f"<StockPrediction(symbol={self.symbol!r}, "
            f"date={self.prediction_date}, score={self.predicted_score})>"
        )


class StockFundamental(Base):
    """Financial fundamental data for factor construction.

    Stores valuation ratios, profitability metrics, and growth data
    per symbol per date. record_type distinguishes quarterly vs TTM.
    """

    __tablename__ = "stock_fundamentals"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(20), nullable=False
    )

    market: Mapped[str] = mapped_column(
        String(10), nullable=False
    )

    date: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    record_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="quarterly, ttm, annual, daily_snapshot",
    )

    pe_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    pb_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    ps_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    roe: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    roa: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    profit_margin: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    gross_margin: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    revenue: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 2), nullable=True
    )

    revenue_growth_yoy: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    net_income: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 2), nullable=True
    )

    eps: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    debt_to_equity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    current_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    dividend_yield: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    market_cap: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 2), nullable=True
    )

    forward_pe: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    dividend_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    book_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )

    operating_margin: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    payout_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    eps_growth: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), nullable=True
    )

    data_source: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "symbol", "date", "record_type",
            name="uq_stock_fundamentals_sym_date_type",
        ),
        Index("ix_fund_symbol_date", "symbol", sa.text("date DESC")),
        Index("ix_fund_market_date", "market", sa.text("date DESC")),
    )

    def __repr__(self) -> str:
        return (
            f"<StockFundamental(symbol={self.symbol!r}, "
            f"date={self.date}, type={self.record_type!r})>"
        )


class DiscoveredFactor(Base):
    """LLM-discovered alpha factor expression.

    Stores factor expressions discovered by the RD-Agent pipeline,
    along with their IC/ICIR metrics and universe context.
    """

    __tablename__ = "discovered_factors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("uuid_generate_v4()"),
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    expression: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Qlib-compatible factor expression",
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    universe_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prediction_universes.id", ondelete="SET NULL"),
        nullable=True,
    )

    ic: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 6),
        nullable=True,
        comment="Information Coefficient",
    )

    icir: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 6),
        nullable=True,
        comment="IC Information Ratio",
    )

    discovery_round: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Which RD-Agent iteration discovered this factor",
    )

    is_active: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        default=True,
    )

    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="Additional factor metadata (hypothesis, code, etc.)",
    )

    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "expression", "market",
            name="uq_discovered_factors_expr_market",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DiscoveredFactor(id={self.id}, name={self.name!r}, "
            f"market={self.market!r}, ic={self.ic})>"
        )
