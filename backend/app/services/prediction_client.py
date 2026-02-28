"""HTTP client for data-processor prediction endpoints.

Reuses QLIB_SERVICE_URL since data-processor replaced qlib-service
at the same URL. Provides typed async methods for prediction and
RD-Agent operations.
"""
import asyncio
import logging
import time as _time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

# Module-level singleton + async lock
# asyncio.Lock() no longer binds to an event loop at creation time (Python 3.10+),
# so module-level creation is safe and avoids the race condition of lazy init.
_client: Optional["PredictionClient"] = None
_client_lock = asyncio.Lock()


class PredictionServiceError(Exception):
    """Raised when data-processor prediction endpoints return an error or are unreachable."""

    def __init__(self, message: str, status_code: Optional[int] = None, endpoint: str = ""):
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(message)


class PredictionClient:
    """Async HTTP client for data-processor prediction/rdagent endpoints."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.QLIB_SERVICE_URL
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        logger.info("PredictionClient initialized: %s", self.base_url)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
        logger.info("PredictionClient closed")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        timeout: Optional[httpx.Timeout] = None,
    ) -> Dict[str, Any]:
        """Execute HTTP request with error wrapping and request ID forwarding."""
        try:
            start_ts = _time.monotonic()
            logger.info("data-processor %s %s", method.upper(), path)

            # Forward request ID to downstream service for distributed tracing
            extra_headers: Dict[str, str] = {}
            rid = get_request_id()
            if rid:
                extra_headers["X-Request-ID"] = rid
            token = settings.INTERNAL_API_TOKEN
            if token:
                extra_headers["X-Internal-Token"] = token

            resp = await self._client.request(
                method, path, json=json, params=params, timeout=timeout,
                headers=extra_headers,
            )
            resp.raise_for_status()
            elapsed = _time.monotonic() - start_ts
            logger.info(
                "data-processor %s %s -> %d (%.2fs)",
                method.upper(), path, resp.status_code, elapsed,
            )
            return resp.json()
        except httpx.TimeoutException as e:
            elapsed = _time.monotonic() - start_ts
            msg = f"data-processor timeout on {method.upper()} {path} after {elapsed:.2f}s: {e}"
            logger.error(msg)
            raise PredictionServiceError(msg, endpoint=path) from e
        except httpx.ConnectError as e:
            elapsed = _time.monotonic() - start_ts
            msg = f"data-processor unreachable on {method.upper()} {path} after {elapsed:.2f}s: {e}"
            logger.error(msg)
            raise PredictionServiceError(msg, endpoint=path) from e
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            msg = (
                f"data-processor error on {method.upper()} {path}: "
                f"HTTP {e.response.status_code} — {body}"
            )
            logger.error(msg)
            raise PredictionServiceError(
                msg, status_code=e.response.status_code, endpoint=path,
            ) from e

    # === Prediction ===

    async def trigger_prediction(
        self,
        market: str,
        force_retrain: bool = False,
        forward_days: int = 5,
    ) -> Dict[str, Any]:
        """Trigger a prediction run for a market."""
        return await self._request(
            "POST", f"/predictions/{market}/run",
            json={"force_retrain": force_retrain, "forward_days": forward_days},
        )

    async def get_latest_predictions(
        self,
        market: str,
        top_n: int = 50,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get latest prediction results for a market."""
        params: Dict[str, str] = {"top_n": str(top_n)}
        if symbol:
            params["symbol"] = symbol
        return await self._request("GET", f"/predictions/{market}/latest", params=params)

    async def get_prediction_task(self, task_id: str) -> Dict[str, Any]:
        """Poll a prediction task by ID."""
        return await self._request("GET", f"/predictions/tasks/{task_id}")

    async def get_models(self, market: Optional[str] = None) -> Dict[str, Any]:
        """List trained prediction models with metrics."""
        params: Dict[str, str] = {}
        if market:
            params["market"] = market
        return await self._request("GET", "/predictions/models", params=params)

    async def get_feature_importance(self, model_id: str) -> Dict[str, Any]:
        """Get feature importance for a specific model."""
        return await self._request(
            "GET", f"/predictions/models/{model_id}/feature-importance"
        )

    async def update_model_quality(
        self, model_id: str, quality_passed: bool
    ) -> Dict[str, Any]:
        """Admin override: update model quality_passed flag."""
        return await self._request(
            "PUT", f"/predictions/models/{model_id}/quality",
            json={"quality_passed": quality_passed},
        )

    async def get_prediction_history(
        self,
        market: str,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Get prediction accuracy history for a market."""
        return await self._request(
            "GET", f"/predictions/{market}/history",
            params={"days": str(days)},
        )

    async def get_performance_metrics(
        self, market: str, days: int = 90
    ) -> Dict[str, Any]:
        """Get model performance metrics over time."""
        return await self._request(
            "GET", f"/predictions/{market}/performance",
            params={"days": str(days)},
        )

    async def backfill_returns(self) -> Dict[str, Any]:
        """Trigger backfill of actual returns for past predictions."""
        return await self._request("POST", "/predictions/backfill-returns")

    # === RD-Agent ===

    async def start_rdagent(
        self,
        market: str,
        universe_id: Optional[str] = None,
        max_rounds: int = 30,
    ) -> Dict[str, Any]:
        """Start an RD-Agent research session for a market."""
        body: Dict[str, Any] = {"max_rounds": max_rounds}
        if universe_id:
            body["universe_id"] = universe_id
        return await self._request("POST", f"/rdagent/{market}/start", json=body)

    async def get_rdagent_status(self, market: str) -> Dict[str, Any]:
        """Get RD-Agent session status for a market."""
        return await self._request("GET", f"/rdagent/{market}/status")

    async def stop_rdagent(self, market: str) -> Dict[str, Any]:
        """Stop a running RD-Agent session for a market."""
        return await self._request("POST", f"/rdagent/{market}/stop")

    async def get_factors(self, market: Optional[str] = None) -> Dict[str, Any]:
        """List discovered factors, optionally filtered by market."""
        params: Dict[str, str] = {}
        if market:
            params["market"] = market
        return await self._request("GET", "/rdagent/factors", params=params)

    async def toggle_factor(self, factor_id: str, is_active: bool) -> Dict[str, Any]:
        """Enable or disable a discovered factor."""
        return await self._request(
            "PUT", f"/rdagent/factors/{factor_id}",
            json={"is_active": is_active},
        )

    # === Fundamentals ===

    async def get_fundamentals_status(self) -> Dict[str, Any]:
        """Get fundamental data collection status."""
        return await self._request(
            "GET", "/predictions/fundamentals/status",
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def collect_fundamentals(self, market: str) -> Dict[str, Any]:
        """Trigger fundamental data collection for a market."""
        return await self._request(
            "POST", f"/predictions/fundamentals/{market}/collect",
            timeout=httpx.Timeout(30.0, connect=10.0),
        )


async def get_prediction_client() -> "PredictionClient":
    """Get the singleton PredictionClient instance (async-safe)."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = PredictionClient()
        return _client


async def close_prediction_client() -> None:
    """Close the singleton PredictionClient. Call on app shutdown."""
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.close()
            _client = None


def reset_prediction_client() -> None:
    """Reset the singleton without async close.

    Used by Celery ``_reset_singletons()`` after the event loop has closed.
    The next ``get_prediction_client()`` call will create a fresh instance on the
    new event loop.
    """
    global _client
    _client = None
