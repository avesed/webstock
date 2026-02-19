"""Finnhub news provider for US stocks and general market news.

Migrated from backend/app/services/news_service.py (FinnhubProvider class).
Only raw news fetching is included -- entity extraction and LLM processing
remain in the main backend.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.core.executor import run_in_executor

logger = logging.getLogger(__name__)

# HTML tag pattern for stripping
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# Maximum articles per request
MAX_COMPANY_NEWS = 50
MAX_GENERAL_NEWS = 30


def _sanitize_html(text: Optional[str]) -> Optional[str]:
    """Strip HTML tags and escape special characters."""
    if text is None:
        return None
    text = _HTML_TAG_PATTERN.sub("", text)
    text = html.escape(text)
    text = " ".join(text.split())
    return text.strip() if text else None


def _generate_news_id(url: str) -> str:
    """Generate deterministic ID from URL."""
    return hashlib.md5(url.encode()).hexdigest()


class FinnhubNewsProvider:
    """Finnhub news provider for US stocks and general market news.

    Uses the finnhub Python client via the shared ThreadPoolExecutor.
    Returns plain dicts matching the NewsArticle model.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def _get_api_key(self) -> Optional[str]:
        """Get Finnhub API key from config."""
        return self._settings.FINNHUB_API_KEY or None

    async def get_company_news(
        self,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch company news from Finnhub API.

        Args:
            symbol: US stock symbol (e.g., AAPL).
            from_date: Start date in YYYY-MM-DD format. Defaults to 7 days ago.
            to_date: End date in YYYY-MM-DD format. Defaults to today.

        Returns:
            List of dicts matching NewsArticle model, or empty list on error.
        """
        api_key = self._get_api_key()
        if not api_key:
            logger.warning("FINNHUB_API_KEY not configured, skipping Finnhub news")
            return []

        try:
            import finnhub

            def fetch():
                client = finnhub.Client(api_key=api_key)
                _from = from_date or (
                    datetime.now(timezone.utc) - timedelta(days=7)
                ).strftime("%Y-%m-%d")
                _to = to_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                return client.company_news(symbol, _from=_from, to=_to)

            news_data = await run_in_executor(fetch)

            articles: List[Dict[str, Any]] = []
            for item in news_data[:MAX_COMPANY_NEWS]:
                try:
                    published = datetime.fromtimestamp(
                        item.get("datetime", 0),
                        tz=timezone.utc,
                    )
                    url = item.get("url", "")
                    article = {
                        "id": _generate_news_id(url),
                        "symbol": symbol,
                        "title": _sanitize_html(item.get("headline", "")) or "",
                        "summary": _sanitize_html(item.get("summary", "")),
                        "source": item.get("source", "finnhub"),
                        "url": url,
                        "published_at": published.isoformat(),
                        "market": "us",
                        "image_url": item.get("image"),
                        "provider": "finnhub",
                    }
                    articles.append(article)
                except Exception as e:
                    logger.warning("Error parsing Finnhub news item: %s", e)
                    continue

            logger.info(
                "Fetched %d news articles from Finnhub for %s",
                len(articles), symbol,
            )
            return articles

        except asyncio.TimeoutError:
            logger.error("Finnhub API timeout for %s", symbol)
            return []
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str:
                logger.warning("Finnhub rate limit exceeded for %s", symbol)
            elif "401" in error_str or "403" in error_str or "unauthorized" in error_str:
                logger.error("Finnhub authentication error for %s: %s", symbol, e)
            elif "timeout" in error_str:
                logger.error("Finnhub connection timeout for %s", symbol)
            else:
                logger.error("Finnhub news error for %s: %s", symbol, e)
            return []

    async def get_general_news(
        self,
        category: str = "general",
    ) -> List[Dict[str, Any]]:
        """Fetch general market news from Finnhub.

        Args:
            category: News category (general, forex, crypto, merger).

        Returns:
            List of dicts matching NewsArticle model, or empty list on error.
        """
        api_key = self._get_api_key()
        if not api_key:
            logger.warning("FINNHUB_API_KEY not configured, skipping general news")
            return []

        try:
            import finnhub

            def fetch():
                client = finnhub.Client(api_key=api_key)
                return client.general_news(category, min_id=0)

            news_data = await run_in_executor(fetch)

            articles: List[Dict[str, Any]] = []
            for item in news_data[:MAX_GENERAL_NEWS]:
                try:
                    published = datetime.fromtimestamp(
                        item.get("datetime", 0),
                        tz=timezone.utc,
                    )
                    url = item.get("url", "")
                    article = {
                        "id": _generate_news_id(url),
                        "symbol": "MARKET",
                        "title": _sanitize_html(item.get("headline", "")) or "",
                        "summary": _sanitize_html(item.get("summary", "")),
                        "source": item.get("source", "finnhub"),
                        "url": url,
                        "published_at": published.isoformat(),
                        "market": "us",
                        "image_url": item.get("image"),
                        "provider": "finnhub",
                    }
                    articles.append(article)
                except Exception as e:
                    logger.warning("Error parsing Finnhub general news item: %s", e)
                    continue

            logger.info(
                "Fetched %d general news articles from Finnhub", len(articles),
            )
            return articles

        except asyncio.TimeoutError:
            logger.error("Finnhub general news API timeout")
            return []
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str:
                logger.warning("Finnhub rate limit exceeded for general news")
            elif "401" in error_str or "403" in error_str or "unauthorized" in error_str:
                logger.error("Finnhub authentication error for general news: %s", e)
            else:
                logger.error("Finnhub general news error: %s", e)
            return []
