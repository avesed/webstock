"""Add pipeline_events table for news processing tracing.

Stores per-node execution events across all 3 pipeline layers
to enable observability, debugging, and performance analysis.

Revision ID: 014_add_pipeline_events
Revises: 013_remove_news_llm_config
Create Date: 2026-02-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "014_add_pipeline_events"
down_revision: Union[str, None] = "013_remove_news_llm_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_events",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("news_id", UUID(as_uuid=False), nullable=False),
        sa.Column("layer", sa.String(10), nullable=False),
        sa.Column("node", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("duration_ms", sa.Float, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_pipeline_events_news_id_created",
        "pipeline_events",
        ["news_id", "created_at"],
    )

    op.create_index(
        "ix_pipeline_events_layer_node_created",
        "pipeline_events",
        ["layer", "node", "created_at"],
    )

    op.create_index(
        "ix_pipeline_events_created_at",
        "pipeline_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("pipeline_events")
