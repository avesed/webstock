"""LLM cost tracking models — pricing configuration and usage records."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date, DateTime, ForeignKey, Integer, Numeric, String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ModelPricing(Base):
    """
    Time-effective pricing for LLM models.

    Each row defines pricing for a model starting from `effective_from`.
    To find the active price for a model on a given date, query:
        WHERE model = ? AND effective_from <= ?
        ORDER BY effective_from DESC LIMIT 1

    When cached_input_price is NULL, the input_price is used for cached
    tokens as well (i.e. no cache discount).
    """

    __tablename__ = "model_pricing"
    __table_args__ = (
        UniqueConstraint("model", "effective_from",
                         name="uq_model_pricing_model_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    model: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Model name, e.g. 'gpt-4o-mini', 'claude-3-5-sonnet'",
    )
    input_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False, default=0,
        comment="Cost per 1M input tokens (USD)",
    )
    cached_input_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 8), nullable=True,
        comment="Cost per 1M cached input tokens (USD); NULL = same as input",
    )
    output_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False, default=0,
        comment="Cost per 1M output tokens (USD)",
    )
    effective_from: Mapped[date] = mapped_column(
        Date(), nullable=False,
        comment="Date this pricing takes effect",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ModelPricing(model={self.model}, "
            f"in={self.input_price}, out={self.output_price}, "
            f"from={self.effective_from})>"
        )


class LlmUsageRecord(Base):
    """
    Permanent record of a single LLM API call with token counts and cost.

    Cost is calculated at insert time using the active ModelPricing for the
    model. This ensures historical records retain accurate costs even after
    pricing changes.
    """

    __tablename__ = "llm_usage_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    model: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Model name used for this call",
    )
    purpose: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Call purpose: chat, analysis, layer1_scoring, etc.",
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who triggered this call (NULL for system/pipeline)",
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    cached_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Cache-hit input tokens (discounted rate)",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=0,
        comment="Calculated cost in USD at insert time",
    )
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata_", JSONB, nullable=True,
        comment="Context: conversation_id, news_id, agent name, etc.",
    )
    pricing_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_pricing.id", ondelete="SET NULL"),
        nullable=True,
        comment="Pricing row used for cost calculation (audit trail)",
    )

    def __repr__(self) -> str:
        return (
            f"<LlmUsageRecord(model={self.model}, purpose={self.purpose}, "
            f"tokens={self.total_tokens}, cost=${self.cost_usd})>"
        )
