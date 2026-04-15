"""NewsForge proxy service -- on-demand news retrieval from NewsForge.

When proxy mode is enabled, WebStock delegates all news API reads to
NewsForge instead of querying the local database.  This module provides:

1. ``is_newsforge_proxy_enabled()`` -- async helper to check proxy status
2. ``NewsForgeProxy`` -- class that mirrors every news API endpoint and
   translates NewsForge responses into WebStock response schemas.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level shared httpx client (connection pooling, race-safe init)
# ---------------------------------------------------------------------------
_client_lock = asyncio.Lock()
_shared_client: httpx.AsyncClient | None = None


async def _get_shared_client(timeout: int = 60) -> httpx.AsyncClient:
    """Return the shared httpx client, creating it under a lock if needed.

    Double-checked locking prevents two concurrent requests from each
    creating their own client instance.
    """
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        return _shared_client
    async with _client_lock:
        # Re-check after acquiring the lock (double-checked locking)
        if _shared_client is not None and not _shared_client.is_closed:
            return _shared_client
        transport = httpx.AsyncHTTPTransport(retries=2)
        _shared_client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        return _shared_client


# ---------------------------------------------------------------------------
# Proxy-enabled check (with TTL cache to reduce DB session pressure)
# ---------------------------------------------------------------------------
_proxy_enabled_cache: bool | None = None
_proxy_enabled_cache_time: float = 0
_CACHE_TTL = 30.0  # seconds


def invalidate_proxy_enabled_cache() -> None:
    """Invalidate the proxy-enabled TTL cache.

    Call this after updating the proxy_enabled integration setting so
    that the next check picks up the new value immediately.
    """
    global _proxy_enabled_cache, _proxy_enabled_cache_time
    _proxy_enabled_cache = None
    _proxy_enabled_cache_time = 0


async def is_newsforge_proxy_enabled() -> bool:
    """Check if NewsForge proxy mode is active.

    Returns True only when *both* ``proxy_enabled=true`` AND
    ``newsforge_url`` is configured (DB setting or env fallback).

    Results are cached for up to 30 seconds to avoid opening a new DB
    session on every single request.
    """
    global _proxy_enabled_cache, _proxy_enabled_cache_time

    now = time.monotonic()
    if _proxy_enabled_cache is not None and (now - _proxy_enabled_cache_time) < _CACHE_TTL:
        return _proxy_enabled_cache

    from sqlalchemy import text as sa_text
    from app.db.database import AsyncSessionLocal

    result_value = False

    async with AsyncSessionLocal() as db:
        try:
            # Read proxy_enabled setting
            result = await db.execute(
                sa_text(
                    "SELECT value FROM integration_settings WHERE key = :key"
                ),
                {"key": "integration.newsforge.proxy_enabled"},
            )
            row = result.first()
            proxy_enabled = row[0] == "true" if row else False

            if not proxy_enabled:
                result_value = False
            else:
                # Read URL setting (DB first, then env fallback)
                result = await db.execute(
                    sa_text(
                        "SELECT value FROM integration_settings WHERE key = :key"
                    ),
                    {"key": "integration.newsforge.url"},
                )
                row = result.first()
                url = row[0] if row else None

                if not url:
                    settings = get_settings()
                    url = settings.NEWSFORGE_URL

                if not url:
                    logger.warning(
                        "NewsForge proxy_enabled=true but no URL configured"
                    )
                    result_value = False
                else:
                    result_value = True
        except Exception:
            logger.warning(
                "Failed to check NewsForge proxy status, defaulting to disabled",
                exc_info=True,
            )
            result_value = False

    _proxy_enabled_cache = result_value
    _proxy_enabled_cache_time = time.monotonic()
    return result_value


def is_newsforge_proxy_enabled_sync() -> bool:
    """Synchronous version for use in Celery tasks (calls asyncio.run)."""
    import asyncio as _asyncio

    try:
        return _asyncio.run(is_newsforge_proxy_enabled())
    except RuntimeError:
        # Already running an event loop -- fall back to a new thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_asyncio.run, is_newsforge_proxy_enabled()).result(
                timeout=10
            )


# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------

def _map_article(nf: dict[str, Any]) -> dict[str, Any]:
    """Map a single NewsForge article dict to WebStock NewsResponse fields.

    Field names are returned in snake_case; CamelModel handles camelCase
    serialization automatically.
    """
    fm = nf.get("finance_metadata") or {}

    # Symbol: take first from symbols list, or fall back to top-level
    symbols = fm.get("symbols") or []
    symbol = symbols[0] if symbols else nf.get("symbol", "")

    # Market: NewsForge uses lowercase (us), WebStock uses uppercase (US)
    market = (fm.get("market") or nf.get("market") or "").upper()

    # Entities: NewsForge {name, type, confidence} -> WebStock {entity, type, score}
    nf_entities = nf.get("entities") or []
    ws_entities = []
    for e in nf_entities:
        if isinstance(e, dict) and e.get("name"):
            ws_entities.append({
                "entity": e["name"],
                "type": e.get("type", "org"),
                "score": e.get("confidence", 0.5),
            })

    # Value score -> content_score (0-100 to 0-300)
    value_score = nf.get("value_score")
    content_score = round(value_score * 3) if value_score is not None else None

    # Parse published_at safely
    pub_raw = nf.get("published_at")
    if isinstance(pub_raw, str):
        try:
            published_at = datetime.fromisoformat(
                pub_raw.replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            published_at = datetime.now(timezone.utc)
    elif isinstance(pub_raw, datetime):
        published_at = pub_raw
    else:
        published_at = datetime.now(timezone.utc)

    return {
        "id": str(nf.get("id", "")),
        "symbol": symbol,
        "title": nf.get("title", ""),
        "summary": fm.get("investment_summary") or nf.get("summary"),
        "source": nf.get("source_name") or nf.get("source", ""),
        "url": nf.get("url", ""),
        "published_at": published_at,
        "market": market,
        "sentiment_score": nf.get("sentiment_score"),
        "sentiment_tag": fm.get("sentiment_tag"),
        "investment_summary": fm.get("investment_summary"),
        "detailed_summary": nf.get("detailed_summary"),
        "ai_analysis": nf.get("ai_analysis"),
        "related_entities": ws_entities or None,
        "industry_tags": fm.get("industry_tags"),
        "event_tags": fm.get("event_tags"),
        "content_score": content_score,
        "processing_path": "newsforge_proxy",
        "score_details": None,
        "content_status": nf.get("content_status"),
        "filter_status": None,
        "created_at": published_at,
    }


# ---------------------------------------------------------------------------
# Proxy class
# ---------------------------------------------------------------------------

class NewsForgeProxy:
    """Proxy that translates NewsForge API calls into WebStock response dicts.

    Instantiated per-request but shares a module-level httpx client for
    connection pooling.
    """

    def __init__(self) -> None:
        self._base_url: str = ""
        self._api_key: str = ""
        self._initialized = False

    async def _ensure_config(self) -> None:
        """Lazily load config from DB / env (avoids DB call at import time)."""
        if self._initialized:
            return

        from sqlalchemy import text as sa_text
        from app.db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            # URL
            result = await db.execute(
                sa_text(
                    "SELECT value FROM integration_settings WHERE key = :key"
                ),
                {"key": "integration.newsforge.url"},
            )
            row = result.first()
            self._base_url = (row[0] if row else "") or ""

            # API key
            result = await db.execute(
                sa_text(
                    "SELECT value FROM integration_settings WHERE key = :key"
                ),
                {"key": "integration.newsforge.api_key"},
            )
            row = result.first()
            self._api_key = (row[0] if row else "") or ""

        # Fallback to env
        if not self._base_url or not self._api_key:
            settings = get_settings()
            self._base_url = self._base_url or (settings.NEWSFORGE_URL or "")
            self._api_key = self._api_key or (settings.NEWSFORGE_API_KEY or "")

        self._base_url = self._base_url.rstrip("/")
        self._initialized = True

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Issue a GET request to NewsForge internal API."""
        await self._ensure_config()
        url = f"{self._base_url}{path}"
        client = await _get_shared_client()
        try:
            resp = await client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "NewsForge proxy HTTP %d from %s: %s",
                exc.response.status_code,
                url,
                exc.response.text[:300],
            )
            raise
        except httpx.RequestError as exc:
            logger.error("NewsForge proxy connection error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Endpoint methods
    # ------------------------------------------------------------------

    async def get_market_news(
        self,
        page: int = 1,
        page_size: int = 20,
        market: str | None = None,
        search: str | None = None,
        sentiment_tag: str | None = None,
        filter_status: str | None = None,
        content_status: str | None = None,
        show_all: bool = False,
    ) -> dict[str, Any]:
        """Proxy for GET /news/market -> NewsForge articles/list (no symbol filter)."""
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "category": "finance",
        }
        if market:
            params["market"] = market.lower()
        if search:
            params["search"] = search
        if sentiment_tag:
            params["sentiment_tag"] = sentiment_tag

        data = await self._get("/api/internal/articles/list", params)

        articles = data.get("articles") or data.get("items") or []
        total = data.get("total", len(articles))

        from app.schemas.news import NewsFeedResponse, NewsResponse

        news_list = [NewsResponse(**_map_article(a)) for a in articles]
        return NewsFeedResponse(
            news=news_list,
            total=total,
            page=page,
            page_size=page_size,
            has_more=(page * page_size) < total,
        )

    async def get_symbol_news(
        self, symbol: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Proxy for GET /news/{symbol} -> NewsForge by-symbol."""
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        data = await self._get("/api/internal/articles/by-symbol", params)

        articles = data if isinstance(data, list) else (
            data.get("articles") or data.get("items") or []
        )

        from app.schemas.news import NewsResponse

        return [NewsResponse(**_map_article(a)) for a in articles]

    async def get_feed(
        self,
        symbols: list[str],
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        sentiment_tag: str | None = None,
    ) -> dict[str, Any]:
        """Proxy for GET /news/feed -> NewsForge feed filtered by symbols."""
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        if search:
            params["search"] = search
        if sentiment_tag:
            params["sentiment_tag"] = sentiment_tag

        data = await self._get("/api/internal/articles/feed", params)

        articles = data.get("articles") or data.get("items") or []
        total = data.get("total", len(articles))

        from app.schemas.news import NewsFeedResponse, NewsResponse

        news_list = [NewsResponse(**_map_article(a)) for a in articles]
        return NewsFeedResponse(
            news=news_list,
            total=total,
            page=page,
            page_size=page_size,
            has_more=(page * page_size) < total,
        )

    async def get_trending(
        self, market: str | None = None
    ) -> dict[str, Any]:
        """Proxy for GET /news/trending -> NewsForge top articles by score."""
        params: dict[str, Any] = {
            "page": 1,
            "page_size": 20,
            "sort_by": "value_score",
            "category": "finance",
        }
        if market:
            params["market"] = market.lower()

        data = await self._get("/api/internal/articles/list", params)

        articles = data.get("articles") or data.get("items") or []

        from app.schemas.news import NewsResponse, TrendingNewsResponse

        news_list = [NewsResponse(**_map_article(a)) for a in articles]
        return TrendingNewsResponse(
            news=news_list,
            market=market,
            fetched_at=datetime.now(timezone.utc),
        )

    async def get_article(self, article_id: str) -> dict[str, Any]:
        """Proxy for GET /news/article/{id} -> NewsForge single article."""
        data = await self._get(f"/api/internal/articles/{article_id}")

        from app.schemas.news import NewsResponse

        return NewsResponse(**_map_article(data))

    async def get_sentiment_timeline(
        self, symbol: str, days: int = 30
    ) -> dict[str, Any]:
        """Proxy for GET /news/{symbol}/sentiment-timeline.

        Calls the NewsForge sentiment batch endpoint and aggregates into
        the timeline format expected by the WebStock frontend.
        """
        params: dict[str, Any] = {"symbols": symbol, "days": days}
        try:
            data = await self._get("/api/internal/sentiment/batch", params)
        except Exception:
            # Return empty timeline on failure
            from app.schemas.news import SentimentTimelineResponse

            return SentimentTimelineResponse(
                symbol=symbol,
                days=days,
                data=[],
            )

        # data is keyed by symbol
        symbol_data = data.get(symbol) or data.get(symbol.upper()) or {}
        timeline = symbol_data.get("timeline") or []

        from app.schemas.news import (
            SentimentTimelineItemResponse,
            SentimentTimelineResponse,
        )

        items = []
        for entry in timeline:
            total = entry.get("total", 0)
            bullish = entry.get("bullish", 0)
            bearish = entry.get("bearish", 0)
            neutral = entry.get("neutral", 0)
            score = (bullish - bearish) / total if total > 0 else 0.0
            items.append(
                SentimentTimelineItemResponse(
                    date=entry.get("date", ""),
                    bullish=bullish,
                    bearish=bearish,
                    neutral=neutral,
                    total=total,
                    score=round(score, 4),
                )
            )

        return SentimentTimelineResponse(
            symbol=symbol,
            days=days,
            data=items,
        )

    async def stream_analysis(self, news_id: str) -> httpx.Response:
        """Proxy SSE stream for article analysis.

        Returns the raw httpx Response object so the caller can stream it
        directly to the client.  If NewsForge returns an error status, the
        streaming response is closed and an appropriate exception is raised.
        """
        await self._ensure_config()
        url = f"{self._base_url}/api/internal/articles/{news_id}/analysis/stream"
        client = await _get_shared_client(timeout=300)
        req = client.build_request("GET", url, headers=self._headers())
        resp = await client.send(req, stream=True)

        # Check upstream status before handing the stream to the caller
        if resp.status_code >= 400:
            # Read the error body so we can report it, then close the stream
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > 2048:
                    break
            await resp.aclose()
            error_text = body.decode("utf-8", errors="replace")[:500]
            logger.error(
                "NewsForge SSE stream error %d for article %s: %s",
                resp.status_code, news_id, error_text,
            )
            raise httpx.HTTPStatusError(
                message=f"NewsForge returned {resp.status_code}: {error_text}",
                request=req,
                response=resp,
            )

        return resp

    async def search(
        self,
        query: str,
        page: int = 1,
        page_size: int = 20,
        market: str | None = None,
        sentiment_tag: str | None = None,
    ) -> dict[str, Any]:
        """Proxy for search -> NewsForge search endpoint.

        The NewsForge ``/api/internal/articles/search`` endpoint uses
        ``limit``/``offset`` pagination (not page/page_size), so we
        convert here.
        """
        offset = (page - 1) * page_size
        params: dict[str, Any] = {
            "q": query,
            "limit": page_size,
            "offset": offset,
            "category": "finance",
        }
        if market:
            params["market"] = market.lower()
        if sentiment_tag:
            params["sentiment_tag"] = sentiment_tag

        data = await self._get("/api/internal/articles/search", params)

        articles = data.get("articles") or data.get("items") or []
        # The search endpoint returns "count" (number of items in this page).
        # Use it as a rough total when a dedicated "total" field is absent.
        total = data.get("total", data.get("count", len(articles)))

        from app.schemas.news import NewsFeedResponse, NewsResponse

        news_list = [NewsResponse(**_map_article(a)) for a in articles]
        return NewsFeedResponse(
            news=news_list,
            total=total,
            page=page,
            page_size=page_size,
            has_more=len(articles) >= page_size,
        )
