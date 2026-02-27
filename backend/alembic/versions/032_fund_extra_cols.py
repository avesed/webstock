"""Add 6 extra columns to stock_fundamentals for DB-first financials reads.

These columns exist in the FinancialsData API response model but were missing
from the DB schema (data-processor collected them via yfinance but didn't
persist them). Adding them allows the financials endpoint to serve from DB
instead of calling live APIs for ~880 universe stocks.

Also widens record_type from VARCHAR(10) to VARCHAR(20) to accommodate
'daily_snapshot' (14 chars) which the fundamental_service inserts.

New columns: forward_pe, dividend_rate, book_value, operating_margin,
payout_ratio, eps_growth.

Revision ID: 032_fund_extra_cols
Revises: 031_prediction_infra
Create Date: 2026-02-27
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "032_fund_extra_cols"
down_revision: Union[str, None] = "031_prediction_infra"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen record_type and add 6 financial metric columns."""

    # Fix record_type: VARCHAR(10) is too narrow for 'daily_snapshot' (14 chars)
    op.execute(
        "ALTER TABLE stock_fundamentals "
        "ALTER COLUMN record_type TYPE VARCHAR(20)"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals "
        "ADD COLUMN IF NOT EXISTS forward_pe NUMERIC(12,4)"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals "
        "ADD COLUMN IF NOT EXISTS dividend_rate NUMERIC(12,4)"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals "
        "ADD COLUMN IF NOT EXISTS book_value NUMERIC(12,4)"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals "
        "ADD COLUMN IF NOT EXISTS operating_margin NUMERIC(8,4)"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals "
        "ADD COLUMN IF NOT EXISTS payout_ratio NUMERIC(8,4)"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals "
        "ADD COLUMN IF NOT EXISTS eps_growth NUMERIC(8,4)"
    )


def downgrade() -> None:
    """Remove the 6 extra columns."""

    op.execute(
        "ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS eps_growth"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS payout_ratio"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS operating_margin"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS book_value"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS dividend_rate"
    )

    op.execute(
        "ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS forward_pe"
    )
