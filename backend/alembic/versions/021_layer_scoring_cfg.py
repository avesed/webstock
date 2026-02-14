"""Add layer1 scoring config and rename pipeline toggle.

Renames use_two_phase_filter -> enable_llm_pipeline and adds 4 new columns
for Layer 1 scoring configuration (discard/analysis thresholds + provider).
Migrates existing phase2_score_threshold data to new 0-300 scale.

Revision ID: 021_layer_scoring_cfg
Revises: 020_add_phase2_cfg
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "021_layer_scoring_cfg"
down_revision: Union[str, None] = "021_add_score_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename pipeline toggle and add Layer 1 scoring config."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    ss_columns = {col["name"] for col in inspector.get_columns("system_settings")}

    # 1. Rename column: use_two_phase_filter -> enable_llm_pipeline
    if "use_two_phase_filter" in ss_columns and "enable_llm_pipeline" not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "RENAME COLUMN use_two_phase_filter TO enable_llm_pipeline"
        )

    # Update the column comment
    op.execute(
        "COMMENT ON COLUMN system_settings.enable_llm_pipeline IS "
        "'启用LLM新闻处理流水线（3层架构：评分->抓取清洗->分析）'"
    )

    # Re-read columns after rename
    ss_columns = {col["name"] for col in inspector.get_columns("system_settings")}

    # 2. Add new columns (one at a time for asyncpg compatibility)
    if "layer1_discard_threshold" not in ss_columns:
        op.add_column(
            "system_settings",
            sa.Column(
                "layer1_discard_threshold",
                sa.Integer(),
                nullable=False,
                server_default="105",
                comment="Layer 1评分丢弃阈值（0-300），低于此分数不抓取不分析",
            ),
        )

    if "layer1_full_analysis_threshold" not in ss_columns:
        op.add_column(
            "system_settings",
            sa.Column(
                "layer1_full_analysis_threshold",
                sa.Integer(),
                nullable=False,
                server_default="195",
                comment="Layer 1评分完整分析阈值（0-300），>=此分数进入5-Agent深度分析",
            ),
        )

    if "layer1_scoring_provider_id" not in ss_columns:
        op.add_column(
            "system_settings",
            sa.Column(
                "layer1_scoring_provider_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="Layer 1评分使用的Provider（FK->llm_providers）",
            ),
        )
        op.execute(
            "ALTER TABLE system_settings "
            "ADD CONSTRAINT fk_ss_layer1_scoring_provider "
            "FOREIGN KEY (layer1_scoring_provider_id) "
            "REFERENCES llm_providers(id) ON DELETE SET NULL"
        )

    if "layer1_scoring_model" not in ss_columns:
        op.add_column(
            "system_settings",
            sa.Column(
                "layer1_scoring_model",
                sa.String(100),
                nullable=True,
                server_default="gpt-4o-mini",
                comment="Layer 1评分模型名称",
            ),
        )

    # 3. Data migration: Convert phase2_score_threshold (0-100) to
    #    layer1_full_analysis_threshold (0-300)
    op.execute(
        "UPDATE system_settings "
        "SET layer1_full_analysis_threshold = phase2_score_threshold * 3 "
        "WHERE phase2_score_threshold IS NOT NULL "
        "AND phase2_score_threshold > 0"
    )

    # 4. Copy old provider config to new columns
    op.execute(
        "UPDATE system_settings "
        "SET layer1_scoring_provider_id = phase2_layer1_filter_provider_id "
        "WHERE phase2_layer1_filter_provider_id IS NOT NULL"
    )

    op.execute(
        "UPDATE system_settings "
        "SET layer1_scoring_model = phase2_layer1_filter_model "
        "WHERE phase2_layer1_filter_model IS NOT NULL"
    )


def downgrade() -> None:
    """Revert: rename back and drop new columns."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    ss_columns = {col["name"] for col in inspector.get_columns("system_settings")}

    # 1. Rename back: enable_llm_pipeline -> use_two_phase_filter
    if "enable_llm_pipeline" in ss_columns and "use_two_phase_filter" not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "RENAME COLUMN enable_llm_pipeline TO use_two_phase_filter"
        )

    op.execute(
        "COMMENT ON COLUMN system_settings.use_two_phase_filter IS "
        "'启用两阶段新闻筛选（渐进式发布）'"
    )

    # 2. Drop the 4 new columns
    for col in [
        "layer1_discard_threshold",
        "layer1_full_analysis_threshold",
        "layer1_scoring_provider_id",
        "layer1_scoring_model",
    ]:
        if col in ss_columns:
            op.execute(f"ALTER TABLE system_settings DROP COLUMN {col}")
