"""Initial schema - create all base tables.

Creates all base tables that were previously only in init-db.sql,
making Alembic self-sufficient for fresh database deployments.
All statements use IF NOT EXISTS for idempotency.

Revision ID: 000_initial_schema
Revises: (none - first migration)
Create Date: 2026-02-09
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '000_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all base tables if they don't exist."""

    # === Extensions ===
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')

    # === Trigger function ===
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ language 'plpgsql'
    """)

    # === users ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_locked BOOLEAN NOT NULL DEFAULT FALSE,
            failed_login_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at)")
    op.execute("""
        DROP TRIGGER IF EXISTS update_users_updated_at ON users;
        CREATE TRIGGER update_users_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === user_settings ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            notify_price_alerts BOOLEAN NOT NULL DEFAULT TRUE,
            notify_news_alerts BOOLEAN NOT NULL DEFAULT TRUE,
            notify_report_generation BOOLEAN NOT NULL DEFAULT TRUE,
            notify_email BOOLEAN NOT NULL DEFAULT FALSE,
            finnhub_api_key TEXT,
            openai_api_key TEXT,
            openai_base_url VARCHAR(500),
            openai_model VARCHAR(100),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_settings_user_id ON user_settings(user_id)")
    op.execute("""
        DROP TRIGGER IF EXISTS update_user_settings_updated_at ON user_settings;
        CREATE TRIGGER update_user_settings_updated_at
            BEFORE UPDATE ON user_settings
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === login_logs ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            ip_address VARCHAR(45) NOT NULL,
            user_agent VARCHAR(512),
            success BOOLEAN NOT NULL,
            failure_reason VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_login_logs_user_id ON login_logs(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_login_logs_ip_address ON login_logs(ip_address)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_login_logs_success ON login_logs(success)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_login_logs_created_at ON login_logs(created_at)")

    # === watchlists ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_watchlists_user_id ON watchlists(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_watchlists_is_default ON watchlists(is_default)")
    op.execute("""
        DROP TRIGGER IF EXISTS update_watchlists_updated_at ON watchlists;
        CREATE TRIGGER update_watchlists_updated_at
            BEFORE UPDATE ON watchlists
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === watchlist_items ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_items (
            id SERIAL PRIMARY KEY,
            watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
            symbol VARCHAR(20) NOT NULL,
            notes TEXT,
            alert_price_above FLOAT,
            alert_price_below FLOAT,
            added_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_watchlist_symbol UNIQUE (watchlist_id, symbol)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_items_watchlist_id ON watchlist_items(watchlist_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_items_symbol ON watchlist_items(symbol)")

    # === news ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            symbol VARCHAR(20) NOT NULL,
            title VARCHAR(500) NOT NULL,
            summary TEXT,
            source VARCHAR(100) NOT NULL,
            url VARCHAR(1024) NOT NULL,
            published_at TIMESTAMP WITH TIME ZONE NOT NULL,
            sentiment_score FLOAT,
            ai_analysis TEXT,
            market VARCHAR(10) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_published_at ON news(published_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_market ON news(market)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_created_at ON news(created_at)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_news_url ON news(url)")

    # === news_alerts ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_alerts (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol VARCHAR(20),
            keywords VARCHAR(100)[] NOT NULL DEFAULT '{}',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_alerts_user_id ON news_alerts(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_alerts_symbol ON news_alerts(symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_alerts_is_active ON news_alerts(is_active)")

    # === portfolios ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS portfolios (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            currency VARCHAR(3) NOT NULL DEFAULT 'USD',
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_portfolios_user_id ON portfolios(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_portfolios_is_default ON portfolios(is_default)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_portfolios_created_at ON portfolios(created_at)")
    op.execute("""
        DROP TRIGGER IF EXISTS update_portfolios_updated_at ON portfolios;
        CREATE TRIGGER update_portfolios_updated_at
            BEFORE UPDATE ON portfolios
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === holdings ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            portfolio_id UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
            symbol VARCHAR(20) NOT NULL,
            quantity NUMERIC(18, 8) NOT NULL DEFAULT 0,
            average_cost NUMERIC(18, 8) NOT NULL DEFAULT 0,
            total_cost NUMERIC(18, 4) NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_portfolio_symbol UNIQUE (portfolio_id, symbol)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_holdings_portfolio_id ON holdings(portfolio_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_holdings_symbol ON holdings(symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_holdings_created_at ON holdings(created_at)")
    op.execute("""
        DROP TRIGGER IF EXISTS update_holdings_updated_at ON holdings;
        CREATE TRIGGER update_holdings_updated_at
            BEFORE UPDATE ON holdings
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === transactions ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            portfolio_id UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
            symbol VARCHAR(20) NOT NULL,
            type VARCHAR(20) NOT NULL,
            quantity NUMERIC(18, 8) NOT NULL,
            price NUMERIC(18, 8) NOT NULL,
            fee NUMERIC(18, 4) NOT NULL DEFAULT 0,
            total NUMERIC(18, 4) NOT NULL,
            date TIMESTAMP WITH TIME ZONE NOT NULL,
            notes TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_transaction_type CHECK (type IN ('buy', 'sell', 'dividend'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_transactions_portfolio_id ON transactions(portfolio_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_transactions_symbol ON transactions(symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_transactions_portfolio_date ON transactions(portfolio_id, date DESC)")

    # === price_alerts ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol VARCHAR(20) NOT NULL,
            condition_type VARCHAR(20) NOT NULL,
            threshold NUMERIC(18, 8) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_triggered BOOLEAN NOT NULL DEFAULT FALSE,
            triggered_at TIMESTAMP WITH TIME ZONE,
            note TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_condition_type CHECK (condition_type IN ('above', 'below', 'change_percent'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_user_id ON price_alerts(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_symbol ON price_alerts(symbol)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_is_active ON price_alerts(is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_is_triggered ON price_alerts(is_triggered)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_created_at ON price_alerts(created_at)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_alerts_active_symbol ON price_alerts(symbol)
            WHERE is_active = TRUE AND is_triggered = FALSE
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS update_price_alerts_updated_at ON price_alerts;
        CREATE TRIGGER update_price_alerts_updated_at
            BEFORE UPDATE ON price_alerts
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === push_subscriptions ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh_key VARCHAR(255) NOT NULL,
            auth_key VARCHAR(255) NOT NULL,
            user_agent VARCHAR(512),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user_id ON push_subscriptions(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_push_subscriptions_is_active ON push_subscriptions(is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_push_subscriptions_created_at ON push_subscriptions(created_at)")

    # === report_schedules ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS report_schedules (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            frequency VARCHAR(20) NOT NULL,
            time_of_day TIME NOT NULL,
            day_of_week INTEGER,
            day_of_month INTEGER,
            symbols VARCHAR(20)[] NOT NULL DEFAULT '{}',
            include_portfolio BOOLEAN NOT NULL DEFAULT FALSE,
            include_news BOOLEAN NOT NULL DEFAULT TRUE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_run_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_frequency CHECK (frequency IN ('daily', 'weekly', 'monthly')),
            CONSTRAINT chk_day_of_week CHECK (day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)),
            CONSTRAINT chk_day_of_month CHECK (day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_report_schedules_user_id ON report_schedules(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_report_schedules_is_active ON report_schedules(is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_report_schedules_frequency ON report_schedules(frequency)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_report_schedules_created_at ON report_schedules(created_at)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_report_schedules_active_time
            ON report_schedules(time_of_day) WHERE is_active = TRUE
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS update_report_schedules_updated_at ON report_schedules;
        CREATE TRIGGER update_report_schedules_updated_at
            BEFORE UPDATE ON report_schedules
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === reports ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            schedule_id UUID REFERENCES report_schedules(id) ON DELETE SET NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            content JSONB,
            format VARCHAR(20) NOT NULL DEFAULT 'json',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT chk_format CHECK (format IN ('json', 'html')),
            CONSTRAINT chk_status CHECK (status IN ('pending', 'generating', 'completed', 'failed'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reports_schedule_id ON reports(schedule_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reports_user_created ON reports(user_id, created_at DESC)")

    # === conversations ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255),
            symbol VARCHAR(20),
            is_archived BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_conversations_symbol ON conversations(symbol)")
    op.execute("""
        DROP TRIGGER IF EXISTS update_conversations_updated_at ON conversations;
        CREATE TRIGGER update_conversations_updated_at
            BEFORE UPDATE ON conversations
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
    """)

    # === chat_messages ===
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            token_count INTEGER,
            model VARCHAR(100),
            tool_calls JSONB,
            rag_context JSONB,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_id ON chat_messages(conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at)")


def downgrade() -> None:
    """Drop all base tables in reverse dependency order."""
    op.execute("DROP TABLE IF EXISTS chat_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS conversations CASCADE")
    op.execute("DROP TABLE IF EXISTS reports CASCADE")
    op.execute("DROP TABLE IF EXISTS report_schedules CASCADE")
    op.execute("DROP TABLE IF EXISTS push_subscriptions CASCADE")
    op.execute("DROP TABLE IF EXISTS price_alerts CASCADE")
    op.execute("DROP TABLE IF EXISTS transactions CASCADE")
    op.execute("DROP TABLE IF EXISTS holdings CASCADE")
    op.execute("DROP TABLE IF EXISTS portfolios CASCADE")
    op.execute("DROP TABLE IF EXISTS news_alerts CASCADE")
    op.execute("DROP TABLE IF EXISTS news CASCADE")
    op.execute("DROP TABLE IF EXISTS watchlist_items CASCADE")
    op.execute("DROP TABLE IF EXISTS watchlists CASCADE")
    op.execute("DROP TABLE IF EXISTS login_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS user_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
