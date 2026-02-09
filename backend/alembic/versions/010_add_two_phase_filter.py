"""Add two-phase news filtering fields.

Adds filter_status for crash recovery, tag fields (industry, event, sentiment),
investment summary, and use_two_phase_filter feature flag.

Revision ID: 010_add_two_phase_filter
Revises: 009_add_related_entities
Create Date: 2026-02-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = '010_add_two_phase_filter'
down_revision: Union[str, None] = '009_add_related_entities'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add two-phase filtering fields to news and system_settings tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ==================== NEWS TABLE ====================
    if 'news' in inspector.get_table_names():
        columns = {col['name'] for col in inspector.get_columns('news')}

        # 筛选状态追踪 (P0: 崩溃恢复)
        if 'filter_status' not in columns:
            op.add_column(
                'news',
                sa.Column(
                    'filter_status',
                    sa.String(20),
                    nullable=True,
                    comment='筛选状态: pending, useful, uncertain, keep, delete, failed',
                )
            )
            op.create_index(
                'ix_news_filter_status',
                'news',
                ['filter_status'],
            )

        # 行业标签
        if 'industry_tags' not in columns:
            op.add_column(
                'news',
                sa.Column(
                    'industry_tags',
                    JSON,
                    nullable=True,
                    comment='行业标签: ["tech", "finance", "healthcare", ...]',
                )
            )

        # 事件标签
        if 'event_tags' not in columns:
            op.add_column(
                'news',
                sa.Column(
                    'event_tags',
                    JSON,
                    nullable=True,
                    comment='事件标签: ["earnings", "merger", "regulatory", ...]',
                )
            )

        # 情绪标签
        if 'sentiment_tag' not in columns:
            op.add_column(
                'news',
                sa.Column(
                    'sentiment_tag',
                    sa.String(20),
                    nullable=True,
                    comment='情绪标签: bullish, bearish, neutral',
                )
            )
            op.create_index(
                'ix_news_sentiment_tag',
                'news',
                ['sentiment_tag'],
            )

        # 投资导向摘要
        if 'investment_summary' not in columns:
            op.add_column(
                'news',
                sa.Column(
                    'investment_summary',
                    sa.Text(),
                    nullable=True,
                    comment='投资导向摘要 (2-3句，由精筛生成)',
                )
            )

    # ==================== SYSTEM_SETTINGS TABLE ====================
    if 'system_settings' in inspector.get_table_names():
        settings_columns = {col['name'] for col in inspector.get_columns('system_settings')}

        # 两阶段筛选开关 (P0: 渐进式发布)
        if 'use_two_phase_filter' not in settings_columns:
            op.add_column(
                'system_settings',
                sa.Column(
                    'use_two_phase_filter',
                    sa.Boolean(),
                    nullable=False,
                    server_default='false',
                    comment='启用两阶段新闻筛选',
                )
            )


def downgrade() -> None:
    """Remove two-phase filtering fields."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ==================== SYSTEM_SETTINGS TABLE ====================
    if 'system_settings' in inspector.get_table_names():
        settings_columns = {col['name'] for col in inspector.get_columns('system_settings')}

        if 'use_two_phase_filter' in settings_columns:
            op.drop_column('system_settings', 'use_two_phase_filter')

    # ==================== NEWS TABLE ====================
    if 'news' in inspector.get_table_names():
        columns = {col['name'] for col in inspector.get_columns('news')}
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('news')}

        if 'investment_summary' in columns:
            op.drop_column('news', 'investment_summary')

        if 'sentiment_tag' in columns:
            if 'ix_news_sentiment_tag' in existing_indexes:
                op.drop_index('ix_news_sentiment_tag', 'news')
            op.drop_column('news', 'sentiment_tag')

        if 'event_tags' in columns:
            op.drop_column('news', 'event_tags')

        if 'industry_tags' in columns:
            op.drop_column('news', 'industry_tags')

        if 'filter_status' in columns:
            if 'ix_news_filter_status' in existing_indexes:
                op.drop_index('ix_news_filter_status', 'news')
            op.drop_column('news', 'filter_status')
