"""Playwright content extraction provider.

Migrated from backend/app/services/full_content_service.py (PlaywrightProvider class).
Connects to a separate Playwright container that renders JavaScript and extracts
clean content. Optional -- if not available, gracefully fails so the fallback
chain continues.
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


class PlaywrightContentProvider:
    """Content provider using the Playwright extraction microservice.

    Features asymmetric TTL for availability checks:
    - 300s cache for positive (available) results
    - 60s cache for negative (unavailable) results for faster recovery
    """

    def __init__(self, service_url: Optional[str] = None) -> None:
        settings = get_settings()
        self.service_url = service_url or settings.PLAYWRIGHT_SERVICE_URL
        self._available: Optional[bool] = None
        self._last_check: float = 0.0
        self._check_ttl_success: float = 300.0   # 5 minutes
        self._check_ttl_failure: float = 60.0     # 1 minute

    async def _check_availability(self) -> bool:
        """Check if Playwright service is available (asymmetric TTL cache)."""
        now = time.monotonic()
        ttl = self._check_ttl_success if self._available else self._check_ttl_failure
        if self._available is not None and (now - self._last_check) < ttl:
            return self._available

        was_available = self._available
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.service_url}/health")
                self._available = response.status_code == 200
        except Exception:
            self._available = False

        self._last_check = time.monotonic()
        if self._available:
            if not was_available:
                logger.info("Playwright service available at %s", self.service_url)
        else:
            if was_available is None or was_available:
                logger.warning("Playwright service not available at %s", self.service_url)
            else:
                logger.debug("Playwright service still not available at %s", self.service_url)
        return self._available

    async def extract(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract content via Playwright service.

        Args:
            url: Article URL to extract.

        Returns:
            Dict matching ContentResult model, or None on failure.
        """
        if not await self._check_availability():
            return {
                "url": url,
                "success": False,
                "error": "Playwright service not available",
                "extraction_method": "playwright",
                "word_count": 0,
            }

        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT + 10) as client:
                response = await client.post(
                    f"{self.service_url}/extract",
                    json={"url": url},
                )
                response.raise_for_status()
                data = response.json()

            if not data.get("success"):
                return {
                    "url": url,
                    "success": False,
                    "error": data.get("error", "Playwright extraction failed")[:500],
                    "extraction_method": "playwright",
                    "word_count": 0,
                }

            full_text = (data.get("full_text") or "").strip()

            detected_language = (
                _detect_language(full_text) if full_text
                else (data.get("language") or "en")
            )
            word_count = _calculate_word_count(full_text, detected_language) if full_text else 0
            is_partial = len(full_text) < MIN_CONTENT_LENGTH

            if len(full_text) > MAX_CONTENT_LENGTH:
                full_text = full_text[:MAX_CONTENT_LENGTH] + "..."

            elapsed = time.monotonic() - start_time
            logger.info(
                "Playwright fetch succeeded: url=%s, words=%d, lang=%s, elapsed=%.2fs",
                url[:80], word_count, detected_language, elapsed,
            )

            return {
                "url": url,
                "full_text": full_text if full_text else None,
                "author": data.get("author") or data.get("authors"),
                "word_count": word_count,
                "language": detected_language,
                "is_partial": is_partial,
                "extraction_method": "playwright",
                "success": True,
                "metadata": data.get("metadata"),
            }

        except httpx.TimeoutException:
            logger.error("Playwright service timeout for %s", url[:80])
            return {
                "url": url,
                "success": False,
                "error": "Playwright service timeout",
                "extraction_method": "playwright",
                "word_count": 0,
            }
        except httpx.HTTPStatusError as e:
            logger.error("Playwright service HTTP error for %s: %s", url[:80], e)
            return {
                "url": url,
                "success": False,
                "error": f"Playwright service error: {e.response.status_code}",
                "extraction_method": "playwright",
                "word_count": 0,
            }
        except Exception as e:
            logger.error("Error fetching from Playwright for %s: %s", url[:80], e)
            return {
                "url": url,
                "success": False,
                "error": str(e)[:500],
                "extraction_method": "playwright",
                "word_count": 0,
            }
