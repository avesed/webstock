"""Create integration_settings key-value table.

Stores admin-configurable integration settings (e.g. NewsForge URL,
API keys) as key-value pairs with upsert support.

Revision ID: 042_integration_settings
Revises: 041_direction_val_cols
"""

from alembic import op
import sqlalchemy as sa

revision = "042_integration_settings"
down_revision = "041_direction_val_cols"


def upgrade():
    op.create_table(
        "integration_settings",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_table("integration_settings")
