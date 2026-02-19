"""Content extraction API endpoints for the data-service.

Provides a unified content extraction endpoint with multi-provider fallback:
trafilatura -> Playwright -> Tavily -> Polygon.

All endpoints require X-Internal-Token authentication.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, HttpUrl

from app.core.auth import verify_internal_token
from app.models.base import ApiResponse
from app.models.content import ContentResult
from app.services.content_service import ContentService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/content",
    tags=["content"],
    dependencies=[Depends(verify_internal_token)],
)

# Singleton service instance (stateless, safe to share)
_content_service = ContentService()


class ContentFetchRequest(BaseModel):
    """Request body for content extraction."""

    url: str
    language: Optional[str] = None
    include_images: bool = False


@router.post(
    "/fetch",
    response_model=ApiResponse[ContentResult],
    summary="Extract full-text content from a URL",
)
async def fetch_content(
    request: ContentFetchRequest,
) -> ApiResponse[ContentResult]:
    """Extract full-text content from a URL using the fallback chain.

    Fallback order:
    1. Trafilatura (fast, accurate, CJK-aware)
    2. Playwright (JS rendering, if service available)
    3. Tavily (server-side extraction, if API key configured)
    4. Polygon (metadata only, last resort)

    Blocked domains (social media, paywalls) are rejected upfront.
    Minimum 500 chars validation, maximum 50K chars truncation.
    """
    result = await _content_service.fetch_content(
        url=request.url,
        language=request.language,
        include_images=request.include_images,
    )

    elapsed_ms = result.pop("elapsed_ms", None)
    extraction_method = result.get("extraction_method", "unknown")
    success = result.get("success", False)

    return ApiResponse(
        success=success,
        data=result,
        source=extraction_method,
        elapsed_ms=elapsed_ms,
        error=result.get("error") if not success else None,
    )
