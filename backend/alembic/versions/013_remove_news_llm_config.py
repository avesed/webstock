"""Remove news-specific LLM config and add news_filter_provider_id.

Consolidates news filtering into the unified LLM provider system.
Migrates any independent news API key into a separate provider if needed.

Revision ID: 013_remove_news_llm_config
Revises: 012_add_llm_providers
Create Date: 2026-02-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "013_remove_news_llm_config"
down_revision: Union[str, None] = "012_add_llm_providers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add news_filter_provider_id FK to system_settings
    op.add_column(
        "system_settings",
        sa.Column("news_filter_provider_id", UUID(as_uuid=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_system_settings_news_filter_provider",
        "system_settings",
        "llm_providers",
        ["news_filter_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2. Data migration: If news has independent API key different from main,
    # create a provider. If news_use_llm_config=true OR news_openai_api_key
    # is not set, reuse the existing chat provider (or OpenAI migrated provider).

    # 2a. Create provider from independent news API key (when news_use_llm_config=false
    # AND news_openai_api_key IS set AND differs from openai_api_key)
    op.execute(
        """
        INSERT INTO llm_providers (id, name, provider_type, api_key, base_url, models, sort_order)
        SELECT
            gen_random_uuid(),
            'News Filter (migrated)',
            'openai',
            s.news_openai_api_key,
            s.news_openai_base_url,
            jsonb_build_array(COALESCE(s.news_filter_model, 'gpt-4o-mini')),
            10
        FROM system_settings s
        WHERE s.id = 1
          AND s.news_use_llm_config = false
          AND s.news_openai_api_key IS NOT NULL
          AND s.news_openai_api_key != ''
          AND (s.openai_api_key IS NULL OR s.news_openai_api_key != s.openai_api_key)
        """
    )

    # 2b. Set news_filter_provider_id to the migrated news provider if it was created
    op.execute(
        """
        UPDATE system_settings
        SET news_filter_provider_id = (
            SELECT id FROM llm_providers WHERE name = 'News Filter (migrated)' LIMIT 1
        )
        WHERE id = 1
          AND EXISTS (SELECT 1 FROM llm_providers WHERE name = 'News Filter (migrated)')
        """
    )

    # 2c. Otherwise, use the chat provider (fallback: the OpenAI migrated provider)
    op.execute(
        """
        UPDATE system_settings
        SET news_filter_provider_id = COALESCE(
            chat_provider_id,
            (SELECT id FROM llm_providers WHERE name = 'OpenAI (migrated)' LIMIT 1)
        )
        WHERE id = 1
          AND news_filter_provider_id IS NULL
        """
    )

    # 3. Drop deprecated columns from system_settings
    op.drop_column("system_settings", "news_use_llm_config")

    op.drop_column("system_settings", "news_openai_base_url")

    op.drop_column("system_settings", "news_openai_api_key")

    # 4. Drop deprecated columns from user_settings
    op.drop_column("user_settings", "news_openai_base_url")

    op.drop_column("user_settings", "news_openai_api_key")

    op.drop_column("user_settings", "news_embedding_model")

    op.drop_column("user_settings", "news_filter_model")


def downgrade() -> None:
    # Re-add user_settings columns
    op.add_column(
        "user_settings",
        sa.Column("news_filter_model", sa.String(100), nullable=True),
    )
    op.add_column(
        "user_settings",
        sa.Column("news_embedding_model", sa.String(100), nullable=True),
    )
    op.add_column(
        "user_settings",
        sa.Column("news_openai_api_key", sa.Text, nullable=True),
    )
    op.add_column(
        "user_settings",
        sa.Column("news_openai_base_url", sa.String(500), nullable=True),
    )

    # Re-add system_settings columns
    op.add_column(
        "system_settings",
        sa.Column("news_openai_api_key", sa.Text, nullable=True),
    )
    op.add_column(
        "system_settings",
        sa.Column("news_openai_base_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "system_settings",
        sa.Column(
            "news_use_llm_config",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # Drop the new FK and column
    op.drop_constraint(
        "fk_system_settings_news_filter_provider",
        "system_settings",
        type_="foreignkey",
    )
    op.drop_column("system_settings", "news_filter_provider_id")
