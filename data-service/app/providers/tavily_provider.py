"""Tavily content extraction provider.

Migrated from backend/app/services/full_content_service.py (TavilyProvider class).
Tavily handles JavaScript rendering server-side and provides clean, structured
content extraction. Useful as a fallback for JS-heavy sites.

API: POST https://api.tavily.com/extract
Docs: https://docs.tavily.com/documentation/api-reference/endpoint/extract
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Timeout for content fetching (seconds)
FETCH_TIMEOUT = 30

# Minimum content length to be considered complete
MIN_CONTENT_LENGTH = 500

# Maximum content length to store
MAX_CONTENT_LENGTH = 50000


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


class TavilyContentProvider:
    """Content provider using Tavily Extract API.

    Requires TAVILY_API_KEY configuration.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.TAVILY_API_KEY or None

    async def extract(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract content via Tavily Extract API.

        Args:
            url: Article URL to extract.

        Returns:
            Dict matching ContentResult model, or None on failure.
        """
        if not self.api_key:
            return {
                "url": url,
                "success": False,
                "error": "Tavily API key not configured",
                "extraction_method": "tavily",
                "word_count": 0,
            }

        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                response = await client.post(
                    "https://api.tavily.com/extract",
                    json={
                        "api_key": self.api_key,
                        "urls": [url],
                        "extract_depth": "advanced",
                    },
                )
                response.raise_for_status()
                data = response.json()

            results = data.get("results", [])
            if not results:
                return {
                    "url": url,
                    "success": False,
                    "error": "No results from Tavily Extract API",
                    "extraction_method": "tavily",
                    "word_count": 0,
                }

            article = results[0]
            full_text = (article.get("raw_content") or "").strip()

            if not full_text:
                return {
                    "url": url,
                    "success": False,
                    "error": "Tavily returned empty content",
                    "extraction_method": "tavily",
                    "word_count": 0,
                }

            detected_language = _detect_language(full_text)
            word_count = _calculate_word_count(full_text, detected_language)
            is_partial = len(full_text) < MIN_CONTENT_LENGTH

            if len(full_text) > MAX_CONTENT_LENGTH:
                full_text = full_text[:MAX_CONTENT_LENGTH] + "..."

            elapsed = time.monotonic() - start_time
            logger.info(
                "Tavily fetch succeeded: url=%s, words=%d, lang=%s, elapsed=%.2fs",
                url[:80], word_count, detected_language, elapsed,
            )

            return {
                "url": url,
                "full_text": full_text,
                "word_count": word_count,
                "language": detected_language,
                "is_partial": is_partial,
                "extraction_method": "tavily",
                "success": True,
                "metadata": {"tavily_url": article.get("url")},
            }

        except httpx.HTTPStatusError as e:
            logger.error("Tavily API error for %s: %s", url[:80], e)
            return {
                "url": url,
                "success": False,
                "error": f"Tavily API error: {e.response.status_code}",
                "extraction_method": "tavily",
                "word_count": 0,
            }
        except Exception as e:
            logger.error("Error fetching from Tavily for %s: %s", url[:80], e)
            return {
                "url": url,
                "success": False,
                "error": str(e)[:500],
                "extraction_method": "tavily",
                "word_count": 0,
            }
