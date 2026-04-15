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

    async def check_status(self, article_ids: list[str]) -> dict[str, Any]:
        """Check processing status of articles."""
        if not self.enabled or not article_ids:
            return {"results": []}

        url = f"{self._base_url}/api/internal/articles/status"
        client = self._get_client()
        resp = await client.get(
            url,
            headers=self._headers(),
            params={"article_ids": ",".join(article_ids[:100])},
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
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get enriched article results from NewsForge."""
        if not self.enabled:
            return []

        url = f"{self._base_url}/api/internal/articles/results"
        params: dict[str, Any] = {
            "include_full_text": str(include_full_text).lower(),
            "limit": limit,
        }
        if article_ids:
            params["article_ids"] = ",".join(article_ids[:100])
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


def map_newsforge_to_webstock(nf_article: dict[str, Any]) -> dict[str, Any]:
    """Map a NewsForge enriched article to WebStock News model fields.

    Returns a dict of field updates for the WebStock News table.
    """
    updates: dict[str, Any] = {}

    # Sentiment
    if nf_article.get("sentiment_score") is not None:
        updates["sentiment_score"] = nf_article["sentiment_score"]

    # Finance metadata
    fm = nf_article.get("finance_metadata") or {}
    if fm.get("sentiment_tag"):
        updates["sentiment_tag"] = fm["sentiment_tag"]
    if fm.get("investment_summary"):
        updates["investment_summary"] = fm["investment_summary"]
    if fm.get("industry_tags"):
        updates["industry_tags"] = fm["industry_tags"]
    if fm.get("event_tags"):
        updates["event_tags"] = fm["event_tags"]

    # Entities: NewsForge {name, type, confidence} -> WebStock {entity, type, score}
    nf_entities = nf_article.get("entities") or []
    if nf_entities:
        ws_entities = []
        for e in nf_entities:
            if isinstance(e, dict) and e.get("name"):
                ws_entities.append({
                    "entity": e["name"],
                    "type": e.get("type", "org"),
                    "score": e.get("confidence", 0.5),
                    "relation": e.get("relation", "direct"),
                })
        if ws_entities:
            updates["related_entities"] = ws_entities
            # Primary entity
            best = max(ws_entities, key=lambda x: x.get("score", 0))
            updates["primary_entity"] = best["entity"]
            updates["primary_entity_type"] = best["type"]
            updates["max_entity_score"] = best.get("score", 0)
            updates["has_stock_entities"] = any(
                e["type"] in ("stock", "index") for e in ws_entities
            )
            updates["has_macro_entities"] = any(
                e["type"] == "macro" for e in ws_entities
            )

    # Summaries
    if nf_article.get("detailed_summary"):
        updates["detailed_summary"] = nf_article["detailed_summary"]
    if nf_article.get("ai_analysis"):
        updates["ai_analysis"] = nf_article["ai_analysis"]

    # Value score mapping: NewsForge 0-100 -> WebStock 0-300
    if nf_article.get("value_score") is not None:
        updates["content_score"] = nf_article["value_score"] * 3

    # Content status
    nf_status = nf_article.get("content_status", "")
    if nf_status in ("processed", "embedded"):
        updates["content_status"] = "fetched"
    elif nf_status == "partial":
        updates["content_status"] = "partial"

    # Processing path
    updates["processing_path"] = "newsforge"

    # Market impact -> has_stock_entities (approximate)
    if nf_article.get("has_market_impact"):
        updates["has_stock_entities"] = True

    return updates
