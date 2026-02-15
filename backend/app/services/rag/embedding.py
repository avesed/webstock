"""Embedding generation via LLM Gateway with rate limiting and cost tracking."""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, EmbeddingRequest
from app.core.token_bucket import get_embedding_rate_limiter
from app.models.document_embedding import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)


async def get_embedding_model_from_db(db: AsyncSession) -> str:
    """Read embedding model name from system_settings (database only).

    For backward compat, returns just the model name string.
    Use get_embedding_config_from_db() for full provider config.
    """
    try:
        from app.models.system_settings import SystemSettings
        result = await db.execute(
            select(SystemSettings.embedding_model).where(SystemSettings.id == 1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row
    except Exception as e:
        logger.warning("Failed to read embedding model from DB: %s", e)
    raise ValueError(
        "No embedding model configured. "
        "Please configure it in Admin Settings."
    )


async def get_embedding_config_from_db(db: AsyncSession):
    """Resolve full embedding provider config (model + api_key + base_url).

    Returns a ResolvedModelConfig dataclass.
    """
    from app.services.settings_service import get_settings_service, ResolvedModelConfig
    try:
        settings_service = get_settings_service()
        return await settings_service.resolve_model_provider(db, "embedding")
    except Exception as e:
        logger.warning("resolve_model_provider('embedding') failed, trying legacy fallback: %s", e)
        model = await get_embedding_model_from_db(db)
        return ResolvedModelConfig(
            model=model,
            provider_type="openai",
            api_key=None,
            base_url=None,
        )


class GatewayEmbedder:
    """Generate embeddings via the LLM Gateway.

    Preserves: rate limiting (token_bucket), cost tracking (purpose="embedding"),
    text truncation (8000 chars).
    """

    async def embed_one(
        self,
        text: str,
        *,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Optional[List[float]]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed (will be truncated if too long)
            model: Embedding model name (must be provided)
            api_key: Provider API key (falls back to context var / env if None)
            base_url: Provider base URL (falls back to context var / env if None)

        Returns:
            Embedding vector or None on failure
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return None

        if not model:
            logger.error("No embedding model specified")
            return None

        # Rate limit check
        rate_limiter = await get_embedding_rate_limiter()
        if not await rate_limiter.acquire():
            logger.warning("Embedding rate limit exceeded")
            return None

        try:
            gateway = get_llm_gateway()
            request = EmbeddingRequest(
                input=text[:8000],
                model=model,
                dimensions=EMBEDDING_DIMENSIONS,
            )
            response = await gateway.embed(
                request,
                system_api_key=api_key,
                system_base_url=base_url,
                purpose="embedding",
            )
            embedding = response.embeddings[0]
            logger.debug(
                "Generated embedding (model=%s, dim=%d) for text of length %d",
                model,
                len(embedding),
                len(text),
            )
            return embedding
        except Exception as e:
            logger.error("Failed to generate embedding: %s", e)
            return None

    async def embed_batch(
        self,
        texts: List[str],
        *,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> List[Optional[List[float]]]:
        """Generate embeddings for multiple texts in a single API call.

        Args:
            texts: List of texts to embed
            model: Embedding model name (must be provided)
            api_key: Provider API key (falls back to context var / env if None)
            base_url: Provider base URL (falls back to context var / env if None)

        Returns:
            List of embeddings (None for failed items)
        """
        if not texts:
            return []

        if not model:
            logger.error("No embedding model specified for batch")
            return [None] * len(texts)

        # Filter empty texts
        valid_texts = []
        valid_indices = []
        for i, text in enumerate(texts):
            if text and text.strip():
                valid_texts.append(text[:8000])
                valid_indices.append(i)

        if not valid_texts:
            return [None] * len(texts)

        # Rate limit check
        rate_limiter = await get_embedding_rate_limiter()
        if not await rate_limiter.acquire():
            logger.warning("Embedding batch rate limit exceeded")
            return [None] * len(texts)

        try:
            gateway = get_llm_gateway()
            request = EmbeddingRequest(
                input=valid_texts,
                model=model,
                dimensions=EMBEDDING_DIMENSIONS,
            )
            response = await gateway.embed(
                request,
                system_api_key=api_key,
                system_base_url=base_url,
                purpose="embedding",
                usage_metadata={"batch_size": len(valid_texts)},
            )

            # Map results back to original indices
            results: List[Optional[List[float]]] = [None] * len(texts)
            for i, embedding in enumerate(response.embeddings):
                if i < len(valid_indices):
                    original_idx = valid_indices[i]
                    results[original_idx] = embedding

            logger.info("Generated %d embeddings in batch (model=%s)", len(response.embeddings), model)
            return results
        except Exception as e:
            logger.error("Failed to generate batch embeddings: %s", e)
            return [None] * len(texts)
