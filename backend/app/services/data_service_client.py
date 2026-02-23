"""HTTP client for the data-service microservice.

The data-service (webstock-data) runs as an independent stateless container
and handles all external market data operations (yfinance, akshare, finnhub,
trafilatura, etc.).  This client provides a clean async interface for the
main backend to call data-service endpoints.

Connection: main backend → httpx → http://data-service:8003

Design:
- Returns ``None`` on any failure (matching existing Provider behaviour so
  that all consumers — skills, services, tasks — keep working without
  exception handling changes).
- Logs errors at WARNING/ERROR level for observability.
- Singleton instance via ``get_data_service_client()`` with asyncio.Lock.

Timeout strategy:
- Default: 30s (quotes, info, news, content)
- Medium: 60s (history, batch quotes, analysis)
- Long: 120s (general long operations)
- Very long: 300s (stock profile collection)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

# Module-level singleton + async lock
_client: Optional["DataServiceClient"] = None
_client_lock = asyncio.Lock()

# Timeout presets
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MEDIUM_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_LONG_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_VERY_LONG_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
# CN concept mapping: ~400 boards × akshare API ≈ 15-20 min
_CONCEPT_MAPPING_TIMEOUT = httpx.Timeout(1500.0, connect=10.0)


class DataServiceClient:
    """Async HTTP client for data-service."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.DATA_SERVICE_URL
        self._token = settings.INTERNAL_API_TOKEN
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=_DEFAULT_TIMEOUT,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"X-Internal-Token": self._token} if self._token else {},
        )
        logger.info("DataServiceClient initialized: %s", self.base_url)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
        logger.info("DataServiceClient closed")

    # ------------------------------------------------------------------
    # Core request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[httpx.Timeout] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute an HTTP request and return the data payload, or ``None`` on error.

        Unwraps the ``ApiResponse`` envelope: if ``success`` is True, returns
        ``data``; otherwise logs and returns ``None``.
        """
        t0 = time.monotonic()
        try:
            # Forward request ID to downstream service for distributed tracing
            extra_headers: Dict[str, str] = {}
            rid = get_request_id()
            if rid:
                extra_headers["X-Request-ID"] = rid

            resp = await self._client.request(
                method, path, json=json, params=params, timeout=timeout,
                headers=extra_headers,
            )
            elapsed = time.monotonic() - t0
            resp.raise_for_status()
            body = resp.json()

            if not body.get("success", True):
                logger.warning(
                    "data-service %s %s -> error: %s (%.2fs)",
                    method.upper(), path, body.get("error"), elapsed,
                )
                return None

            logger.debug(
                "data-service %s %s -> 200 (%.2fs, source=%s, cached=%s)",
                method.upper(), path, elapsed,
                body.get("source"), body.get("cached"),
            )
            return body.get("data")

        except httpx.TimeoutException:
            elapsed = time.monotonic() - t0
            logger.error(
                "data-service timeout: %s %s after %.2fs", method.upper(), path, elapsed,
            )
            return None
        except httpx.ConnectError:
            elapsed = time.monotonic() - t0
            logger.error(
                "data-service unreachable: %s %s after %.2fs", method.upper(), path, elapsed,
            )
            return None
        except httpx.HTTPStatusError as e:
            elapsed = time.monotonic() - t0
            body_text = e.response.text[:300] if e.response else ""
            logger.error(
                "data-service HTTP %d: %s %s (%.2fs) — %s",
                e.response.status_code, method.upper(), path, elapsed, body_text,
            )
            return None
        except Exception:
            elapsed = time.monotonic() - t0
            logger.exception(
                "data-service unexpected error: %s %s (%.2fs)",
                method.upper(), path, elapsed,
            )
            return None

    # ==================================================================
    # Stock endpoints (/v1/quote, /v1/history, /v1/info, ...)
    # ==================================================================

    async def get_quote(
        self, symbol: str, *, market: str = "us",
    ) -> Optional[Dict[str, Any]]:
        """Get real-time quote for a symbol."""
        return await self._request(
            "GET", f"/v1/quote/{symbol}", params={"market": market},
        )

    async def get_history(
        self,
        symbol: str,
        *,
        period: str = "1y",
        interval: str = "1d",
        market: str = "us",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get historical OHLCV bars."""
        params: Dict[str, Any] = {
            "period": period,
            "interval": interval,
            "market": market,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return await self._request(
            "GET", f"/v1/history/{symbol}", params=params,
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_info(
        self, symbol: str, *, market: str = "us",
    ) -> Optional[Dict[str, Any]]:
        """Get company/instrument information."""
        return await self._request(
            "GET", f"/v1/info/{symbol}", params={"market": market},
        )

    async def get_financials(
        self, symbol: str, *, market: str = "us",
    ) -> Optional[Dict[str, Any]]:
        """Get key financial metrics and ratios."""
        return await self._request(
            "GET", f"/v1/financials/{symbol}", params={"market": market},
        )

    async def search(
        self, q: str, *, markets: str = "us,hk,sh,sz,metal",
    ) -> Optional[List[Dict[str, Any]]]:
        """Search for stocks across markets."""
        return await self._request(
            "GET", "/v1/search", params={"q": q, "markets": markets},
        )

    async def batch_quotes(
        self, symbols: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Get quotes for multiple symbols in parallel."""
        return await self._request(
            "POST", "/v1/batch/quotes",
            json={"symbols": symbols},
            timeout=_MEDIUM_TIMEOUT,
        )

    async def fetch_daily_bars_batch(
        self,
        symbols_with_dates: List[Dict[str, Any]],
        market: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch daily bars for a batch of symbols from data-service.

        Args:
            symbols_with_dates: List of {"symbol": "AAPL", "start_date": "2025-01-15"}.
                                start_date can be None for full history.
            market: Market code (us, hk, cn, metal).

        Returns:
            Dict with "results" and "errors" keys, or None on failure.
        """
        return await self._request(
            "POST",
            "/v1/batch/daily-bars",
            json={"symbols": symbols_with_dates, "market": market},
            timeout=_VERY_LONG_TIMEOUT,  # 300s — batch can be slow
        )

    # ==================================================================
    # Market endpoints (/v1/market/...)
    # ==================================================================

    async def get_market_indices(
        self, *, period: str = "5d",
    ) -> Optional[Dict[str, Any]]:
        """Get all major market indices."""
        return await self._request(
            "GET", "/v1/market/indices", params={"period": period},
        )

    async def get_market_context(self) -> Optional[Dict[str, Any]]:
        """Get aggregated market overview (indices + northbound flow)."""
        return await self._request("GET", "/v1/market/context")

    async def get_forex_rates(self) -> Optional[Dict[str, Any]]:
        """Get foreign exchange rates from USD base currency."""
        return await self._request("GET", "/v1/market/forex")

    async def get_hsi_constituents(self) -> Optional[Dict[str, Any]]:
        """Get Hang Seng Index constituent symbols."""
        return await self._request("GET", "/v1/market/hsi")

    # ==================================================================
    # Analysis endpoints (/v1/analysis/...)
    # ==================================================================

    async def get_analyst_ratings(
        self, symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get analyst ratings and consensus recommendations."""
        return await self._request(
            "GET", f"/v1/analysis/analyst-ratings/{symbol}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_technical(
        self, symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get technical indicator data (SMA, ADTV, beta, 52-week range)."""
        return await self._request(
            "GET", f"/v1/analysis/technical/{symbol}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_northbound_holding(
        self, code: str, *, days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound (Stock Connect) holding for a specific A-share."""
        return await self._request(
            "GET", f"/v1/analysis/northbound/holding/{code}",
            params={"days": str(days)},
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_northbound_flow(
        self, indicator: str = "北向资金", *, days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound capital flow history."""
        return await self._request(
            "GET", f"/v1/analysis/northbound/flow/{indicator}",
            params={"days": str(days)},
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_institutional(
        self, symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get institutional holders data."""
        return await self._request(
            "GET", f"/v1/analysis/institutional/{symbol}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_fund_holdings(
        self, code: str,
    ) -> Optional[Dict[str, Any]]:
        """Get China A-share mutual fund holdings."""
        return await self._request(
            "GET", f"/v1/analysis/fund-holdings/{code}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_sector_industry(
        self, symbol: str, *, market: str = "US",
    ) -> Optional[Dict[str, Any]]:
        """Get sector and industry classification."""
        return await self._request(
            "GET", f"/v1/analysis/sector/{symbol}",
            params={"market": market},
            timeout=_MEDIUM_TIMEOUT,
        )

    # ==================================================================
    # News endpoints (/v1/news/...)
    # ==================================================================

    async def get_company_news(
        self,
        symbol: str,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        source: str = "auto",
    ) -> Optional[List[Dict[str, Any]]]:
        """Get news articles for a specific company."""
        params: Dict[str, Any] = {"source": source}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return await self._request(
            "GET", f"/v1/news/company/{symbol}", params=params,
        )

    async def get_general_news(
        self, *, category: str = "general",
    ) -> Optional[List[Dict[str, Any]]]:
        """Get general market news from Finnhub."""
        return await self._request(
            "GET", "/v1/news/general", params={"category": category},
        )

    async def get_trending_cn_news(self) -> Optional[List[Dict[str, Any]]]:
        """Get trending Chinese A-share news from AKShare."""
        return await self._request("GET", "/v1/news/trending-cn")

    # ==================================================================
    # Content endpoint (/v1/content/fetch)
    # ==================================================================

    async def fetch_content(
        self, url: str, *, language: str = "en", include_images: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Fetch full article content via trafilatura/Playwright/Tavily/Polygon fallback chain."""
        return await self._request(
            "POST", "/v1/content/fetch",
            json={"url": url, "language": language, "include_images": include_images},
            timeout=_MEDIUM_TIMEOUT,
        )

    # ==================================================================
    # Reference endpoints (/v1/reference/...)
    # ==================================================================

    async def build_stock_list(self) -> Optional[Dict[str, Any]]:
        """Build the full stock list from all markets (~37K symbols).

        Uses very long timeout because the CN EastMoney API fetches ~5,800
        A-shares in 58 paginated requests (~160s).
        """
        return await self._request(
            "POST", "/v1/reference/stock-list",
            timeout=_VERY_LONG_TIMEOUT,
        )

    async def download_stock_list(self) -> Optional[bytes]:
        """Download the pre-built stock list msgpack from data-service.

        Returns the raw binary content if successful, or None on any error
        (404, timeout, connection failure, etc.).
        """
        try:
            extra_headers: Dict[str, str] = {}
            rid = get_request_id()
            if rid:
                extra_headers["X-Request-ID"] = rid

            resp = await self._client.request(
                "GET",
                "/v1/reference/stock-list/download",
                timeout=_MEDIUM_TIMEOUT,  # 60s -- downloading a ~10MB file
                headers=extra_headers,
            )
            if resp.status_code == 200:
                logger.info(
                    "Downloaded stock list from data-service: %d bytes",
                    len(resp.content),
                )
                return resp.content
            logger.warning(
                "Stock list download returned HTTP %d", resp.status_code,
            )
            return None
        except httpx.TimeoutException:
            logger.error("Stock list download timed out")
            return None
        except httpx.ConnectError:
            logger.error("Data-service unreachable for stock list download")
            return None
        except Exception:
            logger.exception("Unexpected error downloading stock list")
            return None

    async def collect_profiles(
        self, market: str, *, symbols: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Collect stock profiles for a given market (legacy monolithic)."""
        body = {"symbols": symbols or []}
        return await self._request(
            "POST", f"/v1/reference/stock-profiles/{market}",
            json=body,
            timeout=_VERY_LONG_TIMEOUT,
        )

    async def fetch_cn_concept_mapping(self) -> Optional[Dict[str, Any]]:
        """Fetch A-share concept board → stock mapping.

        Returns dict with keys: concepts, names, count.
        """
        return await self._request(
            "POST", "/v1/reference/cn-concept-mapping",
            timeout=_CONCEPT_MAPPING_TIMEOUT,  # ~400 boards × akshare ≈ 15-20min
        )

    async def fetch_stock_profiles_batch(
        self, market: str, symbols: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Fetch stock profiles for a small batch (max 50 symbols).

        Returns dict with keys: profiles, count, market.
        """
        return await self._request(
            "POST", "/v1/reference/stock-profiles-batch",
            json={"market": market, "symbols": symbols},
            timeout=_MEDIUM_TIMEOUT,  # ≤50 symbols ≈ 20-40s
        )

    # ==================================================================
    # Stock profile collection endpoints (/v1/collection/stock-profiles/...)
    #
    # These endpoints return plain JSON (no ApiResponse envelope), so we
    # use _collection_request() instead of _request().
    # ==================================================================

    async def _collection_request(
        self,
        method: str,
        path: str,
        *,
        timeout: Optional[httpx.Timeout] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute an HTTP request for collection endpoints (no ApiResponse envelope).

        Unlike ``_request()``, this returns the raw JSON body directly since
        collection endpoints do not use the ``ApiResponse`` wrapper.
        """
        t0 = time.monotonic()
        try:
            extra_headers: Dict[str, str] = {}
            rid = get_request_id()
            if rid:
                extra_headers["X-Request-ID"] = rid

            resp = await self._client.request(
                method, path, timeout=timeout, headers=extra_headers,
            )
            elapsed = time.monotonic() - t0
            resp.raise_for_status()
            body = resp.json()

            logger.debug(
                "data-service %s %s -> %d (%.2fs)",
                method.upper(), path, resp.status_code, elapsed,
            )
            return body

        except httpx.TimeoutException:
            elapsed = time.monotonic() - t0
            logger.error(
                "data-service timeout: %s %s after %.2fs",
                method.upper(), path, elapsed,
            )
            return None
        except httpx.ConnectError:
            elapsed = time.monotonic() - t0
            logger.error(
                "data-service unreachable: %s %s after %.2fs",
                method.upper(), path, elapsed,
            )
            return None
        except httpx.HTTPStatusError as e:
            elapsed = time.monotonic() - t0
            # 404 is expected for "no profiles available yet"
            if e.response.status_code == 404:
                logger.info(
                    "data-service %s %s -> 404 (%.2fs)",
                    method.upper(), path, elapsed,
                )
            else:
                body_text = e.response.text[:300] if e.response else ""
                logger.error(
                    "data-service HTTP %d: %s %s (%.2fs) -- %s",
                    e.response.status_code, method.upper(), path,
                    elapsed, body_text,
                )
            return None
        except Exception:
            elapsed = time.monotonic() - t0
            logger.exception(
                "data-service unexpected error: %s %s (%.2fs)",
                method.upper(), path, elapsed,
            )
            return None

    async def download_market_profiles(
        self, market: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Download pre-collected stock profiles from data-service.

        The data-service collects profiles on its own schedule and saves
        them to disk. This endpoint downloads the cached result.

        Args:
            market: ``'cn'``, ``'us'``, or ``'hk'``.

        Returns:
            List of profile dicts, or ``None`` if not available or on error.
        """
        result = await self._collection_request(
            "GET",
            f"/v1/collection/stock-profiles/{market}/download",
            timeout=_LONG_TIMEOUT,  # large JSON payload for CN (~5K profiles)
        )
        if result is None:
            return None
        # The endpoint returns {market, count, profiles, metadata, ?concept_mapping}
        profiles = result.get("profiles")
        if isinstance(profiles, list):
            return profiles
        return None

    async def download_concept_mapping(self) -> Optional[Dict[str, Any]]:
        """Download pre-collected CN concept mapping from data-service.

        Returns:
            Dict with keys ``concepts`` and ``names``, or ``None`` on error.
        """
        result = await self._collection_request(
            "GET",
            "/v1/collection/stock-profiles/cn/concept-mapping/download",
            timeout=_MEDIUM_TIMEOUT,
        )
        if result is None:
            return None
        # The endpoint returns {concepts, names, count}
        if "concepts" in result:
            return result
        return None

    async def trigger_profile_collection(
        self, market: str,
    ) -> Optional[Dict[str, Any]]:
        """Trigger stock profile collection on data-service.

        The collection runs asynchronously in the data-service. Use
        ``get_profile_collection_progress`` to poll for completion.

        Args:
            market: ``'cn'``, ``'us'``, or ``'hk'``.

        Returns:
            Status dict with ``status``, ``market``, ``message`` keys,
            or ``None`` on error.
        """
        return await self._collection_request(
            "POST",
            f"/v1/collection/stock-profiles/{market}/collect",
            timeout=_DEFAULT_TIMEOUT,
        )

    async def get_profile_collection_progress(
        self, market: str,
    ) -> Optional[Dict[str, Any]]:
        """Get stock profile collection progress from data-service.

        Args:
            market: ``'cn'``, ``'us'``, or ``'hk'``.

        Returns:
            Progress dict with ``market``, ``progress``, ``metadata``,
            ``task_running`` keys, or ``None`` on error.
        """
        return await self._collection_request(
            "GET",
            f"/v1/collection/stock-profiles/{market}/progress",
            timeout=_DEFAULT_TIMEOUT,
        )

    # ==================================================================
    # Daily bar collection endpoints (/v1/collection/daily-bars/...)
    #
    # These endpoints return plain JSON (no ApiResponse envelope), so we
    # use _collection_request() instead of _request().
    # ==================================================================

    async def trigger_daily_bar_collection(self, market: str) -> Optional[Dict[str, Any]]:
        """Trigger daily bar collection on data-service."""
        return await self._collection_request(
            "POST", f"/v1/collection/daily-bars/{market}/collect",
            timeout=_DEFAULT_TIMEOUT,
        )

    async def trigger_daily_bar_rebuild(self, market: str) -> Optional[Dict[str, Any]]:
        """Trigger daily bar rebuild on data-service."""
        return await self._collection_request(
            "POST", f"/v1/collection/daily-bars/{market}/rebuild",
            timeout=_DEFAULT_TIMEOUT,
        )

    async def get_daily_bar_progress(self, market: str) -> Optional[Dict[str, Any]]:
        """Get daily bar collection progress from data-service."""
        return await self._collection_request(
            "GET", f"/v1/collection/daily-bars/{market}/progress",
            timeout=_DEFAULT_TIMEOUT,
        )

    async def unlock_daily_bars(self, market: str) -> Optional[Dict[str, Any]]:
        """Force-unlock daily bar collection on data-service."""
        return await self._collection_request(
            "POST", f"/v1/collection/daily-bars/{market}/unlock",
            timeout=_DEFAULT_TIMEOUT,
        )

    # ==================================================================
    # Health
    # ==================================================================

    async def health(self) -> Optional[Dict[str, Any]]:
        """Check data-service health."""
        return await self._request(
            "GET", "/health",
            timeout=httpx.Timeout(5.0, connect=3.0),
        )


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------

async def get_data_service_client() -> DataServiceClient:
    """Get the singleton DataServiceClient instance (async-safe)."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = DataServiceClient()
        return _client


async def close_data_service_client() -> None:
    """Close the singleton DataServiceClient. Call on app shutdown."""
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.close()
            _client = None


def reset_data_service_client() -> None:
    """Reset singleton for Celery worker event loop recycling.

    Celery tasks create a fresh event loop per invocation.  The httpx client
    and asyncio.Lock from the previous loop become stale.  Call this after
    each task's event loop closes to force re-creation on next use.
    """
    global _client, _client_lock
    _client = None
    _client_lock = asyncio.Lock()
