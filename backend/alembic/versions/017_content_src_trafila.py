"""Migrate content source from 'scraper' to 'trafilatura'.

Updates existing user_settings records that still have 'scraper' as the
full_content_source value (from pre-Phase-1 installations) to 'trafilatura'.
Also updates the server_default for new records.

Revision ID: 017_content_src_trafila
Revises: 016_add_rss_feeds
Create Date: 2026-02-12
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "017_content_src_trafila"
down_revision: Union[str, None] = "016_add_rss_feeds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migrate existing 'scraper' values to 'trafilatura'
    op.execute(
        "UPDATE user_settings SET full_content_source = 'trafilatura' "
        "WHERE full_content_source = 'scraper'"
    )

    # Update server_default for new records
    op.alter_column(
        "user_settings",
        "full_content_source",
        server_default="trafilatura",
    )


def downgrade() -> None:
    # Revert back to 'scraper'
    op.execute(
        "UPDATE user_settings SET full_content_source = 'scraper' "
        "WHERE full_content_source = 'trafilatura'"
    )

    op.alter_column(
        "user_settings",
        "full_content_source",
        server_default="scraper",
    )
