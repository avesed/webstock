"""YFinance news provider for US, HK, and precious metals stocks.

Migrated from backend/app/services/news_service.py (YFinanceProvider class).
Uses the Search API for US stocks and the Ticker API for HK/metals.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.executor import run_in_executor
from app.providers.constants import detect_market

logger = logging.getLogger(__name__)

# HTML tag pattern for stripping
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


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


class YFinanceNewsProvider:
    """YFinance news provider.

    Uses yfinance Search API (best for US stocks) and Ticker API
    (works for HK, metals, and international stocks) via the shared
    ThreadPoolExecutor.

    Returns plain dicts matching the NewsArticle model.
    """

    @staticmethod
    def _parse_news_items(
        news_data: list,
        symbol: str,
        news_count: int = 20,
    ) -> List[Dict[str, Any]]:
        """Parse yfinance news items into dicts matching NewsArticle model."""
        market = detect_market(symbol)
        articles: List[Dict[str, Any]] = []

        for item in news_data[:news_count]:
            try:
                # Parse published timestamp
                published_timestamp = (
                    item.get("published_at")
                    or item.get("publishedAt")
                    or item.get("datetime")
                    or item.get("providerPublishTime")
                )
                if published_timestamp:
                    if isinstance(published_timestamp, (int, float)):
                        published = datetime.fromtimestamp(
                            published_timestamp, tz=timezone.utc,
                        )
                    else:
                        published = datetime.fromisoformat(
                            published_timestamp.replace("Z", "+00:00"),
                        )
                else:
                    published = datetime.now(timezone.utc)

                url = item.get("link") or item.get("url") or ""
                source = item.get("publisher") or item.get("source") or "yfinance"
                title = item.get("title", "")
                summary = (
                    item.get("summary")
                    or item.get("description")
                    or item.get("content", "")
                )

                article = {
                    "id": _generate_news_id(url or title),
                    "symbol": symbol,
                    "title": _sanitize_html(title) or "",
                    "summary": _sanitize_html(summary) if summary else None,
                    "source": source,
                    "url": url,
                    "published_at": published.isoformat(),
                    "market": market,
                    "provider": "yfinance",
                }
                articles.append(article)
            except Exception as e:
                logger.warning("Error parsing YFinance news item: %s", e)
                continue

        return articles

    async def get_news(
        self,
        symbol: str,
        news_count: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch news from YFinance using Search API (best for US stocks).

        Args:
            symbol: Stock symbol (e.g., AAPL).
            news_count: Number of news articles to fetch (default 20).

        Returns:
            List of dicts matching NewsArticle model, or empty list on error.
        """
        try:
            import yfinance as yf

            def fetch():
                search = yf.Search(symbol, news_count=news_count)
                return search.news if hasattr(search, "news") else []

            news_data = await run_in_executor(fetch)
            articles = self._parse_news_items(news_data, symbol, news_count)
            logger.info(
                "Fetched %d news articles from YFinance Search for %s",
                len(articles), symbol,
            )
            return articles

        except asyncio.TimeoutError:
            logger.error("YFinance news API timeout for %s", symbol)
            return []
        except ImportError:
            logger.warning("yfinance not installed, skipping YFinance news")
            return []
        except Exception as e:
            error_str = str(e).lower()
            if "rate limit" in error_str or "429" in error_str:
                logger.warning("YFinance rate limit exceeded for %s", symbol)
            elif "timeout" in error_str:
                logger.error("YFinance connection timeout for %s", symbol)
            else:
                logger.error("YFinance news error for %s: %s", symbol, e)
            return []

    async def get_news_by_ticker(
        self,
        symbol: str,
        news_count: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch news from YFinance using Ticker API.

        Works for HK, precious metals, and international stocks.
        The Ticker API returns a nested format that is flattened before parsing.

        Args:
            symbol: Stock symbol (e.g., 1810.HK, GC=F).
            news_count: Number of news articles to fetch (default 20).

        Returns:
            List of dicts matching NewsArticle model, or empty list on error.
        """
        try:
            import yfinance as yf

            def fetch():
                ticker = yf.Ticker(symbol)
                return ticker.news if hasattr(ticker, "news") else []

            news_data = await run_in_executor(fetch)

            # Ticker API returns nested format:
            # {content: {title, summary, pubDate, provider: {displayName}, ...}}
            # Flatten to match _parse_news_items expected format
            flattened = []
            for item in news_data or []:
                content = item.get("content", {}) if isinstance(item, dict) else {}
                if not content:
                    continue
                canonical = content.get("canonicalUrl", {}) or {}
                provider = content.get("provider", {}) or {}
                flattened.append({
                    "title": content.get("title", ""),
                    "summary": content.get("summary") or content.get("description", ""),
                    "published_at": content.get("pubDate", ""),
                    "url": canonical.get("url", ""),
                    "publisher": provider.get("displayName", "Yahoo Finance"),
                })

            articles = self._parse_news_items(flattened, symbol, news_count)
            logger.info(
                "Fetched %d news articles from YFinance Ticker for %s",
                len(articles), symbol,
            )
            return articles

        except asyncio.TimeoutError:
            logger.error("YFinance Ticker news timeout for %s", symbol)
            return []
        except ImportError:
            logger.warning("yfinance not installed, skipping YFinance news")
            return []
        except Exception as e:
            logger.error("YFinance Ticker news error for %s: %s", symbol, e)
            return []
