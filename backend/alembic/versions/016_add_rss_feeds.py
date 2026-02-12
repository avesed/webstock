"""Add rss_feeds table and news.rss_feed_id foreign key.

Introduces RSS feed management for RSSHub integration, allowing
admins to configure multiple RSS sources with independent polling
intervals, fulltext mode, and health tracking.

Revision ID: 016_add_rss_feeds
Revises: 015_add_qlib_backtests
Create Date: 2026-02-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "016_add_rss_feeds"
down_revision: Union[str, None] = "015_add_qlib_backtests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create rss_feeds table
    op.create_table(
        "rss_feeds",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("rsshub_route", sa.String(500), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "category",
            sa.String(20),
            nullable=False,
            server_default="media",
        ),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column(
            "market",
            sa.String(10),
            nullable=False,
            server_default="US",
        ),
        sa.Column(
            "poll_interval_minutes",
            sa.Integer,
            nullable=False,
            server_default="15",
        ),
        sa.Column(
            "fulltext_mode",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "last_polled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "consecutive_errors",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "article_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Add rss_feed_id column to news table
    op.add_column(
        "news",
        sa.Column(
            "rss_feed_id",
            UUID(as_uuid=False),
            sa.ForeignKey("rss_feeds.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Add index on news.rss_feed_id for efficient lookups
    op.create_index(
        "ix_news_rss_feed_id",
        "news",
        ["rss_feed_id"],
    )

    # Add updated_at trigger for rss_feeds
    op.execute(
        "DROP TRIGGER IF EXISTS update_rss_feeds_updated_at ON rss_feeds"
    )
    op.execute("""
        CREATE TRIGGER update_rss_feeds_updated_at
            BEFORE UPDATE ON rss_feeds
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS update_rss_feeds_updated_at ON rss_feeds"
    )

    op.drop_index("ix_news_rss_feed_id", table_name="news")

    op.drop_column("news", "rss_feed_id")

    op.drop_table("rss_feeds")
