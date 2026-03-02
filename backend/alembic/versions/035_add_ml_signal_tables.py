"""Add ML signal tables: FCF/valuation columns + earnings/analyst/options/insider tables.

Adds 9 new columns to stock_fundamentals for FCF-derived and short-interest features,
plus 4 new tables for time-series ML signals: EPS surprise events, analyst snapshots,
options flow, and insider activity.

Revision ID: 035_add_ml_signal_tables
Revises: 034_normalize_news_market
Create Date: 2026-03-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "035_add_ml_signal_tables"
down_revision: Union[str, None] = "034_normalize_news_market"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New columns on stock_fundamentals (Category 1: FCF/valuation)
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS fcf_margin NUMERIC(10,4)")
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS fcf_yield NUMERIC(10,4)")
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS capex_ratio NUMERIC(10,4)")
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS buyback_yield NUMERIC(10,4)")
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS ev_ebitda NUMERIC(12,4)")
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS rd_ratio NUMERIC(10,4)")
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS net_cash_ratio NUMERIC(10,4)")
    # Short interest (from daily .info collection)
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS short_pct_float NUMERIC(8,4)")
    op.execute("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS short_ratio NUMERIC(8,4)")

    # ------------------------------------------------------------------
    # 2. stock_earnings_events (Category 2: EPS surprise)
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TABLE IF NOT EXISTS stock_earnings_events ("
        "    id BIGSERIAL PRIMARY KEY,"
        "    symbol TEXT NOT NULL,"
        "    market TEXT NOT NULL,"
        "    earnings_date DATE NOT NULL,"
        "    eps_estimate NUMERIC(10,4),"
        "    eps_actual NUMERIC(10,4),"
        "    surprise_pct NUMERIC(10,4),"
        "    collected_at TIMESTAMPTZ DEFAULT NOW(),"
        "    UNIQUE(symbol, earnings_date)"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_earnings_events_symbol "
        "ON stock_earnings_events(symbol, earnings_date DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_earnings_events_market "
        "ON stock_earnings_events(market, earnings_date DESC)"
    )

    # ------------------------------------------------------------------
    # 3. stock_analyst_snapshots (Category 3: analyst recommendations)
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TABLE IF NOT EXISTS stock_analyst_snapshots ("
        "    id BIGSERIAL PRIMARY KEY,"
        "    symbol TEXT NOT NULL,"
        "    market TEXT NOT NULL,"
        "    snapshot_date DATE NOT NULL,"
        "    target_price_mean NUMERIC(10,2),"
        "    target_price_high NUMERIC(10,2),"
        "    target_price_low NUMERIC(10,2),"
        "    analyst_buy INTEGER,"
        "    analyst_hold INTEGER,"
        "    analyst_sell INTEGER,"
        "    analyst_strong_buy INTEGER,"
        "    analyst_strong_sell INTEGER,"
        "    eps_revision_up_7d INTEGER,"
        "    eps_revision_down_7d INTEGER,"
        "    eps_revision_up_30d INTEGER,"
        "    eps_revision_down_30d INTEGER,"
        "    growth_est_current_q NUMERIC(8,4),"
        "    growth_est_next_y NUMERIC(8,4),"
        "    UNIQUE(symbol, snapshot_date)"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_analyst_snapshots_symbol "
        "ON stock_analyst_snapshots(symbol, snapshot_date DESC)"
    )

    # ------------------------------------------------------------------
    # 4. stock_options_flow (Category 4: put/call ratio)
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TABLE IF NOT EXISTS stock_options_flow ("
        "    id BIGSERIAL PRIMARY KEY,"
        "    symbol TEXT NOT NULL,"
        "    market TEXT NOT NULL,"
        "    flow_date DATE NOT NULL,"
        "    put_call_ratio NUMERIC(8,4),"
        "    total_call_oi BIGINT,"
        "    total_put_oi BIGINT,"
        "    nearest_expiry DATE,"
        "    UNIQUE(symbol, flow_date)"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_options_flow_symbol "
        "ON stock_options_flow(symbol, flow_date DESC)"
    )

    # ------------------------------------------------------------------
    # 5. stock_insider_activity (Category 4: insider buying)
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TABLE IF NOT EXISTS stock_insider_activity ("
        "    id BIGSERIAL PRIMARY KEY,"
        "    symbol TEXT NOT NULL,"
        "    market TEXT NOT NULL,"
        "    activity_date DATE NOT NULL,"
        "    net_shares_pct NUMERIC(8,6),"
        "    buy_transactions INTEGER,"
        "    sell_transactions INTEGER,"
        "    insider_ownership_pct NUMERIC(8,4),"
        "    UNIQUE(symbol, activity_date)"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_insider_activity_symbol "
        "ON stock_insider_activity(symbol, activity_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stock_insider_activity")
    op.execute("DROP TABLE IF EXISTS stock_options_flow")
    op.execute("DROP TABLE IF EXISTS stock_analyst_snapshots")
    op.execute("DROP TABLE IF EXISTS stock_earnings_events")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS short_ratio")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS short_pct_float")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS net_cash_ratio")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS rd_ratio")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS ev_ebitda")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS buyback_yield")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS capex_ratio")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS fcf_yield")
    op.execute("ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS fcf_margin")
