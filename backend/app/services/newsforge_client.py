"""NewsForge client -- sync watched symbols and fetch sentiment data.

Config priority: DB ``integration_settings`` → env vars.
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
        self._config_loaded = bool(self._base_url and self._api_key)

    async def _ensure_config(self) -> None:
        """Load config from DB if env vars are missing."""
        if self._config_loaded:
            return
        try:
            from sqlalchemy import text as sa_text
            from app.db.task_session import get_task_session

            async with get_task_session() as db:
                result = await db.execute(
                    sa_text("SELECT value FROM integration_settings WHERE key = :key"),
                    {"key": "integration.newsforge.url"},
                )
                row = result.first()
                if row and row[0]:
                    self._base_url = row[0].rstrip("/")

                result = await db.execute(
                    sa_text("SELECT value FROM integration_settings WHERE key = :key"),
                    {"key": "integration.newsforge.api_key"},
                )
                row = result.first()
                if row and row[0]:
                    self._api_key = row[0]
        except Exception:
            logger.debug("Failed to load NewsForge config from DB", exc_info=True)
        self._config_loaded = True

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
        await self._ensure_config()
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
        await self._ensure_config()
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
        await self._ensure_config()
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
