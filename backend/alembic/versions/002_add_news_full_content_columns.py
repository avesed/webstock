"""Add news full content columns.

Adds columns to news table for full content references and metadata.
Also adds columns to user_settings for news content configuration.

Revision ID: 002_add_news_full_content
Revises: 001_add_user_ai_config
Create Date: 2026-02-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = '002_add_news_full_content'
down_revision: Union[str, None] = '001_add_user_ai_config'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add news full content columns."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # === News table columns ===
    if 'news' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('news')]

        # Full content reference fields
        if 'content_file_path' not in existing_columns:
            op.add_column('news', sa.Column(
                'content_file_path',
                sa.String(500),
                nullable=True,
                comment='JSON文件路径，存储全文内容'
            ))

        if 'content_status' not in existing_columns:
            op.add_column('news', sa.Column(
                'content_status',
                sa.String(20),
                nullable=False,
                server_default='pending',
                comment='内容状态: pending, fetched, embedded, partial, failed, blocked, deleted'
            ))
            # Add index for content_status
            op.create_index(
                'ix_news_content_status',
                'news',
                ['content_status'],
                unique=False
            )

        if 'content_fetched_at' not in existing_columns:
            op.add_column('news', sa.Column(
                'content_fetched_at',
                sa.DateTime(timezone=True),
                nullable=True,
                comment='全文抓取时间'
            ))

        if 'content_error' not in existing_columns:
            op.add_column('news', sa.Column(
                'content_error',
                sa.String(500),
                nullable=True,
                comment='抓取错误信息'
            ))

        if 'language' not in existing_columns:
            op.add_column('news', sa.Column(
                'language',
                sa.String(10),
                nullable=True,
                comment='文章语言: en, zh 等'
            ))

        # newspaper4k metadata fields
        if 'authors' not in existing_columns:
            op.add_column('news', sa.Column(
                'authors',
                JSON,
                nullable=True,
                comment='作者列表'
            ))

        if 'keywords' not in existing_columns:
            op.add_column('news', sa.Column(
                'keywords',
                JSON,
                nullable=True,
                comment='关键词列表'
            ))

        if 'top_image' not in existing_columns:
            op.add_column('news', sa.Column(
                'top_image',
                sa.String(1024),
                nullable=True,
                comment='文章主图URL'
            ))

    # === UserSettings table columns ===
    if 'user_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('user_settings')]

        # News content settings
        if 'full_content_source' not in existing_columns:
            op.add_column('user_settings', sa.Column(
                'full_content_source',
                sa.String(20),
                nullable=True,
                server_default='scraper',
                comment='全文抓取来源: scraper (newspaper4k) 或 polygon'
            ))

        if 'polygon_api_key' not in existing_columns:
            op.add_column('user_settings', sa.Column(
                'polygon_api_key',
                sa.Text(),
                nullable=True,
                comment='用户的Polygon.io API Key（用于新闻全文）'
            ))

        if 'news_retention_days' not in existing_columns:
            op.add_column('user_settings', sa.Column(
                'news_retention_days',
                sa.Integer(),
                nullable=True,
                server_default='30',
                comment='新闻内容保留天数'
            ))

        # AI model settings
        if 'news_embedding_model' not in existing_columns:
            op.add_column('user_settings', sa.Column(
                'news_embedding_model',
                sa.String(50),
                nullable=True,
                server_default='text-embedding-3-small',
                comment='新闻向量模型'
            ))

        if 'news_filter_model' not in existing_columns:
            op.add_column('user_settings', sa.Column(
                'news_filter_model',
                sa.String(50),
                nullable=True,
                server_default='gpt-4o-mini',
                comment='新闻筛选模型（用于判断相关性）'
            ))


def downgrade() -> None:
    """Remove news full content columns."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # === UserSettings table columns ===
    if 'user_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('user_settings')]

        for col in ['news_filter_model', 'news_embedding_model', 'news_retention_days',
                    'polygon_api_key', 'full_content_source']:
            if col in existing_columns:
                op.drop_column('user_settings', col)

    # === News table columns ===
    if 'news' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('news')]

        # Drop index first if exists
        try:
            op.drop_index('ix_news_content_status', table_name='news')
        except Exception:
            pass

        for col in ['top_image', 'keywords', 'authors', 'language', 'content_error',
                    'content_fetched_at', 'content_status', 'content_file_path']:
            if col in existing_columns:
                op.drop_column('news', col)
