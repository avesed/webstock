"""Add direction prediction columns and auto-tune settings.

- stock_predictions: add up_probability
- prediction_models: add model_type, auc, brier_score; update unique constraint
- system_settings: add auto_retrain/auto_tune columns

Revision ID: 040_direction_autotune
Revises: 039_ml_bt_status_idx
"""

from alembic import op
import sqlalchemy as sa

revision = "040_direction_autotune"
down_revision = "039_ml_bt_status_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- stock_predictions: add up_probability ---
    op.add_column(
        "stock_predictions",
        sa.Column("up_probability", sa.Float, nullable=True),
    )

    # --- prediction_models: add model_type, auc, brier_score ---
    op.add_column(
        "prediction_models",
        sa.Column(
            "model_type",
            sa.String(20),
            nullable=False,
            server_default="ranking",
        ),
    )

    op.add_column(
        "prediction_models",
        sa.Column("auc", sa.Numeric(8, 6), nullable=True),
    )

    op.add_column(
        "prediction_models",
        sa.Column("brier_score", sa.Numeric(8, 6), nullable=True),
    )

    # Drop old unique constraint, create new one including model_type.
    # Migration 031 created the constraint via raw SQL `UNIQUE(market, model_date, forward_days)`
    # so PostgreSQL auto-generated the name. Use DROP IF EXISTS for both possible names.
    op.execute(
        "ALTER TABLE prediction_models "
        "DROP CONSTRAINT IF EXISTS prediction_models_market_model_date_forward_days_key"
    )
    op.execute(
        "ALTER TABLE prediction_models "
        "DROP CONSTRAINT IF EXISTS uq_prediction_models_market_date_fwd"
    )

    op.create_unique_constraint(
        "uq_pred_models_mkt_date_fwd_type",
        "prediction_models",
        ["market", "model_date", "forward_days", "model_type"],
    )

    # --- system_settings: add auto-retrain / auto-tune columns ---
    op.add_column(
        "system_settings",
        sa.Column(
            "auto_retrain_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.add_column(
        "system_settings",
        sa.Column(
            "auto_retrain_interval_days",
            sa.Integer,
            nullable=False,
            server_default=sa.text("7"),
        ),
    )

    op.add_column(
        "system_settings",
        sa.Column(
            "auto_tune_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.add_column(
        "system_settings",
        sa.Column(
            "auto_tune_interval_days",
            sa.Integer,
            nullable=False,
            server_default=sa.text("30"),
        ),
    )

    op.add_column(
        "system_settings",
        sa.Column(
            "auto_tune_max_iterations",
            sa.Integer,
            nullable=False,
            server_default=sa.text("3"),
        ),
    )


def downgrade() -> None:
    # --- system_settings: drop auto-tune columns ---
    op.drop_column("system_settings", "auto_tune_max_iterations")
    op.drop_column("system_settings", "auto_tune_interval_days")
    op.drop_column("system_settings", "auto_tune_enabled")
    op.drop_column("system_settings", "auto_retrain_interval_days")
    op.drop_column("system_settings", "auto_retrain_enabled")

    # --- prediction_models: revert unique constraint, drop new columns ---
    op.drop_constraint(
        "uq_pred_models_mkt_date_fwd_type",
        "prediction_models",
        type_="unique",
    )

    # Restore original auto-generated constraint name format
    op.execute(
        "ALTER TABLE prediction_models "
        "ADD CONSTRAINT prediction_models_market_model_date_forward_days_key "
        "UNIQUE (market, model_date, forward_days)"
    )

    op.drop_column("prediction_models", "brier_score")
    op.drop_column("prediction_models", "auc")
    op.drop_column("prediction_models", "model_type")

    # --- stock_predictions: drop up_probability ---
    op.drop_column("stock_predictions", "up_probability")
