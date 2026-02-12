"""Skill: fetch full article content and save to storage."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class FetchFullContentSkill(BaseSkill):
    """Fetch full article content via trafilatura/Tavily/Playwright/Polygon and persist to file storage.

    Wraps ``FullContentService.fetch_with_fallback`` for content fetching and
    ``NewsStorageService.save_content`` for persisting the result as a JSON
    file.  Returns the fetched text, word count, language, and file path.

    Designed to be called by LangGraph news pipeline nodes.
    """

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="fetch_full_content",
            description=(
                "Fetch the full text of a news article from its URL using "
                "trafilatura, Tavily Extract API, Playwright, or Polygon.io, "
                "then save the content to file storage. Returns full_text, "
                "word_count, language, and file_path."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="url",
                    type="string",
                    description="URL of the news article to fetch.",
                    required=True,
                ),
                SkillParameter(
                    name="news_id",
                    type="string",
                    description="UUID of the news article (for storage path).",
                    required=True,
                ),
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock symbol associated with the article.",
                    required=True,
                ),
                SkillParameter(
                    name="market",
                    type="string",
                    description="Market identifier (US, HK, SH, SZ, METAL). Default US.",
                    required=False,
                    default="US",
                ),
                SkillParameter(
                    name="content_source",
                    type="string",
                    description=(
                        "Primary content source: 'trafilatura', 'tavily', "
                        "'playwright', or 'polygon'. Default 'trafilatura'."
                    ),
                    required=False,
                    default="trafilatura",
                    enum=["trafilatura", "polygon", "tavily", "playwright"],
                ),
                SkillParameter(
                    name="polygon_api_key",
                    type="string",
                    description="Optional Polygon.io API key for the polygon provider.",
                    required=False,
                ),
                SkillParameter(
                    name="published_at",
                    type="string",
                    description="ISO 8601 publish date for file path organization. Optional.",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        from app.services.full_content_service import (
            ContentSource,
            get_full_content_service,
        )
        from app.services.news_storage_service import get_news_storage_service

        url = kwargs.get("url")
        news_id_str = kwargs.get("news_id")
        symbol = kwargs.get("symbol")
        market = kwargs.get("market", "US")
        content_source_str = kwargs.get("content_source", "trafilatura")
        polygon_api_key = kwargs.get("polygon_api_key")
        published_at_str = kwargs.get("published_at")

        # Validate required parameters
        if not url:
            return SkillResult(success=False, error="url parameter is required")
        if not news_id_str:
            return SkillResult(success=False, error="news_id parameter is required")
        if not symbol:
            return SkillResult(success=False, error="symbol parameter is required")

        # Parse news_id as UUID
        try:
            news_uuid = uuid.UUID(news_id_str)
        except (ValueError, TypeError):
            return SkillResult(
                success=False,
                error=f"Invalid news_id UUID: {news_id_str}",
            )

        # Parse published_at
        published_at = None
        if published_at_str:
            try:
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                published_at = None

        # Determine content source enum
        if content_source_str == "polygon":
            primary_source = ContentSource.POLYGON
        elif content_source_str == "tavily":
            primary_source = ContentSource.TAVILY
        elif content_source_str == "playwright":
            primary_source = ContentSource.PLAYWRIGHT
        else:
            primary_source = ContentSource.TRAFILATURA

        # Detect expected language from market
        language = "zh" if market in ("SH", "SZ") else "en"

        # Fetch content with fallback (uses singleton with all configured providers)
        service = get_full_content_service()

        fetch_result = await service.fetch_with_fallback(
            url=url,
            primary_source=primary_source,
            language=language,
            polygon_api_key=polygon_api_key,
        )

        if not fetch_result.success or not fetch_result.full_text:
            return SkillResult(
                success=False,
                error=fetch_result.error or "No content fetched",
                metadata={
                    "url": url,
                    "news_id": news_id_str,
                    "source": content_source_str,
                },
            )

        # Save content to file storage
        storage_service = get_news_storage_service()
        content_data = {
            "url": url,
            "title": kwargs.get("title", ""),
            "full_text": fetch_result.full_text,
            "authors": fetch_result.authors,
            "keywords": fetch_result.keywords,
            "top_image": fetch_result.top_image,
            "language": fetch_result.language or language,
            "word_count": fetch_result.word_count,
            "is_partial": fetch_result.is_partial,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "metadata": fetch_result.metadata,
        }

        try:
            file_path = storage_service.save_content(
                news_id=news_uuid,
                symbol=symbol,
                content=content_data,
                published_at=published_at,
            )
        except IOError as e:
            return SkillResult(
                success=False,
                error=f"Failed to save content: {e}",
                metadata={
                    "url": url,
                    "news_id": news_id_str,
                    "word_count": fetch_result.word_count,
                },
            )

        return SkillResult(
            success=True,
            data={
                "full_text": fetch_result.full_text,
                "word_count": fetch_result.word_count,
                "language": fetch_result.language or language,
                "is_partial": fetch_result.is_partial,
                "file_path": file_path,
                "authors": fetch_result.authors,
                "keywords": fetch_result.keywords,
            },
            metadata={
                "url": url,
                "news_id": news_id_str,
                "symbol": symbol,
                "source": str(fetch_result.source) if fetch_result.source else content_source_str,
            },
        )
