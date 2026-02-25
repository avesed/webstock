"""POST /v1/embeddings — OpenAI-compatible embedding endpoint."""

import logging
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.provider_cache import provider_cache
from app.routes.chat import _get_provider_id, _get_or_create_provider
from app.providers.types import EmbeddingRequest

logger = logging.getLogger(__name__)
router = APIRouter()


class EmbeddingRequestBody(BaseModel):
    model: str
    input: Union[str, List[str]]
    dimensions: Optional[int] = None


@router.post("/v1/embeddings")
async def create_embeddings(body: EmbeddingRequestBody, request: Request):
    """OpenAI-compatible embedding endpoint."""
    provider_id = _get_provider_id(request)

    row = await provider_cache.get_provider(provider_id)
    if not row:
        raise HTTPException(404, f"Provider {provider_id} not found or disabled")

    if row.provider_type != "openai":
        raise HTTPException(400, "Embeddings are only supported for OpenAI providers")

    provider = _get_or_create_provider(row)
    if not provider.supports_embeddings():
        raise HTTPException(400, f"Provider {row.name} does not support embeddings")

    internal_req = EmbeddingRequest(
        model=body.model,
        input=body.input,
        dimensions=body.dimensions,
    )

    response = await provider.embed(internal_req)

    # Build OpenAI-format response
    result: Dict[str, Any] = {
        "object": "list",
        "model": response.model,
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": emb,
            }
            for i, emb in enumerate(response.embeddings)
        ],
    }

    if response.usage:
        result["usage"] = {
            "prompt_tokens": response.usage.prompt_tokens,
            "total_tokens": response.usage.total_tokens,
        }

    return result
