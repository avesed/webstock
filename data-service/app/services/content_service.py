"""Content extraction service with multi-provider fallback chain.

Orchestrates content extraction from URLs using a fallback chain:
1. Trafilatura (primary -- F1 ~0.92, strong CJK support)
2. Playwright (JS rendering for dynamic pages)
3. Tavily (server-side rendering, advanced extraction)
4. Polygon (metadata only, last resort)

Blocked domains (social media, paywalls) are rejected upfront.
Minimum 500 chars validation, maximum 50K chars truncation.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.providers.playwright_provider import PlaywrightContentProvider
from app.providers.polygon_provider import PolygonContentProvider
from app.providers.tavily_provider import TavilyContentProvider
from app.providers.trafilatura_provider import TrafilaturaContentProvider

logger = logging.getLogger(__name__)

# Minimum content length to be considered complete
MIN_CONTENT_LENGTH = 500

# Blocked domains that don't allow scraping or have paywalls
BLOCKED_DOMAINS = [
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "tiktok.com",
    "reddit.com",
    "youtube.com",
    "wsj.com",            # Paywall
    "ft.com",             # Paywall
    "barrons.com",        # Paywall
    "economist.com",      # Paywall
    "nytimes.com",        # Paywall
    "washingtonpost.com", # Paywall
    "bloomberg.com",      # Paywall
    "seekingalpha.com",   # Paywall (premium)
    "marketwatch.com",    # Paywall (401)
]


def _is_blocked_domain(url: str) -> bool:
    """Check if URL is from a blocked domain."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        for blocked in BLOCKED_DOMAINS:
            if domain == blocked or domain.endswith("." + blocked):
                return True
        return False
    except Exception:
        return False


class ContentService:
    """Unified content extraction service with fallback chain.

    Stateless -- each call creates fresh provider instances (providers
    are lightweight and hold no connection state).
    """

    def __init__(self) -> None:
        self._trafilatura = TrafilaturaContentProvider()
        self._playwright = PlaywrightContentProvider()
        self._tavily = TavilyContentProvider()
        self._polygon = PolygonContentProvider()

    async def fetch_content(
        self,
        url: str,
        language: Optional[str] = None,
        include_images: bool = False,
    ) -> Dict[str, Any]:
        """Fetch full content from URL using fallback chain.

        Fallback order:
        1. Trafilatura (fast, accurate, CJK-aware)
        2. Playwright (JS rendering, if service available)
        3. Tavily (server-side extraction, if API key configured)
        4. Polygon (metadata only, last resort)

        Args:
            url: Article URL.
            language: Expected language hint (en, zh, etc.).
            include_images: Whether to download/base64-encode images
                in trafilatura (default False for speed).

        Returns:
            Dict matching ContentResult model. Always has 'success' field.
        """
        start_time = time.monotonic()

        # Check blocked domains first
        if _is_blocked_domain(url):
            logger.info("Blocked domain detected: %s", url)
            return {
                "url": url,
                "success": False,
                "error": "Domain blocked (social media or paywall)",
                "extraction_method": "none",
                "word_count": 0,
            }

        # 1. Try trafilatura (primary)
        result = await self._trafilatura.extract(
            url, language=language or "en", include_images=include_images,
        )
        if result and result.get("success") and result.get("full_text"):
            if not result.get("is_partial"):
                result["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
                return result
            logger.info(
                "Trafilatura returned partial content for %s, trying fallbacks",
                url[:80],
            )

        # Track best partial result
        best_result = result if (result and result.get("success") and result.get("full_text")) else None

        # 2. Try Playwright
        playwright_result = await self._playwright.extract(url)
        if playwright_result and playwright_result.get("success") and playwright_result.get("full_text"):
            if not playwright_result.get("is_partial"):
                playwright_result["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
                return playwright_result
            if best_result is None or playwright_result.get("word_count", 0) > best_result.get("word_count", 0):
                best_result = playwright_result

        # 3. Try Tavily
        tavily_result = await self._tavily.extract(url)
        if tavily_result and tavily_result.get("success") and tavily_result.get("full_text"):
            if not tavily_result.get("is_partial"):
                tavily_result["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
                return tavily_result
            if best_result is None or tavily_result.get("word_count", 0) > best_result.get("word_count", 0):
                best_result = tavily_result

        # 4. Try Polygon (metadata only, last resort)
        polygon_result = await self._polygon.extract(url)
        if polygon_result and polygon_result.get("success") and polygon_result.get("full_text"):
            if best_result is None or polygon_result.get("word_count", 0) > best_result.get("word_count", 0):
                best_result = polygon_result

        # Return best result or original error
        if best_result:
            if best_result.get("is_partial"):
                logger.info(
                    "Returning best partial result: method=%s, words=%d",
                    best_result.get("extraction_method"), best_result.get("word_count", 0),
                )
            best_result["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
            return best_result

        # All providers truly failed
        logger.warning("All providers failed for %s", url[:80])
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        if result:
            result["elapsed_ms"] = elapsed_ms
            return result

        return {
            "url": url,
            "success": False,
            "error": "All content providers failed",
            "extraction_method": "none",
            "word_count": 0,
            "elapsed_ms": elapsed_ms,
        }
