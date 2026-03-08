"""Add ml_backtests table for historical cutoff backtesting.

Revision ID: 037_add_ml_backtests
Revises: 036_add_stock_sectors
Create Date: 2026-03-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "037_add_ml_backtests"
down_revision: Union[str, None] = "036_add_stock_sectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ml_backtests (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            market VARCHAR(10) NOT NULL,
            cutoff_date DATE NOT NULL,
            validation_days INT NOT NULL,
            forward_days INT NOT NULL DEFAULT 5,
            -- Configuration
            config_override JSONB,
            effective_config JSONB NOT NULL,
            -- Training metrics
            train_ic FLOAT,
            train_icir FLOAT,
            train_ndcg FLOAT,
            fold_ics JSONB,
            ensemble_size INT,
            feature_count INT,
            symbol_count INT,
            -- Validation metrics (core)
            val_ic FLOAT,
            val_icir FLOAT,
            val_direction_accuracy FLOAT,
            val_spread FLOAT,
            val_q1_return FLOAT,
            val_q5_return FLOAT,
            val_hit_rate FLOAT,
            val_max_drawdown FLOAT,
            -- Detailed results
            results JSONB NOT NULL DEFAULT '{}'::jsonb,
            -- Metadata
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            error TEXT,
            duration_seconds FLOAT,
            agent_run_id VARCHAR(64),
            agent_iteration INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ml_bt_market
        ON ml_backtests(market, created_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ml_bt_status
        ON ml_backtests(status)
        WHERE status IN ('pending', 'running')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ml_bt_status")
    op.execute("DROP INDEX IF EXISTS ix_ml_bt_market")
    op.execute("DROP TABLE IF EXISTS ml_backtests")
