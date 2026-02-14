"""RSS feed service for RSSHub integration.

Provides CRUD operations for RSS feeds and polling logic that
fetches articles from RSSHub, deduplicates against the News table,
and dispatches them into the existing news pipeline.
"""

import asyncio
import logging
import re
import time
import uuid
from calendar import timegm
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import feedparser
import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news import ContentStatus, FilterStatus, News
from app.models.rss_feed import FeedCategory, RssFeed
from app.services.news_storage_service import NewsStorageService, get_news_storage_service

logger = logging.getLogger(__name__)


class RssService:
    """
    Core service for RSSHub feed management and polling.

    Responsibilities:
    - CRUD operations for rss_feeds table
    - Polling feeds via RSSHub HTTP API
    - Deduplicating articles against existing News records
    - Creating News records and optionally saving fulltext content
    - Tracking feed health (consecutive errors, auto-disable)
    """

    def __init__(
        self,
        rsshub_base_url: str = "http://rsshub:1200",
        access_key: Optional[str] = None,
    ) -> None:
        self.rsshub_base_url = rsshub_base_url.rstrip("/")
        self.access_key = access_key or None

    def _build_feed_url(self, route: str, fulltext: bool = False) -> str:
        """Build the full RSSHub URL for a given route."""
        url = self.rsshub_base_url + route
        params = []
        if fulltext:
            params.append("mode=fulltext")
        if self.access_key:
            params.append(f"key={self.access_key}")
        if params:
            url += "?" + "&".join(params)
        return url

    async def test_feed(
        self, route: str, fulltext: bool = False
    ) -> Dict[str, Any]:
        """
        Test an RSSHub route without writing to the database.

        Returns parsed articles or error information.
        """
        url = self._build_feed_url(route, fulltext)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

            parsed = feedparser.parse(response.text)
            articles = []
            for entry in (parsed.entries or [])[:20]:
                articles.append({
                    "title": getattr(entry, "title", ""),
                    "url": getattr(entry, "link", ""),
                    "summary": self._extract_summary(entry),
                    "published_at": self._parse_entry_date(entry),
                    "source": parsed.feed.get("title", "") if hasattr(parsed, "feed") else "",
                })

            return {
                "route": route,
                "article_count": len(articles),
                "articles": articles,
                "error": None,
            }

        except httpx.HTTPStatusError as e:
            logger.warning("RSSHub test failed for %s: HTTP %d", route, e.response.status_code)
            return {
                "route": route,
                "article_count": 0,
                "articles": [],
                "error": self._friendly_http_error(e.response.status_code, e.response.text),
            }
        except httpx.ConnectError:
            logger.warning("RSSHub test failed for %s: connection refused", route)
            return {
                "route": route,
                "article_count": 0,
                "articles": [],
                "error": "RSSHub 服务无法连接，请确认 RSSHub 容器已启动",
            }
        except httpx.TimeoutException:
            logger.warning("RSSHub test failed for %s: timeout", route)
            return {
                "route": route,
                "article_count": 0,
                "articles": [],
                "error": "请求超时 (30s)，该路由的上游源响应过慢",
            }
        except Exception as e:
            logger.warning("RSSHub test failed for %s: %s", route, e)
            return {
                "route": route,
                "article_count": 0,
                "articles": [],
                "error": str(e)[:500],
            }

    async def _fetch_feed_data(
        self,
        feed: RssFeed,
    ) -> Dict[str, Any]:
        """
        Phase 1: HTTP fetch + feedparser parse. No DB operations.

        Returns a dict with parsed entries data or error information.
        """
        url = self._build_feed_url(feed.rsshub_route, feed.fulltext_mode)
        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

            parsed = feedparser.parse(response.text)
            entries = parsed.entries or []

            MAX_ENTRIES_PER_POLL = 100
            if len(entries) > MAX_ENTRIES_PER_POLL:
                logger.warning("Feed %s returned %d entries, limiting to %d", feed.name, len(entries), MAX_ENTRIES_PER_POLL)
                entries = entries[:MAX_ENTRIES_PER_POLL]

            # Pre-process entries into plain dicts (no DB needed)
            prepared_entries = []
            for entry in entries:
                link = getattr(entry, "link", None)
                if not link:
                    continue
                prepared_entries.append({
                    "link": link,
                    "title": getattr(entry, "title", "")[:500],
                    "summary": self._extract_summary(entry),
                    "published_at": self._parse_entry_date_as_datetime(entry),
                    "fulltext_content": self._extract_fulltext(entry),
                })

            elapsed = time.monotonic() - start_time
            return {
                "entries": prepared_entries,
                "error": None,
                "elapsed": elapsed,
            }

        except httpx.ConnectError as e:
            return {"entries": [], "error": f"Connection error: {str(e)[:200]}", "elapsed": time.monotonic() - start_time}
        except httpx.HTTPStatusError as e:
            return {"entries": [], "error": f"HTTP {e.response.status_code}", "elapsed": time.monotonic() - start_time}
        except Exception as e:
            return {"entries": [], "error": str(e)[:500], "elapsed": time.monotonic() - start_time}

    async def poll_feed(
        self,
        db: AsyncSession,
        feed: RssFeed,
        system_settings: Any = None,
        fetched_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Poll a single RSS feed, deduplicate, and create News records.

        Args:
            db: Database session
            feed: RssFeed model instance
            system_settings: SystemSettings for feature flags
            fetched_data: Optional pre-fetched data from _fetch_feed_data()

        Returns:
            Dict with new_count, skipped_count, fulltext_articles, standard_articles
        """
        result = {
            "feed_id": str(feed.id),
            "feed_name": feed.name,
            "new_count": 0,
            "skipped_count": 0,
            "fulltext_articles": [],  # Articles with fulltext already saved (for Layer 2)
            "standard_articles": [],  # Articles needing Layer 1.5 fetch
            "error": None,
        }

        # Phase 1: Fetch data (skip if pre-fetched)
        if fetched_data is None:
            fetched_data = await self._fetch_feed_data(feed)

        if fetched_data.get("error"):
            result["error"] = fetched_data["error"]
            feed.last_polled_at = datetime.now(timezone.utc)
            feed.consecutive_errors += 1
            feed.last_error = result["error"]
            logger.warning("Feed %s fetch failed: %s", feed.name, result["error"])

            if feed.consecutive_errors >= 10:
                feed.is_enabled = False
                logger.warning(
                    "Auto-disabled feed %s after %d consecutive errors",
                    feed.name, feed.consecutive_errors,
                )
            return result

        prepared_entries = fetched_data["entries"]

        if not prepared_entries:
            logger.info("Feed %s returned 0 entries", feed.name)
            feed.last_polled_at = datetime.now(timezone.utc)
            feed.consecutive_errors = 0
            feed.last_error = None
            return result

        # Phase 2: DB operations (dedup, create News records)
        try:
            # Batch dedup: collect all URLs from entries and check against DB
            entry_urls = [e["link"] for e in prepared_entries]

            existing_urls: set = set()
            if entry_urls:
                dedup_query = select(News.url).where(News.url.in_(entry_urls))
                dedup_result = await db.execute(dedup_query)
                existing_urls = {row[0] for row in dedup_result.fetchall()}

            storage_service = get_news_storage_service()

            for entry_data in prepared_entries:
                link = entry_data["link"]

                if link in existing_urls:
                    result["skipped_count"] += 1
                    continue

                # Mark as seen to avoid intra-batch duplicates
                existing_urls.add(link)

                title = entry_data["title"]
                summary = entry_data["summary"]
                published_at = entry_data["published_at"]
                symbol = feed.symbol or "MARKET"

                # Create News record
                news = News(
                    symbol=symbol,
                    title=title,
                    summary=summary,
                    source=f"rss:{feed.name}"[:100],
                    url=link,
                    published_at=published_at,
                    market=feed.market,
                    related_entities=None,
                    has_stock_entities=False,
                    has_macro_entities=False,
                    max_entity_score=None,
                    primary_entity=None,
                    primary_entity_type=None,
                    filter_status=FilterStatus.PENDING.value,
                    rss_feed_id=feed.id,
                )

                # Check if fulltext mode provides enough content
                fulltext_content = entry_data["fulltext_content"]
                if feed.fulltext_mode and fulltext_content and len(fulltext_content) >= 500:
                    # Save content directly, skip Layer 1.5
                    try:
                        async with db.begin_nested():
                            db.add(news)
                            await db.flush()  # Get the ID assigned
                    except IntegrityError:
                        logger.debug("Duplicate URL skipped (race): %s", link)
                        result["skipped_count"] += 1
                        continue

                    try:
                        # Detect language (CJK-aware)
                        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", fulltext_content))
                        total_chars = len(fulltext_content)
                        detected_language = "zh" if (total_chars > 0 and chinese_chars / total_chars > 0.1) else "en"

                        # Calculate word count (CJK-aware)
                        if detected_language in ("zh", "ja", "ko"):
                            # For CJK: count characters
                            word_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", fulltext_content))
                        else:
                            # For other languages: count words
                            word_count = len(fulltext_content.split())

                        content_data = {
                            "url": link,
                            "title": title,
                            "full_text": fulltext_content,
                            "authors": [],
                            "keywords": [],
                            "top_image": None,
                            "language": detected_language,
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                            "word_count": word_count,
                            "metadata": {
                                "source_domain": "rsshub",
                                "source_feed": feed.name,
                            },
                        }
                        file_path = storage_service.save_content(
                            news.id, symbol, content_data, published_at
                        )
                        news.content_file_path = file_path
                        news.content_status = ContentStatus.FETCHED.value
                        news.content_fetched_at = datetime.now(timezone.utc)
                        result["fulltext_articles"].append(news)
                    except Exception as e:
                        logger.warning(
                            "Failed to save fulltext for %s: %s", link, e
                        )
                        # Fall back to standard pipeline
                        result["standard_articles"].append(news)
                else:
                    try:
                        async with db.begin_nested():
                            db.add(news)
                            await db.flush()
                    except IntegrityError:
                        logger.debug("Duplicate URL skipped (race): %s", link)
                        result["skipped_count"] += 1
                        continue
                    result["standard_articles"].append(news)

                result["new_count"] += 1

            # Update feed stats on success
            feed.last_polled_at = datetime.now(timezone.utc)
            feed.consecutive_errors = 0
            feed.last_error = None
            feed.article_count += result["new_count"]

            elapsed = fetched_data.get("elapsed", 0)
            logger.info(
                "Polled feed %s: %d new, %d skipped (%.1fs)",
                feed.name, result["new_count"], result["skipped_count"], elapsed,
            )

        except Exception as e:
            result["error"] = str(e)[:500]
            feed.last_polled_at = datetime.now(timezone.utc)
            feed.consecutive_errors += 1
            feed.last_error = result["error"]
            logger.exception("Feed %s poll failed: %s", feed.name, e)

            if feed.consecutive_errors >= 10:
                feed.is_enabled = False
                logger.warning(
                    "Auto-disabled feed %s after %d consecutive errors",
                    feed.name, feed.consecutive_errors,
                )

        return result

    async def poll_all_due_feeds(
        self,
        db: AsyncSession,
        system_settings: Any = None,
    ) -> Dict[str, Any]:
        """
        Poll all enabled feeds that are due for polling.

        Phase 1: HTTP fetch + parse concurrently with Semaphore(3).
        Phase 2: DB writes sequentially using the single db session.
        """
        now = datetime.now(timezone.utc)

        # Find all enabled feeds that are due
        query = select(RssFeed).where(RssFeed.is_enabled == True)
        result = await db.execute(query)
        all_feeds = result.scalars().all()

        due_feeds = []
        for feed in all_feeds:
            if feed.last_polled_at is None:
                due_feeds.append(feed)
            else:
                next_poll = feed.last_polled_at + timedelta(
                    minutes=feed.poll_interval_minutes
                )
                if now >= next_poll:
                    due_feeds.append(feed)

        if not due_feeds:
            return {
                "total_feeds": len(all_feeds),
                "due_feeds": 0,
                "polled": 0,
                "total_new": 0,
                "errors": 0,
            }

        logger.info(
            "RSS monitor: %d/%d feeds due for polling",
            len(due_feeds), len(all_feeds),
        )

        # Phase 1: Fetch all feeds concurrently (HTTP only, no DB)
        semaphore = asyncio.Semaphore(3)

        async def _fetch_one(feed: RssFeed) -> Dict[str, Any]:
            async with semaphore:
                return await self._fetch_feed_data(feed)

        fetch_tasks = [_fetch_one(feed) for feed in due_feeds]
        fetched_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # Phase 2: Process DB writes sequentially
        stats = {
            "total_feeds": len(all_feeds),
            "due_feeds": len(due_feeds),
            "polled": 0,
            "total_new": 0,
            "errors": 0,
            "feed_results": [],
        }

        for i, fetched_data in enumerate(fetched_results):
            feed = due_feeds[i]

            if isinstance(fetched_data, Exception):
                logger.error(
                    "Feed %s fetch raised exception: %s",
                    feed.name, fetched_data,
                )
                stats["errors"] += 1
                continue

            feed_result = await self.poll_feed(
                db, feed, system_settings, fetched_data=fetched_data
            )

            stats["polled"] += 1
            stats["total_new"] += feed_result.get("new_count", 0)
            if feed_result.get("error"):
                stats["errors"] += 1
            stats["feed_results"].append(feed_result)

        return stats

    # ==================== CRUD Operations ====================

    async def list_feeds(
        self,
        db: AsyncSession,
        category: Optional[str] = None,
        is_enabled: Optional[bool] = None,
    ) -> tuple[List[RssFeed], int]:
        """List feeds with optional filters."""
        conditions = []
        if category is not None:
            conditions.append(RssFeed.category == category)
        if is_enabled is not None:
            conditions.append(RssFeed.is_enabled == is_enabled)

        count_query = select(func.count()).select_from(RssFeed)
        if conditions:
            for cond in conditions:
                count_query = count_query.where(cond)
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        query = select(RssFeed)
        if conditions:
            for cond in conditions:
                query = query.where(cond)
        query = query.order_by(RssFeed.created_at.desc())

        result = await db.execute(query)
        feeds = result.scalars().all()

        return feeds, total

    async def get_feed(
        self, db: AsyncSession, feed_id: uuid.UUID
    ) -> Optional[RssFeed]:
        """Get a single feed by ID."""
        result = await db.execute(
            select(RssFeed).where(RssFeed.id == feed_id)
        )
        return result.scalar_one_or_none()

    async def create_feed(
        self, db: AsyncSession, data: Dict[str, Any]
    ) -> RssFeed:
        """Create a new RSS feed."""
        feed = RssFeed(**data)
        db.add(feed)
        await db.commit()
        await db.refresh(feed)
        return feed

    async def update_feed(
        self,
        db: AsyncSession,
        feed: RssFeed,
        data: Dict[str, Any],
    ) -> RssFeed:
        """Update an existing RSS feed."""
        for key, value in data.items():
            if hasattr(feed, key) and value is not None:
                setattr(feed, key, value)
        await db.commit()
        await db.refresh(feed)
        return feed

    async def delete_feed(self, db: AsyncSession, feed: RssFeed) -> None:
        """Delete an RSS feed."""
        await db.delete(feed)
        await db.commit()

    async def get_feed_stats(
        self, db: AsyncSession
    ) -> Dict[str, Any]:
        """Get per-feed statistics."""
        result = await db.execute(
            select(RssFeed).order_by(RssFeed.article_count.desc())
        )
        feeds = result.scalars().all()

        # Count recent articles per feed (last 7 days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        feed_ids = [f.id for f in feeds]

        recent_counts: Dict[uuid.UUID, int] = {}
        if feed_ids:
            recent_query = (
                select(News.rss_feed_id, func.count().label("cnt"))
                .where(
                    News.rss_feed_id.in_(feed_ids),
                    News.created_at >= cutoff,
                )
                .group_by(News.rss_feed_id)
            )
            recent_result = await db.execute(recent_query)
            for row in recent_result.fetchall():
                recent_counts[row[0]] = row[1]

        total_feeds = len(feeds)
        enabled_feeds = sum(1 for f in feeds if f.is_enabled)
        total_articles = sum(f.article_count for f in feeds)

        feed_stats = []
        for f in feeds:
            feed_stats.append({
                "feed_id": str(f.id),
                "feed_name": f.name,
                "rsshub_route": f.rsshub_route,
                "category": f.category,
                "is_enabled": f.is_enabled,
                "article_count": f.article_count,
                "last_polled_at": f.last_polled_at,
                "consecutive_errors": f.consecutive_errors,
                "recent_articles": recent_counts.get(f.id, 0),
            })

        return {
            "total_feeds": total_feeds,
            "enabled_feeds": enabled_feeds,
            "total_articles": total_articles,
            "feeds": feed_stats,
        }

    # ==================== Helpers ====================

    @staticmethod
    def _friendly_http_error(status_code: int, body: str) -> str:
        """Convert an RSSHub HTTP error into a clean, readable message."""
        # Strip HTML tags from response body (RSSHub returns its welcome page on errors)
        clean_body = re.sub(r"<[^>]+>", "", body)
        clean_body = re.sub(r"\s+", " ", clean_body).strip()[:200]

        messages = {
            404: "路由不存在，请检查 RSSHub 路由路径是否正确",
            403: "访问被拒绝，请检查 ACCESS_KEY 配置",
            429: "请求过于频繁，RSSHub 正在限流，请稍后重试",
            500: "RSSHub 内部错误，该路由的上游源可能存在问题",
            503: "上游源暂时不可用，RSSHub 无法获取该路由的数据。通常为临时性问题，可稍后重试",
        }
        friendly = messages.get(status_code)
        if friendly:
            return f"HTTP {status_code}: {friendly}"
        return f"HTTP {status_code}: {clean_body}" if clean_body else f"HTTP {status_code}"

    @staticmethod
    def _extract_summary(entry: Any) -> Optional[str]:
        """Extract summary from a feedparser entry."""
        summary = getattr(entry, "summary", None)
        if not summary:
            summary = getattr(entry, "description", None)
        if summary:
            # Strip HTML tags commonly found in RSS summaries
            summary = re.sub(r'<[^>]+>', '', summary)
            summary = re.sub(r'\s+', ' ', summary).strip()
        if summary and len(summary) > 2000:
            summary = summary[:2000]
        return summary

    @staticmethod
    def _extract_fulltext(entry: Any) -> Optional[str]:
        """Extract fulltext content from a feedparser entry (fulltext mode)."""
        # Try entry.content first (RSS 2.0 content:encoded)
        content_list = getattr(entry, "content", None)
        if content_list and isinstance(content_list, list) and len(content_list) > 0:
            value = content_list[0].get("value", "")
            if value:
                # Strip HTML tags
                clean_text = re.sub(r'<[^>]+>', '', value)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                if clean_text and len(clean_text) >= 500:
                    return clean_text

        # Try summary_detail (Atom summary with full content)
        summary_detail = getattr(entry, "summary_detail", None)
        if summary_detail and hasattr(summary_detail, "value"):
            value = summary_detail.value
            if value:
                # Strip HTML tags
                clean_text = re.sub(r'<[^>]+>', '', value)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                if clean_text and len(clean_text) >= 500:
                    return clean_text

        return None

    @staticmethod
    def _parse_entry_date(entry: Any) -> Optional[str]:
        """Parse entry date as ISO string for response schemas."""
        published_parsed = getattr(entry, "published_parsed", None)
        if published_parsed:
            try:
                ts = timegm(published_parsed)
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except Exception:
                pass

        published = getattr(entry, "published", None)
        if published:
            return published

        return None

    @staticmethod
    def _parse_entry_date_as_datetime(entry: Any) -> datetime:
        """Parse entry date as datetime for News model."""
        published_parsed = getattr(entry, "published_parsed", None)
        if published_parsed:
            try:
                ts = timegm(published_parsed)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass
        return datetime.now(timezone.utc)


# Module-level singleton
_rss_service: Optional[RssService] = None


def get_rss_service() -> RssService:
    """Get singleton RssService instance."""
    global _rss_service
    if _rss_service is None:
        from app.config import settings
        _rss_service = RssService(
            rsshub_base_url=settings.RSSHUB_URL,
            access_key=settings.RSSHUB_ACCESS_KEY or None,
        )
    return _rss_service
