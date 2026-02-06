"""Full content fetching service for news articles."""

import asyncio
import logging
import re
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
]


class ContentSource(str, Enum):
    """Content source providers."""

    SCRAPER = "scraper"    # newspaper4k scraper
    POLYGON = "polygon"    # Polygon.io API


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


class ScraperProvider(ContentProvider):
    """
    Content provider using newspaper4k for web scraping.

    Supports multiple languages including English and Chinese.
    """

    def __init__(self) -> None:
        self._newspaper_available: Optional[bool] = None

    def _check_newspaper_available(self) -> bool:
        """Check if newspaper4k is available."""
        if self._newspaper_available is None:
            try:
                import newspaper
                self._newspaper_available = True
            except ImportError:
                logger.warning("newspaper4k not installed. Install with: pip install newspaper4k")
                self._newspaper_available = False
        return self._newspaper_available

    async def fetch(self, url: str, language: str = "en", **kwargs) -> FetchResult:
        """
        Fetch content using newspaper4k.

        Args:
            url: Article URL
            language: Expected language (en, zh, etc.)
            **kwargs: Additional options

        Returns:
            FetchResult with scraped content
        """
        import time

        start_time = time.monotonic()
        logger.info("Starting content fetch: url=%s, language=%s", url[:100], language)

        if not self._check_newspaper_available():
            return FetchResult(
                success=False,
                error="newspaper4k not available",
                source=ContentSource.SCRAPER,
            )

        try:
            import newspaper
            from newspaper import Article

            # Run blocking operations in thread pool
            loop = asyncio.get_event_loop()

            def _fetch_article():
                # Configure article with language hint
                config = newspaper.Config()
                config.browser_user_agent = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
                config.request_timeout = FETCH_TIMEOUT
                config.language = language if language in ["en", "zh", "ja", "ko", "de", "fr", "es"] else "en"

                article = Article(url, config=config)
                article.download()
                article.parse()

                # Try NLP for keywords (only works for English)
                try:
                    if article.text and len(article.text) > 100:
                        article.nlp()
                except Exception:
                    pass  # NLP is optional

                return article

            article = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_article),
                timeout=FETCH_TIMEOUT + 5,
            )

            # Extract content
            full_text = article.text.strip() if article.text else ""
            word_count = len(full_text.split())

            # Determine if content is partial
            is_partial = len(full_text) < MIN_CONTENT_LENGTH

            # Truncate if too long
            if len(full_text) > MAX_CONTENT_LENGTH:
                full_text = full_text[:MAX_CONTENT_LENGTH] + "..."

            # Extract publish date
            publish_date = None
            if article.publish_date:
                if isinstance(article.publish_date, datetime):
                    publish_date = article.publish_date
                    if publish_date.tzinfo is None:
                        publish_date = publish_date.replace(tzinfo=timezone.utc)

            elapsed = time.monotonic() - start_time
            logger.info(
                "Content fetch succeeded: url=%s, words=%d, chars=%d, partial=%s, elapsed=%.2fs",
                url[:80], word_count, len(full_text), is_partial, elapsed
            )

            return FetchResult(
                success=True,
                full_text=full_text if full_text else None,
                authors=list(article.authors) if article.authors else None,
                keywords=list(article.keywords) if article.keywords else None,
                top_image=article.top_image if article.top_image else None,
                language=language,
                publish_date=publish_date,
                is_partial=is_partial,
                word_count=word_count,
                source=ContentSource.SCRAPER,
                metadata={
                    "movies": list(article.movies) if article.movies else None,
                    "meta_lang": article.meta_lang,
                    "meta_description": article.meta_description,
                },
            )

        except asyncio.TimeoutError:
            logger.error("Timeout fetching content from %s", url)
            return FetchResult(
                success=False,
                error=f"Timeout after {FETCH_TIMEOUT}s",
                source=ContentSource.SCRAPER,
            )
        except Exception as e:
            logger.error("Error scraping content from %s: %s", url, e)
            return FetchResult(
                success=False,
                error=str(e)[:500],
                source=ContentSource.SCRAPER,
            )


class PolygonProvider(ContentProvider):
    """
    Content provider using Polygon.io Reference API.

    Requires a Polygon.io API key with news access.
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


class FullContentService:
    """
    Unified service for fetching full news content.

    Supports multiple providers and automatic fallback.
    """

    def __init__(
        self,
        default_source: ContentSource = ContentSource.SCRAPER,
        polygon_api_key: Optional[str] = None,
    ) -> None:
        """
        Initialize the service.

        Args:
            default_source: Default content source to use
            polygon_api_key: Polygon.io API key for Polygon provider
        """
        self.default_source = default_source
        self.providers: Dict[ContentSource, ContentProvider] = {
            ContentSource.SCRAPER: ScraperProvider(),
            ContentSource.POLYGON: PolygonProvider(api_key=polygon_api_key),
        }

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
        Fetch content with fallback to alternative provider.

        Args:
            url: Article URL
            primary_source: Primary source to try first
            language: Expected language
            polygon_api_key: Polygon API key for fallback

        Returns:
            FetchResult from first successful provider
        """
        # Check blocked domains first
        if self.is_blocked_domain(url):
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

        if result.success and result.full_text:
            return result

        # Try fallback source
        fallback = ContentSource.POLYGON if primary == ContentSource.SCRAPER else ContentSource.SCRAPER

        logger.info(
            "Primary source %s failed for %s, trying fallback %s",
            primary, url, fallback
        )

        fallback_result = await self.fetch_content(
            url,
            source=fallback,
            language=language,
            polygon_api_key=polygon_api_key,
        )

        # Return fallback result if successful, otherwise return original error
        if fallback_result.success:
            return fallback_result

        return result


# Singleton instance
_full_content_service: Optional[FullContentService] = None


def get_full_content_service(
    default_source: ContentSource = ContentSource.SCRAPER,
    polygon_api_key: Optional[str] = None,
) -> FullContentService:
    """
    Get FullContentService instance.

    Args:
        default_source: Default content source
        polygon_api_key: Polygon.io API key

    Returns:
        FullContentService instance
    """
    global _full_content_service
    if _full_content_service is None:
        _full_content_service = FullContentService(
            default_source=default_source,
            polygon_api_key=polygon_api_key,
        )
    return _full_content_service
