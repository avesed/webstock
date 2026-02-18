"""Add (market, symbol) index to stock_daily_bars for fast symbol counting.

Revision ID: 025_daily_bars_market_sym_idx
Revises: 024_stock_daily_bars
Create Date: 2026-02-17

The admin /knowledge-base/stats endpoint runs COUNT(DISTINCT symbol) GROUP BY
market on stock_daily_bars. Without a covering index this requires an external
merge sort of all 8M+ rows (~17s). The new index makes it an index-only scan
(~1-5ms).

CONCURRENTLY cannot be used inside a transaction, so we use op.execute() with
the standard CREATE INDEX and rely on the migration running against an
otherwise-idle DB during startup.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "025_daily_bars_market_sym_idx"
down_revision: Union[str, None] = "024_stock_daily_bars"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_daily_bars_market_symbol "
        "ON stock_daily_bars (market, symbol)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_daily_bars_market_symbol")
