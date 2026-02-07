"""Celery-safe async database utilities.

Celery workers run tasks in separate processes with different event loops.
SQLAlchemy async engines/pools cannot be shared across event loops.
This module provides utilities to create fresh connections per task.
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.user_ai_config import UserAIConfig, current_user_ai_config

logger = logging.getLogger(__name__)


def create_task_engine():
    """Create a new async engine for use in a Celery task.

    Uses NullPool to avoid connection pool issues across event loops.
    Each task gets fresh connections that are closed when done.
    """
    return create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,  # No pooling - fresh connections per task
        echo=settings.DEBUG,
    )


@asynccontextmanager
async def get_task_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session for use in a Celery task.

    Creates a fresh engine and session, ensuring no event loop conflicts.
    The engine and connection are disposed after the session exits.

    Usage:
        async with get_task_session() as db:
            result = await db.execute(query)
            await db.commit()
    """
    engine = create_task_engine()
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        try:
            yield session
        except Exception as e:
            logger.warning(f"Task session rollback due to exception: {e}")
            await session.rollback()
            raise
        finally:
            await engine.dispose()


async def get_system_ai_config(db: AsyncSession) -> UserAIConfig:
    """Load system AI configuration from database.

    For Celery tasks that don't have a user context, this loads
    the system-level settings configured by admin.

    Priority: system_settings > environment variables

    Returns:
        UserAIConfig with system-level settings
    """
    from app.models.system_settings import SystemSettings

    result = await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    system = result.scalar_one_or_none()

    if system:
        api_key = system.openai_api_key or settings.OPENAI_API_KEY
        base_url = system.openai_base_url or settings.OPENAI_API_BASE
        model = system.openai_model or settings.OPENAI_MODEL
        max_tokens = system.openai_max_tokens
        temperature = system.openai_temperature
    else:
        api_key = settings.OPENAI_API_KEY
        base_url = settings.OPENAI_API_BASE
        model = settings.OPENAI_MODEL
        max_tokens = None
        temperature = None

    return UserAIConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system_prompt=None,
    )


@asynccontextmanager
async def setup_task_ai_context():
    """Context manager to set up AI config for a Celery task.

    Loads system settings from database and sets them in the
    current_user_ai_config context variable so that OpenAI client
    uses the correct API key and settings.

    Usage:
        async with setup_task_ai_context():
            # OpenAI calls will use system settings
            embedding = await embedding_service.generate_embedding(text)
    """
    async with get_task_session() as db:
        config = await get_system_ai_config(db)

    # Set the config in context
    token = current_user_ai_config.set(config)
    try:
        yield config
    finally:
        current_user_ai_config.reset(token)
