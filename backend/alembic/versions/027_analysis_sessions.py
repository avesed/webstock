"""Add analysis sessions table.

Revision ID: 027_analysis_sessions
Revises: 026_add_discussion
Create Date: 2026-02-22

New tables:
  - analysis_sessions: Stores AI analysis results for browsing history
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "027_analysis_sessions"
down_revision = "026_add_discussion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("language", sa.String(5), nullable=False, server_default="zh"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("agent_results", JSONB, nullable=True),
        sa.Column("synthesis_content", sa.Text(), nullable=True),
        sa.Column("clarification_rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_analysis_sessions_user_id", "analysis_sessions", ["user_id"])
    op.create_index("ix_analysis_sessions_symbol", "analysis_sessions", ["symbol"])
    op.create_index("ix_analysis_sessions_created_at", "analysis_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_analysis_sessions_created_at", table_name="analysis_sessions")
    op.drop_index("ix_analysis_sessions_symbol", table_name="analysis_sessions")
    op.drop_index("ix_analysis_sessions_user_id", table_name="analysis_sessions")
    op.drop_table("analysis_sessions")
