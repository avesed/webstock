"""Shared helper functions for admin endpoints."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings

logger = logging.getLogger(__name__)


async def get_or_create_system_settings(db: AsyncSession) -> SystemSettings:
    """Get system settings or create default if not exists."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    settings = result.scalar_one_or_none()

    if not settings:
        settings = SystemSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
        logger.info("Created default system settings")

    return settings
