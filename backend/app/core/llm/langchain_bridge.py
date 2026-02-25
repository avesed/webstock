"""Bridge between LLM gateway config and LangChain model instances.

LangGraph nodes use LangChain's BaseChatModel (ChatOpenAI/ChatAnthropic).
When USE_AI_GATEWAY is enabled, all models use ChatOpenAI pointing at
the gateway (which handles Anthropic translation internally).

Note: LangChain dependencies are imported lazily to avoid import errors
when langchain-openai or langchain-anthropic are not installed.
"""

import logging
from typing import Any, Optional, TYPE_CHECKING, Union

from app.config import settings

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)


def _create_gateway_model(
    model: str,
    provider_id: Optional[str] = None,
    *,
    response_format: Optional[dict] = None,
) -> "ChatOpenAI":
    """Create a LangChain ChatOpenAI instance pointing at the AI Gateway.

    The gateway exposes an OpenAI-compatible API and internally routes
    to the correct provider (OpenAI/Anthropic) based on X-Provider-Id.
    """
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": model,
        "base_url": f"{settings.AI_GATEWAY_URL}/v1",
        "api_key": "internal",  # Auth via X-Internal-Token header
        "streaming": True,
    }

    # Pass provider_id and internal token as default headers
    default_headers: dict[str, str] = {}
    if settings.INTERNAL_API_TOKEN:
        default_headers["X-Internal-Token"] = settings.INTERNAL_API_TOKEN
    if provider_id:
        default_headers["X-Provider-Id"] = provider_id
    if default_headers:
        kwargs["default_headers"] = default_headers

    if response_format:
        kwargs["model_kwargs"] = {"response_format": response_format}

    logger.info(
        "Creating LangChain ChatOpenAI via gateway: model=%s, provider_id=%s",
        model, provider_id or "none",
    )
    return ChatOpenAI(**kwargs)


def get_langchain_model(
    model: str,
    *,
    response_format: Optional[dict] = None,
    # DB-level config passthrough (used in local path only)
    system_openai_api_key: Optional[str] = None,
    system_openai_base_url: Optional[str] = None,
    system_anthropic_api_key: Optional[str] = None,
    system_anthropic_base_url: Optional[str] = None,
    local_llm_base_url: Optional[str] = None,
    use_local_models: bool = False,
    # Gateway path
    provider_id: Optional[str] = None,
) -> Union["ChatOpenAI", "ChatAnthropic"]:
    """Create a LangChain chat model for the given model name.

    Only passes model, credentials, and response_format.
    temperature / max_tokens are NOT sent -- the proxy (NewAPI/OneAPI)
    controls these per-model settings.

    Returns:
        ChatOpenAI or ChatAnthropic instance
    """
    # Gateway path: all models go through ChatOpenAI
    if settings.USE_AI_GATEWAY and provider_id:
        return _create_gateway_model(model, provider_id, response_format=response_format)

    # Local path: original logic
    from app.core.llm.config import (
        ProviderType,
        resolve_provider_config,
    )

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
            "max_tokens": 4096,  # Required by Anthropic API
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
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
            # Always use streaming internally -- some OpenAI-compatible proxies
            # return SSE even for non-streaming requests, which breaks the SDK.
            "streaming": True,
        }
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if response_format:
            kwargs["model_kwargs"] = {"response_format": response_format}

        logger.info("Creating LangChain ChatOpenAI: model=%s, base_url=%s", model, config.base_url or "default")
        return ChatOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Convenience functions for LangGraph tiers
# ---------------------------------------------------------------------------


async def _resolve_and_create(
    purpose: str,
    *,
    db_session=None,
    response_format: Optional[dict] = None,
) -> Union["ChatOpenAI", "ChatAnthropic"]:
    """Shared helper: resolve model via settings_service, then create LangChain model."""
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    if db_session:
        service = get_settings_service()
        resolved = await service.resolve_model_provider(db_session, purpose)
    else:
        async with get_async_session() as db:
            service = get_settings_service()
            resolved = await service.resolve_model_provider(db, purpose)

    # Gateway path: pass provider_id
    if settings.USE_AI_GATEWAY and resolved.provider_id:
        return _create_gateway_model(
            resolved.model, resolved.provider_id, response_format=response_format,
        )

    # Local path: pass provider credentials
    return get_langchain_model(
        model=resolved.model,
        response_format=response_format,
        system_openai_api_key=resolved.api_key if resolved.provider_type == "openai" else None,
        system_openai_base_url=resolved.base_url if resolved.provider_type == "openai" else None,
        system_anthropic_api_key=resolved.api_key if resolved.provider_type == "anthropic" else None,
        system_anthropic_base_url=resolved.base_url if resolved.provider_type == "anthropic" else None,
    )


async def get_analysis_langchain_model(
    db_session=None,
) -> Union["ChatOpenAI", "ChatAnthropic"]:
    """Get LangChain model for the analysis tier.

    Uses resolve_model_provider() for provider-aware config resolution.
    Falls back to legacy flat columns when no provider is assigned.
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    # Need to check provider_type for response_format decision
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

    # Gateway path: pass provider_id
    if settings.USE_AI_GATEWAY and resolved.provider_id:
        return _create_gateway_model(
            resolved.model, resolved.provider_id, response_format=response_format,
        )

    # Local path
    return get_langchain_model(
        model=resolved.model,
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
    return await _resolve_and_create("synthesis", db_session=db_session)


async def get_discussion_langchain_model(
    db_session=None,
) -> Union["ChatOpenAI", "ChatAnthropic"]:
    """Get LangChain model for the discussion group.

    Uses resolve_model_provider() for provider-aware config resolution.
    No response_format -- discussion agents output natural language.
    """
    return await _resolve_and_create("discussion", db_session=db_session)


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
