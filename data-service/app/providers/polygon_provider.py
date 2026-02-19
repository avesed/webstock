"""Polygon.io content extraction provider.

Migrated from backend/app/services/full_content_service.py (PolygonProvider class).
Polygon provides news metadata/summaries, not full article text. This provider
is mainly useful as a last-resort fallback for getting additional article details.

API: GET https://api.polygon.io/v2/reference/news
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Timeout for content fetching (seconds)
FETCH_TIMEOUT = 30


def _detect_language(text: str) -> str:
    """Simple language detection based on Chinese character ratio."""
    if not text:
        return "en"
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    total_chars = len(text)
    if total_chars > 0 and chinese_chars / total_chars > 0.1:
        return "zh"
    return "en"


def _calculate_word_count(text: str, language: str) -> int:
    """Calculate word count aware of CJK languages."""
    if not text:
        return 0
    if language in ("zh", "ja", "ko"):
        return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    return len(text.split())


class PolygonContentProvider:
    """Content provider using Polygon.io Reference API.

    Note: Polygon provides metadata/summary only, not full article text.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.POLYGON_API_KEY or None
        self.base_url = "https://api.polygon.io"

    async def extract(self, url: str) -> Optional[Dict[str, Any]]:
        """Search Polygon news by URL to get article metadata.

        Note: Polygon.io doesn't provide full article text, only enhanced
        metadata. This provider is mainly useful for getting additional
        article details as a last-resort fallback.

        Args:
            url: Original article URL.

        Returns:
            Dict matching ContentResult model, or None on failure.
        """
        if not self.api_key:
            return {
                "url": url,
                "success": False,
                "error": "Polygon API key not configured",
                "extraction_method": "polygon",
                "word_count": 0,
            }

        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                response = await client.get(
                    f"{self.base_url}/v2/reference/news",
                    params={
                        "apiKey": self.api_key,
                        "limit": 1,
                        "sort": "published_utc",
                    },
                )
                response.raise_for_status()
                data = response.json()

            results = data.get("results", [])
            if not results:
                return {
                    "url": url,
                    "success": False,
                    "error": "No results from Polygon API",
                    "extraction_method": "polygon",
                    "word_count": 0,
                }

            article = results[0]
            description = article.get("description", "")

            # Parse publish date
            publish_date = None
            pub_str = article.get("published_utc")
            if pub_str:
                try:
                    parsed = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    publish_date = parsed.isoformat()
                except Exception:
                    pass

            detected_language = _detect_language(description) if description else "en"
            word_count = _calculate_word_count(description, detected_language) if description else 0

            elapsed = time.monotonic() - start_time
            logger.info(
                "Polygon fetch succeeded: url=%s, words=%d, elapsed=%.2fs",
                url[:80], word_count, elapsed,
            )

            return {
                "url": url,
                "full_text": description if description else None,
                "keywords": article.get("keywords"),
                "top_image": article.get("image_url"),
                "word_count": word_count,
                "language": detected_language,
                "publish_date": publish_date,
                "is_partial": True,  # Polygon only provides summary
                "extraction_method": "polygon",
                "success": True,
                "metadata": {
                    "polygon_id": article.get("id"),
                    "publisher": article.get("publisher", {}).get("name"),
                    "tickers": article.get("tickers"),
                },
            }

        except httpx.HTTPStatusError as e:
            logger.error("Polygon API error: %s", e)
            return {
                "url": url,
                "success": False,
                "error": f"Polygon API error: {e.response.status_code}",
                "extraction_method": "polygon",
                "word_count": 0,
            }
        except Exception as e:
            logger.error("Error fetching from Polygon: %s", e)
            return {
                "url": url,
                "success": False,
                "error": str(e)[:500],
                "extraction_method": "polygon",
                "word_count": 0,
            }
