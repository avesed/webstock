"""Add related_entities and RAG helper fields to news table.

Adds fields for LLM-extracted related entities (stocks, indices, macro factors)
with relevance scores, plus helper fields for efficient RAG querying.

Revision ID: 009_add_related_entities
Revises: 008_add_langgraph_settings
Create Date: 2026-02-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = '009_add_related_entities'
down_revision: Union[str, None] = '008_add_langgraph_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add related_entities and RAG helper fields to news table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table exists
    if 'news' not in inspector.get_table_names():
        return

    # Get existing columns
    columns = {col['name'] for col in inspector.get_columns('news')}

    # Main field: related entities JSON
    if 'related_entities' not in columns:
        op.add_column(
            'news',
            sa.Column(
                'related_entities',
                JSON,
                nullable=True,
                comment='关联实体及评分，格式: [{entity, type, score}, ...]',
            )
        )

    # RAG helper field: has stock entities
    if 'has_stock_entities' not in columns:
        op.add_column(
            'news',
            sa.Column(
                'has_stock_entities',
                sa.Boolean(),
                nullable=False,
                server_default='false',
                comment='是否包含个股实体',
            )
        )
        op.create_index(
            'ix_news_has_stock_entities',
            'news',
            ['has_stock_entities'],
        )

    # RAG helper field: has macro entities
    if 'has_macro_entities' not in columns:
        op.add_column(
            'news',
            sa.Column(
                'has_macro_entities',
                sa.Boolean(),
                nullable=False,
                server_default='false',
                comment='是否包含宏观因素',
            )
        )
        op.create_index(
            'ix_news_has_macro_entities',
            'news',
            ['has_macro_entities'],
        )

    # RAG helper field: max entity score
    if 'max_entity_score' not in columns:
        op.add_column(
            'news',
            sa.Column(
                'max_entity_score',
                sa.Float(),
                nullable=True,
                comment='最高实体评分，用于快速过滤高相关性新闻',
            )
        )
        op.create_index(
            'ix_news_max_entity_score',
            'news',
            ['max_entity_score'],
        )

    # RAG helper field: primary entity
    if 'primary_entity' not in columns:
        op.add_column(
            'news',
            sa.Column(
                'primary_entity',
                sa.String(100),
                nullable=True,
                comment='主实体（最高分股票，或最高分指数/宏观）',
            )
        )
        op.create_index(
            'ix_news_primary_entity',
            'news',
            ['primary_entity'],
        )

    # RAG helper field: primary entity type
    if 'primary_entity_type' not in columns:
        op.add_column(
            'news',
            sa.Column(
                'primary_entity_type',
                sa.String(20),
                nullable=True,
                comment='主实体类型: stock, index, macro',
            )
        )
        op.create_index(
            'ix_news_primary_entity_type',
            'news',
            ['primary_entity_type'],
        )

    # Create composite indexes for common RAG queries
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('news')}

    if 'ix_news_stock_entities_score' not in existing_indexes:
        op.create_index(
            'ix_news_stock_entities_score',
            'news',
            ['has_stock_entities', 'max_entity_score'],
        )

    if 'ix_news_macro_entities_score' not in existing_indexes:
        op.create_index(
            'ix_news_macro_entities_score',
            'news',
            ['has_macro_entities', 'max_entity_score'],
        )


def downgrade() -> None:
    """Remove related_entities and RAG helper fields from news table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table exists
    if 'news' not in inspector.get_table_names():
        return

    # Get existing indexes and columns
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('news')}
    columns = {col['name'] for col in inspector.get_columns('news')}

    # Drop composite indexes first
    if 'ix_news_macro_entities_score' in existing_indexes:
        op.drop_index('ix_news_macro_entities_score', 'news')
    if 'ix_news_stock_entities_score' in existing_indexes:
        op.drop_index('ix_news_stock_entities_score', 'news')

    # Drop columns and their indexes
    if 'primary_entity_type' in columns:
        if 'ix_news_primary_entity_type' in existing_indexes:
            op.drop_index('ix_news_primary_entity_type', 'news')
        op.drop_column('news', 'primary_entity_type')

    if 'primary_entity' in columns:
        if 'ix_news_primary_entity' in existing_indexes:
            op.drop_index('ix_news_primary_entity', 'news')
        op.drop_column('news', 'primary_entity')

    if 'max_entity_score' in columns:
        if 'ix_news_max_entity_score' in existing_indexes:
            op.drop_index('ix_news_max_entity_score', 'news')
        op.drop_column('news', 'max_entity_score')

    if 'has_macro_entities' in columns:
        if 'ix_news_has_macro_entities' in existing_indexes:
            op.drop_index('ix_news_has_macro_entities', 'news')
        op.drop_column('news', 'has_macro_entities')

    if 'has_stock_entities' in columns:
        if 'ix_news_has_stock_entities' in existing_indexes:
            op.drop_index('ix_news_has_stock_entities', 'news')
        op.drop_column('news', 'has_stock_entities')

    if 'related_entities' in columns:
        op.drop_column('news', 'related_entities')
