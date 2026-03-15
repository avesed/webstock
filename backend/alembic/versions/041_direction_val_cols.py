"""Add direction model columns to ml_backtests.

Stores direction model AUC and Brier score alongside ranking metrics
so the ML agent can track direction model performance across sessions.

Revision ID: 041_direction_val_cols
Revises: 040_direction_autotune
"""

from alembic import op
import sqlalchemy as sa

revision = "041_direction_val_cols"
down_revision = "040_direction_autotune"


def upgrade():
    op.add_column(
        "ml_backtests",
        sa.Column("direction_auc", sa.Float(), nullable=True),
    )
    op.add_column(
        "ml_backtests",
        sa.Column("direction_brier", sa.Float(), nullable=True),
    )


def downgrade():
    op.drop_column("ml_backtests", "direction_brier")
    op.drop_column("ml_backtests", "direction_auc")
