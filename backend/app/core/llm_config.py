"""LLM configuration for the layered AI architecture.

This module provides configuration and client management for the multi-tier
LLM architecture:
- Analysis Layer: Uses local models (via OpenAI-compatible API) for individual agent analysis
- Synthesis Layer: Uses cloud models (GPT-4o) for final synthesis

The configuration supports both local models (vLLM, Ollama, LMStudio, etc. via
OpenAI-compatible API) and cloud-hosted models.

Note: LangChain dependencies are imported lazily to avoid import errors
when the dependencies are not installed.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    """Model tier for different purposes."""

    ANALYSIS = "analysis"  # Individual agent analysis (can use local models)
    SYNTHESIS = "synthesis"  # Final synthesis (typically cloud model)
    EMBEDDING = "embedding"  # Embedding generation


@dataclass
class LLMConfig:
    """
    Configuration for an LLM endpoint.

    Supports both OpenAI and OpenAI-compatible APIs (vLLM, Ollama, etc.)
    """

    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: int = 120
    max_retries: int = 3

    def to_langchain_kwargs(self) -> dict:
        """Convert to kwargs for ChatOpenAI initialization."""
        kwargs = {
            "model": self.model,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }

        if self.api_key:
            kwargs["api_key"] = self.api_key

        if self.base_url:
            kwargs["base_url"] = self.base_url

        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        return kwargs


# Default configurations (can be overridden by environment/system settings)
DEFAULT_ANALYSIS_CONFIG = LLMConfig(
    model="gpt-4o-mini",  # Default, can be overridden to local model
    temperature=0.3,  # Lower temperature for more deterministic analysis
    max_tokens=2000,
)

DEFAULT_SYNTHESIS_CONFIG = LLMConfig(
    model="gpt-4o",  # Higher capability model for synthesis
    temperature=0.5,  # Moderate temperature for balanced output
    max_tokens=4000,
)


def get_analysis_config() -> LLMConfig:
    """
    Get LLM configuration for analysis layer.

    Priority:
    1. Environment variables for local/vLLM configuration
    2. System settings (admin configured)
    3. Default configuration

    For local models, set:
    - ANALYSIS_MODEL_BASE_URL: e.g., "http://localhost:8080/v1"
    - ANALYSIS_MODEL_NAME: e.g., "Qwen/Qwen2.5-32B-Instruct"
    - ANALYSIS_MODEL_API_KEY: API key if required (can be "none" for local)

    Returns:
        LLMConfig for analysis layer
    """
    # Check for local model configuration
    base_url = getattr(settings, "ANALYSIS_MODEL_BASE_URL", None)
    model_name = getattr(settings, "ANALYSIS_MODEL_NAME", None)
    api_key = getattr(settings, "ANALYSIS_MODEL_API_KEY", None)

    if base_url and model_name:
        logger.info(f"Using local analysis model: {model_name} at {base_url}")
        return LLMConfig(
            model=model_name,
            api_key=api_key or "not-required",  # Some local servers don't need keys
            base_url=base_url,
            temperature=0.3,
            max_tokens=2000,
        )

    # Fall back to cloud model using existing settings
    return LLMConfig(
        model=settings.OPENAI_MODEL or DEFAULT_ANALYSIS_CONFIG.model,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_API_BASE,
        temperature=0.3,
        max_tokens=settings.OPENAI_MAX_TOKENS or 2000,
    )


def get_synthesis_config() -> LLMConfig:
    """
    Get LLM configuration for synthesis layer.

    The synthesis layer typically uses a more capable cloud model
    to combine and synthesize results from multiple agents.

    Priority:
    1. Environment variables for custom synthesis model
    2. System settings
    3. Default configuration (gpt-4o)

    For custom synthesis model, set:
    - SYNTHESIS_MODEL_NAME: e.g., "gpt-4o" or "claude-3-opus"
    - SYNTHESIS_MODEL_BASE_URL: Custom API endpoint if not OpenAI
    - SYNTHESIS_MODEL_API_KEY: API key

    Returns:
        LLMConfig for synthesis layer
    """
    # Check for custom synthesis model configuration
    model_name = getattr(settings, "SYNTHESIS_MODEL_NAME", None)
    base_url = getattr(settings, "SYNTHESIS_MODEL_BASE_URL", None)
    api_key = getattr(settings, "SYNTHESIS_MODEL_API_KEY", None)

    if model_name:
        logger.info(f"Using custom synthesis model: {model_name}")
        return LLMConfig(
            model=model_name,
            api_key=api_key or settings.OPENAI_API_KEY,
            base_url=base_url or settings.OPENAI_API_BASE,
            temperature=0.5,
            max_tokens=4000,
        )

    # Fall back to default synthesis configuration
    return LLMConfig(
        model=DEFAULT_SYNTHESIS_CONFIG.model,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_API_BASE,
        temperature=0.5,
        max_tokens=4000,
    )


def _get_chat_openai_class() -> type:
    """
    Lazily import ChatOpenAI to avoid import errors.

    Returns:
        The ChatOpenAI class

    Raises:
        ImportError: If langchain-openai is not installed
    """
    try:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "langchain-openai is required for LangGraph agents. "
            "Install it with: pip install langchain-openai"
        ) from e


def get_analysis_model() -> "ChatOpenAI":
    """
    Get LangChain ChatOpenAI instance for analysis layer.

    Returns:
        Configured ChatOpenAI instance for analysis

    Raises:
        ImportError: If langchain-openai is not installed
    """
    ChatOpenAI = _get_chat_openai_class()
    config = get_analysis_config()
    logger.debug(f"Creating analysis model: {config.model}")
    return ChatOpenAI(**config.to_langchain_kwargs())


def get_synthesis_model() -> "ChatOpenAI":
    """
    Get LangChain ChatOpenAI instance for synthesis layer.

    Returns:
        Configured ChatOpenAI instance for synthesis

    Raises:
        ImportError: If langchain-openai is not installed
    """
    ChatOpenAI = _get_chat_openai_class()
    config = get_synthesis_config()
    logger.debug(f"Creating synthesis model: {config.model}")
    return ChatOpenAI(**config.to_langchain_kwargs())


def get_model_for_tier(tier: ModelTier) -> "ChatOpenAI":
    """
    Get LangChain model for the specified tier.

    Args:
        tier: The model tier (ANALYSIS or SYNTHESIS)

    Returns:
        Configured ChatOpenAI instance

    Raises:
        ValueError: If tier is EMBEDDING (not supported by this function)
        ImportError: If langchain-openai is not installed
    """
    if tier == ModelTier.ANALYSIS:
        return get_analysis_model()
    elif tier == ModelTier.SYNTHESIS:
        return get_synthesis_model()
    elif tier == ModelTier.EMBEDDING:
        raise ValueError(
            "Use embedding_service for embedding models, not ChatOpenAI"
        )
    else:
        raise ValueError(f"Unknown model tier: {tier}")


async def check_model_availability(config: LLMConfig) -> bool:
    """
    Check if a model endpoint is available.

    This is useful for health checks and fallback logic.

    Args:
        config: The LLM configuration to check

    Returns:
        True if the model is available, False otherwise
    """
    try:
        ChatOpenAI = _get_chat_openai_class()
        model = ChatOpenAI(**config.to_langchain_kwargs())
        # Simple test call
        response = await model.ainvoke("test")
        return response is not None
    except ImportError:
        logger.warning("langchain-openai not installed, cannot check model availability")
        return False
    except Exception as e:
        logger.warning(f"Model availability check failed for {config.model}: {e}")
        return False


def get_model_info() -> dict:
    """
    Get information about configured models for debugging/monitoring.

    Returns:
        Dictionary with model configuration information
    """
    analysis_config = get_analysis_config()
    synthesis_config = get_synthesis_config()

    return {
        "analysis": {
            "model": analysis_config.model,
            "base_url": analysis_config.base_url,
            "is_local": analysis_config.base_url is not None
            and "localhost" in (analysis_config.base_url or ""),
        },
        "synthesis": {
            "model": synthesis_config.model,
            "base_url": synthesis_config.base_url,
            "is_local": synthesis_config.base_url is not None
            and "localhost" in (synthesis_config.base_url or ""),
        },
    }


# ============== Database-based Configuration ==============


async def get_analysis_model_from_settings() -> "ChatOpenAI":
    """
    Get analysis model from system settings (database).

    This function fetches LangGraph configuration from the database
    and creates an appropriate ChatOpenAI instance for the analysis layer.

    For local models (vLLM, Ollama, LMStudio, etc.), it uses the configured local_llm_base_url.
    For cloud models, it uses the standard OpenAI API or custom base_url.

    Returns:
        Configured ChatOpenAI instance for analysis

    Raises:
        ImportError: If langchain-openai is not installed
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    ChatOpenAI = _get_chat_openai_class()

    async with get_async_session() as db:
        service = get_settings_service()
        config = await service.get_langgraph_config(db)

        if config.use_local_models and config.local_llm_base_url:
            # Use local model via OpenAI-compatible API
            logger.info(
                f"Using local analysis model: {config.analysis_model} at {config.local_llm_base_url}"
            )
            return ChatOpenAI(
                base_url=config.local_llm_base_url,
                api_key="not-needed",  # Local servers typically don't require an API key
                model=config.analysis_model,
                temperature=0.3,  # Lower temperature for stable analysis
                max_tokens=2000,
            )
        else:
            # Use cloud model
            logger.info(f"Using cloud analysis model: {config.analysis_model}")
            kwargs: dict[str, Any] = {
                "model": config.analysis_model,
                "temperature": 0.3,
                "max_tokens": 2000,
            }
            if config.openai_api_key:
                kwargs["api_key"] = config.openai_api_key
            if config.openai_base_url:
                kwargs["base_url"] = config.openai_base_url
            return ChatOpenAI(**kwargs)


async def get_synthesis_model_from_settings() -> "ChatOpenAI":
    """
    Get synthesis model from system settings (database).

    This function fetches LangGraph configuration from the database
    and creates an appropriate ChatOpenAI instance for the synthesis layer.

    The synthesis layer typically uses a more capable model (gpt-4o by default)
    for combining and presenting the final analysis to users.

    Returns:
        Configured ChatOpenAI instance for synthesis

    Raises:
        ImportError: If langchain-openai is not installed
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    ChatOpenAI = _get_chat_openai_class()

    async with get_async_session() as db:
        service = get_settings_service()
        config = await service.get_langgraph_config(db)

        if config.use_local_models and config.local_llm_base_url:
            # Use local model via OpenAI-compatible API for synthesis
            logger.info(
                f"Using local synthesis model: {config.synthesis_model} at {config.local_llm_base_url}"
            )
            return ChatOpenAI(
                base_url=config.local_llm_base_url,
                api_key="not-needed",
                model=config.synthesis_model,
                temperature=0.5,  # Moderate temperature for synthesis
                max_tokens=4000,
            )
        else:
            # Use cloud model
            logger.info(f"Using cloud synthesis model: {config.synthesis_model}")
            kwargs: dict[str, Any] = {
                "model": config.synthesis_model,
                "temperature": 0.5,
                "max_tokens": 4000,
            }
            if config.openai_api_key:
                kwargs["api_key"] = config.openai_api_key
            if config.openai_base_url:
                kwargs["base_url"] = config.openai_base_url
            return ChatOpenAI(**kwargs)


async def get_langgraph_settings() -> dict:
    """
    Get LangGraph workflow settings from database.

    Returns settings needed for workflow configuration such as
    max clarification rounds and confidence threshold.

    Returns:
        Dictionary with workflow settings
    """
    from app.services.settings_service import get_settings_service
    from app.db.database import get_async_session

    async with get_async_session() as db:
        service = get_settings_service()
        config = await service.get_langgraph_config(db)

        return {
            "max_clarification_rounds": config.max_clarification_rounds,
            "clarification_confidence_threshold": config.clarification_confidence_threshold,
            "use_local_models": config.use_local_models,
            "analysis_model": config.analysis_model,
            "synthesis_model": config.synthesis_model,
        }
