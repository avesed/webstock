"""Add stock_symbols table.

Revision ID: 028_stock_symbols
Revises: 027_analysis_sessions
Create Date: 2026-02-24

Migrates stock list storage from msgpack files to PostgreSQL.
Data-service writes directly to this table; backend reads into memory.
"""

from alembic import op
import sqlalchemy as sa

revision = "028_stock_symbols"
down_revision = "027_analysis_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE stock_symbols (
            symbol         VARCHAR(20)  PRIMARY KEY,
            name           VARCHAR(200) NOT NULL DEFAULT '',
            name_zh        VARCHAR(200) NOT NULL DEFAULT '',
            exchange       VARCHAR(20)  NOT NULL DEFAULT '',
            market         VARCHAR(10)  NOT NULL,
            pinyin         VARCHAR(100) NOT NULL DEFAULT '',
            pinyin_initial VARCHAR(20)  NOT NULL DEFAULT '',
            updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_stock_symbols_market ON stock_symbols (market)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stock_symbols")
