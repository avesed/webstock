"""Configuration and model routing for the LLM gateway.

Handles provider detection from model names and config resolution
using two-layer priority: user settings -> system settings (DB).
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Supported LLM provider types."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class ProviderConfig:
    """Resolved configuration for creating a provider instance."""
    provider_type: ProviderType
    api_key: str
    base_url: Optional[str] = None  # Only for OpenAI-compatible endpoints


def detect_provider(model: str) -> ProviderType:
    """Detect provider from model name.

    Rules (checked in order):
    1. Starts with 'claude' -> Anthropic
    2. Everything else -> OpenAI (covers gpt-*, o1-*, local models, etc.)

    Simple and extensible. Add more prefixes for future providers.
    """
    model_lower = model.lower()
    if model_lower.startswith("claude"):
        return ProviderType.ANTHROPIC
    return ProviderType.OPENAI


def resolve_provider_config(
    model: str,
    *,
    # Per-request user override (from current_user_ai_config)
    user_api_key: Optional[str] = None,
    user_base_url: Optional[str] = None,
    # Database SystemSettings
    system_openai_api_key: Optional[str] = None,
    system_openai_base_url: Optional[str] = None,
    system_anthropic_api_key: Optional[str] = None,
    system_anthropic_base_url: Optional[str] = None,
    # Local model config (from SystemSettings.langgraph_config)
    local_llm_base_url: Optional[str] = None,
    use_local_models: bool = False,
) -> ProviderConfig:
    """Resolve provider configuration using three-layer priority.

    Priority: user settings -> system settings (DB) -> environment variables.

    Args:
        model: Model name (used to detect provider type)
        user_api_key: User's custom API key (if permitted)
        user_base_url: User's custom base URL
        system_openai_api_key: Admin-configured OpenAI key from DB
        system_openai_base_url: Admin-configured OpenAI base URL from DB
        system_anthropic_api_key: Admin-configured Anthropic key from DB
        system_anthropic_base_url: Admin-configured Anthropic base URL from DB
        local_llm_base_url: Local LLM endpoint URL
        use_local_models: Whether to use local models

    Returns:
        Resolved ProviderConfig

    Raises:
        ValueError: If no API key is available for the detected provider
    """
    provider_type = detect_provider(model)
    logger.debug("Detected provider %s for model %s", provider_type.value, model)

    if provider_type == ProviderType.ANTHROPIC:
        # Resolve API key: user -> system DB (no env fallback)
        if user_api_key:
            api_key = user_api_key
            key_source = "user"
        elif system_anthropic_api_key:
            api_key = system_anthropic_api_key
            key_source = "system_db"
        else:
            api_key = None
            key_source = "none"
        if not api_key:
            logger.error("No Anthropic API key available for model %s", model)
            raise ValueError(
                "No Anthropic API key configured. "
                "Please configure it in Admin Settings."
            )
        base_url = user_base_url or system_anthropic_base_url
        logger.debug(
            "Resolved Anthropic config: key_source=%s, base_url=%s",
            key_source, base_url or "default",
        )
        return ProviderConfig(
            provider_type=ProviderType.ANTHROPIC,
            api_key=api_key,
            base_url=base_url,
        )
    else:  # OpenAI / OpenAI-compatible
        # Local model override
        if use_local_models and local_llm_base_url:
            logger.debug(
                "Using local LLM override: base_url=%s", local_llm_base_url
            )
            return ProviderConfig(
                provider_type=ProviderType.OPENAI,
                api_key="not-needed",
                base_url=local_llm_base_url,
            )

        # Resolve API key: user -> system DB (no env fallback)
        if user_api_key:
            api_key = user_api_key
            key_source = "user"
        elif system_openai_api_key:
            api_key = system_openai_api_key
            key_source = "system_db"
        else:
            api_key = None
            key_source = "none"
        base_url = user_base_url or system_openai_base_url
        if not api_key:
            logger.error("No OpenAI API key available for model %s", model)
            raise ValueError(
                "No OpenAI API key configured. "
                "Please configure it in Admin Settings."
            )
        logger.debug(
            "Resolved OpenAI config: key_source=%s, base_url=%s",
            key_source, base_url or "default",
        )
        return ProviderConfig(
            provider_type=ProviderType.OPENAI,
            api_key=api_key,
            base_url=base_url,
        )
