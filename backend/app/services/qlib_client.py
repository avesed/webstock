"""HTTP client for communicating with the qlib-service microservice.

The qlib-service runs as an independent container (webstock-qlib) and handles
all Qlib quantitative operations. This client provides a clean async interface
for the main backend to call qlib-service endpoints.

Connection: main backend → httpx → http://qlib-service:8001

Timeout strategy:
- Default: 60s (factor queries, expression evaluation)
- Long: 120s (IC analysis, cross-sectional operations)
- Background: 10s connect + streaming for backtests (polled separately)
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Module-level singleton + async lock
# asyncio.Lock() no longer binds to an event loop at creation time (Python 3.10+),
# so module-level creation is safe and avoids the race condition of lazy init.
_client: Optional["QlibClient"] = None
_client_lock = asyncio.Lock()


class QlibServiceError(Exception):
    """Raised when qlib-service returns an error or is unreachable."""

    def __init__(self, message: str, status_code: Optional[int] = None, endpoint: str = ""):
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(message)


class QlibClient:
    """Async HTTP client for qlib-service."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.QLIB_SERVICE_URL
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        logger.info("QlibClient initialized: %s", self.base_url)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
        logger.info("QlibClient closed")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        timeout: Optional[httpx.Timeout] = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request with error wrapping and logging."""
        import time as _time
        try:
            start_ts = _time.monotonic()
            logger.info("qlib-service %s %s", method.upper(), path)
            resp = await self._client.request(
                method, path, json=json, params=params, timeout=timeout,
            )
            resp.raise_for_status()
            elapsed = _time.monotonic() - start_ts
            logger.info(
                "qlib-service %s %s -> %d (%.2fs)",
                method.upper(), path, resp.status_code, elapsed,
            )
            return resp.json()
        except httpx.TimeoutException as e:
            elapsed = _time.monotonic() - start_ts
            msg = f"qlib-service timeout on {method.upper()} {path} after {elapsed:.2f}s: {e}"
            logger.error(msg)
            raise QlibServiceError(msg, endpoint=path) from e
        except httpx.ConnectError as e:
            elapsed = _time.monotonic() - start_ts
            msg = f"qlib-service unreachable on {method.upper()} {path} after {elapsed:.2f}s: {e}"
            logger.error(msg)
            raise QlibServiceError(msg, endpoint=path) from e
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            msg = (
                f"qlib-service error on {method.upper()} {path}: "
                f"HTTP {e.response.status_code} — {body}"
            )
            logger.error(msg)
            raise QlibServiceError(
                msg, status_code=e.response.status_code, endpoint=path,
            ) from e

    # === Health ===

    async def health(self) -> Dict[str, Any]:
        """Check qlib-service health."""
        return await self._request("GET", "/health")

    # === Expression Engine ===

    async def evaluate_expression(
        self,
        symbol: str,
        expression: str,
        market: str = "us",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: str = "3mo",
    ) -> Dict[str, Any]:
        """Evaluate a Qlib expression for a symbol."""
        payload: Dict[str, Any] = {
            "symbol": symbol,
            "expression": expression,
            "market": market,
            "period": period,
        }
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date

        return await self._request("POST", "/expression/evaluate", json=payload)

    async def evaluate_expression_batch(
        self,
        symbols: List[str],
        expression: str,
        market: str = "us",
        target_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate expression across multiple symbols."""
        payload: Dict[str, Any] = {
            "symbols": symbols,
            "expression": expression,
            "market": market,
        }
        if target_date:
            payload["target_date"] = target_date

        return await self._request(
            "POST", "/expression/batch", json=payload,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def validate_expression(self, expression: str) -> Dict[str, Any]:
        """Validate expression syntax without executing."""
        return await self._request(
            "POST", "/expression/validate",
            json={"expression": expression},
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    # === Factors ===

    async def get_factors(
        self,
        symbol: str,
        market: str = "us",
        alpha_type: str = "alpha158",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get Alpha158/360 factors for a symbol."""
        params: Dict[str, str] = {"market": market, "alpha_type": alpha_type}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        return await self._request("GET", f"/factors/{symbol}", params=params)

    async def get_factor_summary(
        self,
        symbol: str,
        market: str = "us",
    ) -> Dict[str, Any]:
        """Get factor summary (top 10) for a symbol -- optimized for LLM agents."""
        return await self._request(
            "GET", f"/factors/{symbol}/summary",
            params={"market": market},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def compute_ic(
        self,
        universe: List[str],
        factor_names: Optional[List[str]] = None,
        market: str = "us",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        forward_days: int = 5,
    ) -> Dict[str, Any]:
        """Compute IC/ICIR for factors."""
        payload: Dict[str, Any] = {
            "universe": universe,
            "market": market,
            "forward_days": forward_days,
        }
        if factor_names:
            payload["factor_names"] = factor_names
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date

        return await self._request(
            "POST", "/factors/ic", json=payload,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def compute_cs_rank(
        self,
        expression: str,
        symbols: List[str],
        market: str = "us",
        target_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute cross-sectional ranking."""
        payload: Dict[str, Any] = {
            "expression": expression,
            "symbols": symbols,
            "market": market,
        }
        if target_date:
            payload["target_date"] = target_date

        return await self._request("POST", "/factors/cs-rank", json=payload)

    # === Backtests ===

    async def create_backtest(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new backtest task."""
        return await self._request("POST", "/backtests", json=config)

    async def get_backtest(self, task_id: str) -> Dict[str, Any]:
        """Get backtest status and results."""
        return await self._request("GET", f"/backtests/{task_id}")

    async def cancel_backtest(self, task_id: str) -> Dict[str, Any]:
        """Cancel a running backtest."""
        return await self._request("POST", f"/backtests/{task_id}/cancel")

    async def delete_backtest(self, task_id: str) -> Dict[str, Any]:
        """Delete a backtest."""
        return await self._request("DELETE", f"/backtests/{task_id}")

    # === Data Sync ===

    async def sync_market(
        self,
        market: str,
        symbols: Optional[List[str]] = None,
        update_only: bool = True,
    ) -> Dict[str, Any]:
        """Trigger market data sync."""
        payload: Dict[str, Any] = {
            "market": market,
            "update_only": update_only,
        }
        if symbols:
            payload["symbols"] = symbols

        return await self._request(
            "POST", f"/data/sync/{market}", json=payload,
            timeout=httpx.Timeout(300.0, connect=10.0),
        )

    async def get_data_status(self) -> Dict[str, Any]:
        """Get data sync status for all markets."""
        return await self._request(
            "GET", "/data/status",
            timeout=httpx.Timeout(10.0, connect=5.0),
        )


async def get_qlib_client() -> "QlibClient":
    """Get the singleton QlibClient instance (async-safe)."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = QlibClient()
        return _client


async def close_qlib_client() -> None:
    """Close the singleton QlibClient. Call on app shutdown."""
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.close()
            _client = None
