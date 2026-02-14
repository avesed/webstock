"""Add Phase 2 multi-agent architecture config and multimodal support.

Adds Phase 2 configuration to system_settings (feature toggle, 5 provider
FKs + model names, source tiering, cache config) and multimodal fields to
news table (image_insights, has_visual_data, content_score, processing_path).
Also extends pipeline_events with cache_metadata.

Revision ID: 020_add_phase2_cfg
Revises: 019_add_detailed_sum
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '020_add_phase2_cfg'
down_revision: Union[str, None] = '019_add_detailed_sum'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Phase 2 config columns and multimodal fields."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── system_settings: Phase 2 Feature Toggle ──
    ss_columns = {col['name'] for col in inspector.get_columns('system_settings')}

    if 'phase2_enabled' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_enabled BOOLEAN NOT NULL DEFAULT false"
        )

    if 'phase2_score_threshold' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_score_threshold INTEGER NOT NULL DEFAULT 50"
        )

    # ── system_settings: 5 Provider FK columns ──
    if 'phase2_layer1_filter_provider_id' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer1_filter_provider_id UUID "
            "REFERENCES llm_providers(id) ON DELETE SET NULL"
        )

    if 'phase2_layer15_cleaning_provider_id' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer15_cleaning_provider_id UUID "
            "REFERENCES llm_providers(id) ON DELETE SET NULL"
        )

    if 'phase2_layer2_scoring_provider_id' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer2_scoring_provider_id UUID "
            "REFERENCES llm_providers(id) ON DELETE SET NULL"
        )

    if 'phase2_layer2_analysis_provider_id' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer2_analysis_provider_id UUID "
            "REFERENCES llm_providers(id) ON DELETE SET NULL"
        )

    if 'phase2_layer2_lightweight_provider_id' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer2_lightweight_provider_id UUID "
            "REFERENCES llm_providers(id) ON DELETE SET NULL"
        )

    # ── system_settings: 5 Model Name columns ──
    if 'phase2_layer1_filter_model' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer1_filter_model VARCHAR(100) DEFAULT 'gpt-4o-mini'"
        )

    if 'phase2_layer15_cleaning_model' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer15_cleaning_model VARCHAR(100) DEFAULT 'gpt-4o'"
        )

    if 'phase2_layer2_scoring_model' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer2_scoring_model VARCHAR(100) DEFAULT 'gpt-4o-mini'"
        )

    if 'phase2_layer2_analysis_model' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer2_analysis_model VARCHAR(100) DEFAULT 'gpt-4o'"
        )

    if 'phase2_layer2_lightweight_model' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_layer2_lightweight_model VARCHAR(100) DEFAULT 'gpt-4o-mini'"
        )

    # ── system_settings: Source tiering config ──
    if 'phase2_high_value_sources' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_high_value_sources JSONB "
            """DEFAULT '["reuters", "bloomberg", "sec", "company_announcement"]'::jsonb"""
        )

    if 'phase2_high_value_pct' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_high_value_pct FLOAT NOT NULL DEFAULT 0.20"
        )

    # ── system_settings: Cache config ──
    if 'phase2_cache_enabled' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_cache_enabled BOOLEAN NOT NULL DEFAULT true"
        )

    if 'phase2_cache_ttl_minutes' not in ss_columns:
        op.execute(
            "ALTER TABLE system_settings "
            "ADD COLUMN phase2_cache_ttl_minutes INTEGER NOT NULL DEFAULT 60"
        )

    # ── system_settings: Column comments ──
    op.execute(
        "COMMENT ON COLUMN system_settings.phase2_enabled IS "
        "'Phase 2多Agent架构开关（默认关闭）'"
    )

    op.execute(
        "COMMENT ON COLUMN system_settings.phase2_score_threshold IS "
        "'评分阈值，≥此分数进入完整5-Agent分析（默认50）'"
    )

    op.execute(
        "COMMENT ON COLUMN system_settings.phase2_high_value_sources IS "
        "'高价值新闻源JSON数组，这些源使用Phase 2完整多Agent分析'"
    )

    op.execute(
        "COMMENT ON COLUMN system_settings.phase2_cache_enabled IS "
        "'Prompt Caching开关（90%成本节省）'"
    )

    # ── news: Multimodal fields ──
    news_columns = {col['name'] for col in inspector.get_columns('news')}

    if 'image_insights' not in news_columns:
        op.execute(
            "ALTER TABLE news ADD COLUMN image_insights TEXT"
        )

    if 'has_visual_data' not in news_columns:
        op.execute(
            "ALTER TABLE news ADD COLUMN has_visual_data BOOLEAN NOT NULL DEFAULT false"
        )

    if 'content_score' not in news_columns:
        op.execute(
            "ALTER TABLE news ADD COLUMN content_score INTEGER"
        )

    if 'processing_path' not in news_columns:
        op.execute(
            "ALTER TABLE news ADD COLUMN processing_path VARCHAR(20)"
        )

    # ── news: Column comments ──
    op.execute(
        "COMMENT ON COLUMN news.image_insights IS "
        "'多模态LLM从图片中提取的关键数据（财报、图表、榜单等）'"
    )

    op.execute(
        "COMMENT ON COLUMN news.content_score IS "
        "'100分制新闻价值评分（信息价值40+投资相关30+完整性20+稀缺性10）'"
    )

    op.execute(
        "COMMENT ON COLUMN news.processing_path IS "
        "'Layer 2处理路径: full_analysis（≥阈值）或 lightweight（<阈值）'"
    )

    # ── news: Partial indexes ──
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('news')}

    if 'idx_news_content_score' not in existing_indexes:
        op.execute(
            "CREATE INDEX idx_news_content_score "
            "ON news(content_score) "
            "WHERE content_score IS NOT NULL"
        )

    if 'idx_news_processing_path' not in existing_indexes:
        op.execute(
            "CREATE INDEX idx_news_processing_path "
            "ON news(processing_path) "
            "WHERE processing_path IS NOT NULL"
        )

    if 'idx_news_has_visual_data' not in existing_indexes:
        op.execute(
            "CREATE INDEX idx_news_has_visual_data "
            "ON news(has_visual_data) "
            "WHERE has_visual_data = true"
        )

    # ── pipeline_events: Cache metadata ──
    pe_columns = {col['name'] for col in inspector.get_columns('pipeline_events')}

    if 'cache_metadata' not in pe_columns:
        op.execute(
            "ALTER TABLE pipeline_events ADD COLUMN cache_metadata JSONB"
        )

    op.execute(
        "COMMENT ON COLUMN pipeline_events.cache_metadata IS "
        "'Prompt cache命中率和token统计'"
    )


def downgrade() -> None:
    """Remove Phase 2 config columns and multimodal fields."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── Drop indexes first ──
    op.execute("DROP INDEX IF EXISTS idx_news_content_score")
    op.execute("DROP INDEX IF EXISTS idx_news_processing_path")
    op.execute("DROP INDEX IF EXISTS idx_news_has_visual_data")

    # ── pipeline_events ──
    pe_columns = {col['name'] for col in inspector.get_columns('pipeline_events')}
    if 'cache_metadata' in pe_columns:
        op.execute("ALTER TABLE pipeline_events DROP COLUMN cache_metadata")

    # ── news: Multimodal fields ──
    news_columns = {col['name'] for col in inspector.get_columns('news')}
    for col in ['image_insights', 'has_visual_data', 'content_score', 'processing_path']:
        if col in news_columns:
            op.execute(f"ALTER TABLE news DROP COLUMN {col}")

    # ── system_settings: Phase 2 columns ──
    ss_columns = {col['name'] for col in inspector.get_columns('system_settings')}
    phase2_cols = [
        'phase2_enabled', 'phase2_score_threshold',
        'phase2_layer1_filter_provider_id', 'phase2_layer15_cleaning_provider_id',
        'phase2_layer2_scoring_provider_id', 'phase2_layer2_analysis_provider_id',
        'phase2_layer2_lightweight_provider_id',
        'phase2_layer1_filter_model', 'phase2_layer15_cleaning_model',
        'phase2_layer2_scoring_model', 'phase2_layer2_analysis_model',
        'phase2_layer2_lightweight_model',
        'phase2_high_value_sources', 'phase2_high_value_pct',
        'phase2_cache_enabled', 'phase2_cache_ttl_minutes',
    ]
    for col in phase2_cols:
        if col in ss_columns:
            op.execute(f"ALTER TABLE system_settings DROP COLUMN {col}")
