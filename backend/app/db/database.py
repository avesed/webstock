"""PostgreSQL async database connection using SQLAlchemy."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event, text
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base class."""

    pass


# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,  # Recycle connections after 1 hour to handle stale connections
    echo=settings.DEBUG,
)

logger.info(
    "Database engine created (pool_size=%d, max_overflow=%d)",
    settings.DATABASE_POOL_SIZE,
    settings.DATABASE_MAX_OVERFLOW,
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency to get async database session.

    Note: This does NOT auto-commit. Callers must explicitly commit
    their transactions when needed. Rollback is automatic on exceptions.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as e:
            logger.warning(f"Database session rollback due to exception: {e}")
            await session.rollback()
            raise


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager to get async database session.

    This is useful for non-FastAPI contexts (e.g., services, background tasks)
    where dependency injection is not available.

    Usage:
        async with get_async_session() as session:
            result = await session.execute(query)

    Note: This does NOT auto-commit. Callers must explicitly commit
    their transactions when needed.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as e:
            logger.warning(f"Database session rollback due to exception: {e}")
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database extensions required by the application.

    Schema DDL is managed exclusively by Alembic migrations.
    This function only ensures PostgreSQL extensions are available.
    """
    async with engine.begin() as conn:
        logger.info("Ensuring required PostgreSQL extensions exist")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\""))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"pg_trgm\""))

        # pgvector is optional -- RAG features degrade gracefully without it
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("PostgreSQL extensions verified (including pgvector)")

            raw_conn = await conn.get_raw_connection()
            await _register_vector_on_connection(raw_conn.driver_connection)
        except Exception as e:
            logger.warning(
                "pgvector extension not available -- RAG features will be disabled: %s", e
            )

    logger.info("Database initialization complete")


async def _register_vector_on_connection(connection) -> None:
    """Register pgvector type codecs with an asyncpg connection."""
    try:
        from pgvector.asyncpg import register_vector
        await register_vector(connection)
        logger.debug("pgvector types registered on connection")
    except Exception as e:
        logger.error("Failed to register pgvector types: %s", e)
        raise


@event.listens_for(engine.sync_engine, "connect")
def _on_connect(dbapi_connection, connection_record):
    """Log new database connections for monitoring."""
    logger.debug("New database connection established")


async def close_db() -> None:
    """Close database connection pool."""
    logger.info("Disposing database engine")
    await engine.dispose()
