"""Add per-agent model config for L3 news pipeline.

Adds 12 columns (6 purposes x 2 fields) to system_settings for independent
model selection per L3 agent: entity, sentiment, summary, impact, report,
lightweight.  Each purpose gets a provider_id (UUID FK) and model (String).
NULL means fallback to the existing general L3 model configuration.

Revision ID: 030_l3_agent_models
Revises: 029_news_indexes
Create Date: 2026-02-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "030_l3_agent_models"
down_revision: Union[str, None] = "029_news_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# L3 Agent配置: (列名前缀, 中文描述)
_AGENT_PURPOSES = [
    ("news_entity", "L3实体提取Agent"),
    ("news_sentiment", "L3情感分析Agent"),
    ("news_summary", "L3摘要生成Agent"),
    ("news_impact", "L3影响评估Agent"),
    ("news_report", "L3报告撰写Agent"),
    ("news_lightweight", "L3轻量处理Agent"),
]


def upgrade() -> None:
    """为L3新闻流水线的6个Agent分别添加独立的Provider和模型配置。"""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    ss_columns = {col["name"] for col in inspector.get_columns("system_settings")}

    for prefix, label in _AGENT_PURPOSES:
        provider_col = f"{prefix}_provider_id"
        model_col = f"{prefix}_model"

        # 添加 provider_id 列 + FK约束
        if provider_col not in ss_columns:
            op.add_column(
                "system_settings",
                sa.Column(
                    provider_col,
                    UUID(as_uuid=True),
                    nullable=True,
                    comment=f"{label}使用的Provider",
                ),
            )
            op.execute(
                f"ALTER TABLE system_settings "
                f"ADD CONSTRAINT fk_ss_{prefix}_provider "
                f"FOREIGN KEY ({provider_col}) "
                f"REFERENCES llm_providers(id) ON DELETE SET NULL"
            )

        # 添加 model 列
        if model_col not in ss_columns:
            op.add_column(
                "system_settings",
                sa.Column(
                    model_col,
                    sa.String(100),
                    nullable=True,
                    comment=f"{label}模型名称",
                ),
            )


def downgrade() -> None:
    """移除L3 Agent独立模型配置的12个列。"""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    ss_columns = {col["name"] for col in inspector.get_columns("system_settings")}

    for prefix, _label in _AGENT_PURPOSES:
        provider_col = f"{prefix}_provider_id"
        model_col = f"{prefix}_model"

        # 先删FK约束，再删列
        if provider_col in ss_columns:
            op.execute(
                f"ALTER TABLE system_settings "
                f"DROP CONSTRAINT IF EXISTS fk_ss_{prefix}_provider"
            )
            op.execute(
                f"ALTER TABLE system_settings "
                f"DROP COLUMN {provider_col}"
            )

        if model_col in ss_columns:
            op.execute(
                f"ALTER TABLE system_settings "
                f"DROP COLUMN {model_col}"
            )
