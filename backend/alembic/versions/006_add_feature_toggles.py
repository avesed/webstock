"""Add feature toggle columns to system_settings.

Adds enable_news_analysis and enable_stock_analysis columns to
system_settings table to allow admin to control feature availability.

Revision ID: 006_add_feature_toggles
Revises: 005_add_account_status
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '006_add_feature_toggles'
down_revision: Union[str, None] = '005_add_account_status'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add feature toggle columns to system_settings."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'system_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('system_settings')]

        if 'enable_news_analysis' not in existing_columns:
            op.add_column('system_settings', sa.Column(
                'enable_news_analysis',
                sa.Boolean(),
                nullable=False,
                server_default='true',  # Enabled by default
                comment='是否启用新闻分析功能',
            ))

        if 'enable_stock_analysis' not in existing_columns:
            op.add_column('system_settings', sa.Column(
                'enable_stock_analysis',
                sa.Boolean(),
                nullable=False,
                server_default='true',  # Enabled by default
                comment='是否启用股票分析功能',
            ))


def downgrade() -> None:
    """Remove feature toggle columns from system_settings."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'system_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('system_settings')]

        if 'enable_stock_analysis' in existing_columns:
            op.drop_column('system_settings', 'enable_stock_analysis')

        if 'enable_news_analysis' in existing_columns:
            op.drop_column('system_settings', 'enable_news_analysis')
