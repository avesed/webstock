"""LLM Gateway — the single entry point for all LLM API calls.

All services call this instead of directly creating OpenAI/Anthropic clients.
Manages provider instances, config resolution, and lifecycle.

Provider caching policy:
- Environment-sourced providers: cached (reuse HTTP connections)
- DB-sourced providers: NOT cached (admin may change keys at any time)
- Per-user providers: NOT cached (request-scoped)
"""

import logging
from typing import Any, AsyncIterator, Callable, Coroutine, Dict, Optional

from app.config import settings
from app.core.llm.config import (
    ProviderConfig,
    ProviderType,
    resolve_provider_config,
)
from app.core.llm.providers.base import LLMProvider
from app.core.llm.types import (
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    StreamEvent,
    TokenUsage,
    UsageInfo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level usage recorder — survives reset_llm_gateway() in Celery workers
# ---------------------------------------------------------------------------

# Signature: async (purpose, model, usage: TokenUsage, user_id?, metadata?) -> None
_usage_recorder: Optional[Callable[..., Coroutine]] = None


def set_llm_usage_recorder(fn: Optional[Callable[..., Coroutine]]) -> None:
    """Register a callback to record every LLM usage event.

    The callback receives (purpose, model, usage, user_id=, metadata=).
    Set to None to disable recording.

    This is module-level so it survives reset_llm_gateway() which only
    resets the _gateway instance (discarding cached providers).
    """
    global _usage_recorder
    _usage_recorder = fn


class LLMGateway:
    """Unified LLM gateway with built-in provider registry.

    Handles:
    - Provider detection from model name
    - Config resolution (user -> system -> env)
    - Provider instance caching (env-sourced only)
    - Celery worker compatibility (reset)
    """

    def __init__(self) -> None:
        # Only cache env-sourced providers to reuse connections
        self._env_providers: Dict[str, LLMProvider] = {}

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def _cache_key(self, config: ProviderConfig) -> str:
        return f"{config.provider_type}:{config.base_url or 'default'}"

    def _create_provider(self, config: ProviderConfig) -> LLMProvider:
        """Create a new provider instance from config."""
        if config.provider_type == ProviderType.OPENAI:
            from app.core.llm.providers.openai_provider import OpenAIProvider
            return OpenAIProvider(api_key=config.api_key, base_url=config.base_url)
        elif config.provider_type == ProviderType.ANTHROPIC:
            from app.core.llm.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(api_key=config.api_key, base_url=config.base_url)
        logger.error(
            "Unknown provider type: %s (api_key present: %s, base_url: %s)",
            config.provider_type, bool(config.api_key), config.base_url,
        )
        raise ValueError(f"Unknown provider type: {config.provider_type}")

    def _get_env_provider(self, config: ProviderConfig) -> LLMProvider:
        """Get cached env-sourced provider or create one."""
        key = self._cache_key(config)
        if key not in self._env_providers:
            self._env_providers[key] = self._create_provider(config)
        return self._env_providers[key]

    # ------------------------------------------------------------------
    # Config resolution
    # ------------------------------------------------------------------

    def _resolve_and_get_provider(
        self,
        model: str,
        *,
        system_api_key: Optional[str] = None,
        system_base_url: Optional[str] = None,
        system_anthropic_key: Optional[str] = None,
        system_anthropic_base_url: Optional[str] = None,
        local_llm_base_url: Optional[str] = None,
        use_local_models: bool = False,
        use_user_config: bool = True,
    ) -> LLMProvider:
        """Resolve config and return the appropriate provider.

        Args:
            model: Model name
            system_api_key: DB-sourced OpenAI API key
            system_base_url: DB-sourced OpenAI base URL
            system_anthropic_key: DB-sourced Anthropic API key
            system_anthropic_base_url: DB-sourced Anthropic base URL
            local_llm_base_url: Local LLM endpoint
            use_local_models: Whether to use local models
            use_user_config: Whether to read current_user_ai_config (False for Celery)
        """
        # Read per-request user override
        user_api_key = None
        user_base_url = None
        if use_user_config:
            from app.core.user_ai_config import current_user_ai_config
            user_config = current_user_ai_config.get()
            if user_config:
                user_api_key = user_config.api_key
                user_base_url = user_config.base_url

        config = resolve_provider_config(
            model=model,
            user_api_key=user_api_key,
            user_base_url=user_base_url,
            system_openai_api_key=system_api_key,
            system_openai_base_url=system_base_url,
            system_anthropic_api_key=system_anthropic_key,
            system_anthropic_base_url=system_anthropic_base_url,
            local_llm_base_url=local_llm_base_url,
            use_local_models=use_local_models,
        )

        # Decide caching: per-user and DB-sourced providers are NOT cached
        is_per_user = bool(user_api_key or user_base_url)
        is_db_sourced = bool(
            system_api_key or system_base_url
            or system_anthropic_key or system_anthropic_base_url
        )

        if is_per_user or is_db_sourced:
            return self._create_provider(config)
        else:
            return self._get_env_provider(config)

    # ------------------------------------------------------------------
    # Internal: fire usage recording callback
    # ------------------------------------------------------------------

    @staticmethod
    async def _record_usage(
        purpose: str,
        model: str,
        usage: Optional[TokenUsage],
        user_id: Optional[int],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        """Fire the module-level usage recorder if set. Never raises."""
        if not _usage_recorder or not usage or not purpose:
            return
        try:
            await _usage_recorder(
                purpose=purpose,
                model=model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cached_tokens=usage.cached_tokens,
                user_id=user_id,
                metadata=metadata,
            )
        except Exception:
            logger.debug("Usage recording failed", exc_info=True)

    # ------------------------------------------------------------------
    # Chat (non-streaming)
    # ------------------------------------------------------------------

    async def chat(
        self,
        request: ChatRequest,
        *,
        purpose: str = "",
        user_id: Optional[int] = None,
        usage_metadata: Optional[Dict[str, Any]] = None,
        system_api_key: Optional[str] = None,
        system_base_url: Optional[str] = None,
        system_anthropic_key: Optional[str] = None,
        system_anthropic_base_url: Optional[str] = None,
        local_llm_base_url: Optional[str] = None,
        use_local_models: bool = False,
        use_user_config: bool = True,
    ) -> ChatResponse:
        """Non-streaming chat completion through the appropriate provider."""
        provider = self._resolve_and_get_provider(
            request.model,
            system_api_key=system_api_key,
            system_base_url=system_base_url,
            system_anthropic_key=system_anthropic_key,
            system_anthropic_base_url=system_anthropic_base_url,
            local_llm_base_url=local_llm_base_url,
            use_local_models=use_local_models,
            use_user_config=use_user_config,
        )
        response = await provider.chat(request)
        await self._record_usage(
            purpose, request.model, response.usage, user_id, usage_metadata,
        )
        return response

    # ------------------------------------------------------------------
    # Chat (streaming)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        request: ChatRequest,
        *,
        purpose: str = "",
        user_id: Optional[int] = None,
        usage_metadata: Optional[Dict[str, Any]] = None,
        system_api_key: Optional[str] = None,
        system_base_url: Optional[str] = None,
        system_anthropic_key: Optional[str] = None,
        system_anthropic_base_url: Optional[str] = None,
        local_llm_base_url: Optional[str] = None,
        use_local_models: bool = False,
        use_user_config: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion through the appropriate provider."""
        provider = self._resolve_and_get_provider(
            request.model,
            system_api_key=system_api_key,
            system_base_url=system_base_url,
            system_anthropic_key=system_anthropic_key,
            system_anthropic_base_url=system_anthropic_base_url,
            local_llm_base_url=local_llm_base_url,
            use_local_models=use_local_models,
            use_user_config=use_user_config,
        )
        captured_usage: Optional[TokenUsage] = None
        async for event in provider.chat_stream(request):
            if isinstance(event, UsageInfo):
                captured_usage = event.usage
            yield event
        # Record usage after stream completes
        await self._record_usage(
            purpose, request.model, captured_usage, user_id, usage_metadata,
        )

    # ------------------------------------------------------------------
    # Embeddings (always OpenAI)
    # ------------------------------------------------------------------

    async def embed(
        self,
        request: EmbeddingRequest,
        *,
        purpose: str = "",
        user_id: Optional[int] = None,
        usage_metadata: Optional[Dict[str, Any]] = None,
        system_api_key: Optional[str] = None,
        system_base_url: Optional[str] = None,
        use_user_config: bool = True,
    ) -> EmbeddingResponse:
        """Generate embeddings (always uses OpenAI provider)."""
        provider = self._resolve_and_get_provider(
            request.model,  # Embedding models are always OpenAI
            system_api_key=system_api_key,
            system_base_url=system_base_url,
            use_user_config=use_user_config,
        )
        if not provider.supports_embeddings():
            logger.error(
                "Provider %s does not support embeddings (model=%s)",
                provider.provider_name, request.model,
            )
            raise ValueError(
                f"Provider {provider.provider_name} does not support embeddings. "
                "Use an OpenAI-compatible model for embeddings."
            )
        response = await provider.embed(request)
        await self._record_usage(
            purpose, request.model, response.usage, user_id, usage_metadata,
        )
        return response

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Graceful shutdown — close all cached providers."""
        for provider in self._env_providers.values():
            await provider.close()
        self._env_providers.clear()

    def reset(self) -> None:
        """Sync reset for Celery workers.

        Discards all cached provider instances to avoid holding references
        to a closed event loop. Must be called after each Celery task.
        """
        count = len(self._env_providers)
        for provider in self._env_providers.values():
            provider.reset()
        self._env_providers.clear()
        if count:
            logger.debug("Reset LLM gateway: discarded %d cached providers", count)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_gateway: Optional[LLMGateway] = None


def get_llm_gateway() -> LLMGateway:
    """Get the singleton LLMGateway instance."""
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


def reset_llm_gateway() -> None:
    """Reset for Celery workers (replaces reset_openai_client)."""
    if _gateway is not None:
        _gateway.reset()
