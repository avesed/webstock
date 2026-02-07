"""add news api settings to system settings

Revision ID: 004_add_news_api_settings
Revises: 003_add_user_role_and_admin
Create Date: 2026-02-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004_add_news_api_settings'
down_revision: Union[str, None] = '003_add_user_role_and_admin'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add news API settings columns to system_settings table."""
    # Add news_use_llm_config column
    op.add_column(
        'system_settings',
        sa.Column(
            'news_use_llm_config',
            sa.Boolean(),
            nullable=False,
            server_default='true',
            comment='新闻处理是否使用 LLM 配置的 API 设置'
        )
    )

    # Add news_openai_base_url column
    op.add_column(
        'system_settings',
        sa.Column(
            'news_openai_base_url',
            sa.String(500),
            nullable=True,
            comment='新闻处理专用 OpenAI API 地址'
        )
    )

    # Add news_openai_api_key column
    op.add_column(
        'system_settings',
        sa.Column(
            'news_openai_api_key',
            sa.Text(),
            nullable=True,
            comment='新闻处理专用 OpenAI API Key'
        )
    )


def downgrade() -> None:
    """Remove news API settings columns from system_settings table."""
    op.drop_column('system_settings', 'news_openai_api_key')
    op.drop_column('system_settings', 'news_openai_base_url')
    op.drop_column('system_settings', 'news_use_llm_config')
