"""Add Anthropic API configuration columns.

Adds anthropic_api_key and anthropic_base_url to system_settings,
and anthropic_api_key to user_settings for multi-provider LLM gateway support.

Revision ID: 011_add_anthropic_config
Revises: 010_add_two_phase_filter
Create Date: 2026-02-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '011_add_anthropic_config'
down_revision: Union[str, None] = '010_add_two_phase_filter'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'system_settings',
        sa.Column(
            'anthropic_api_key',
            sa.Text(),
            nullable=True,
            comment='系统级 Anthropic API Key（加密存储）',
        ),
    )
    op.add_column(
        'system_settings',
        sa.Column(
            'anthropic_base_url',
            sa.String(500),
            nullable=True,
            comment='自定义 Anthropic API 地址（用于代理）',
        ),
    )
    op.add_column(
        'user_settings',
        sa.Column(
            'anthropic_api_key',
            sa.Text(),
            nullable=True,
            comment='用户的 Anthropic API Key（用于 Claude 模型）',
        ),
    )


def downgrade() -> None:
    op.drop_column('user_settings', 'anthropic_api_key')
    op.drop_column('system_settings', 'anthropic_base_url')
    op.drop_column('system_settings', 'anthropic_api_key')
