"""HTTP client for the StockPulse external data platform.

StockPulse is a standalone universal stock data platform deployed
independently from WebStock. Provides stock quotes, historical bars,
fundamentals, and news via ``X-API-Key`` authentication.

Connection: main backend → httpx → http://<stockpulse-host>:8010

Design:
- Returns ``None`` on any failure so consumers that handle ``Optional``
  returns keep working without added try/except.
- Singleton instance via ``get_stockpulse_client()`` with asyncio.Lock.
- Path prefix ``/api/v1/data/`` (StockPulse public namespace).
- Auth header ``X-API-Key``.

Configuration:
- Reads ``STOCKPULSE_URL`` and ``STOCKPULSE_API_KEY`` from
  ``app.config.settings`` at construction time.
- ``enabled`` property reports whether both URL and key are populated.

Timeout strategy:
- Default: 30s (quotes, info)
- Medium: 60s (history, batch quotes, analysis, batched profiles)
- Long: 120s (long-running operations)
- Very long: 300s (batch daily-bars, market-wide profile downloads)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings as _module_settings  # kept for backward refs
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

# Module-level singleton + lazy async lock.  The lock is created lazily
# inside ``get_stockpulse_client`` so that Celery tasks (which build a
# fresh event loop per invocation) do not inherit a lock bound to the
# previous, now-closed loop.
_client_instance: Optional["StockPulseClient"] = None
_client_lock: Optional[asyncio.Lock] = None

# Timeout presets
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MEDIUM_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_VERY_LONG_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


def _normalize_market_for_sector(symbol: str, market: str) -> str:
    """Normalize market parameter for StockPulse sector routing.

    StockPulse's sector endpoint switches behavior on lowercase ``sh``/``sz``
    (akshare branch) versus ``us``/``hk`` (yfinance branch).  WebStock callers
    pass ``"US"``, ``"CN"``, ``"HK"``, or ``"A"`` — translate to lowercase
    and resolve ``"CN"``/``"A"`` to ``"sh"`` or ``"sz"`` based on the symbol
    prefix.

    Args:
        symbol: Stock symbol (may carry ``.SS``/``.SZ``/``.HK`` suffix).
        market: Caller-supplied market label (case-insensitive).

    Returns:
        Lowercase market code suitable for StockPulse routing.
    """
    market_lower = (market or "").lower()
    if market_lower in ("cn", "a"):
        bare = symbol.split(".")[0] if "." in symbol else symbol
        if bare and bare[0] == "6":
            return "sh"
        if bare and bare[0] in ("0", "3"):
            return "sz"
        # 8 = bj, but stockpulse may not handle separately; default to 'cn'
        # (yfinance branch as fallback).
        return "cn"
    return market_lower or "us"


async def _resolve_creds_from_db() -> tuple[str, str]:
    """Read URL + API key from ``integration_settings``, fall back to env.

    Creates a fresh per-call asyncpg connection bound to the current event
    loop (no pooling). This is critical for Celery contexts where each task
    runs in a fresh event loop — a global SQLAlchemy engine cannot be reused
    across loops without raising "Event loop is closed" / "attached to a
    different loop" errors.

    Returns:
        Tuple ``(base_url, api_key)``.  Either may be empty when unset.
    """
    from app.config import settings as runtime_settings

    url = ""
    api_key = ""
    conn = None
    try:
        # Lazy import to avoid forcing asyncpg dep at module import time
        import asyncpg

        # Convert SQLAlchemy URL (postgresql+asyncpg://...) to plain
        # postgresql:// for direct asyncpg.
        dsn = runtime_settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncpg.connect(dsn, timeout=5.0)
        rows = await conn.fetch(
            "SELECT key, value FROM integration_settings WHERE key = ANY($1::text[])",
            ["integration.stockpulse.url", "integration.stockpulse.api_key"],
        )
        for row in rows:
            if row["key"] == "integration.stockpulse.url":
                url = row["value"] or ""
            elif row["key"] == "integration.stockpulse.api_key":
                api_key = row["value"] or ""
    except Exception as e:
        # Common in Celery context (table missing on fresh deploy, asyncpg
        # transient errors, etc.). Env fallback below keeps the client
        # functional. Logged at DEBUG to avoid noise — a real
        # misconfiguration will surface at the disabled-state warning in
        # __init__.
        logger.debug(
            "[stockpulse] DB config read skipped, using env: %s", e,
        )
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass

    url = url or (runtime_settings.STOCKPULSE_URL or "")
    api_key = api_key or (runtime_settings.STOCKPULSE_API_KEY or "")
    return url, api_key


class StockPulseClient:
    """Async HTTP client for the StockPulse external data platform."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        # Strip trailing slash so we can safely concat ``/api/v1/data/...``.
        # When constructed without explicit args, fall back to env-derived
        # settings (the singleton accessor normally injects DB-resolved values).
        raw_url = (
            base_url
            if base_url is not None
            else (_module_settings.STOCKPULSE_URL or "")
        )
        self.base_url = (raw_url or "").rstrip("/")
        self._api_key = (
            api_key
            if api_key is not None
            else (_module_settings.STOCKPULSE_API_KEY or "")
        )

        headers: Dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key

        # Mirror NewsForgeClient connection pool defaults; the StockPulse fleet
        # is expected to be smaller than the internal data-service pool.
        self._client = httpx.AsyncClient(
            base_url=self.base_url or None,
            timeout=_DEFAULT_TIMEOUT,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            transport=httpx.AsyncHTTPTransport(retries=3),
            headers=headers,
        )
        if not self.base_url or not self._api_key:
            logger.warning(
                "[stockpulse] Client constructed in DISABLED state "
                "(URL=%r, API_KEY=%s). All data calls will return None until "
                "configured via admin UI.",
                self.base_url, "set" if self._api_key else "empty",
            )
        else:
            logger.info(
                "[stockpulse] Client initialized: base_url=%s", self.base_url,
            )

    @property
    def enabled(self) -> bool:
        """True when both URL and API key are configured."""
        return bool(self.base_url) and bool(self._api_key)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
        logger.info("[stockpulse] client closed")

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
    ) -> Optional[Any]:
        """Execute an HTTP request and return the data payload, or ``None`` on error.

        Unwraps the ``ApiResponse`` envelope: when ``success`` is True returns
        the ``data`` field; otherwise logs and returns ``None``.

        Returns ``None`` (without making any HTTP call) when the client is not
        ``enabled`` (no URL or no API key configured) so deployments without
        StockPulse don't blow up — they simply behave like every provider call
        is unavailable.
        """
        if not self.enabled:
            logger.debug(
                "[stockpulse] skipping %s %s: client not configured "
                "(base_url=%s, key=%s)",
                method.upper(), path, bool(self.base_url), bool(self._api_key),
            )
            return None

        t0 = time.monotonic()
        try:
            # Forward request ID to downstream service for distributed tracing
            extra_headers: Dict[str, str] = {}
            rid = get_request_id()
            if rid:
                extra_headers["X-Request-ID"] = rid

            resp = await self._client.request(
                method, path,
                json=json, params=params, timeout=timeout,
                headers=extra_headers,
            )
            elapsed = time.monotonic() - t0
            resp.raise_for_status()
            try:
                body = resp.json()
            except ValueError:
                logger.error(
                    "[stockpulse] non-JSON response: %s %s (%.2fs, "
                    "content_type=%s, body_preview=%r)",
                    method.upper(), path, elapsed,
                    resp.headers.get("content-type"), resp.text[:200],
                )
                return None

            if not body.get("success", True):
                logger.warning(
                    "[stockpulse] %s %s -> error: %s (%.2fs)",
                    method.upper(), path, body.get("error"), elapsed,
                )
                return None

            logger.debug(
                "[stockpulse] %s %s -> 200 (%.2fs, source=%s, cached=%s)",
                method.upper(), path, elapsed,
                body.get("source"), body.get("cached"),
            )
            return body.get("data")

        except httpx.TimeoutException:
            elapsed = time.monotonic() - t0
            logger.error(
                "[stockpulse] timeout: %s %s after %.2fs",
                method.upper(), path, elapsed,
            )
            return None
        except httpx.ConnectError:
            elapsed = time.monotonic() - t0
            logger.error(
                "[stockpulse] unreachable: %s %s after %.2fs",
                method.upper(), path, elapsed,
            )
            return None
        except httpx.HTTPStatusError as e:
            elapsed = time.monotonic() - t0
            status = e.response.status_code
            body_text = e.response.text[:300] if e.response else ""
            if status in (401, 403):
                logger.critical(
                    "[stockpulse] AUTH FAILURE (HTTP %d) — API key may be "
                    "revoked or invalid.  Update via admin UI: "
                    "/admin -> Settings -> Integrations.  path=%s %s (%.2fs)",
                    status, method.upper(), path, elapsed,
                )
            else:
                logger.error(
                    "[stockpulse] HTTP %d: %s %s (%.2fs) — %s",
                    status, method.upper(), path, elapsed, body_text,
                )
            return None
        except Exception:
            elapsed = time.monotonic() - t0
            logger.exception(
                "[stockpulse] unexpected error: %s %s (%.2fs)",
                method.upper(), path, elapsed,
            )
            return None

    # ==================================================================
    # Stock data endpoints (/api/v1/data/quote, /history, /info, ...)
    # ==================================================================

    async def get_quote(
        self, symbol: str, *, market: str = "us",
    ) -> Optional[Dict[str, Any]]:
        """Get real-time quote for a symbol."""
        return await self._request(
            "GET", f"/api/v1/data/quote/{symbol}", params={"market": market},
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
            "GET", f"/api/v1/data/history/{symbol}", params=params,
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_info(
        self, symbol: str, *, market: str = "us",
    ) -> Optional[Dict[str, Any]]:
        """Get company/instrument information."""
        return await self._request(
            "GET", f"/api/v1/data/info/{symbol}", params={"market": market},
        )

    async def get_financials(
        self, symbol: str, *, market: str = "us",
    ) -> Optional[Dict[str, Any]]:
        """Get key financial metrics and ratios."""
        return await self._request(
            "GET", f"/api/v1/data/financials/{symbol}", params={"market": market},
        )

    async def search(
        self, q: str, *, markets: str = "us,hk,sh,sz,metal",
    ) -> Optional[List[Dict[str, Any]]]:
        """Search for stocks across markets."""
        return await self._request(
            "GET", "/api/v1/data/search", params={"q": q, "markets": markets},
        )

    async def batch_quotes(
        self, symbols: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Get quotes for multiple symbols in parallel."""
        return await self._request(
            "POST", "/api/v1/data/batch/quotes",
            json={"symbols": symbols},
            timeout=_MEDIUM_TIMEOUT,
        )

    async def fetch_daily_bars_batch(
        self,
        symbols_with_dates: List[Dict[str, Any]],
        market: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch daily bars for a batch of symbols.

        Args:
            symbols_with_dates: List of ``{"symbol": "AAPL", "start_date": "2025-01-15"}``.
                ``start_date`` may be ``None`` for full history.
            market: Market code (us, hk, cn, metal).

        Returns:
            Dict with ``results`` and ``errors`` keys, or ``None`` on failure.
        """
        return await self._request(
            "POST",
            "/api/v1/data/batch/daily-bars",
            json={"symbols": symbols_with_dates, "market": market},
            timeout=_VERY_LONG_TIMEOUT,
        )

    # ==================================================================
    # Market endpoints (/api/v1/data/market/...)
    # ==================================================================

    async def get_market_indices(
        self, *, period: str = "5d",
    ) -> Optional[Dict[str, Any]]:
        """Get all major market indices."""
        return await self._request(
            "GET", "/api/v1/data/market/indices", params={"period": period},
        )

    async def get_market_context(self) -> Optional[Dict[str, Any]]:
        """Get aggregated market overview (indices + northbound flow)."""
        return await self._request("GET", "/api/v1/data/market/context")

    async def get_forex_rates(self) -> Optional[Dict[str, Any]]:
        """Get foreign exchange rates from USD base currency."""
        return await self._request("GET", "/api/v1/data/market/forex")

    async def get_hsi_constituents(self) -> Optional[Dict[str, Any]]:
        """Get Hang Seng Index constituent symbols."""
        return await self._request("GET", "/api/v1/data/market/hsi")

    # ==================================================================
    # Analysis endpoints (/api/v1/data/analysis/...)
    # ==================================================================

    async def get_analyst_ratings(
        self, symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get analyst ratings and consensus recommendations."""
        return await self._request(
            "GET", f"/api/v1/data/analysis/analyst-ratings/{symbol}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_technical(
        self, symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get technical indicator data (SMA, ADTV, beta, 52-week range)."""
        return await self._request(
            "GET", f"/api/v1/data/analysis/technical/{symbol}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_northbound_holding(
        self, code: str, *, days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound (Stock Connect) holding for a specific A-share."""
        return await self._request(
            "GET", f"/api/v1/data/analysis/northbound/holding/{code}",
            params={"days": str(days)},
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_northbound_flow(
        self, indicator: str = "北向资金", *, days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound capital flow history."""
        return await self._request(
            "GET", f"/api/v1/data/analysis/northbound/flow/{indicator}",
            params={"days": str(days)},
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_institutional(
        self, symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get institutional holders data."""
        return await self._request(
            "GET", f"/api/v1/data/analysis/institutional/{symbol}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_fund_holdings(
        self, code: str,
    ) -> Optional[Dict[str, Any]]:
        """Get China A-share mutual fund holdings."""
        return await self._request(
            "GET", f"/api/v1/data/analysis/fund-holdings/{code}",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_sector_industry(
        self, symbol: str, *, market: str = "US",
    ) -> Optional[Dict[str, Any]]:
        """Get sector and industry classification.

        StockPulse routes the sector endpoint by lowercase ``"sh"`` /
        ``"sz"`` (akshare branch) versus ``"us"`` / ``"hk"`` (yfinance
        branch).  WebStock callers historically pass uppercase ``"US"``,
        ``"CN"``, ``"HK"``, or ``"A"``; ``_normalize_market_for_sector``
        translates those into the lowercase code StockPulse expects,
        consulting the symbol prefix to disambiguate ``"CN"`` between
        ``"sh"`` and ``"sz"``.
        """
        normalized = _normalize_market_for_sector(symbol, market)
        return await self._request(
            "GET", f"/api/v1/data/analysis/sector/{symbol}",
            params={"market": normalized},
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_sector_list(self) -> Optional[Dict[str, Any]]:
        """Fetch all industry sectors with real-time data (A-share via akshare).

        Returns:
            Dict with the StockPulse sector-list payload, or ``None`` on
            failure / when the client is disabled.

        StockPulse path: ``GET /api/v1/data/analysis/sector-list``.
        """
        return await self._request(
            "GET", "/api/v1/data/analysis/sector-list",
            timeout=_MEDIUM_TIMEOUT,
        )

    async def get_sector_history(
        self,
        sector_name: str,
        *,
        period: str = "日k",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch historical sector performance via akshare.

        Args:
            sector_name: Industry sector name (e.g. ``"半导体"``).  The
                StockPulse path treats it as a path segment, so callers
                should pass it pre-encoded if it contains slashes.
            period: Bar period (default ``"日k"``).
            start_date: Optional ``YYYY-MM-DD`` lower bound.
            end_date: Optional ``YYYY-MM-DD`` upper bound.

        Returns:
            Dict with the historical bars, or ``None`` on failure.

        StockPulse path:
            ``GET /api/v1/data/analysis/sector-history/{sector_name}``.
        """
        params: Dict[str, Any] = {"period": period}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return await self._request(
            "GET",
            f"/api/v1/data/analysis/sector-history/{sector_name}",
            params=params,
            timeout=_MEDIUM_TIMEOUT,
        )

    # ==================================================================
    # Reference endpoints (/api/v1/data/reference/...)
    # ==================================================================

    async def fetch_stock_profiles_batch(
        self, market: str, symbols: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Fetch stock profiles for a small batch (max 50 symbols).

        Returns:
            Dict with keys ``profiles``, ``count``, ``market``.
        """
        return await self._request(
            "POST", "/api/v1/data/reference/stock-profiles-batch",
            json={"market": market, "symbols": symbols},
            timeout=_MEDIUM_TIMEOUT,  # ≤50 symbols ≈ 20-40s
        )

    # NOTE: Control endpoints (`download_market_profiles`,
    # `download_concept_mapping`) are intentionally NOT exposed here.
    # StockPulse runs heavy collection on its own scheduler and WebStock
    # does not participate in control of that fleet.

    # ==================================================================
    # Health
    # ==================================================================

    async def health(self) -> Optional[Dict[str, Any]]:
        """Check StockPulse health."""
        return await self._request(
            "GET", "/health",
            timeout=httpx.Timeout(5.0, connect=3.0),
        )


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------

async def get_stockpulse_client() -> StockPulseClient:
    """Get the singleton ``StockPulseClient`` instance (async-safe).

    Resolves credentials from ``integration_settings`` first (admin-managed
    DB overrides), falling back to env-derived ``settings.STOCKPULSE_URL``
    / ``STOCKPULSE_API_KEY``.  The async lock is created lazily so each
    fresh event loop (e.g. per Celery task) gets its own lock and never
    awaits a lock bound to a closed loop.
    """
    global _client_instance, _client_lock
    if _client_instance is not None:
        return _client_instance
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    async with _client_lock:
        if _client_instance is None:
            url, api_key = await _resolve_creds_from_db()
            _client_instance = StockPulseClient(base_url=url, api_key=api_key)
        return _client_instance


async def close_stockpulse_client() -> None:
    """Close the singleton ``StockPulseClient``.  Call on app shutdown."""
    global _client_instance, _client_lock
    if _client_lock is None:
        # Nothing has ever been instantiated on this loop; just null and exit.
        _client_instance = None
        return
    async with _client_lock:
        if _client_instance is not None:
            await _client_instance.close()
            _client_instance = None


def reset_stockpulse_client() -> None:
    """Null the singleton so the next caller rebuilds it.

    Safe to call from request handlers — relies on the async lock for
    ordering.  Also nulls the lock so that a subsequent ``get_*`` call
    on a NEW event loop (e.g. inside a fresh Celery task) creates a
    fresh ``asyncio.Lock`` bound to the current loop.  The httpx client
    inside the previous singleton is NOT closed here — call
    ``close_stockpulse_client()`` first if you need to release sockets
    on the current loop.
    """
    global _client_instance, _client_lock
    _client_instance = None
    _client_lock = None  # force re-creation on next event loop
