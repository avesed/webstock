"""Create stock_daily_bars table for persistent daily OHLCV data.

Revision ID: 024_stock_daily_bars
Revises: 023_news_title_idx
Create Date: 2026-02-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "024_stock_daily_bars"
down_revision: Union[str, None] = "023_news_title_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_daily_bars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "symbol", sa.String(20), nullable=False,
            comment="WebStock format: AAPL, 0700.HK, 600000.SS",
        ),
        sa.Column(
            "market", sa.String(10), nullable=False,
            comment="us, hk, cn, metal",
        ),
        sa.Column("date", sa.Date(), nullable=False, comment="Trading date"),
        sa.Column("open", sa.Numeric(18, 8), nullable=False),
        sa.Column("high", sa.Numeric(18, 8), nullable=False),
        sa.Column("low", sa.Numeric(18, 8), nullable=False),
        sa.Column("close", sa.Numeric(18, 8), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "data_source", sa.String(20), nullable=True,
            comment="yfinance, akshare, etc.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date", name="uq_daily_bars_symbol_date"),
    )

    op.create_index(
        "ix_daily_bars_market_date",
        "stock_daily_bars",
        ["market", "date"],
    )

    # NOTE: No separate (symbol, date) index -- the unique constraint
    # uq_daily_bars_symbol_date already provides one.


def downgrade() -> None:
    op.drop_index("ix_daily_bars_market_date", table_name="stock_daily_bars")
    op.drop_table("stock_daily_bars")
