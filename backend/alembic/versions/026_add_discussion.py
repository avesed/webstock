"""Add discussion group tables and columns.

Revision ID: 026_add_discussion
Revises: 025_daily_bars_market_sym_idx
Create Date: 2026-02-21

New tables:
  - discussion_sessions: Stores discussion group sessions with metadata
  - discussion_messages: Stores individual messages from discussion rounds

Altered tables:
  - conversations: Add type and discussion_session_id columns
  - chat_messages: Add metadata JSONB column
  - system_settings: Add discussion feature config columns
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "026_add_discussion"
down_revision = "025_daily_bars_market_sym_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- discussion_sessions ---
    op.create_table(
        "discussion_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("language", sa.String(5), nullable=False, server_default="zh"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("config", JSONB, nullable=True),
        sa.Column("synthesis_report", sa.Text(), nullable=True),
        sa.Column("compact_context", sa.Text(), nullable=True),
        sa.Column("discussion_rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_discussion_sessions_user_id", "discussion_sessions", ["user_id"])
    op.create_index("ix_discussion_sessions_symbol", "discussion_sessions", ["symbol"])
    op.create_index("ix_discussion_sessions_status", "discussion_sessions", ["status"])

    # updated_at trigger (matches project convention)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_discussion_sessions_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_discussion_sessions_updated_at
        BEFORE UPDATE ON discussion_sessions
        FOR EACH ROW
        EXECUTE FUNCTION update_discussion_sessions_updated_at()
        """
    )

    # --- discussion_messages ---
    op.create_table(
        "discussion_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("discussion_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agent_type", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_data", JSONB, nullable=True),
        sa.Column("tool_calls", JSONB, nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_index(
        "ix_discussion_messages_session_round",
        "discussion_messages",
        ["session_id", "round", "created_at"],
    )

    # --- conversations: add type and discussion_session_id ---
    op.add_column(
        "conversations",
        sa.Column("type", sa.String(20), nullable=False, server_default="chat"),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "discussion_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("discussion_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_conversations_discussion_session_id",
        "conversations",
        ["discussion_session_id"],
        unique=True,
        postgresql_where=sa.text("discussion_session_id IS NOT NULL"),
    )

    # --- chat_messages: add metadata ---
    op.add_column(
        "chat_messages",
        sa.Column("metadata", JSONB, nullable=True),
    )

    # --- system_settings: add discussion config ---
    op.add_column(
        "system_settings",
        sa.Column("discussion_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "system_settings",
        sa.Column("discussion_max_rounds", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "system_settings",
        sa.Column(
            "discussion_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("llm_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "system_settings",
        sa.Column("discussion_model", sa.String(100), nullable=True, server_default="gpt-4o"),
    )


def downgrade() -> None:
    # Trigger cleanup
    op.execute("DROP TRIGGER IF EXISTS trg_discussion_sessions_updated_at ON discussion_sessions")
    op.execute("DROP FUNCTION IF EXISTS update_discussion_sessions_updated_at()")

    # system_settings columns
    op.drop_column("system_settings", "discussion_model")
    op.drop_column("system_settings", "discussion_provider_id")
    op.drop_column("system_settings", "discussion_max_rounds")
    op.drop_column("system_settings", "discussion_enabled")

    # chat_messages
    op.drop_column("chat_messages", "metadata")

    # conversations
    op.drop_index("ix_conversations_discussion_session_id", table_name="conversations")
    op.drop_column("conversations", "discussion_session_id")
    op.drop_column("conversations", "type")

    # discussion tables
    op.drop_index("ix_discussion_messages_session_round", table_name="discussion_messages")
    op.drop_table("discussion_messages")
    op.drop_index("ix_discussion_sessions_symbol", table_name="discussion_sessions")
    op.drop_index("ix_discussion_sessions_user_id", table_name="discussion_sessions")
    op.drop_table("discussion_sessions")
