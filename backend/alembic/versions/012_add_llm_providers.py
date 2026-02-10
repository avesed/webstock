"""Add llm_providers table and provider FK columns to system_settings.

Creates the llm_providers table for multi-provider LLM configuration,
adds provider_id FK columns to system_settings, and migrates existing
API key/URL data into provider rows.

Revision ID: 012_add_llm_providers
Revises: 011_add_anthropic_config
Create Date: 2026-02-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision: str = "012_add_llm_providers"
down_revision: Union[str, None] = "011_add_anthropic_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create llm_providers table
    op.create_table(
        "llm_providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("provider_type", sa.String(20), nullable=False),
        sa.Column("api_key", sa.Text, nullable=True),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("models", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    # 2. Add provider FK columns to system_settings
    op.add_column(
        "system_settings",
        sa.Column("chat_provider_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "system_settings",
        sa.Column("analysis_provider_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "system_settings",
        sa.Column("synthesis_provider_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "system_settings",
        sa.Column("embedding_provider_id", UUID(as_uuid=True), nullable=True),
    )

    # 3. Add foreign key constraints
    op.create_foreign_key(
        "fk_system_settings_chat_provider",
        "system_settings",
        "llm_providers",
        ["chat_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_system_settings_analysis_provider",
        "system_settings",
        "llm_providers",
        ["analysis_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_system_settings_synthesis_provider",
        "system_settings",
        "llm_providers",
        ["synthesis_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_system_settings_embedding_provider",
        "system_settings",
        "llm_providers",
        ["embedding_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 4. Data migration: create provider rows from existing flat columns
    # Each op.execute() must contain exactly ONE SQL statement (asyncpg constraint)

    # 4a. Migrate OpenAI settings (if openai_api_key is set)
    op.execute("""
        INSERT INTO llm_providers (id, name, provider_type, api_key, base_url, models, sort_order)
        SELECT
            gen_random_uuid(),
            'OpenAI (migrated)',
            'openai',
            s.openai_api_key,
            s.openai_base_url,
            (
                SELECT COALESCE(jsonb_agg(DISTINCT m), '[]'::jsonb)
                FROM (
                    SELECT unnest(ARRAY[
                        s.openai_model,
                        s.analysis_model,
                        s.synthesis_model
                    ]) AS m
                ) sub
                WHERE m IS NOT NULL AND m != ''
            ),
            0
        FROM system_settings s
        WHERE s.id = 1
          AND s.openai_api_key IS NOT NULL
          AND s.openai_api_key != ''
    """)

    # 4b. Migrate Anthropic settings (if anthropic_api_key is set)
    op.execute("""
        INSERT INTO llm_providers (id, name, provider_type, api_key, base_url, models, sort_order)
        SELECT
            gen_random_uuid(),
            'Anthropic (migrated)',
            'anthropic',
            s.anthropic_api_key,
            s.anthropic_base_url,
            '[]'::jsonb,
            1
        FROM system_settings s
        WHERE s.id = 1
          AND s.anthropic_api_key IS NOT NULL
          AND s.anthropic_api_key != ''
    """)

    # 4c. Migrate local LLM settings (if use_local_models and local_llm_base_url are set)
    op.execute("""
        INSERT INTO llm_providers (id, name, provider_type, api_key, base_url, models, sort_order)
        SELECT
            gen_random_uuid(),
            'Local LLM (migrated)',
            'openai',
            NULL,
            s.local_llm_base_url,
            (
                SELECT COALESCE(jsonb_agg(DISTINCT m), '[]'::jsonb)
                FROM (
                    SELECT unnest(ARRAY[
                        s.analysis_model,
                        s.synthesis_model
                    ]) AS m
                ) sub
                WHERE m IS NOT NULL AND m != ''
            ),
            2
        FROM system_settings s
        WHERE s.id = 1
          AND s.use_local_models = true
          AND s.local_llm_base_url IS NOT NULL
          AND s.local_llm_base_url != ''
    """)

    # 4d. Set chat_provider_id to the OpenAI migrated provider (if it exists)
    op.execute("""
        UPDATE system_settings
        SET chat_provider_id = (
            SELECT id FROM llm_providers WHERE name = 'OpenAI (migrated)' LIMIT 1
        )
        WHERE id = 1
          AND EXISTS (SELECT 1 FROM llm_providers WHERE name = 'OpenAI (migrated)')
    """)

    # 4e. Set analysis_provider_id
    op.execute("""
        UPDATE system_settings
        SET analysis_provider_id = CASE
            WHEN use_local_models = true
                AND (SELECT id FROM llm_providers WHERE name = 'Local LLM (migrated)' LIMIT 1) IS NOT NULL
            THEN (SELECT id FROM llm_providers WHERE name = 'Local LLM (migrated)' LIMIT 1)
            ELSE (SELECT id FROM llm_providers WHERE name = 'OpenAI (migrated)' LIMIT 1)
        END
        WHERE id = 1
    """)

    # 4f. Set synthesis_provider_id
    op.execute("""
        UPDATE system_settings
        SET synthesis_provider_id = CASE
            WHEN use_local_models = true
                AND (SELECT id FROM llm_providers WHERE name = 'Local LLM (migrated)' LIMIT 1) IS NOT NULL
            THEN (SELECT id FROM llm_providers WHERE name = 'Local LLM (migrated)' LIMIT 1)
            ELSE (SELECT id FROM llm_providers WHERE name = 'OpenAI (migrated)' LIMIT 1)
        END
        WHERE id = 1
    """)

    # 4g. Set embedding_provider_id to OpenAI (embedding is always OpenAI-compatible)
    op.execute("""
        UPDATE system_settings
        SET embedding_provider_id = (
            SELECT id FROM llm_providers WHERE name = 'OpenAI (migrated)' LIMIT 1
        )
        WHERE id = 1
          AND EXISTS (SELECT 1 FROM llm_providers WHERE name = 'OpenAI (migrated)')
    """)


def downgrade() -> None:
    # Drop FK constraints
    op.drop_constraint("fk_system_settings_chat_provider", "system_settings", type_="foreignkey")
    op.drop_constraint("fk_system_settings_analysis_provider", "system_settings", type_="foreignkey")
    op.drop_constraint("fk_system_settings_synthesis_provider", "system_settings", type_="foreignkey")
    op.drop_constraint("fk_system_settings_embedding_provider", "system_settings", type_="foreignkey")

    # Drop FK columns
    op.drop_column("system_settings", "chat_provider_id")
    op.drop_column("system_settings", "analysis_provider_id")
    op.drop_column("system_settings", "synthesis_provider_id")
    op.drop_column("system_settings", "embedding_provider_id")

    # Drop table
    op.drop_table("llm_providers")
