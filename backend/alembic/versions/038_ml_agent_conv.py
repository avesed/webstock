"""Add agent_conversation JSONB to ml_backtests.

Revision ID: 038_ml_agent_conv
Revises: 037_add_ml_backtests
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "038_ml_agent_conv"
down_revision: Union[str, None] = "037_add_ml_backtests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agent conversation state (messages + pending info) for async checkpoint/resume
    op.execute(
        "ALTER TABLE ml_backtests ADD COLUMN IF NOT EXISTS agent_conversation JSONB"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ml_backtests DROP COLUMN IF EXISTS agent_conversation"
    )
