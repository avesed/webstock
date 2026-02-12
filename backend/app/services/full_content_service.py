"""Full content fetching service for news articles."""

import asyncio
import json as _json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Timeout for content fetching (seconds)
FETCH_TIMEOUT = 30

# Minimum content length to be considered complete
MIN_CONTENT_LENGTH = 500

# Maximum content length to store
MAX_CONTENT_LENGTH = 50000

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
    "wsj.com",           # Paywall
    "ft.com",            # Paywall
    "barrons.com",       # Paywall
    "economist.com",     # Paywall
    "nytimes.com",       # Paywall
    "washingtonpost.com",  # Paywall
    "bloomberg.com",     # Paywall
    "seekingalpha.com",  # Paywall (premium)
]


class ContentSource(str, Enum):
    """Content source providers."""

    TRAFILATURA = "trafilatura"    # trafilatura extractor
    POLYGON = "polygon"            # Polygon.io API
    TAVILY = "tavily"              # Tavily API (Phase 2)
    PLAYWRIGHT = "playwright"      # Playwright browser (Phase 3)


@dataclass
class FetchResult:
    """Result of content fetching."""

    success: bool
    full_text: Optional[str] = None
    authors: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    top_image: Optional[str] = None
    language: Optional[str] = None
    publish_date: Optional[datetime] = None
    is_partial: bool = False  # True if content < MIN_CONTENT_LENGTH
    error: Optional[str] = None
    word_count: int = 0
    source: Optional[ContentSource] = None
    metadata: Optional[Dict[str, Any]] = None


class ContentProvider(ABC):
    """Abstract base class for content providers."""

    @abstractmethod
    async def fetch(self, url: str, **kwargs) -> FetchResult:
        """
        Fetch full content from URL.

        Args:
            url: Article URL
            **kwargs: Provider-specific options

        Returns:
            FetchResult with content or error
        """
        pass


class TrafilaturaProvider(ContentProvider):
    """
    Content provider using trafilatura for web content extraction.

    trafilatura provides a 3-tier fallback extraction pipeline
    (own algorithm -> jusText -> readability-lxml) with excellent accuracy
    (F1 ~0.92) and strong Chinese content support.
    """

    def __init__(self) -> None:
        self._trafilatura_available: Optional[bool] = None

    def _check_trafilatura_available(self) -> bool:
        """Check if trafilatura is available."""
        if self._trafilatura_available is None:
            try:
                import trafilatura  # noqa: F401
                self._trafilatura_available = True
            except ImportError:
                logger.warning("trafilatura not installed. Install with: pip install trafilatura")
                self._trafilatura_available = False
        return self._trafilatura_available

    async def fetch(self, url: str, language: str = "en", **kwargs) -> FetchResult:
        """
        Fetch content using trafilatura.

        Args:
            url: Article URL
            language: Expected language (en, zh, etc.)
            **kwargs: Additional options

        Returns:
            FetchResult with extracted content
        """
        start_time = time.monotonic()
        logger.info("Starting content fetch: url=%s, language=%s", url[:100], language)

        if not self._check_trafilatura_available():
            return FetchResult(
                success=False,
                error="trafilatura not available",
                source=ContentSource.TRAFILATURA,
            )

        try:
            import trafilatura

            loop = asyncio.get_running_loop()

            def _fetch_and_extract() -> Optional[str]:
                """Download page and extract content in a worker thread."""
                downloaded = trafilatura.fetch_url(url)
                if downloaded is None:
                    return None

                result_json = trafilatura.extract(
                    downloaded,
                    output_format="json",
                    include_comments=False,
                    include_tables=True,
                    favor_recall=True,
                    with_metadata=True,
                )
                return result_json

            result_json = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_and_extract),
                timeout=FETCH_TIMEOUT + 5,
            )

            if result_json is None:
                elapsed = time.monotonic() - start_time
                logger.warning(
                    "Content fetch returned no data: url=%s, elapsed=%.2fs",
                    url[:80], elapsed,
                )
                return FetchResult(
                    success=False,
                    error="trafilatura returned no content (download or extraction failed)",
                    source=ContentSource.TRAFILATURA,
                )

            # Parse the JSON result from trafilatura
            try:
                extracted = _json.loads(result_json)
            except _json.JSONDecodeError as je:
                logger.error(
                    "Trafilatura returned invalid JSON for %s: %s (first 200 chars: %s)",
                    url[:80], str(je)[:200], result_json[:200] if result_json else "None",
                )
                return FetchResult(
                    success=False,
                    error=f"Content extraction returned invalid JSON: {str(je)[:200]}",
                    source=ContentSource.TRAFILATURA,
                )

            full_text = (extracted.get("text") or "").strip()
            word_count = len(full_text.split())

            # Determine if content is partial
            is_partial = len(full_text) < MIN_CONTENT_LENGTH

            # Truncate if too long
            if len(full_text) > MAX_CONTENT_LENGTH:
                full_text = full_text[:MAX_CONTENT_LENGTH] + "..."

            # Parse authors (trafilatura returns comma-separated string)
            authors = None
            raw_author = extracted.get("author")
            if raw_author:
                authors = [a.strip() for a in raw_author.split(",") if a.strip()]

            # Parse publish date
            publish_date = None
            raw_date = extracted.get("date")
            if raw_date:
                try:
                    from dateutil.parser import parse as parse_date
                    publish_date = parse_date(raw_date)
                    if publish_date.tzinfo is None:
                        publish_date = publish_date.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass

            # Parse tags/keywords (trafilatura returns comma-separated string)
            keywords = None
            raw_tags = extracted.get("tags")
            if raw_tags:
                keywords = [t.strip() for t in raw_tags.split(",") if t.strip()]

            # Extract other metadata
            top_image = extracted.get("image")
            detected_language = extracted.get("language") or language
            hostname = extracted.get("hostname")
            sitename = extracted.get("sitename")

            elapsed = time.monotonic() - start_time
            logger.info(
                "Content fetch succeeded: url=%s, words=%d, chars=%d, partial=%s, elapsed=%.2fs",
                url[:80], word_count, len(full_text), is_partial, elapsed,
            )

            return FetchResult(
                success=True,
                full_text=full_text if full_text else None,
                authors=authors,
                keywords=keywords,
                top_image=top_image if top_image else None,
                language=detected_language,
                publish_date=publish_date,
                is_partial=is_partial,
                word_count=word_count,
                source=ContentSource.TRAFILATURA,
                metadata={
                    "hostname": hostname,
                    "sitename": sitename,
                    "categories": extracted.get("categories"),
                },
            )

        except asyncio.TimeoutError:
            logger.error("Timeout fetching content from %s", url)
            return FetchResult(
                success=False,
                error=f"Timeout after {FETCH_TIMEOUT}s",
                source=ContentSource.TRAFILATURA,
            )
        except Exception as e:
            logger.error("Error extracting content from %s: %s", url, e)
            return FetchResult(
                success=False,
                error=str(e)[:500],
                source=ContentSource.TRAFILATURA,
            )


class PolygonProvider(ContentProvider):
    """
    Content provider using Polygon.io Reference API.

    Requires a Polygon.io API key with news access.
    Note: Polygon provides metadata/summary only, not full article text.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key
        self.base_url = "https://api.polygon.io"

    async def fetch(self, url: str, article_id: Optional[str] = None, **kwargs) -> FetchResult:
        """
        Fetch content from Polygon.io API.

        Note: Polygon.io doesn't provide full article text, only enhanced metadata.
        This provider is mainly useful for getting additional article details.

        Args:
            url: Original article URL
            article_id: Polygon article ID if available
            **kwargs: Additional options

        Returns:
            FetchResult with Polygon data
        """
        if not self.api_key:
            return FetchResult(
                success=False,
                error="Polygon API key not configured",
                source=ContentSource.POLYGON,
            )

        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                # Polygon news endpoint
                # Note: Polygon provides news summaries, not full text
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
                    return FetchResult(
                        success=False,
                        error="No results from Polygon API",
                        source=ContentSource.POLYGON,
                    )

                article = results[0]

                # Polygon provides summary, not full text
                description = article.get("description", "")

                # Parse publish date
                publish_date = None
                pub_str = article.get("published_utc")
                if pub_str:
                    try:
                        publish_date = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                return FetchResult(
                    success=True,
                    full_text=description if description else None,
                    authors=None,  # Polygon doesn't provide authors
                    keywords=article.get("keywords"),
                    top_image=article.get("image_url"),
                    language="en",  # Polygon primarily English
                    publish_date=publish_date,
                    is_partial=True,  # Polygon only provides summary
                    word_count=len(description.split()) if description else 0,
                    source=ContentSource.POLYGON,
                    metadata={
                        "polygon_id": article.get("id"),
                        "publisher": article.get("publisher", {}).get("name"),
                        "tickers": article.get("tickers"),
                    },
                )

        except httpx.HTTPStatusError as e:
            logger.error("Polygon API error: %s", e)
            return FetchResult(
                success=False,
                error=f"Polygon API error: {e.response.status_code}",
                source=ContentSource.POLYGON,
            )
        except Exception as e:
            logger.error("Error fetching from Polygon: %s", e)
            return FetchResult(
                success=False,
                error=str(e)[:500],
                source=ContentSource.POLYGON,
            )


class TavilyProvider(ContentProvider):
    """
    Content provider using Tavily Extract API.

    Tavily handles JavaScript rendering server-side and provides clean,
    structured content extraction. Useful as a fallback for JS-heavy sites
    and pages where trafilatura fails.

    API: POST https://api.tavily.com/extract
    Docs: https://docs.tavily.com/documentation/api-reference/endpoint/extract

    Requires TAVILY_API_KEY environment variable.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key

    async def fetch(self, url: str, **kwargs) -> FetchResult:
        if not self.api_key:
            return FetchResult(
                success=False,
                error="Tavily API key not configured",
                source=ContentSource.TAVILY,
            )

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
                return FetchResult(
                    success=False,
                    error="No results from Tavily Extract API",
                    source=ContentSource.TAVILY,
                )

            article = results[0]
            full_text = (article.get("raw_content") or "").strip()

            if not full_text:
                return FetchResult(
                    success=False,
                    error="Tavily returned empty content",
                    source=ContentSource.TAVILY,
                )

            word_count = len(full_text.split())
            is_partial = len(full_text) < MIN_CONTENT_LENGTH

            if len(full_text) > MAX_CONTENT_LENGTH:
                full_text = full_text[:MAX_CONTENT_LENGTH] + "..."

            elapsed = time.monotonic() - start_time
            logger.info(
                "Tavily fetch succeeded: url=%s, words=%d, elapsed=%.2fs",
                url[:80], word_count, elapsed,
            )

            return FetchResult(
                success=True,
                full_text=full_text,
                authors=None,
                keywords=None,
                top_image=None,
                language=None,  # Tavily doesn't detect language; caller will detect
                publish_date=None,
                is_partial=is_partial,
                word_count=word_count,
                source=ContentSource.TAVILY,
                metadata={"tavily_url": article.get("url")},
            )

        except httpx.HTTPStatusError as e:
            logger.error("Tavily API error for %s: %s", url[:80], e)
            return FetchResult(
                success=False,
                error=f"Tavily API error: {e.response.status_code}",
                source=ContentSource.TAVILY,
            )
        except Exception as e:
            logger.error("Error fetching from Tavily for %s: %s", url[:80], e)
            return FetchResult(
                success=False,
                error=str(e)[:500],
                source=ContentSource.TAVILY,
            )


class PlaywrightProvider(ContentProvider):
    """
    Content provider using the Playwright extraction microservice.

    Connects to a separate Playwright container that renders JavaScript
    and extracts clean content. The service is optional - if not available,
    this provider gracefully fails so the fallback chain continues.

    Requires playwright-service running at PLAYWRIGHT_SERVICE_URL.
    """

    def __init__(self, service_url: str = "http://playwright-service:8002") -> None:
        self.service_url = service_url
        self._available: Optional[bool] = None
        self._last_check: float = 0.0
        self._check_ttl_success: float = 300.0  # 5 minutes for positive results
        self._check_ttl_failure: float = 60.0   # 1 minute for negative results (faster recovery)

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

        self._last_check = time.monotonic()  # Use current time after check completes
        if self._available:
            if not was_available:
                logger.info("Playwright service available at %s", self.service_url)
        else:
            if was_available is None or was_available:
                # First check or transition from available -> unavailable
                logger.warning("Playwright service not available at %s", self.service_url)
            else:
                logger.debug("Playwright service still not available at %s", self.service_url)
        return self._available

    async def fetch(self, url: str, **kwargs) -> FetchResult:
        if not await self._check_availability():
            return FetchResult(
                success=False,
                error="Playwright service not available",
                source=ContentSource.PLAYWRIGHT,
            )

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
                return FetchResult(
                    success=False,
                    error=data.get("error", "Playwright extraction failed")[:500],
                    source=ContentSource.PLAYWRIGHT,
                )

            full_text = (data.get("full_text") or "").strip()
            word_count = data.get("word_count", len(full_text.split()) if full_text else 0)
            is_partial = len(full_text) < MIN_CONTENT_LENGTH

            # Apply MAX_CONTENT_LENGTH truncation for consistency with other providers
            if len(full_text) > MAX_CONTENT_LENGTH:
                full_text = full_text[:MAX_CONTENT_LENGTH] + "..."

            elapsed = time.monotonic() - start_time
            logger.info(
                "Playwright fetch succeeded: url=%s, words=%d, elapsed=%.2fs",
                url[:80], word_count, elapsed,
            )

            return FetchResult(
                success=True,
                full_text=full_text if full_text else None,
                authors=data.get("authors"),
                keywords=None,
                top_image=None,
                language=data.get("language"),
                publish_date=None,
                is_partial=is_partial,
                word_count=word_count,
                source=ContentSource.PLAYWRIGHT,
                metadata=data.get("metadata"),
            )

        except httpx.TimeoutException:
            logger.error("Playwright service timeout for %s", url[:80])
            return FetchResult(
                success=False,
                error="Playwright service timeout",
                source=ContentSource.PLAYWRIGHT,
            )
        except httpx.HTTPStatusError as e:
            logger.error("Playwright service HTTP error for %s: %s", url[:80], e)
            return FetchResult(
                success=False,
                error=f"Playwright service error: {e.response.status_code}",
                source=ContentSource.PLAYWRIGHT,
            )
        except Exception as e:
            logger.error("Error fetching from Playwright for %s: %s", url[:80], e)
            return FetchResult(
                success=False,
                error=str(e)[:500],
                source=ContentSource.PLAYWRIGHT,
            )


class FullContentService:
    """
    Unified service for fetching full news content.

    Supports multiple providers and automatic fallback.
    """

    def __init__(
        self,
        default_source: ContentSource = ContentSource.TRAFILATURA,
        polygon_api_key: Optional[str] = None,
        tavily_api_key: Optional[str] = None,
        playwright_service_url: Optional[str] = None,
    ) -> None:
        """
        Initialize the service.

        Args:
            default_source: Default content source to use
            polygon_api_key: Polygon.io API key for Polygon provider
            tavily_api_key: Tavily Extract API key
            playwright_service_url: Playwright microservice URL
        """
        self.default_source = default_source
        self.providers: Dict[ContentSource, ContentProvider] = {
            ContentSource.TRAFILATURA: TrafilaturaProvider(),
            ContentSource.POLYGON: PolygonProvider(api_key=polygon_api_key),
        }

        # Optional providers - only add if configured
        if tavily_api_key:
            self.providers[ContentSource.TAVILY] = TavilyProvider(api_key=tavily_api_key)
            logger.info("Tavily provider initialized")

        if playwright_service_url:
            self.providers[ContentSource.PLAYWRIGHT] = PlaywrightProvider(
                service_url=playwright_service_url
            )
            logger.info("Playwright provider initialized (url=%s)", playwright_service_url)

    def is_blocked_domain(self, url: str) -> bool:
        """
        Check if URL is from a blocked domain.

        Args:
            url: URL to check

        Returns:
            True if domain is blocked
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove www. prefix
            if domain.startswith("www."):
                domain = domain[4:]

            for blocked in BLOCKED_DOMAINS:
                if domain == blocked or domain.endswith("." + blocked):
                    return True

            return False
        except Exception:
            return False

    def detect_language(self, text: str) -> str:
        """
        Simple language detection based on character ranges.

        Args:
            text: Text to analyze

        Returns:
            Language code (en, zh, etc.)
        """
        if not text:
            return "en"

        # Count Chinese characters
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        total_chars = len(text)

        if total_chars > 0 and chinese_chars / total_chars > 0.1:
            return "zh"

        return "en"

    async def fetch_content(
        self,
        url: str,
        source: Optional[ContentSource] = None,
        language: Optional[str] = None,
        polygon_api_key: Optional[str] = None,
    ) -> FetchResult:
        """
        Fetch full content from URL.

        Args:
            url: Article URL
            source: Content source to use (defaults to service default)
            language: Expected language (auto-detected if not provided)
            polygon_api_key: Override Polygon API key

        Returns:
            FetchResult with content or error
        """
        # Check blocked domains first
        if self.is_blocked_domain(url):
            logger.info("Blocked domain detected: %s", url)
            return FetchResult(
                success=False,
                error="Domain blocked (social media or paywall)",
                source=source or self.default_source,
            )

        # Select source
        use_source = source or self.default_source

        # Get provider
        provider = self.providers.get(use_source)
        if not provider:
            return FetchResult(
                success=False,
                error=f"Unknown content source: {use_source}",
            )

        # Override Polygon API key if provided
        if use_source == ContentSource.POLYGON and polygon_api_key:
            provider = PolygonProvider(api_key=polygon_api_key)

        # Fetch content
        result = await provider.fetch(url, language=language or "en")

        # Auto-detect language from content if not specified
        if result.success and result.full_text and not language:
            result.language = self.detect_language(result.full_text)

        return result

    async def fetch_with_fallback(
        self,
        url: str,
        primary_source: Optional[ContentSource] = None,
        language: Optional[str] = None,
        polygon_api_key: Optional[str] = None,
    ) -> FetchResult:
        """
        Fetch content with intelligent multi-provider fallback.

        Fallback chain (based on available providers):
        1. Primary source (default: trafilatura)
        2. Playwright (if available - handles JS rendering)
        3. Tavily (if configured - ultimate fallback)
        4. Polygon (metadata only)

        Args:
            url: Article URL
            primary_source: Primary source to try first
            language: Expected language
            polygon_api_key: Polygon API key for fallback

        Returns:
            FetchResult from best successful provider
        """
        # Check blocked domains first
        if self.is_blocked_domain(url):
            logger.info("Blocked domain detected (fallback path): %s", url)
            return FetchResult(
                success=False,
                error="Domain blocked (social media or paywall)",
            )

        primary = primary_source or self.default_source

        # Try primary source
        result = await self.fetch_content(
            url,
            source=primary,
            language=language,
            polygon_api_key=polygon_api_key,
        )

        # Success with sufficient content -- return immediately
        if result.success and result.full_text and not result.is_partial:
            return result

        logger.info(
            "Primary source %s result for %s: success=%s, partial=%s — trying fallbacks",
            primary, url[:80], result.success, result.is_partial,
        )

        # Build fallback chain dynamically based on available providers
        fallback_chain = []
        if ContentSource.PLAYWRIGHT in self.providers:
            fallback_chain.append(ContentSource.PLAYWRIGHT)
        if ContentSource.TAVILY in self.providers:
            fallback_chain.append(ContentSource.TAVILY)
        if ContentSource.POLYGON in self.providers:
            fallback_chain.append(ContentSource.POLYGON)

        # Remove primary from fallback chain to avoid retry
        fallback_chain = [s for s in fallback_chain if s != primary]

        if not fallback_chain:
            logger.warning(
                "No fallback providers configured for %s (primary=%s)",
                url[:80], primary,
            )
            return result

        # Track best partial result by word count
        best_result = result if (result.success and result.full_text) else None

        for fallback_source in fallback_chain:
            logger.info("Trying fallback provider: %s for %s", fallback_source, url[:80])

            fallback_result = await self.fetch_content(
                url,
                source=fallback_source,
                language=language,
                polygon_api_key=polygon_api_key,
            )

            if fallback_result.success and fallback_result.full_text:
                if not fallback_result.is_partial:
                    # Full content from fallback — use it immediately
                    logger.info(
                        "Fallback provider %s succeeded (full): words=%d",
                        fallback_source, fallback_result.word_count,
                    )
                    return fallback_result
                # Partial content — track if it's the best so far
                if best_result is None or fallback_result.word_count > best_result.word_count:
                    best_result = fallback_result

        # Return best partial result if we have one
        if best_result:
            if best_result.is_partial:
                logger.info(
                    "Returning best partial result: source=%s, words=%d",
                    best_result.source, best_result.word_count,
                )
            return best_result

        # All providers truly failed — return original error
        logger.warning("All providers failed for %s", url[:80])
        return result


# Singleton instance
_full_content_service: Optional[FullContentService] = None


def reset_full_content_service() -> None:
    """Reset the singleton instance (used by Celery workers between tasks)."""
    global _full_content_service
    _full_content_service = None


def get_full_content_service(
    default_source: ContentSource = ContentSource.TRAFILATURA,
    polygon_api_key: Optional[str] = None,
    tavily_api_key: Optional[str] = None,
    playwright_service_url: Optional[str] = None,
) -> FullContentService:
    """
    Get FullContentService singleton instance.

    On first call, reads fallback provider config from app settings
    unless explicit values are provided.

    Args:
        default_source: Default content source
        polygon_api_key: Polygon.io API key
        tavily_api_key: Tavily Extract API key
        playwright_service_url: Playwright microservice URL

    Returns:
        FullContentService instance
    """
    global _full_content_service
    if _full_content_service is None:
        from app.config import settings

        _full_content_service = FullContentService(
            default_source=default_source,
            polygon_api_key=polygon_api_key or getattr(settings, "POLYGON_API_KEY", None),
            tavily_api_key=tavily_api_key or getattr(settings, "TAVILY_API_KEY", None),
            playwright_service_url=playwright_service_url
            or getattr(settings, "PLAYWRIGHT_SERVICE_URL", None),
        )
    return _full_content_service
