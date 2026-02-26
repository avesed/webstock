"""Add prediction infrastructure tables and system_settings columns.

Creates tables for ML prediction pipeline: prediction_universes (stock pools),
prediction_models (trained model metadata), stock_predictions (per-symbol scores),
stock_fundamentals (financial data), discovered_factors (LLM-discovered alpha).
Also adds prediction_provider_id/model/enabled to system_settings.

Revision ID: 031_prediction_infra
Revises: 030_l3_agent_models
Create Date: 2026-02-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "031_prediction_infra"
down_revision: Union[str, None] = "030_l3_agent_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """添加ML预测基础设施表和system_settings配置列。"""

    # --- system_settings new columns ---
    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN prediction_provider_id UUID "
        "REFERENCES llm_providers(id) ON DELETE SET NULL"
    )

    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN prediction_model VARCHAR(100)"
    )

    op.execute(
        "ALTER TABLE system_settings "
        "ADD COLUMN prediction_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )

    # --- prediction_universes table ---
    op.execute(
        "CREATE TABLE prediction_universes ("
        "    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),"
        "    name VARCHAR(100) NOT NULL,"
        "    market VARCHAR(10) NOT NULL,"
        "    universe_type VARCHAR(20) NOT NULL,"
        "    index_code VARCHAR(20),"
        "    symbols TEXT[],"
        "    is_default BOOLEAN NOT NULL DEFAULT FALSE,"
        "    is_active BOOLEAN NOT NULL DEFAULT TRUE,"
        "    created_at TIMESTAMPTZ DEFAULT NOW(),"
        "    updated_at TIMESTAMPTZ DEFAULT NOW(),"
        "    UNIQUE(name, market)"
        ")"
    )

    # --- Seed default universes ---
    op.execute(
        "INSERT INTO prediction_universes (name, market, universe_type, index_code, is_default) "
        "VALUES ('CSI300', 'cn', 'index', '000300', true)"
    )

    op.execute(
        "INSERT INTO prediction_universes (name, market, universe_type, index_code, is_default) "
        "VALUES ('S&P500', 'us', 'index', 'SPX', true)"
    )

    op.execute(
        "INSERT INTO prediction_universes (name, market, universe_type, index_code, is_default) "
        "VALUES ('HSI', 'hk', 'index', 'HSI', true)"
    )

    # --- prediction_models table ---
    op.execute(
        "CREATE TABLE prediction_models ("
        "    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),"
        "    market VARCHAR(10) NOT NULL,"
        "    model_date DATE NOT NULL,"
        "    train_start DATE,"
        "    train_end DATE,"
        "    val_start DATE,"
        "    val_end DATE,"
        "    forward_days INTEGER DEFAULT 5,"
        "    feature_count INTEGER,"
        "    symbol_count INTEGER,"
        "    feature_sources TEXT[] DEFAULT '{alpha158}',"
        "    ic NUMERIC(8,6),"
        "    icir NUMERIC(8,6),"
        "    ndcg NUMERIC(8,6),"
        "    model_path TEXT,"
        "    metadata JSONB,"
        "    created_at TIMESTAMPTZ DEFAULT NOW(),"
        "    UNIQUE(market, model_date, forward_days)"
        ")"
    )

    # --- stock_predictions table ---
    op.execute(
        "CREATE TABLE stock_predictions ("
        "    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),"
        "    market VARCHAR(10) NOT NULL,"
        "    prediction_date DATE NOT NULL,"
        "    model_id UUID REFERENCES prediction_models(id),"
        "    symbol VARCHAR(20) NOT NULL,"
        "    predicted_score NUMERIC(12,8),"
        "    percentile_rank NUMERIC(5,4),"
        "    predicted_direction VARCHAR(10),"
        "    actual_return NUMERIC(12,8),"
        "    forward_days INTEGER DEFAULT 5,"
        "    created_at TIMESTAMPTZ DEFAULT NOW(),"
        "    UNIQUE(market, prediction_date, symbol, forward_days)"
        ")"
    )

    op.execute(
        "CREATE INDEX ix_pred_market_date "
        "ON stock_predictions(market, prediction_date DESC)"
    )

    # --- stock_fundamentals table ---
    op.execute(
        "CREATE TABLE stock_fundamentals ("
        "    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
        "    symbol VARCHAR(20) NOT NULL,"
        "    market VARCHAR(10) NOT NULL,"
        "    date DATE NOT NULL,"
        "    record_type VARCHAR(10) NOT NULL,"
        "    pe_ratio NUMERIC(12,4),"
        "    pb_ratio NUMERIC(12,4),"
        "    ps_ratio NUMERIC(12,4),"
        "    roe NUMERIC(8,4),"
        "    roa NUMERIC(8,4),"
        "    profit_margin NUMERIC(8,4),"
        "    gross_margin NUMERIC(8,4),"
        "    revenue NUMERIC(20,2),"
        "    revenue_growth_yoy NUMERIC(8,4),"
        "    net_income NUMERIC(20,2),"
        "    eps NUMERIC(12,4),"
        "    debt_to_equity NUMERIC(12,4),"
        "    current_ratio NUMERIC(8,4),"
        "    dividend_yield NUMERIC(8,4),"
        "    market_cap NUMERIC(20,2),"
        "    data_source VARCHAR(20),"
        "    created_at TIMESTAMPTZ DEFAULT NOW(),"
        "    UNIQUE(symbol, date, record_type)"
        ")"
    )

    op.execute(
        "CREATE INDEX ix_fund_symbol_date "
        "ON stock_fundamentals(symbol, date DESC)"
    )

    op.execute(
        "CREATE INDEX ix_fund_market_date "
        "ON stock_fundamentals(market, date DESC)"
    )

    # --- discovered_factors table ---
    op.execute(
        "CREATE TABLE discovered_factors ("
        "    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),"
        "    name VARCHAR(100) NOT NULL,"
        "    expression TEXT NOT NULL,"
        "    description TEXT,"
        "    market VARCHAR(10) NOT NULL,"
        "    universe_id UUID REFERENCES prediction_universes(id),"
        "    ic NUMERIC(8,6),"
        "    icir NUMERIC(8,6),"
        "    discovery_round INTEGER,"
        "    is_active BOOLEAN DEFAULT TRUE,"
        "    metadata JSONB,"
        "    created_at TIMESTAMPTZ DEFAULT NOW(),"
        "    UNIQUE(expression, market)"
        ")"
    )

    # --- Additional indexes (H2, H3, H4) ---

    # H2: Partial index for backfill queries (rows missing actual_return)
    op.execute("""
CREATE INDEX ix_pred_backfill ON stock_predictions(prediction_date)
WHERE actual_return IS NULL
""")

    # H3: Index on discovered_factors for market+active filtering
    op.execute("CREATE INDEX ix_disc_factors_market_active ON discovered_factors(market, is_active)")

    # H4: Lookup index on prediction_models
    op.execute("CREATE INDEX ix_pred_models_lookup ON prediction_models(market, forward_days, model_date DESC)")

    # --- Fix FK actions for clean deletion (L7, L8) ---

    op.execute("ALTER TABLE stock_predictions DROP CONSTRAINT IF EXISTS stock_predictions_model_id_fkey")

    op.execute("""
ALTER TABLE stock_predictions ADD CONSTRAINT stock_predictions_model_id_fkey
FOREIGN KEY (model_id) REFERENCES prediction_models(id) ON DELETE SET NULL
""")

    op.execute("ALTER TABLE discovered_factors DROP CONSTRAINT IF EXISTS discovered_factors_universe_id_fkey")

    op.execute("""
ALTER TABLE discovered_factors ADD CONSTRAINT discovered_factors_universe_id_fkey
FOREIGN KEY (universe_id) REFERENCES prediction_universes(id) ON DELETE SET NULL
""")


def downgrade() -> None:
    """移除预测基础设施表和system_settings配置列。"""

    op.execute("DROP INDEX IF EXISTS ix_pred_backfill")

    op.execute("DROP INDEX IF EXISTS ix_disc_factors_market_active")

    op.execute("DROP INDEX IF EXISTS ix_pred_models_lookup")

    op.execute("DROP TABLE IF EXISTS discovered_factors")

    op.execute("DROP TABLE IF EXISTS stock_fundamentals")

    op.execute("DROP INDEX IF EXISTS ix_pred_market_date")

    op.execute("DROP TABLE IF EXISTS stock_predictions")

    op.execute("DROP TABLE IF EXISTS prediction_models")

    op.execute("DROP TABLE IF EXISTS prediction_universes")

    op.execute(
        "ALTER TABLE system_settings "
        "DROP COLUMN IF EXISTS prediction_enabled"
    )

    op.execute(
        "ALTER TABLE system_settings "
        "DROP COLUMN IF EXISTS prediction_model"
    )

    op.execute(
        "ALTER TABLE system_settings "
        "DROP COLUMN IF EXISTS prediction_provider_id"
    )
