"""Synchronous HTTP client for fetching market data via the internal API.

Designed for use in ProcessPoolExecutor (DataSyncService) where all I/O
must be synchronous.  Communicates via ``X-Internal-Token`` header auth.

By default targets data-service (``DATA_SERVICE_URL``), falling back to
the main backend (``WEBSTOCK_BACKEND_URL``) for backward compatibility.

Internal endpoints consumed:
    GET  /api/v1/internal/symbols/{market}
    POST /api/v1/internal/history/batch
    GET  /v1/reference/index-constituents/{index_code}
"""

import logging
import os

import httpx
from httpx import HTTPTransport

logger = logging.getLogger(__name__)


class BackendDataClient:
    """Synchronous HTTP client for fetching market data via the internal API.

    Targets data-service by default, with fallback to the main backend.
    Designed for use in ProcessPoolExecutor (DataSyncService).
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get(
            "DATA_SERVICE_URL",
            os.environ.get("WEBSTOCK_BACKEND_URL", "http://app:80"),
        )
        self.token = os.environ.get("INTERNAL_API_TOKEN", "")
        # Transport retries handle transient connection errors (refused, reset)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
            headers={"X-Internal-Token": self.token},
            transport=HTTPTransport(retries=2),
        )
        logger.info(
            "BackendDataClient initialized: base_url=%s, token_configured=%s",
            self.base_url, bool(self.token),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_symbols(self, market: str) -> list[str]:
        """Fetch symbol list for a market from the main backend.

        Raises RuntimeError on failure.
        """
        try:
            resp = self._client.get(f"/api/v1/internal/symbols/{market}")
            resp.raise_for_status()
            data = resp.json()
            symbols = data.get("symbols", [])
            logger.info(
                "Fetched %d symbols for market=%s from backend",
                len(symbols),
                market,
            )
            return symbols
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Backend returned HTTP {e.response.status_code} "
                f"for symbols/{market}"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Failed to fetch symbols for market={market}: "
                f"[{type(e).__name__}] {e}"
            ) from e

    def get_history_batch(
        self,
        symbols: list[str],
        market: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Fetch daily bars for a batch of symbols from the main backend.

        Returns columnar format::

            {"AAPL": {"dates": [...], "open": [...], "high": [...], ...}}

        Raises RuntimeError on failure.
        """
        payload: dict = {
            "symbols": symbols,
            "market": market,
        }
        if start_date:
            payload["startDate"] = start_date  # camelCase for CamelModel
        if end_date:
            payload["endDate"] = end_date

        try:
            resp = self._client.post(
                "/api/v1/internal/history/batch",
                json=payload,
                # 10s above Nginx's 180s proxy_read_timeout to avoid ambiguous races
                timeout=httpx.Timeout(190.0, connect=10.0),
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            total_bars = sum(len(v.get("dates", [])) for v in data.values())
            logger.info(
                "Fetched history batch: market=%s, requested=%d, received=%d symbols, %d bars",
                market, len(symbols), len(data), total_bars,
            )
            if len(data) < len(symbols):
                missing = set(symbols) - set(data.keys())
                logger.warning(
                    "Backend returned data for %d/%d symbols (market=%s), missing: %s",
                    len(data), len(symbols), market, list(missing)[:10],
                )
            return data
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Backend returned HTTP {e.response.status_code} "
                f"for history batch ({len(symbols)} symbols, market={market})"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Failed to fetch history batch for {len(symbols)} symbols "
                f"(market={market}): [{type(e).__name__}] {e}"
            ) from e

    def get_index_history(
        self,
        symbol: str,
        market: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict] | None:
        """Fetch daily OHLCV bars for an index symbol via data-service.

        Uses the public ``/v1/stock/history/{symbol}`` endpoint which falls
        through to yfinance for symbols not in ``stock_daily_bars`` (like
        index symbols ``^GSPC``, ``^HSI``, ``000300.SS``).

        Returns a list of bar dicts ``[{date, open, high, low, close, volume}]``
        or None on failure.
        """
        params: dict[str, str] = {"market": market, "interval": "1d"}
        if start_date and end_date:
            params["start"] = start_date
            params["end"] = end_date
            params["period"] = "max"  # hint to override server default of "1y"
        else:
            params["period"] = "2y"

        try:
            resp = self._client.get(
                f"/v1/history/{symbol}",
                params=params,
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                logger.warning(
                    "Index history response unsuccessful: symbol=%s error=%s",
                    symbol, body.get("error"),
                )
                return None
            bars = body.get("data", {}).get("bars", [])
            if bars:
                logger.info(
                    "Fetched index history: symbol=%s, %d bars, %s ~ %s",
                    symbol, len(bars),
                    bars[0].get("date", "?"), bars[-1].get("date", "?"),
                )
            else:
                logger.warning("No bars in index history for %s", symbol)
            return bars or None
        except Exception as e:
            logger.warning(
                "Failed to fetch index history for %s (market=%s): %s",
                symbol, market, e,
            )
            return None

    def get_index_constituents(self, index_code: str, market: str) -> list[str]:
        """Fetch constituent symbols for a market index from data-service.

        Uses the ``/v1/reference/index-constituents/{index_code}`` endpoint.
        Returns an empty list on failure (never raises).
        """
        try:
            resp = self._client.get(
                f"/v1/reference/index-constituents/{index_code}",
                params={"market": market},
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("success") and body.get("data"):
                symbols = body["data"].get("symbols", [])
                logger.info(
                    "Fetched %d constituents for index=%s market=%s",
                    len(symbols), index_code, market,
                )
                return symbols
            logger.warning(
                "Index constituents response unsuccessful: index=%s market=%s error=%s",
                index_code, market, body.get("error"),
            )
            return []
        except Exception as e:
            logger.warning(
                "Failed to fetch index constituents for %s (%s): %s",
                index_code, market, e,
            )
            return []

    def is_available(self) -> bool:
        """Check if the backend internal API is reachable.

        Tries ``/health`` first (data-service), then ``/api/v1/health``
        (main backend) for backward compatibility.
        """
        if not self.token:
            logger.debug("Backend client: no INTERNAL_API_TOKEN configured")
            return False
        for path in ("/health", "/api/v1/health"):
            try:
                resp = self._client.get(path)
                if resp.status_code == 200:
                    return True
            except Exception as e:
                logger.debug("Health check %s failed: %s", path, e)
        return False

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_client: BackendDataClient | None = None


def get_backend_client() -> BackendDataClient:
    """Get or create a singleton BackendDataClient."""
    global _client
    if _client is None:
        _client = BackendDataClient()
    return _client


def reset_backend_client() -> None:
    """Reset the singleton. Must be called after fork in child processes."""
    global _client
    if _client is not None:
        _client.close()
    _client = None
