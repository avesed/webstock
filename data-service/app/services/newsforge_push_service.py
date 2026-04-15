"""NewsForge push service — sends collected news to NewsForge for processing.

Data-service collects raw news from Finnhub/AKShare/YFinance and pushes
them to NewsForge's /api/internal/articles/ingest endpoint. NewsForge
handles all LLM processing (classification, entity extraction, sentiment,
summarization, translation, embedding).

This enables multiple WebStock instances to share a single NewsForge
instance without duplicate news processing.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Redis key for tracking pushed URLs (prevents re-pushing)
PUSHED_URLS_KEY = "ds:newsforge:pushed_urls"
# Redis key for push statistics
PUSH_STATS_KEY = "ds:newsforge:push_stats"


class NewsForgePushService:
    """Async client for pushing articles to NewsForge internal API."""

    _client: httpx.AsyncClient | None = None

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.NEWSFORGE_URL.rstrip("/")
        self._api_key = settings.NEWSFORGE_API_KEY
        self._enabled = settings.NEWSFORGE_PUSH_ENABLED
        self._batch_size = settings.NEWSFORGE_PUSH_BATCH_SIZE

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            transport = httpx.AsyncHTTPTransport(retries=3)
            self._client = httpx.AsyncClient(
                timeout=60,
                transport=transport,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._base_url) and bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
        }

    async def push_articles(
        self,
        articles: list[dict[str, Any]],
        redis=None,
    ) -> dict[str, Any]:
        """Push a batch of articles to NewsForge.

        Args:
            articles: List of dicts matching data-service NewsArticle model.
            redis: Optional Redis connection for dedup tracking.

        Returns:
            Dict with push results (new_count, duplicate_count, etc.)
        """
        if not self.enabled:
            logger.debug("NewsForge push disabled, skipping %d articles", len(articles))
            return {"skipped": True, "reason": "disabled"}

        if not articles:
            return {"skipped": True, "reason": "empty"}

        # Filter already-pushed URLs if Redis available
        to_push = articles
        if redis:
            to_push = []
            for art in articles:
                url = art.get("url", "")
                if url and not await redis.sismember(PUSHED_URLS_KEY, url):
                    to_push.append(art)
            if len(to_push) < len(articles):
                logger.debug(
                    "Filtered %d already-pushed articles, %d remaining",
                    len(articles) - len(to_push), len(to_push),
                )

        if not to_push:
            return {"skipped": True, "reason": "all_already_pushed"}

        # Map to NewsForge IngestArticle format
        ingest_articles = []
        for art in to_push:
            ingest_articles.append({
                "url": art.get("url", ""),
                "title": art.get("title", ""),
                "published_at": art.get("published_at"),
                "summary": art.get("summary"),
                "source_name": art.get("source"),
                "language": None,  # NewsForge will auto-detect
                "symbol": art.get("symbol"),
                "market": (art.get("market") or "").lower() or None,
                "external_id": art.get("id"),  # MD5 hash
                "image_url": art.get("image_url"),
                "provider": art.get("provider"),
            })

        # Push in batches
        total_new = 0
        total_dup = 0
        total_err = 0
        all_results = []

        for i in range(0, len(ingest_articles), self._batch_size):
            batch = ingest_articles[i : i + self._batch_size]
            try:
                result = await self._send_batch(batch)
                total_new += result.get("new_count", 0)
                total_dup += result.get("duplicate_count", 0)
                total_err += result.get("error_count", 0)
                all_results.extend(result.get("results", []))

                # Track pushed URLs in Redis
                if redis:
                    for r in result.get("results", []):
                        if r.get("status") in ("new", "duplicate"):
                            url = r.get("url", "")
                            if url:
                                await redis.sadd(PUSHED_URLS_KEY, url)
                    # Set TTL on the set (48 hours)
                    await redis.expire(PUSHED_URLS_KEY, 172800)

            except Exception:
                logger.exception("Failed to push batch %d-%d to NewsForge", i, i + len(batch))
                total_err += len(batch)

        # Update stats in Redis
        if redis:
            await redis.hincrby(PUSH_STATS_KEY, "total_pushed", total_new)
            await redis.hincrby(PUSH_STATS_KEY, "total_duplicates", total_dup)
            await redis.hincrby(PUSH_STATS_KEY, "total_errors", total_err)
            await redis.hset(PUSH_STATS_KEY, "last_push_at", str(int(time.time())))

        logger.info(
            "NewsForge push complete: %d new, %d dup, %d err (of %d total)",
            total_new, total_dup, total_err, len(ingest_articles),
        )

        return {
            "total": len(ingest_articles),
            "new_count": total_new,
            "duplicate_count": total_dup,
            "error_count": total_err,
            "results": all_results,
        }

    async def check_status(self, article_ids: list[str]) -> dict[str, Any]:
        """Check processing status of articles in NewsForge."""
        if not self.enabled:
            return {"error": "disabled"}

        url = f"{self._base_url}/api/internal/articles/status"
        client = self._get_client()
        resp = await client.get(
            url,
            headers=self._headers(),
            params={"article_ids": ",".join(article_ids)},
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "NewsForge API error (HTTP %d) %s: %s",
                e.response.status_code, url, e.response.text[:200],
            )
            raise
        return resp.json()

    async def get_results(
        self,
        article_ids: list[str] | None = None,
        since: str | None = None,
        include_full_text: bool = False,
    ) -> list[dict[str, Any]]:
        """Get enriched results from NewsForge."""
        if not self.enabled:
            return []

        url = f"{self._base_url}/api/internal/articles/results"
        params: dict[str, Any] = {"include_full_text": str(include_full_text).lower()}
        if article_ids:
            params["article_ids"] = ",".join(article_ids)
        if since:
            params["since"] = since

        client = self._get_client()
        resp = await client.get(url, headers=self._headers(), params=params)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "NewsForge API error (HTTP %d) %s: %s",
                e.response.status_code, url, e.response.text[:200],
            )
            raise
        return resp.json()

    async def get_push_stats(self, redis) -> dict[str, Any]:
        """Get push statistics from Redis."""
        stats = await redis.hgetall(PUSH_STATS_KEY)
        if not stats:
            return {"total_pushed": 0, "total_duplicates": 0, "total_errors": 0}
        return {
            k.decode() if isinstance(k, bytes) else k: int(v.decode() if isinstance(v, bytes) else v)
            for k, v in stats.items()
        }

    async def _send_batch(self, batch: list[dict]) -> dict[str, Any]:
        """Send a single batch to NewsForge ingest endpoint."""
        url = f"{self._base_url}/api/internal/articles/ingest"
        payload = {"articles": batch}

        client = self._get_client()
        resp = await client.post(url, headers=self._headers(), json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "NewsForge API error (HTTP %d) %s: %s",
                e.response.status_code, url, e.response.text[:200],
            )
            raise
        return resp.json()
