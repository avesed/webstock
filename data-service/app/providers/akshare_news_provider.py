"""AKShare news provider for A-shares and Chinese market news.

Migrated from backend/app/services/news_service.py (AKShareProvider class).
Only raw news fetching is included -- entity extraction stays in the backend.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.executor import run_in_executor

logger = logging.getLogger(__name__)

# HTML tag pattern for stripping
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# China Standard Time offset
_CST = timezone(timedelta(hours=8))


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


def _parse_cst_datetime(pub_str: str) -> datetime:
    """Parse a CST datetime string to UTC datetime.

    Handles both 'YYYY-MM-DD HH:MM:SS' and 'YYYY-MM-DD' formats.
    Falls back to current UTC time on parse error.
    """
    try:
        if len(pub_str) > 10:
            published = datetime.strptime(pub_str[:19], "%Y-%m-%d %H:%M:%S")
        else:
            published = datetime.strptime(pub_str[:10], "%Y-%m-%d")
        return published.replace(tzinfo=_CST).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


class AKShareNewsProvider:
    """AKShare news provider for A-shares and Chinese market news.

    Uses akshare via the shared ThreadPoolExecutor.
    Returns plain dicts matching the NewsArticle model.
    """

    async def get_news_cn(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch news for A-shares from AKShare (Eastmoney source).

        Args:
            symbol: A-share symbol (e.g., 600519.SS or 000001.SZ).

        Returns:
            List of dicts matching NewsArticle model, or empty list on error.
        """
        try:
            import akshare as ak

            # Extract stock code without market suffix
            code = symbol.replace(".SS", "").replace(".SZ", "")
            market = "sh" if symbol.endswith(".SS") else "sz"

            def fetch():
                try:
                    df = ak.stock_news_em(symbol=code)
                    return df.to_dict("records") if df is not None and not df.empty else []
                except Exception:
                    return []

            news_data = await run_in_executor(fetch)

            articles: List[Dict[str, Any]] = []
            for item in news_data[:30]:
                try:
                    pub_str = str(
                        item.get("\u53d1\u5e03\u65f6\u95f4", "")
                        or item.get("\u65f6\u95f4", "")
                    )
                    published = _parse_cst_datetime(pub_str)

                    title = (
                        item.get("\u65b0\u95fb\u6807\u9898", "")
                        or item.get("\u6807\u9898", "")
                    )
                    url = (
                        item.get("\u65b0\u95fb\u94fe\u63a5", "")
                        or item.get("\u94fe\u63a5", "")
                        or ""
                    )
                    content = (
                        item.get("\u65b0\u95fb\u5185\u5bb9", "")
                        or item.get("\u5185\u5bb9", "")
                        or ""
                    )

                    article = {
                        "id": _generate_news_id(url or title),
                        "symbol": symbol,
                        "title": _sanitize_html(title) or "",
                        "summary": _sanitize_html(content[:500]) if content else None,
                        "source": "eastmoney",
                        "url": url,
                        "published_at": published.isoformat(),
                        "market": market,
                        "provider": "akshare",
                    }
                    articles.append(article)
                except Exception as e:
                    logger.warning("Error parsing AKShare CN news item: %s", e)
                    continue

            logger.info(
                "Fetched %d news articles from AKShare for %s",
                len(articles), symbol,
            )
            return articles

        except asyncio.TimeoutError:
            logger.error("AKShare CN news API timeout for %s", symbol)
            return []
        except Exception as e:
            logger.error("AKShare CN news error for %s: %s", symbol, e)
            return []

    async def get_trending_news_cn(self) -> List[Dict[str, Any]]:
        """Fetch trending/hot A-share market news from AKShare.

        Tries stock_info_global_em first, then falls back to CCTV news.

        Returns:
            List of dicts matching NewsArticle model, or empty list on error.
        """
        try:
            import akshare as ak

            def fetch():
                try:
                    df = ak.stock_info_global_em()
                    return df.to_dict("records") if df is not None and not df.empty else []
                except Exception:
                    try:
                        df = ak.news_cctv(date=datetime.now().strftime("%Y%m%d"))
                        return df.to_dict("records") if df is not None and not df.empty else []
                    except Exception:
                        return []

            news_data = await run_in_executor(fetch)

            articles: List[Dict[str, Any]] = []
            for item in news_data[:20]:
                try:
                    pub_str = str(
                        item.get("\u53d1\u5e03\u65f6\u95f4", "")
                        or item.get("date", "")
                    )
                    published = _parse_cst_datetime(pub_str)

                    title = (
                        item.get("\u6807\u9898", "")
                        or item.get("title", "")
                    )
                    url = (
                        item.get("\u94fe\u63a5", "")
                        or item.get("url", "")
                        or ""
                    )
                    content = (
                        item.get("\u5185\u5bb9", "")
                        or item.get("content", "")
                        or ""
                    )

                    article = {
                        "id": _generate_news_id(url or title),
                        "symbol": "MARKET",
                        "title": _sanitize_html(title) or "",
                        "summary": _sanitize_html(content[:500]) if content else None,
                        "source": "eastmoney",
                        "url": url,
                        "published_at": published.isoformat(),
                        "market": "sh",
                        "provider": "akshare",
                    }
                    articles.append(article)
                except Exception as e:
                    logger.warning("Error parsing AKShare trending news item: %s", e)
                    continue

            logger.info(
                "Fetched %d trending news articles from AKShare", len(articles),
            )
            return articles

        except asyncio.TimeoutError:
            logger.error("AKShare trending news API timeout")
            return []
        except Exception as e:
            logger.error("AKShare trending news error: %s", e)
            return []
