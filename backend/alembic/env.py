"""Alembic environment configuration for WebStock async migrations."""

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.db.database import Base

# Import all models so that Base.metadata is fully populated
# with all table definitions before Alembic inspects it.
from app.models import (  # noqa: F401
    User,
    UserSettings,
    LoginLog,
    Watchlist,
    WatchlistItem,
    News,
    NewsAlert,
    Portfolio,
    Holding,
    Transaction,
    PriceAlert,
    PushSubscription,
    Report,
    ReportSchedule,
)

logger = logging.getLogger("alembic.env")

# This is the Alembic Config object, which provides access
# to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the SQLAlchemy URL from application settings so that
# migrations always target the same database as the running app.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# The target metadata for 'autogenerate' support.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    # Log host/db only, not credentials
    safe_url = url.split("@")[-1] if "@" in url else url
    logger.info("Running offline migrations against: ...@%s", safe_url)

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations within a database connection context."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using an async engine.

    Creates an async Engine and associates a connection with the
    context, then runs all migrations within that connection.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    logger.info("Running online migrations with async engine")

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Delegates to the async migration runner to support
    the asyncpg driver used by the application.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    logger.info("Running migrations in offline mode")
    run_migrations_offline()
else:
    logger.info("Running migrations in online mode")
    run_migrations_online()
