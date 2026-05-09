"""HTTP client for the AlphaForge ML prediction engine.

AlphaForge is a standalone ML stock prediction compute engine deployed
independently from WebStock. It replaces both the old ``data-processor``
(PredictionClient) and ``qlib-service`` (QlibClient) microservices.

Connection: main backend → httpx → http://<alphaforge-host>:8015

Design:
- Unified replacement for ``PredictionClient`` + ``QlibClient``.
- Raises ``AlphaForgeServiceError`` on any failure (matching existing
  PredictionServiceError / QlibServiceError behaviour so callers keep
  working with a single exception type).
- Singleton instance via ``get_alphaforge_client()`` with asyncio.Lock.
- Auth header ``X-API-Key`` (AlphaForge consumer key).

Configuration:
- Reads ``ALPHAFORGE_URL`` and ``ALPHAFORGE_API_KEY`` from
  ``app.config.settings`` at construction time.
- ``enabled`` property reports whether both URL and key are populated.
"""

import asyncio
import logging
import time as _time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

_client: Optional["AlphaForgeClient"] = None
_client_lock = asyncio.Lock()


class AlphaForgeServiceError(Exception):
    """Raised when AlphaForge endpoints return an error or are unreachable."""

    def __init__(self, message: str, status_code: Optional[int] = None, endpoint: str = ""):
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(message)


class AlphaForgeClient:
    """Async HTTP client for AlphaForge prediction + Qlib endpoints."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self._root_url = (base_url or settings.ALPHAFORGE_URL).rstrip("/")
        self.base_url = f"{self._root_url}/api/v1" if self._root_url and "/api/v1" not in self._root_url else self._root_url
        self._api_key = api_key or settings.ALPHAFORGE_API_KEY
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        logger.info("AlphaForgeClient initialized: %s", self.base_url)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url) and bool(self._api_key)

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("AlphaForgeClient closed")

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
            logger.info("alphaforge %s %s", method.upper(), path)

            extra_headers: Dict[str, str] = {}
            if self._api_key:
                extra_headers["X-API-Key"] = self._api_key
            rid = get_request_id()
            if rid:
                extra_headers["X-Request-ID"] = rid

            resp = await self._client.request(
                method, path, json=json, params=params, timeout=timeout,
                headers=extra_headers,
            )
            resp.raise_for_status()
            elapsed = _time.monotonic() - start_ts
            logger.info(
                "alphaforge %s %s -> %d (%.2fs)",
                method.upper(), path, resp.status_code, elapsed,
            )
            return resp.json()
        except httpx.TimeoutException as e:
            elapsed = _time.monotonic() - start_ts
            msg = f"alphaforge timeout on {method.upper()} {path} after {elapsed:.2f}s: {e}"
            logger.error(msg)
            raise AlphaForgeServiceError(msg, endpoint=path) from e
        except httpx.ConnectError as e:
            elapsed = _time.monotonic() - start_ts
            msg = f"alphaforge unreachable on {method.upper()} {path} after {elapsed:.2f}s: {e}"
            logger.error(msg)
            raise AlphaForgeServiceError(msg, endpoint=path) from e
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            msg = (
                f"alphaforge error on {method.upper()} {path}: "
                f"HTTP {e.response.status_code} — {body}"
            )
            logger.error(msg)
            raise AlphaForgeServiceError(
                msg, status_code=e.response.status_code, endpoint=path,
            ) from e

    # =====================================================================
    # Prediction endpoints (were PredictionClient)
    # =====================================================================

    async def trigger_prediction(
        self,
        market: str,
        force_retrain: bool = False,
        forward_days: int = 5,
    ) -> Dict[str, Any]:
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
        params: Dict[str, str] = {"top_n": str(top_n)}
        if symbol:
            params["symbol"] = symbol
        return await self._request("GET", f"/predictions/{market}/latest", params=params)

    async def get_prediction_task(self, task_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/predictions/tasks/{task_id}")

    async def get_models(self, market: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        if market:
            params["market"] = market
        return await self._request("GET", "/predictions/models", params=params)

    async def get_feature_importance(self, model_id: str) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/models/{model_id}/feature-importance",
        )

    async def update_model_quality(
        self, model_id: str, quality_passed: bool,
    ) -> Dict[str, Any]:
        return await self._request(
            "PUT", f"/predictions/models/{model_id}/quality",
            json={"quality_passed": quality_passed},
        )

    async def get_prediction_history(
        self, market: str, days: int = 30,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/history",
            params={"days": str(days)},
        )

    async def get_accuracy(
        self, market: str, days: int = 30,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/accuracy",
            params={"days": str(days)},
        )

    async def get_direction_accuracy(
        self, market: str, days: int = 30,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/direction/accuracy",
            params={"days": str(days)},
        )

    async def get_performance_metrics(
        self, market: str, days: int = 90,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/performance",
            params={"days": str(days)},
        )

    async def backfill_returns(self) -> Dict[str, Any]:
        return await self._request("POST", "/predictions/backfill-returns")

    # --- Signal quality ---

    async def get_ic_decay(
        self, market: str, days: int = 60,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/ic-decay",
            params={"days": str(days)},
        )

    async def get_turnover(
        self, market: str, days: int = 60, top_n: int = 20,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/turnover",
            params={"days": str(days), "top_n": str(top_n)},
        )

    async def get_sectors(self, market: str) -> Dict[str, Any]:
        return await self._request("GET", f"/predictions/sectors/{market}")

    async def collect_sectors(self, market: str) -> Dict[str, Any]:
        raise AlphaForgeServiceError(
            "Sector collection is now managed by StockPulse",
            endpoint="/predictions/sectors/collect",
        )

    async def get_attribution(
        self, market: str, days: int = 90, top_n: int = 20,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/attribution",
            params={"days": str(days), "top_n": str(top_n)},
        )

    async def get_prediction_dates(
        self, market: str, n_dates: int = 2, forward_days: int = 5,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/prediction-dates",
            params={"n_dates": str(n_dates), "forward_days": str(forward_days)},
        )

    # --- RD-Agent ---

    async def start_rdagent(
        self,
        market: str,
        universe_id: Optional[str] = None,
        max_rounds: int = 30,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"max_rounds": max_rounds}
        if universe_id:
            body["universe_id"] = universe_id
        return await self._request("POST", f"/rdagent/{market}/start", json=body)

    async def get_rdagent_status(self, market: str) -> Dict[str, Any]:
        return await self._request("GET", f"/rdagent/{market}/status")

    async def stop_rdagent(self, market: str) -> Dict[str, Any]:
        return await self._request("POST", f"/rdagent/{market}/stop")

    async def get_rdagent_factors(self, market: Optional[str] = None) -> Dict[str, Any]:
        """List discovered RD-Agent factors (renamed from get_factors to avoid Qlib conflict)."""
        params: Dict[str, str] = {}
        if market:
            params["market"] = market
        return await self._request("GET", "/rdagent/factors", params=params)

    async def toggle_factor(self, factor_id: str, is_active: bool) -> Dict[str, Any]:
        return await self._request(
            "PUT", f"/rdagent/factors/{factor_id}",
            json={"is_active": is_active},
        )

    # --- Fundamentals (collection now in StockPulse) ---

    async def get_fundamentals_status(self) -> Dict[str, Any]:
        raise AlphaForgeServiceError(
            "Fundamentals collection is now managed by StockPulse",
            endpoint="/predictions/fundamentals/status",
        )

    async def collect_fundamentals(self, market: str) -> Dict[str, Any]:
        raise AlphaForgeServiceError(
            "Fundamentals collection is now managed by StockPulse",
            endpoint=f"/predictions/fundamentals/{market}/collect",
        )

    async def backfill_fundamentals(self, market: str) -> Dict[str, Any]:
        raise AlphaForgeServiceError(
            "Fundamentals backfill is now managed by StockPulse",
            endpoint=f"/predictions/fundamentals/backfill/{market}",
        )

    async def collect_earnings(self, market: str) -> Dict[str, Any]:
        raise AlphaForgeServiceError(
            "Earnings collection is now managed by StockPulse",
            endpoint=f"/predictions/earnings/collect/{market}",
        )

    async def collect_analyst(self, market: str) -> Dict[str, Any]:
        raise AlphaForgeServiceError(
            "Analyst collection is now managed by StockPulse",
            endpoint=f"/predictions/analyst/collect/{market}",
        )

    async def collect_options(self, market: str) -> Dict[str, Any]:
        raise AlphaForgeServiceError(
            "Options collection is now managed by StockPulse",
            endpoint=f"/predictions/options/collect/{market}",
        )

    # --- ML Tools (agent-driven training) ---

    async def ml_profile_data(
        self,
        market: str,
        cutoff_date: str,
        validation_days: int = 60,
        forward_days: int = 5,
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", "/ml-tools/profile",
            json={
                "market": market,
                "cutoff_date": cutoff_date,
                "validation_days": validation_days,
                "forward_days": forward_days,
            },
            timeout=httpx.Timeout(300.0, connect=10.0),
        )

    async def ml_submit_training(
        self,
        market: str,
        cutoff_date: str,
        forward_days: int,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", "/ml-tools/train",
            json={
                "market": market,
                "cutoff_date": cutoff_date,
                "forward_days": forward_days,
                "config": config,
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def ml_get_training_task(self, task_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/ml-tools/tasks/{task_id}")

    async def ml_run_validation(
        self,
        task_id: str,
        cutoff_date: str,
        validation_days: int,
        forward_days: int,
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", "/ml-tools/validate",
            json={
                "task_id": task_id,
                "cutoff_date": cutoff_date,
                "validation_days": validation_days,
                "forward_days": forward_days,
            },
            timeout=httpx.Timeout(600.0, connect=10.0),
        )

    async def ml_submit_rolling_backtest(
        self,
        market: str,
        cutoff_date: str,
        validation_days: int,
        forward_days: int,
        retrain_interval: int,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", "/ml-tools/rolling-backtest",
            json={
                "market": market,
                "cutoff_date": cutoff_date,
                "validation_days": validation_days,
                "forward_days": forward_days,
                "retrain_interval": retrain_interval,
                "config": config,
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def ml_deploy_config(
        self,
        market: str,
        backtest_id: str,
        effective_config: Dict[str, Any],
        iteration: int = 1,
        val_ic: float = 0.0,
        train_ic: Optional[float] = None,
        train_icir: Optional[float] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "market": market,
            "backtest_id": backtest_id,
            "effective_config": effective_config,
            "iteration": iteration,
            "val_ic": val_ic,
        }
        if train_ic is not None:
            body["train_ic"] = train_ic
        if train_icir is not None:
            body["train_icir"] = train_icir
        return await self._request("POST", "/ml-tools/deploy", json=body)

    # --- Prediction backtests (prefixed to avoid Qlib backtest conflict) ---

    async def start_prediction_backtest(
        self, market: str, body: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/predictions/{market}/backtest", json=body,
        )

    async def get_prediction_backtest_task(self, task_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/predictions/backtests/tasks/{task_id}")

    async def list_prediction_backtests(
        self, market: str, limit: int = 50,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/predictions/{market}/backtests",
            params={"limit": str(limit)},
        )

    async def get_prediction_backtest(self, backtest_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/predictions/backtests/{backtest_id}")

    async def delete_prediction_backtest(self, backtest_id: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/predictions/backtests/{backtest_id}")

    # =====================================================================
    # Qlib endpoints (were QlibClient)
    # =====================================================================

    async def health(self) -> Dict[str, Any]:
        """Health endpoint is at root (no /api/v1 prefix)."""
        extra_headers: Dict[str, str] = {}
        if self._api_key:
            extra_headers["X-API-Key"] = self._api_key
        resp = await self._client.get(f"{self._root_url}/health", headers=extra_headers)
        resp.raise_for_status()
        return resp.json()

    # --- Expression engine ---

    async def evaluate_expression(
        self,
        symbol: str,
        expression: str,
        market: str = "us",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: str = "3mo",
    ) -> Dict[str, Any]:
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
        return await self._request(
            "POST", "/expression/validate",
            json={"expression": expression},
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    # --- Factors (Qlib Alpha158) ---

    async def get_factors(
        self,
        symbol: str,
        market: str = "us",
        alpha_type: str = "alpha158",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, str] = {"market": market, "alpha_type": alpha_type}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return await self._request("GET", f"/factors/{symbol}", params=params)

    async def get_factor_summary(
        self, symbol: str, market: str = "us",
    ) -> Dict[str, Any]:
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
        payload: Dict[str, Any] = {
            "expression": expression,
            "symbols": symbols,
            "market": market,
        }
        if target_date:
            payload["target_date"] = target_date
        return await self._request("POST", "/factors/cs-rank", json=payload)

    # --- Indicators ---

    async def compute_indicators(
        self,
        bars: list[dict],
        indicator_types: list[str],
        *,
        ma_periods: list[int] | None = None,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
        atr_period: int = 14,
        kdj_k_period: int = 9,
        kdj_d_period: int = 3,
        williams_r_period: int = 14,
        cci_period: int = 20,
        sar_af_start: float = 0.02,
        sar_af_step: float = 0.02,
        sar_af_max: float = 0.2,
        intraday: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "bars": bars,
            "indicator_types": indicator_types,
            "ma_periods": ma_periods or [20, 50, 200],
            "rsi_period": rsi_period,
            "macd_fast": macd_fast,
            "macd_slow": macd_slow,
            "macd_signal": macd_signal,
            "bb_period": bb_period,
            "bb_std": bb_std,
            "atr_period": atr_period,
            "kdj_k_period": kdj_k_period,
            "kdj_d_period": kdj_d_period,
            "williams_r_period": williams_r_period,
            "cci_period": cci_period,
            "sar_af_start": sar_af_start,
            "sar_af_step": sar_af_step,
            "sar_af_max": sar_af_max,
            "intraday": intraday,
        }
        return await self._request(
            "POST", "/indicators/compute", json=payload,
            timeout=httpx.Timeout(35.0, connect=10.0),
        )

    # --- Qlib backtests ---

    async def create_backtest(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/backtests", json=config)

    async def get_backtest(self, task_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/backtests/{task_id}")

    async def cancel_backtest(self, task_id: str) -> Dict[str, Any]:
        return await self._request("POST", f"/backtests/{task_id}/cancel")

    async def delete_backtest(self, task_id: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/backtests/{task_id}")

    # --- Data sync ---

    async def sync_market(
        self,
        market: str,
        symbols: Optional[List[str]] = None,
        update_only: bool = True,
    ) -> Dict[str, Any]:
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
        return await self._request(
            "GET", "/data/status",
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def trigger_sync(
        self, market: str, update_only: bool = True,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "market": market,
            "update_only": update_only,
        }
        return await self._request(
            "POST", f"/data/sync/{market}/trigger", json=payload,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def get_sync_progress(self) -> Dict[str, Any]:
        return await self._request(
            "GET", "/data/sync/progress",
            timeout=httpx.Timeout(10.0, connect=5.0),
        )


# ------------------------------------------------------------------
# Singleton lifecycle
# ------------------------------------------------------------------

async def _resolve_alphaforge_creds() -> tuple[str, str]:
    """Read AlphaForge URL + API key from integration_settings DB, env fallback."""
    url = settings.ALPHAFORGE_URL
    api_key = settings.ALPHAFORGE_API_KEY
    try:
        from sqlalchemy import text
        from app.db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            for db_key, attr in [
                ("integration.alphaforge.url", "url"),
                ("integration.alphaforge.api_key", "api_key"),
            ]:
                result = await db.execute(
                    text("SELECT value FROM integration_settings WHERE key = :key"),
                    {"key": db_key},
                )
                row = result.first()
                if row and row[0]:
                    if attr == "url":
                        url = row[0]
                    else:
                        api_key = row[0]
    except Exception as e:
        logger.debug("Could not read AlphaForge config from DB, using env: %s", e)
    return url, api_key


async def get_alphaforge_client() -> AlphaForgeClient:
    """Get the singleton AlphaForgeClient instance (async-safe)."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            url, api_key = await _resolve_alphaforge_creds()
            _client = AlphaForgeClient(base_url=url, api_key=api_key)
        return _client


async def close_alphaforge_client() -> None:
    """Close the singleton AlphaForgeClient. Call on app shutdown."""
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.close()
            _client = None


def reset_alphaforge_client() -> None:
    """Reset the singleton without async close.

    Used by Celery ``_reset_singletons()`` after the event loop has closed.
    """
    global _client
    _client = None
