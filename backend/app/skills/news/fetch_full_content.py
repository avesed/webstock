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
        from app.services.data_service_client import get_data_service_client
        from app.services.news_storage_service import get_news_storage_service

        url = kwargs.get("url")
        news_id_str = kwargs.get("news_id")
        symbol = kwargs.get("symbol")
        market = kwargs.get("market", "US")
        content_source_str = kwargs.get("content_source", "trafilatura")
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

        # Detect expected language from market
        language = "zh" if market in ("SH", "SZ") else "en"

        # Fetch content via data-service
        client = await get_data_service_client()
        result = await client.fetch_content(url, language=language)

        if not result or not result.get("full_text"):
            return SkillResult(
                success=False,
                error=result.get("error") if result else "No content fetched",
                metadata={
                    "url": url,
                    "news_id": news_id_str,
                    "source": content_source_str,
                },
            )

        # Extract fields from data-service response
        full_text = result.get("full_text", "")
        word_count = result.get("word_count", 0)
        result_language = result.get("language") or language
        extraction_method = result.get("extraction_method")
        images_raw = result.get("images", [])
        top_image = images_raw[0] if images_raw else None
        author = result.get("author")

        # Save content to file storage
        storage_service = get_news_storage_service()
        content_data = {
            "url": url,
            "title": kwargs.get("title", ""),
            "full_text": full_text,
            "authors": [author] if author else [],
            "keywords": [],
            "top_image": top_image,
            "images": [{"url": img} for img in images_raw] if images_raw else [],
            "language": result_language,
            "word_count": word_count,
            "is_partial": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"extraction_method": extraction_method},
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
                    "word_count": word_count,
                },
            )

        return SkillResult(
            success=True,
            data={
                "full_text": full_text,
                "word_count": word_count,
                "language": result_language,
                "is_partial": False,
                "file_path": file_path,
                "authors": [author] if author else [],
                "keywords": [],
            },
            metadata={
                "url": url,
                "news_id": news_id_str,
                "symbol": symbol,
                "source": extraction_method or content_source_str,
            },
        )
