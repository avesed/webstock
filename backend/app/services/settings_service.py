"""Unified settings service with priority resolution."""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.models.system_settings import SystemSettings
from app.models.user_settings import UserSettings

logger = logging.getLogger(__name__)


@dataclass
class ResolvedAIConfig:
    """
    Resolved AI configuration with priority: user > system > env.

    This dataclass represents the final resolved configuration for AI services,
    after applying the priority chain:
    1. User settings (if user has can_use_custom_api_key permission)
    2. System settings (admin configured)
    3. Environment variables (fallback)

    Attributes:
        api_key: The OpenAI API key to use
        base_url: Custom OpenAI-compatible API base URL (optional)
        model: The model to use for chat/analysis
        max_tokens: Maximum tokens for completions (optional)
        temperature: Temperature parameter for responses (optional)
        system_prompt: Custom system prompt from user settings (optional)
    """

    api_key: Optional[str]
    base_url: Optional[str]
    model: str
    max_tokens: Optional[int]
    temperature: Optional[float]
    system_prompt: Optional[str]


class SettingsService:
    """
    Service for resolving settings with proper priority.

    This service implements the three-tier settings priority system:
    1. User settings (highest priority, requires permission)
    2. System settings (admin-configured defaults)
    3. Environment variables (fallback)

    The service ensures that users can only use custom API keys if they
    have been explicitly granted permission by an administrator.
    """

    async def get_system_settings(self, db: AsyncSession) -> SystemSettings:
        """
        Get system settings (creates default if not exists).

        This method implements a singleton pattern for system settings.
        If no settings exist in the database, default settings are created.

        Args:
            db: Async database session

        Returns:
            The SystemSettings instance (always id=1)

        Raises:
            Exception: If database query fails
        """
        try:
            result = await db.execute(
                select(SystemSettings).where(SystemSettings.id == 1)
            )
            system_settings = result.scalar_one_or_none()

            if not system_settings:
                system_settings = SystemSettings(id=1)
                db.add(system_settings)
                await db.commit()
                await db.refresh(system_settings)
                logger.info("Created default system settings")

            return system_settings
        except Exception as e:
            logger.error("Failed to get system settings: %s", str(e))
            raise

    async def get_user_ai_config(
        self,
        db: AsyncSession,
        user_id: int,
        user_settings: Optional[UserSettings] = None,
    ) -> ResolvedAIConfig:
        """
        Resolve AI configuration for a user.

        Priority (highest to lowest):
        1. User settings (if user has can_use_custom_api_key permission)
        2. System settings (admin configured)
        3. Environment variables (fallback)

        Args:
            db: Async database session
            user_id: The user's ID
            user_settings: Optional pre-loaded user settings to avoid extra query

        Returns:
            ResolvedAIConfig with the final configuration values
        """
        system = await self.get_system_settings(db)

        # Get user settings if not provided
        if user_settings is None:
            try:
                result = await db.execute(
                    select(UserSettings).where(UserSettings.user_id == user_id)
                )
                user_settings = result.scalar_one_or_none()
            except Exception as e:
                logger.error("Failed to get user settings for user %d: %s", user_id, str(e))
                raise

        # Check if user can use custom API keys
        # User can customize if:
        # 1. They have individual permission (can_use_custom_api_key), OR
        # 2. System allows all users to use custom keys (allow_user_custom_api_keys)
        can_customize = False
        if user_settings:
            can_customize = (
                user_settings.can_use_custom_api_key
                or system.allow_user_custom_api_keys
            )

        # Resolve each setting with priority
        if can_customize and user_settings:
            # User can customize - apply user > system > env priority
            api_key = (
                user_settings.openai_api_key
                or system.openai_api_key
                or app_settings.OPENAI_API_KEY
            )
            base_url = (
                user_settings.openai_base_url
                or system.openai_base_url
                or app_settings.OPENAI_API_BASE
            )
            model = (
                user_settings.openai_model
                or system.openai_model
                or app_settings.OPENAI_MODEL
            )
            max_tokens = (
                user_settings.openai_max_tokens or system.openai_max_tokens
            )
            # Temperature needs special handling for 0.0 (falsy but valid)
            if user_settings.openai_temperature is not None:
                temperature = user_settings.openai_temperature
            else:
                temperature = system.openai_temperature
            system_prompt = user_settings.openai_system_prompt
        else:
            # User cannot customize - use system/env only
            api_key = system.openai_api_key or app_settings.OPENAI_API_KEY
            base_url = system.openai_base_url or app_settings.OPENAI_API_BASE
            model = system.openai_model or app_settings.OPENAI_MODEL
            max_tokens = system.openai_max_tokens
            temperature = system.openai_temperature
            system_prompt = None

        logger.debug(
            f"Resolved AI config for user {user_id}: "
            f"model={model}, can_customize={can_customize}"
        )

        return ResolvedAIConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )

    async def user_can_customize_api(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> bool:
        """
        Check if a user has permission to customize API settings.

        A user can customize API settings if:
        1. They have individual permission (can_use_custom_api_key), OR
        2. System allows all users to use custom keys (allow_user_custom_api_keys)

        Args:
            db: Async database session
            user_id: The user's ID

        Returns:
            True if user can customize API settings, False otherwise
        """
        system = await self.get_system_settings(db)

        try:
            result = await db.execute(
                select(UserSettings.can_use_custom_api_key).where(
                    UserSettings.user_id == user_id
                )
            )
            row = result.first()
            user_can = row[0] if row else False
        except Exception as e:
            logger.error("Failed to check user API permission for user %d: %s", user_id, str(e))
            raise

        can_customize = user_can or system.allow_user_custom_api_keys

        logger.debug(
            "User %d can_customize_api: %s (user_permission=%s, global_allow=%s)",
            user_id,
            can_customize,
            user_can,
            system.allow_user_custom_api_keys,
        )

        return can_customize


# Singleton instance
_settings_service: Optional[SettingsService] = None


def get_settings_service() -> SettingsService:
    """
    Get singleton instance of SettingsService.

    Returns:
        The shared SettingsService instance
    """
    global _settings_service
    if _settings_service is None:
        _settings_service = SettingsService()
        logger.debug("Created SettingsService singleton instance")
    return _settings_service
