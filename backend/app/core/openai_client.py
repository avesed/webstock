"""Shared OpenAI client manager for all AI features."""

import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.user_ai_config import current_user_ai_config

logger = logging.getLogger(__name__)


class OpenAIClientManager:
    """
    Manages OpenAI client instances with per-user config support.

    Provides a shared global client for default config and creates
    per-request clients when users have custom API keys/base URLs.
    Used by ChatService and EmbeddingService.
    """

    def __init__(self) -> None:
        self._global_client: Optional[AsyncOpenAI] = None

    def get_client(self) -> AsyncOpenAI:
        """
        Get an OpenAI client, respecting per-user configuration.

        If the current context has user-specific API key/base_url,
        creates a fresh client for that request. Otherwise returns
        the shared global client.

        Returns:
            AsyncOpenAI client instance

        Raises:
            ValueError: If no API key is configured
        """
        user_config = current_user_ai_config.get()

        # Per-user client for custom API key/base_url
        if user_config and (user_config.api_key or user_config.base_url):
            api_key = user_config.api_key or settings.OPENAI_API_KEY
            base_url = user_config.base_url or settings.OPENAI_API_BASE
            if not api_key:
                raise ValueError("OPENAI_API_KEY is not configured")
            logger.debug("Creating per-user OpenAI client")
            return AsyncOpenAI(api_key=api_key, base_url=base_url)

        # Global shared client
        if self._global_client is None:
            if not settings.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY is not configured")
            logger.info("Creating global OpenAI client (base_url=%s)", settings.OPENAI_API_BASE or "default")
            self._global_client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_API_BASE,
            )
        return self._global_client

    def get_model(self) -> str:
        """Get the model to use, respecting per-user configuration."""
        user_config = current_user_ai_config.get()
        if user_config and user_config.model:
            return user_config.model
        return settings.OPENAI_MODEL

    def get_max_tokens(self) -> Optional[int]:
        """Get max_tokens if user explicitly set it, otherwise None (use API default)."""
        user_config = current_user_ai_config.get()
        if user_config and user_config.max_tokens:
            return user_config.max_tokens
        return None  # Let API use its default

    def get_temperature(self) -> Optional[float]:
        """Get temperature, respecting per-user configuration. Returns None if not set."""
        user_config = current_user_ai_config.get()
        if user_config and user_config.temperature is not None:
            return user_config.temperature
        return None  # Let model use its default

    def get_system_prompt(self) -> Optional[str]:
        """Get custom system prompt if set by user."""
        user_config = current_user_ai_config.get()
        if user_config and user_config.system_prompt:
            return user_config.system_prompt
        return None

    async def close(self) -> None:
        """Close the global client."""
        if self._global_client is not None:
            logger.info("Closing global OpenAI client")
            try:
                await self._global_client.close()
            finally:
                self._global_client = None


# Singleton instance
_manager = OpenAIClientManager()


def get_openai_client_manager() -> OpenAIClientManager:
    """Get the singleton OpenAIClientManager instance."""
    return _manager


def get_openai_client() -> AsyncOpenAI:
    """Convenience function to get an OpenAI client."""
    return _manager.get_client()


def get_openai_model() -> str:
    """Convenience function to get the current model name."""
    return _manager.get_model()


def get_openai_max_tokens() -> Optional[int]:
    """Convenience function to get max_tokens (None if not set by user)."""
    return _manager.get_max_tokens()


def get_openai_temperature() -> Optional[float]:
    """Convenience function to get temperature (None if not set)."""
    return _manager.get_temperature()


def get_openai_system_prompt() -> Optional[str]:
    """Convenience function to get custom system prompt."""
    return _manager.get_system_prompt()


async def get_synthesis_model_config() -> tuple[AsyncOpenAI, str]:
    """
    Get OpenAI client and model configured for synthesis layer.

    This function returns the client and model name for the synthesis layer,
    which is used by the chat service. The synthesis layer typically uses
    a more capable model (e.g., gpt-4o) for user interactions.

    Returns:
        Tuple of (AsyncOpenAI client, model name)

    Raises:
        ValueError: If no API key is configured
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    try:
        async with get_async_session() as db:
            service = get_settings_service()
            config = await service.get_langgraph_config(db)

            if config.use_local_models and config.local_llm_base_url:
                # Use local model via OpenAI-compatible API
                logger.info(
                    "Chat using local synthesis model: %s at %s",
                    config.synthesis_model,
                    config.local_llm_base_url,
                )
                client = AsyncOpenAI(
                    api_key="not-needed",
                    base_url=config.local_llm_base_url,
                )
                return client, config.synthesis_model
            else:
                # Use cloud model
                api_key = config.openai_api_key
                base_url = config.openai_base_url
                if not api_key:
                    raise ValueError("OPENAI_API_KEY is not configured")
                logger.info("Chat using cloud synthesis model: %s", config.synthesis_model)
                client = AsyncOpenAI(api_key=api_key, base_url=base_url)
                return client, config.synthesis_model
    except Exception as e:
        logger.warning("Failed to get synthesis config from database: %s, using default", e)
        # Fall back to default client and model
        return _manager.get_client(), _manager.get_model()
