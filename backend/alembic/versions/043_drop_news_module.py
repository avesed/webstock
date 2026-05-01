"""Drop news module tables and settings after NewsForge migration.

News processing is now handled entirely by the external NewsForge service.
This migration removes the local news tables, news-related settings columns,
and cleans up orphaned document embeddings.

Revision ID: 043_drop_news_module
Revises: 042_integration_settings
"""

from alembic import op

revision = "043_drop_news_module"
down_revision = "042_integration_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Clean document_embeddings of news source_type
    op.execute("DELETE FROM document_embeddings WHERE source_type = 'news'")

    # 2. Drop tables (order respects FK dependencies)
    op.execute("DROP TABLE IF EXISTS pipeline_events CASCADE")
    op.execute("DROP TABLE IF EXISTS news_alerts CASCADE")
    op.execute("DROP TABLE IF EXISTS news CASCADE")
    op.execute("DROP TABLE IF EXISTS rss_feeds CASCADE")

    # 3. Remove obsolete integration_settings keys
    op.execute(
        "DELETE FROM integration_settings WHERE key = 'integration.newsforge.push_enabled'"
    )
    op.execute(
        "DELETE FROM integration_settings WHERE key = 'integration.newsforge.proxy_enabled'"
    )
    op.execute(
        "DELETE FROM integration_settings WHERE key = 'integration.newsforge.webhook_secret'"
    )

    # 4. Drop news-related columns from system_settings
    op.drop_column("system_settings", "news_filter_model")
    op.drop_column("system_settings", "news_retention_days")
    op.drop_column("system_settings", "enable_news_analysis")
    op.drop_column("system_settings", "news_filter_provider_id")
    op.drop_column("system_settings", "news_entity_provider_id")
    op.drop_column("system_settings", "news_entity_model")
    op.drop_column("system_settings", "news_sentiment_provider_id")
    op.drop_column("system_settings", "news_sentiment_model")
    op.drop_column("system_settings", "news_summary_provider_id")
    op.drop_column("system_settings", "news_summary_model")
    op.drop_column("system_settings", "news_impact_provider_id")
    op.drop_column("system_settings", "news_impact_model")
    op.drop_column("system_settings", "news_report_provider_id")
    op.drop_column("system_settings", "news_report_model")
    op.drop_column("system_settings", "news_lightweight_provider_id")
    op.drop_column("system_settings", "news_lightweight_model")

    # 5. Drop news-related columns from user_settings
    op.drop_column("user_settings", "notify_news_alerts")
    op.drop_column("user_settings", "news_retention_days")


def downgrade() -> None:
    raise NotImplementedError(
        "News module migration is one-way. Restore from database backup."
    )
