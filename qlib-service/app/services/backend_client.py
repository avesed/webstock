"""Synchronous HTTP client for fetching data from WebStock main backend.

Designed for use in ProcessPoolExecutor (DataSyncService) where all I/O
must be synchronous.  Communicates with the main backend's internal API
using the ``X-Internal-Token`` header for authentication.

Internal endpoints consumed:
    GET  /api/v1/internal/symbols/{market}
    POST /api/v1/internal/history/batch
"""

import logging
import os

import httpx
from httpx import HTTPTransport

logger = logging.getLogger(__name__)


class BackendDataClient:
    """Synchronous HTTP client for fetching data from WebStock main backend.

    Designed for use in ProcessPoolExecutor (DataSyncService).
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("WEBSTOCK_BACKEND_URL", "http://app:80")
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

        Returns empty list on failure (allows fallback to direct sync).
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
        except Exception as e:
            logger.warning("Failed to fetch symbols for market=%s: %s", market, e)
            return []

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

        Returns empty dict on failure.
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
        except Exception as e:
            logger.warning(
                "Failed to fetch history batch for %d symbols (market=%s): %s",
                len(symbols),
                market,
                e,
            )
            return {}

    def is_available(self) -> bool:
        """Check if the backend internal API is reachable."""
        if not self.token:
            logger.debug("Backend client: no INTERNAL_API_TOKEN configured")
            return False
        try:
            resp = self._client.get("/api/v1/health")
            return resp.status_code == 200
        except Exception as e:
            logger.debug("Backend health check failed: %s", e)
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
