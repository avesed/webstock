"""Add LangGraph layered architecture settings.

Adds new columns for vLLM/local model configuration and clarification settings
to support the LangGraph-based multi-agent analysis workflow.

Revision ID: 008_add_langgraph_settings
Revises: 007_create_document_embeddings
Create Date: 2026-02-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '008_add_langgraph_settings'
down_revision: Union[str, None] = '007_create_document_embeddings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add LangGraph configuration columns to system_settings."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table exists
    if 'system_settings' not in inspector.get_table_names():
        return

    # Get existing columns
    columns = {col['name'] for col in inspector.get_columns('system_settings')}

    # vLLM / Local Model Configuration
    if 'local_llm_base_url' not in columns:
        op.add_column(
            'system_settings',
            sa.Column(
                'local_llm_base_url',
                sa.String(500),
                nullable=True,
                comment='OpenAI 兼容端点地址（支持 vLLM, Ollama, LMStudio 等）',
            )
        )

    if 'analysis_model' not in columns:
        op.add_column(
            'system_settings',
            sa.Column(
                'analysis_model',
                sa.String(100),
                nullable=True,
                server_default='gpt-4o-mini',
                comment='分析层模型（支持本地模型如 Qwen2.5-14B-Instruct）',
            )
        )

    if 'synthesis_model' not in columns:
        op.add_column(
            'system_settings',
            sa.Column(
                'synthesis_model',
                sa.String(100),
                nullable=True,
                server_default='gpt-4o',
                comment='综合层模型（用于最终综合分析和用户交互）',
            )
        )

    if 'use_local_models' not in columns:
        op.add_column(
            'system_settings',
            sa.Column(
                'use_local_models',
                sa.Boolean(),
                nullable=False,
                server_default='false',
                comment='是否使用本地模型（vLLM）进行分析',
            )
        )

    # Clarification Settings
    if 'max_clarification_rounds' not in columns:
        op.add_column(
            'system_settings',
            sa.Column(
                'max_clarification_rounds',
                sa.Integer(),
                nullable=False,
                server_default='2',
                comment='最大追问轮次（综合层向分析层追问的最大次数）',
            )
        )

    if 'clarification_confidence_threshold' not in columns:
        op.add_column(
            'system_settings',
            sa.Column(
                'clarification_confidence_threshold',
                sa.Float(),
                nullable=False,
                server_default='0.6',
                comment='触发追问的置信度阈值（低于此值时可能触发追问）',
            )
        )


def downgrade() -> None:
    """Remove LangGraph configuration columns from system_settings."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table exists
    if 'system_settings' not in inspector.get_table_names():
        return

    # Get existing columns
    columns = {col['name'] for col in inspector.get_columns('system_settings')}

    # Remove clarification settings (if they exist)
    if 'clarification_confidence_threshold' in columns:
        op.drop_column('system_settings', 'clarification_confidence_threshold')
    if 'max_clarification_rounds' in columns:
        op.drop_column('system_settings', 'max_clarification_rounds')

    # Remove vLLM / local model settings (if they exist)
    if 'use_local_models' in columns:
        op.drop_column('system_settings', 'use_local_models')
    if 'synthesis_model' in columns:
        op.drop_column('system_settings', 'synthesis_model')
    if 'analysis_model' in columns:
        op.drop_column('system_settings', 'analysis_model')
    if 'local_llm_base_url' in columns:
        op.drop_column('system_settings', 'local_llm_base_url')
