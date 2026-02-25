"""Add composite indexes, GIN index, and URL unique constraint on news table.

Revision ID: 029_news_indexes
Revises: 028_stock_symbols
Create Date: 2026-02-25

Adds:
  - Composite index (market, content_status, published_at DESC) for /news/market queries
  - Convert related_entities from JSON to JSONB, then add GIN index for @> queries
  - UNIQUE constraint on url (after deduplicating existing rows)

Uses regular CREATE INDEX (not CONCURRENTLY) since migrations run at startup
against an otherwise-idle DB.  Each op.execute() is a single statement
(asyncpg requirement).
"""

from alembic import op

revision = "029_news_indexes"
down_revision = "028_stock_symbols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Composite index for market news queries
    #    Covers: WHERE market=X AND content_status=Y ORDER BY published_at DESC
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_news_market_status_published "
        "ON news (market, content_status, published_at DESC)"
    )

    # 2. Convert related_entities from JSON to JSONB (GIN requires JSONB)
    op.execute(
        "ALTER TABLE news "
        "ALTER COLUMN related_entities TYPE jsonb USING related_entities::jsonb"
    )

    # 3. GIN index on related_entities JSONB for containment queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_news_related_entities_gin "
        "ON news USING gin (related_entities)"
    )

    # 4. URL unique constraint
    #    First, deduplicate: keep the newest row per URL, delete older duplicates
    op.execute(
        "DELETE FROM news WHERE id IN ("
        "  SELECT id FROM ("
        "    SELECT id, ROW_NUMBER() OVER ("
        "      PARTITION BY url ORDER BY created_at DESC"
        "    ) AS rn FROM news"
        "  ) ranked WHERE rn > 1"
        ")"
    )

    # Now add unique index
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_news_url_unique "
        "ON news (url)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_news_url_unique")

    op.execute("DROP INDEX IF EXISTS ix_news_related_entities_gin")

    # Revert JSONB back to JSON
    op.execute(
        "ALTER TABLE news "
        "ALTER COLUMN related_entities TYPE json USING related_entities::json"
    )

    op.execute("DROP INDEX IF EXISTS ix_news_market_status_published")
