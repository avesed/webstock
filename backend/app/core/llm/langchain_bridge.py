"""Bridge between LLM gateway config and LangChain model instances.

LangGraph nodes use LangChain's BaseChatModel (ChatOpenAI/ChatAnthropic).
This module creates the correct LangChain model using the same config
resolution as the gateway (shared resolve_provider_config).

Note: LangChain dependencies are imported lazily to avoid import errors
when langchain-openai or langchain-anthropic are not installed.
"""

import logging
from typing import Any, Optional, TYPE_CHECKING, Union

from app.core.llm.config import (
    ProviderType,
    resolve_provider_config,
)

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)


def get_langchain_model(
    model: str,
    *,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: int = 120,
    max_retries: int = 3,
    response_format: Optional[dict] = None,
    # DB-level config passthrough
    system_openai_api_key: Optional[str] = None,
    system_openai_base_url: Optional[str] = None,
    system_anthropic_api_key: Optional[str] = None,
    system_anthropic_base_url: Optional[str] = None,
    local_llm_base_url: Optional[str] = None,
    use_local_models: bool = False,
) -> Union["ChatOpenAI", "ChatAnthropic"]:
    """Create a LangChain chat model for the given model name.

    Detects provider from model name and creates the appropriate
    LangChain wrapper with correct credentials.

    Returns:
        ChatOpenAI or ChatAnthropic instance
    """
    config = resolve_provider_config(
        model=model,
        system_openai_api_key=system_openai_api_key,
        system_openai_base_url=system_openai_base_url,
        system_anthropic_api_key=system_anthropic_api_key,
        system_anthropic_base_url=system_anthropic_base_url,
        local_llm_base_url=local_llm_base_url,
        use_local_models=use_local_models,
    )

    if config.provider_type == ProviderType.ANTHROPIC:
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:
            raise ImportError(
                "langchain-anthropic is required for Claude models in LangGraph. "
                "Install it with: pip install langchain-anthropic"
            ) from e

        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": config.api_key,
            "temperature": temperature,
            "timeout": timeout,
            "max_retries": max_retries,
            "max_tokens": max_tokens or 4096,  # Required for Anthropic
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        # Note: Claude does not support response_format (JSON mode)
        # Prompt engineering is used instead
        logger.info("Creating LangChain ChatAnthropic: model=%s, base_url=%s", model, config.base_url or "default")
        return ChatAnthropic(**kwargs)

    else:  # OpenAI / OpenAI-compatible
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise ImportError(
                "langchain-openai is required for LangGraph agents. "
                "Install it with: pip install langchain-openai"
            ) from e

        kwargs = {
            "model": model,
            "temperature": temperature,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["model_kwargs"] = {"response_format": response_format}

        logger.info("Creating LangChain ChatOpenAI: model=%s, base_url=%s", model, config.base_url or "default")
        return ChatOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Convenience functions for LangGraph tiers
# ---------------------------------------------------------------------------


async def get_analysis_langchain_model(
    db_session=None,
) -> Union["ChatOpenAI", "ChatAnthropic"]:
    """Get LangChain model for the analysis tier.

    Uses resolve_model_provider() for provider-aware config resolution.
    Falls back to legacy flat columns when no provider is assigned.
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    if db_session:
        service = get_settings_service()
        resolved = await service.resolve_model_provider(db_session, "analysis")
    else:
        async with get_async_session() as db:
            service = get_settings_service()
            resolved = await service.resolve_model_provider(db, "analysis")

    # Determine if response_format should be used (only for OpenAI models)
    response_format = None
    if resolved.provider_type == "openai":
        response_format = {"type": "json_object"}

    return get_langchain_model(
        model=resolved.model,
        temperature=0.3,
        response_format=response_format,
        system_openai_api_key=resolved.api_key if resolved.provider_type == "openai" else None,
        system_openai_base_url=resolved.base_url if resolved.provider_type == "openai" else None,
        system_anthropic_api_key=resolved.api_key if resolved.provider_type == "anthropic" else None,
        system_anthropic_base_url=resolved.base_url if resolved.provider_type == "anthropic" else None,
    )


async def get_synthesis_langchain_model(
    db_session=None,
) -> Union["ChatOpenAI", "ChatAnthropic"]:
    """Get LangChain model for the synthesis tier.

    Uses resolve_model_provider() for provider-aware config resolution.
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    if db_session:
        service = get_settings_service()
        resolved = await service.resolve_model_provider(db_session, "synthesis")
    else:
        async with get_async_session() as db:
            service = get_settings_service()
            resolved = await service.resolve_model_provider(db, "synthesis")

    return get_langchain_model(
        model=resolved.model,
        temperature=0.5,
        max_tokens=4000,
        system_openai_api_key=resolved.api_key if resolved.provider_type == "openai" else None,
        system_openai_base_url=resolved.base_url if resolved.provider_type == "openai" else None,
        system_anthropic_api_key=resolved.api_key if resolved.provider_type == "anthropic" else None,
        system_anthropic_base_url=resolved.base_url if resolved.provider_type == "anthropic" else None,
    )


async def get_chat_model_config() -> tuple[str, dict]:
    """Get model name and provider config for the chat service.

    Uses resolve_model_provider() for provider-aware config resolution.
    Returns (model_name, provider_kwargs) so the chat service can
    pass them to gateway.chat_stream().

    Returns:
        Tuple of (model_name, dict with system_api_key etc.)
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    try:
        async with get_async_session() as db:
            service = get_settings_service()
            resolved = await service.resolve_model_provider(db, "chat")

            provider_kwargs = {
                "system_api_key": resolved.api_key if resolved.provider_type == "openai" else None,
                "system_base_url": resolved.base_url if resolved.provider_type == "openai" else None,
                "system_anthropic_key": resolved.api_key if resolved.provider_type == "anthropic" else None,
                "system_anthropic_base_url": resolved.base_url if resolved.provider_type == "anthropic" else None,
            }
            return resolved.model, provider_kwargs
    except Exception as e:
        logger.error(
            "Failed to get chat model config from database: %s", e
        )
        raise ValueError(
            "Cannot load LLM configuration from database. "
            "Please configure it in Admin Settings."
        ) from e
