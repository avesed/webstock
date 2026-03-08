"""Add stock_sectors table for industry classification cache.

Stores GICS sector/industry (US/HK via yfinance) and Chinese industry
classification (CN via akshare).  Used for sector-neutral label construction
and sector-adjusted feature ranking in the ML prediction pipeline.

Revision ID: 036_add_stock_sectors
Revises: 035_add_ml_signal_tables
Create Date: 2026-03-02
"""

from typing import Sequence, Union

from alembic import op

revision: str = "036_add_stock_sectors"
down_revision: Union[str, None] = "035_add_ml_signal_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_sectors (
            id BIGSERIAL PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            market VARCHAR(10) NOT NULL,
            sector VARCHAR(100),
            industry VARCHAR(200),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(symbol, market)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sectors_market
        ON stock_sectors(market)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sectors_market")
    op.execute("DROP TABLE IF EXISTS stock_sectors")
