"""Playwright-based content extraction."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Browser, Page
from playwright_stealth import stealth_async
import trafilatura

from .config import settings

logger = logging.getLogger(__name__)

# Max recursion depth for accessibility snapshot serialization (P3-4.5)
MAX_SNAPSHOT_DEPTH = 50


class PlaywrightExtractor:
    """Content extractor using Playwright for JS rendering + trafilatura for extraction."""

    def __init__(self) -> None:
        self._browser: Optional[Browser] = None
        self._playwright = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize Playwright browser."""
        async with self._lock:
            if self._browser is not None:
                return

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            logger.info("Playwright browser initialized (headless=%s)", settings.HEADLESS)

    async def close(self) -> None:
        """Close browser and playwright."""
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            logger.info("Playwright browser closed")

    async def _create_page(self) -> Page:
        """Create a new page with stealth settings.

        Uses self._lock to guard against race conditions where
        initialize() completes between the check and new_context() call (P0-1.1).
        """
        async with self._lock:
            if not self._browser:
                # Release lock, initialize (which re-acquires), then re-acquire
                pass

        if not self._browser:
            await self.initialize()

        # After initialize(), self._browser is guaranteed non-None
        if not self._browser:
            raise RuntimeError("Playwright browser failed to initialize")

        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await stealth_async(page)
        return page

    async def extract(self, url: str) -> Dict[str, Any]:
        """
        Fast mode: Playwright render -> trafilatura extract.

        Returns dict with: success, full_text, word_count, language, authors, metadata, error
        """
        page = await self._create_page()
        try:
            await page.goto(
                url,
                wait_until="networkidle",
                timeout=settings.NAVIGATION_TIMEOUT,
            )
            await page.wait_for_timeout(500)

            html = await page.content()

            result = trafilatura.extract(
                html,
                output_format="json",
                include_comments=False,
                include_tables=True,
                favor_recall=True,
                with_metadata=True,
                url=url,
            )

            if not result:
                return {"success": False, "error": "trafilatura extraction returned no content"}

            # Explicit JSON parse error handling (P1-2.1)
            try:
                data = json.loads(result) if isinstance(result, str) else result
            except json.JSONDecodeError as je:
                logger.error(
                    "trafilatura returned invalid JSON for %s: %s",
                    url[:80], str(je)[:200],
                )
                return {"success": False, "error": f"Content extraction returned invalid JSON: {str(je)[:200]}"}

            full_text = (data.get("text") or "").strip()

            if not full_text:
                return {"success": False, "error": "No text content extracted"}

            word_count = len(full_text.split())
            if len(full_text) > settings.MAX_CONTENT_LENGTH:
                full_text = full_text[:settings.MAX_CONTENT_LENGTH] + "..."

            authors = None
            raw_author = data.get("author")
            if raw_author:
                authors = [a.strip() for a in raw_author.split(",") if a.strip()]

            return {
                "success": True,
                "full_text": full_text,
                "word_count": word_count,
                "language": data.get("language"),
                "authors": authors,
                "metadata": {
                    "hostname": data.get("hostname"),
                    "sitename": data.get("sitename"),
                    "title": data.get("title"),
                },
            }

        except Exception as e:
            error_name = type(e).__name__
            logger.error("Playwright extraction error for %s: %s: %s", url[:80], error_name, e)
            return {"success": False, "error": f"{error_name}: {str(e)[:400]}"}
        finally:
            context = page.context
            await page.close()
            await context.close()

    async def snapshot(self, url: str) -> Dict[str, Any]:
        """
        Smart mode: Playwright render -> accessibility snapshot.
        Returns structured text suitable for LLM processing.
        """
        page = await self._create_page()
        try:
            await page.goto(
                url,
                wait_until="networkidle",
                timeout=settings.NAVIGATION_TIMEOUT,
            )
            await page.wait_for_timeout(500)

            # Use page.accessibility.snapshot() — Playwright still supports this API
            snapshot_data = await page.accessibility.snapshot()
            if not snapshot_data:
                return {"success": False, "error": "Accessibility snapshot returned no data"}

            text = self._serialize_snapshot(snapshot_data)
            return {"success": True, "snapshot_text": text, "url": url}

        except Exception as e:
            error_name = type(e).__name__
            logger.error("Playwright snapshot error for %s: %s: %s", url[:80], error_name, e)
            return {"success": False, "error": f"{error_name}: {str(e)[:400]}"}
        finally:
            context = page.context
            await page.close()
            await context.close()

    def _serialize_snapshot(self, node: Dict[str, Any], depth: int = 0) -> str:
        """Recursively serialize accessibility tree to readable text.

        Bounded by MAX_SNAPSHOT_DEPTH to prevent unbounded recursion (P3-4.5).
        """
        if depth > MAX_SNAPSHOT_DEPTH:
            return ""

        lines: List[str] = []
        indent = "  " * depth

        role = node.get("role", "")
        name = node.get("name", "")
        value = node.get("value", "")

        # Build text representation
        parts = []
        if role and role not in ("none", "generic"):
            parts.append(f"[{role}]")
        if name:
            parts.append(name)
        if value:
            parts.append(f"= {value}")

        if parts:
            lines.append(f"{indent}{' '.join(parts)}")

        # Recurse into children
        for child in node.get("children", []):
            child_text = self._serialize_snapshot(child, depth + 1)
            if child_text:
                lines.append(child_text)

        return "\n".join(lines)


# Global extractor instance
_extractor: Optional[PlaywrightExtractor] = None
_extractor_lock = asyncio.Lock()


async def get_extractor() -> PlaywrightExtractor:
    """Get or create global extractor instance.

    Uses asyncio.Lock for concurrency-safe initialization (P0-1.2).
    """
    global _extractor
    if _extractor is not None:
        return _extractor

    async with _extractor_lock:
        # Double-check after acquiring lock
        if _extractor is not None:
            return _extractor
        _extractor = PlaywrightExtractor()
        await _extractor.initialize()
        return _extractor
