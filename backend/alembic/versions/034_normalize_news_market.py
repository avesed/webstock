"""Normalize news market field to lowercase and backfill sentiment_score.

Step 1: Convert all market fields to lowercase for consistency.
Step 4: Backfill numeric sentiment_score from categorical sentiment_tag.

Revision ID: 034_normalize_news_market
Revises: 033_model_quality_gate
Create Date: 2026-03-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "034_normalize_news_market"
down_revision: Union[str, None] = "033_model_quality_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Normalize market fields to lowercase
    op.execute("UPDATE news SET market = LOWER(market) WHERE market != LOWER(market)")
    op.execute("UPDATE rss_feeds SET market = LOWER(market) WHERE market != LOWER(market)")

    # Step 4: Backfill sentiment_score from sentiment_tag
    op.execute(
        "UPDATE news SET sentiment_score = 0.7 "
        "WHERE sentiment_tag = 'bullish' AND sentiment_score IS NULL"
    )
    op.execute(
        "UPDATE news SET sentiment_score = -0.7 "
        "WHERE sentiment_tag = 'bearish' AND sentiment_score IS NULL"
    )
    op.execute(
        "UPDATE news SET sentiment_score = 0.0 "
        "WHERE sentiment_tag = 'neutral' AND sentiment_score IS NULL"
    )


def downgrade() -> None:
    # No-op: cannot distinguish migration-backfilled sentiment_score values
    # from pipeline-set values (L3 agents may produce 0.7, -0.7, 0.0 legitimately).
    # Lowercase market normalization is also intentionally preserved as the
    # correct canonical form.
    pass
