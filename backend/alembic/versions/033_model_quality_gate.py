"""Add quality_passed to prediction_models.

Adds a boolean column to track whether a trained model passed the quality
gate (IC/ICIR thresholds). Defaults to TRUE so all existing models are
considered passed for backward compatibility.

Revision ID: 033_model_quality_gate
Revises: 032_fund_extra_cols
Create Date: 2026-02-28
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "033_model_quality_gate"
down_revision: Union[str, None] = "032_fund_extra_cols"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add quality_passed boolean column to prediction_models."""

    op.execute(
        "ALTER TABLE prediction_models "
        "ADD COLUMN IF NOT EXISTS quality_passed BOOLEAN DEFAULT TRUE"
    )


def downgrade() -> None:
    """Remove quality_passed column."""

    op.execute(
        "ALTER TABLE prediction_models "
        "DROP COLUMN IF EXISTS quality_passed"
    )
