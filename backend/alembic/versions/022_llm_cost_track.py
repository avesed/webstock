"""Add model_pricing and llm_usage_records tables for LLM cost tracking.

Enables configurable per-model pricing with time-effective history and
permanent recording of all LLM usage across the platform (chat, analysis,
news pipeline, embeddings, reports).

Revision ID: 022_llm_cost_track
Revises: 021_layer_scoring_cfg
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = "022_llm_cost_track"
down_revision: Union[str, None] = "021_layer_scoring_cfg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create model_pricing and llm_usage_records tables."""

    # ---- model_pricing ----
    op.create_table(
        "model_pricing",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_price", sa.Numeric(12, 8), nullable=False,
                  server_default="0"),
        sa.Column("cached_input_price", sa.Numeric(12, 8), nullable=True),
        sa.Column("output_price", sa.Numeric(12, 8), nullable=False,
                  server_default="0"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("model", "effective_from",
                            name="uq_model_pricing_model_date"),
    )

    op.create_index(
        "ix_model_pricing_lookup",
        "model_pricing",
        ["model", sa.text("effective_from DESC")],
    )

    # ---- llm_usage_records ----
    op.create_table(
        "llm_usage_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False,
                  server_default="0"),
        sa.Column("metadata_", JSONB, nullable=True),
        sa.Column("pricing_id", UUID(as_uuid=True),
                  sa.ForeignKey("model_pricing.id", ondelete="SET NULL"),
                  nullable=True),
    )

    op.create_index("ix_usage_created", "llm_usage_records", ["created_at"])
    op.create_index("ix_usage_purpose", "llm_usage_records",
                    ["purpose", "created_at"])
    op.create_index("ix_usage_model", "llm_usage_records",
                    ["model", "created_at"])

    # Partial index: only rows with a user
    op.execute(
        "CREATE INDEX ix_usage_user ON llm_usage_records "
        "(user_id, created_at) WHERE user_id IS NOT NULL"
    )

    # No seeded pricing — admin configures via the Cost Tracking tab


def downgrade() -> None:
    """Drop cost tracking tables."""
    op.execute("DROP INDEX IF EXISTS ix_usage_user")
    op.drop_index("ix_usage_model", table_name="llm_usage_records")
    op.drop_index("ix_usage_purpose", table_name="llm_usage_records")
    op.drop_index("ix_usage_created", table_name="llm_usage_records")
    op.drop_table("llm_usage_records")
    op.drop_index("ix_model_pricing_lookup", table_name="model_pricing")
    op.drop_table("model_pricing")
