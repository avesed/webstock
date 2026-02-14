"""Unified settings service with priority resolution."""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.llm_provider import LlmProvider
from app.models.system_settings import SystemSettings
from app.models.user_settings import UserSettings

logger = logging.getLogger(__name__)


@dataclass
class ResolvedModelConfig:
    """
    Fully resolved configuration for a single model purpose.

    Returned by resolve_model_provider() after looking up the provider
    table and falling back to legacy flat columns.
    """

    model: str
    provider_type: str  # "openai" or "anthropic"
    api_key: Optional[str]
    base_url: Optional[str]


@dataclass
class LangGraphConfig:
    """
    Configuration for LangGraph workflow.

    Contains settings for the layered LLM architecture:
    - OpenAI-compatible local model settings (vLLM, Ollama, LMStudio, etc.)
    - Analysis and synthesis model configurations
    - Clarification behavior settings
    """

    local_llm_base_url: Optional[str]
    analysis_model: str
    synthesis_model: str
    use_local_models: bool
    max_clarification_rounds: int
    clarification_confidence_threshold: float

    # API keys for cloud models (fallback when not using local)
    openai_api_key: Optional[str]
    openai_base_url: Optional[str]

    # Anthropic settings
    anthropic_api_key: Optional[str]
    anthropic_base_url: Optional[str]


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

        # Resolve each setting with priority (user > system DB, no env fallback)
        if can_customize and user_settings:
            # User can customize - apply user > system priority
            api_key = (
                user_settings.openai_api_key
                or system.openai_api_key
            )
            base_url = (
                user_settings.openai_base_url
                or system.openai_base_url
            )
            model = (
                user_settings.openai_model
                or system.openai_model
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
            # User cannot customize - use system DB only
            api_key = system.openai_api_key
            base_url = system.openai_base_url
            model = system.openai_model
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

    async def resolve_model_provider(
        self, db: AsyncSession, purpose: str
    ) -> ResolvedModelConfig:
        """
        Resolve model assignment to a complete provider configuration.

        Priority: provider_id FK -> fallback to legacy flat columns.

        Args:
            db: Async database session
            purpose: One of 'chat', 'analysis', 'synthesis', 'embedding',
                'news_filter', 'content_extraction', 'phase2_layer15_cleaning',
                'phase2_layer2_scoring', 'phase2_layer2_analysis',
                'phase2_layer2_lightweight', 'layer1_scoring'

        Returns:
            ResolvedModelConfig with model, provider_type, api_key, base_url

        Raises:
            ValueError: If no configuration can be resolved
        """
        system = await self.get_system_settings(db)

        # Map purpose to FK column and model column
        purpose_map = {
            "chat": ("chat_provider_id", "openai_model"),
            "analysis": ("analysis_provider_id", "analysis_model"),
            "synthesis": ("synthesis_provider_id", "synthesis_model"),
            "embedding": ("embedding_provider_id", "embedding_model"),
            "news_filter": ("news_filter_provider_id", "news_filter_model"),
            "content_extraction": ("content_extraction_provider_id", "content_extraction_model"),
            # Phase 2: 4-layer multi-agent architecture
            "phase2_layer15_cleaning": ("phase2_layer15_cleaning_provider_id", "phase2_layer15_cleaning_model"),
            "phase2_layer2_scoring": ("phase2_layer2_scoring_provider_id", "phase2_layer2_scoring_model"),
            "phase2_layer2_analysis": ("phase2_layer2_analysis_provider_id", "phase2_layer2_analysis_model"),
            "phase2_layer2_lightweight": ("phase2_layer2_lightweight_provider_id", "phase2_layer2_lightweight_model"),
            # Layer 1: 3-agent scoring
            "layer1_scoring": ("layer1_scoring_provider_id", "layer1_scoring_model"),
        }

        if purpose not in purpose_map:
            raise ValueError(f"Unknown purpose: {purpose}")

        provider_id_attr, model_attr = purpose_map[purpose]
        provider_id = getattr(system, provider_id_attr, None)
        model_name = getattr(system, model_attr, None)

        # Try provider FK first
        if provider_id:
            result = await db.execute(
                select(LlmProvider).where(
                    LlmProvider.id == provider_id,
                    LlmProvider.is_enabled == True,
                )
            )
            provider = result.scalar_one_or_none()
            if provider:
                logger.debug(
                    "Resolved %s via provider '%s' (type=%s, model=%s)",
                    purpose, provider.name, provider.provider_type, model_name,
                )
                return ResolvedModelConfig(
                    model=model_name or "",
                    provider_type=provider.provider_type,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                )
            else:
                logger.warning(
                    "Provider %s for purpose '%s' not found or disabled, "
                    "falling back to legacy columns",
                    provider_id, purpose,
                )

        # Fallback to legacy flat columns
        logger.debug(
            "Resolving %s via legacy flat columns (no provider_id set)",
            purpose,
        )

        # Detect provider type from model name
        from app.core.llm.config import detect_provider, ProviderType

        if not model_name:
            raise ValueError(
                f"No model configured for '{purpose}'. "
                f"Please configure it in Admin Settings."
            )

        provider_type = detect_provider(model_name)

        if provider_type == ProviderType.ANTHROPIC:
            api_key = system.anthropic_api_key
            base_url = system.anthropic_base_url
        else:
            # Check local model override
            if system.use_local_models and system.local_llm_base_url:
                api_key = "not-needed"
                base_url = system.local_llm_base_url
            else:
                api_key = system.openai_api_key
                base_url = system.openai_base_url

        return ResolvedModelConfig(
            model=model_name,
            provider_type=provider_type.value,
            api_key=api_key,
            base_url=base_url,
        )

    async def get_langgraph_config(self, db: AsyncSession) -> LangGraphConfig:
        """
        Get LangGraph workflow configuration.

        Returns configuration for the layered LLM architecture including:
        - vLLM/local model settings
        - Analysis and synthesis model configurations
        - Clarification behavior settings
        - Fallback API keys for cloud models

        Args:
            db: Async database session

        Returns:
            LangGraphConfig with the workflow configuration
        """
        system = await self.get_system_settings(db)

        # Get API key/base URL from database only (no env fallback)
        openai_api_key = system.openai_api_key
        openai_base_url = system.openai_base_url
        anthropic_api_key = system.anthropic_api_key
        anthropic_base_url = system.anthropic_base_url

        # Get model configurations with fallbacks
        analysis_model = system.analysis_model or "gpt-4o-mini"
        synthesis_model = system.synthesis_model or "gpt-4o"

        config = LangGraphConfig(
            local_llm_base_url=system.local_llm_base_url,
            analysis_model=analysis_model,
            synthesis_model=synthesis_model,
            use_local_models=system.use_local_models,
            max_clarification_rounds=system.max_clarification_rounds,
            clarification_confidence_threshold=system.clarification_confidence_threshold,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            anthropic_api_key=anthropic_api_key,
            anthropic_base_url=anthropic_base_url,
        )

        logger.debug(
            "LangGraph config: use_local=%s, analysis_model=%s, synthesis_model=%s",
            config.use_local_models,
            config.analysis_model,
            config.synthesis_model,
        )

        return config


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
