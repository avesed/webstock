"""Add Tavily API key and MCP content extraction settings.

Adds tavily_api_key, enable_mcp_extraction, content_extraction_model,
and content_extraction_provider_id to system_settings.

Revision ID: 018_tavily_mcp_config
Revises: 017_content_src_trafila
Create Date: 2026-02-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "018_tavily_mcp_config"
down_revision: Union[str, None] = "017_content_src_trafila"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("tavily_api_key", sa.Text(), nullable=True,
                  comment="系统级 Tavily API Key（用于内容抓取兜底）"),
    )

    op.add_column(
        "system_settings",
        sa.Column("enable_mcp_extraction", sa.Boolean(), nullable=False,
                  server_default=sa.text("false"),
                  comment="是否启用 LLM+MCP 抓取新闻全文（需 Playwright MCP 服务）"),
    )

    op.add_column(
        "system_settings",
        sa.Column("content_extraction_model", sa.String(100), nullable=True,
                  server_default="gpt-4o-mini",
                  comment="MCP 内容抓取使用的 LLM 模型"),
    )

    op.add_column(
        "system_settings",
        sa.Column("content_extraction_provider_id",
                  postgresql.UUID(as_uuid=True), nullable=True,
                  comment="Provider for MCP content extraction model"),
    )

    op.create_foreign_key(
        "fk_system_settings_content_extraction_provider",
        "system_settings",
        "llm_providers",
        ["content_extraction_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_system_settings_content_extraction_provider",
        "system_settings",
        type_="foreignkey",
    )

    op.drop_column("system_settings", "content_extraction_provider_id")
    op.drop_column("system_settings", "content_extraction_model")
    op.drop_column("system_settings", "enable_mcp_extraction")
    op.drop_column("system_settings", "tavily_api_key")
