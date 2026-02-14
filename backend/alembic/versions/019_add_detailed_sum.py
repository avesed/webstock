"""Add detailed_summary field to news table.

Adds detailed_summary for preserving all key details from Layer 2 processing,
with partial index for storage optimization and column comments for clarity.

Revision ID: 019_add_detailed_sum
Revises: 018_tavily_mcp_config
Create Date: 2026-02-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '019_add_detailed_sum'
down_revision: Union[str, None] = '018_tavily_mcp_config'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add detailed_summary column with partial index and field comments."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'news' in inspector.get_table_names():
        columns = {col['name'] for col in inspector.get_columns('news')}

        # Add detailed_summary column
        if 'detailed_summary' not in columns:
            op.add_column(
                'news',
                sa.Column(
                    'detailed_summary',
                    sa.Text(),
                    nullable=True,
                    comment='保留所有关键细节的完整总结，用于"阅读更多"展示',
                )
            )

            # Create partial index for non-NULL values to optimize storage
            op.execute("""
                CREATE INDEX idx_news_detailed_summary_not_null
                ON news(detailed_summary)
                WHERE detailed_summary IS NOT NULL
            """)

        # Add/update column comments for clarity
        op.execute("""
            COMMENT ON COLUMN news.investment_summary IS
            '1句话投资概况，用于卡片预览（最多50字）'
        """)

        op.execute("""
            COMMENT ON COLUMN news.detailed_summary IS
            '保留所有关键细节的完整总结，用于"阅读更多"展示'
        """)

        op.execute("""
            COMMENT ON COLUMN news.ai_analysis IS
            'Markdown格式的AI分析报告，由Layer 2生成或on-demand分析生成'
        """)


def downgrade() -> None:
    """Remove detailed_summary column and index."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'news' in inspector.get_table_names():
        columns = {col['name'] for col in inspector.get_columns('news')}
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('news')}

        # Drop partial index first
        if 'idx_news_detailed_summary_not_null' in existing_indexes:
            op.execute("DROP INDEX IF EXISTS idx_news_detailed_summary_not_null")

        # Drop column
        if 'detailed_summary' in columns:
            op.drop_column('news', 'detailed_summary')

        # Restore original comments (if needed)
        op.execute("""
            COMMENT ON COLUMN news.investment_summary IS
            '投资导向摘要 (2-3句，由精筛生成)'
        """)

        op.execute("""
            COMMENT ON COLUMN news.ai_analysis IS NULL
        """)
