"""Update ml_backtests partial index to include 'suspended' status.

Revision ID: 039_ml_bt_status_idx
Revises: 038_ml_agent_conv
"""

from alembic import op

revision = "039_ml_bt_status_idx"
down_revision = "038_ml_agent_conv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ml_bt_status")

    op.execute("""
        CREATE INDEX ix_ml_bt_status
        ON ml_backtests(status)
        WHERE status IN ('pending', 'running', 'suspended')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ml_bt_status")

    op.execute("""
        CREATE INDEX ix_ml_bt_status
        ON ml_backtests(status)
        WHERE status IN ('pending', 'running')
    """)
