"""Settings API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.settings import (
    UpdateSettingsRequest,
    UserSettingsResponse,
    NotificationSettings,
    ApiKeySettings,
    NewsSourceSettings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


async def get_or_create_user_settings(
    user_id: int,
    db: AsyncSession,
) -> UserSettings:
    """Get or create user settings."""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    settings = result.scalar_one_or_none()
    
    if not settings:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
        logger.info(f"Created settings for user {user_id}")
    
    return settings


@router.get(
    "",
    response_model=UserSettingsResponse,
    summary="Get user settings",
    description="Get current user's settings including notifications and API keys.",
)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user settings."""
    settings = await get_or_create_user_settings(current_user.id, db)
    
    return UserSettingsResponse(
        notifications=NotificationSettings(
            price_alerts=settings.notify_price_alerts,
            news_alerts=settings.notify_news_alerts,
            report_notifications=settings.notify_report_generation,
            email_notifications=settings.notify_email,
        ),
        api_keys=ApiKeySettings(
            finnhub_api_key=settings.finnhub_api_key,
            openai_api_key=settings.openai_api_key,
            openai_base_url=settings.openai_base_url,
            openai_model=settings.openai_model,
            openai_max_tokens=settings.openai_max_tokens,
            openai_temperature=settings.openai_temperature,
            openai_system_prompt=settings.openai_system_prompt,
        ),
        news_source=NewsSourceSettings(
            source=settings.news_source or "yfinance",
        ),
    )


@router.put(
    "",
    response_model=UserSettingsResponse,
    summary="Update user settings",
    description="Update user settings including notifications and API keys.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def update_settings(
    data: UpdateSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user settings."""
    settings = await get_or_create_user_settings(current_user.id, db)
    
    # Update notifications
    if data.notifications:
        if data.notifications.price_alerts is not None:
            settings.notify_price_alerts = data.notifications.price_alerts
        if data.notifications.news_alerts is not None:
            settings.notify_news_alerts = data.notifications.news_alerts
        if data.notifications.report_notifications is not None:
            settings.notify_report_generation = data.notifications.report_notifications
        if data.notifications.email_notifications is not None:
            settings.notify_email = data.notifications.email_notifications
    
    # Update API keys
    if data.api_keys:
        if data.api_keys.finnhub_api_key is not None:
            settings.finnhub_api_key = data.api_keys.finnhub_api_key or None
        if data.api_keys.openai_api_key is not None:
            settings.openai_api_key = data.api_keys.openai_api_key or None
        if data.api_keys.openai_base_url is not None:
            settings.openai_base_url = data.api_keys.openai_base_url or None
        if data.api_keys.openai_model is not None:
            settings.openai_model = data.api_keys.openai_model or None
        if data.api_keys.openai_max_tokens is not None:
            settings.openai_max_tokens = data.api_keys.openai_max_tokens or None
        if data.api_keys.openai_temperature is not None:
            settings.openai_temperature = data.api_keys.openai_temperature
        if data.api_keys.openai_system_prompt is not None:
            settings.openai_system_prompt = data.api_keys.openai_system_prompt or None

    # Update news source
    if data.news_source and data.news_source.source is not None:
        settings.news_source = data.news_source.source
    
    await db.commit()
    await db.refresh(settings)
    
    logger.info(f"Updated settings for user {current_user.id}")
    
    return UserSettingsResponse(
        notifications=NotificationSettings(
            price_alerts=settings.notify_price_alerts,
            news_alerts=settings.notify_news_alerts,
            report_notifications=settings.notify_report_generation,
            email_notifications=settings.notify_email,
        ),
        api_keys=ApiKeySettings(
            finnhub_api_key=settings.finnhub_api_key,
            openai_api_key=settings.openai_api_key,
            openai_base_url=settings.openai_base_url,
            openai_model=settings.openai_model,
            openai_max_tokens=settings.openai_max_tokens,
            openai_temperature=settings.openai_temperature,
            openai_system_prompt=settings.openai_system_prompt,
        ),
        news_source=NewsSourceSettings(
            source=settings.news_source or "yfinance",
        ),
    )
