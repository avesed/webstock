"""Add score_details JSONB to news table.

Stores per-dimension scoring breakdown (information_value, investment_relevance,
completeness, scarcity), reasoning text, and critical event flag alongside the
existing integer content_score column.

Revision ID: 021_add_score_details
Revises: 020_add_phase2_cfg
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "021_add_score_details"
down_revision = "020_add_phase2_cfg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "news",
        sa.Column(
            "score_details",
            JSONB,
            nullable=True,
            comment="Dimension scores, reasoning, and critical flag from scoring LLM",
        ),
    )


def downgrade() -> None:
    op.drop_column("news", "score_details")
