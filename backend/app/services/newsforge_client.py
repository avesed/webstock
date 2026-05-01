"""NewsForge client -- pulls enriched article results from NewsForge.

Used by the main WebStock backend to retrieve processed news articles
that were pushed by the data-service. Supports both polling and
webhook-triggered pulls.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class NewsForgeClient:
    """Async client for NewsForge internal API."""

    _client: httpx.AsyncClient | None = None

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = (settings.NEWSFORGE_URL or "").rstrip("/")
        self._api_key = settings.NEWSFORGE_API_KEY or ""

    def _get_client(self, timeout: int = 60) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            transport = httpx.AsyncHTTPTransport(retries=3)
            self._client = httpx.AsyncClient(
                timeout=timeout,
                transport=transport,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    @property
    def enabled(self) -> bool:
        return bool(self._base_url) and bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> dict[str, Any]:
        """Test connection to NewsForge API."""
        if not self.enabled:
            return {"connected": False, "error": "Not configured"}
        try:
            url = f"{self._base_url}/api/internal/articles/recent"
            client = self._get_client()
            resp = await client.get(
                url, headers=self._headers(), params={"limit": 1}
            )
            resp.raise_for_status()
            return {"connected": True, "status_code": resp.status_code}
        except Exception as e:
            logger.warning("NewsForge connection test failed: %s", e)
            return {"connected": False, "error": str(e)[:200]}

    async def sync_watched_symbols(
        self,
        symbols: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Sync this consumer's full watchlist of symbols to NewsForge.

        Each item: {"symbol": str, "market": str | None, "last_viewed_at": ISO str | None}

        Idempotent — caller sends the full current set on every sync.
        NewsForge upserts by (symbol, market) and uses `last_viewed_at` for
        StockPulse hot/warm/cold tiering.

        IMPORTANT: bare 6-digit A-share codes (e.g. "600519") MUST come with
        an explicit `market` of "sh" or "sz" — otherwise StockPulse's
        auto-detection will treat them as US tickers and akshare/tushare
        won't run.
        """
        if not self.enabled or not symbols:
            return {"received": 0, "upserted": 0}

        url = f"{self._base_url}/api/internal/watched-symbols/sync"
        payload = {"symbols": symbols}
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

    async def get_sentiment_batch(
        self, symbols: list[str], days: int = 30
    ) -> dict[str, Any]:
        """Get batch sentiment data from NewsForge."""
        if not self.enabled or not symbols:
            return {}

        url = f"{self._base_url}/api/internal/sentiment/batch"
        client = self._get_client()
        resp = await client.get(
            url,
            headers=self._headers(),
            params={"symbols": ",".join(symbols), "days": days},
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
