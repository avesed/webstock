"""Add qlib_backtests table for quantitative backtesting.

Stores backtest configurations and results from the Qlib
quantitative analysis service, including strategy parameters,
execution config, and equity curve / risk metrics.

Revision ID: 015_add_qlib_backtests
Revises: 014_add_pipeline_events
Create Date: 2026-02-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "015_add_qlib_backtests"
down_revision: Union[str, None] = "014_add_pipeline_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qlib_backtests",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("symbols", ARRAY(sa.Text), nullable=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=True),
        sa.Column("strategy_config", JSONB, nullable=True),
        sa.Column("execution_config", JSONB, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "progress",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column("qlib_task_id", sa.String(100), nullable=True),
        sa.Column("results", JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_qlib_backtests_user_id",
        "qlib_backtests",
        ["user_id"],
    )

    op.create_index(
        "ix_qlib_backtests_status",
        "qlib_backtests",
        ["status"],
    )


def downgrade() -> None:
    op.drop_table("qlib_backtests")
