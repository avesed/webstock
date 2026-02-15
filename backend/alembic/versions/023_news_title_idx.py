"""Add pg_trgm GIN index on news.title for keyword search.

Revision ID: 023_news_title_idx
Revises: 022_llm_cost_track
Create Date: 2026-02-14
"""

from typing import Sequence, Union

from alembic import op

revision: str = "023_news_title_idx"
down_revision: Union[str, None] = "022_llm_cost_track"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pg_trgm extension already enabled in migration 000
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_news_title_trgm "
        "ON news USING gin (title gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_news_title_trgm")
