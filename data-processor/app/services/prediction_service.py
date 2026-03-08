"""LightGBM prediction service -- training + inference engine.

Training pipeline:
1. QlibContext.ensure_init(market)
2. Resolve universe symbols from settings_cache or BackendDataClient
3. feature_service.build_feature_matrix() -> ~87 features x N stocks x T days
4. Label engineering: forward N-day return -> percentile score (continuous target)
5. Purged time-series split (train/val) with gap = forward_days
6. LightGBM training: objective='lambdarank', early_stopping_rounds=50
7. joblib.dump() model -> /app/data/predictions/{market}/{YYYYMMDD}/model.pkl
8. Evaluate IC/ICIR/NDCG -> write to prediction_models table

Inference pipeline:
1. joblib.load() latest model
2. feature_service.build_feature_matrix() for latest date
3. Predict -> score -> cross-sectional rank -> direction
4. Write to stock_predictions table + Redis cache (24h)

Execution model:
All orchestration is async (asyncio.create_task). LightGBM training runs
in native C code that releases the GIL, so it does not block the event loop
significantly. DB access uses the shared asyncpg pool from settings_cache.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import joblib
import lightgbm as lgb
import msgpack
import numpy as np
import pandas as pd
import redis.asyncio as aioredis

from app.config import get_settings
from app.services.feature_service import (
    ALPHA158_FEATURES,
    FUNDAMENTAL_FEATURES,
    SENTIMENT_FEATURES,
    feature_service,
)
from app.services.fundamental_service import fundamental_service
from app.services.market_config import get_market_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Redis cache TTL for latest predictions (24 hours)
_PREDICTION_CACHE_TTL = 86400

# Maximum number of concurrent prediction tasks
_MAX_CONCURRENT_PREDICTIONS = 1

# Training history lookback (calendar days -> ~2 years of trading days)
_TRAIN_LOOKBACK_DAYS = 730

# Minimum number of dates required for a valid train/val split
_MIN_TRAIN_DATES = 60

# Minimum number of symbols per date for valid ranking labels.
# With 5 quintiles, need ≥5 stocks per bin → 25 minimum.
# HK (smallest universe) has ≥78 stocks/date, so 25 is safe.
_MIN_SYMBOLS_PER_DATE = 25

# Percentile rank thresholds for directional classification
DIRECTION_UP_THRESHOLD = 0.70
DIRECTION_DOWN_THRESHOLD = 0.30

# Seeds for ensemble members.  Each model uses a different seed triplet
# (seed, feature_fraction_seed, bagging_seed) to diversify random subsampling.
# Averaging N models reduces IC variance by ~sqrt(N).
_ENSEMBLE_SEEDS: list[int] = [42, 137, 271, 419, 503, 631, 769, 887, 953, 1031]

# LightGBM base hyperparameters (lambdarank objective).
# Seeds are injected per ensemble member in _train_ensemble_sync().
_BASE_LGB_PARAMS: dict[str, Any] = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "ndcg_eval_at": [5, 10, 20],
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
}


def _get_lgb_params(market: str) -> dict[str, Any]:
    """Return merged LightGBM params for the given market."""
    params = dict(_BASE_LGB_PARAMS)
    params.update(get_market_config(market).lgb_overrides)
    return params


def _get_boost_round(market: str) -> int:
    return get_market_config(market).num_boost_round


def _get_early_stopping(market: str) -> int:
    return get_market_config(market).early_stopping_rounds


def _get_prediction_horizons() -> list[int]:
    """Parse PREDICTION_HORIZONS from comma-separated string to int list."""
    raw = get_settings().PREDICTION_HORIZONS
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _numpy_default(obj: Any) -> Any:
    """JSON serializer fallback for numpy types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _safe_round(val: float, decimals: int) -> float | None:
    """Round a float, returning None if NaN or Inf (prevents invalid JSON)."""
    if math.isnan(val) or math.isinf(val):
        return None
    return round(val, decimals)


_redis_client: Optional[aioredis.Redis] = None
_redis_lock = asyncio.Lock()  # Python 3.10+: safe at module level


async def _get_redis_client() -> aioredis.Redis:
    """Return a module-level shared async Redis client (lazy singleton)."""
    global _redis_client
    if _redis_client is None:
        async with _redis_lock:
            if _redis_client is None:
                settings = get_settings()
                _redis_client = aioredis.from_url(
                    settings.REDIS_URL, decode_responses=False
                )
    return _redis_client


def _prediction_cache_key(market: str) -> str:
    """Redis key for cached latest predictions."""
    return f"pred:latest:{market}"


# ---------------------------------------------------------------------------
# Task dataclass
# ---------------------------------------------------------------------------


@dataclass
class PredictionTask:
    """In-memory representation of a prediction task."""

    task_id: str
    market: str
    status: str = "pending"  # pending, training, predicting, completed, failed
    progress: float = 0.0
    message: str = ""
    results: Optional[dict] = None
    error: Optional[str] = None
    _asyncio_task: Optional[asyncio.Task] = field(
        default=None, repr=False, compare=False
    )
    _psi_data: Optional[dict] = field(
        default=None, repr=False, compare=False
    )
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Serialize to API-compatible dict (excludes internal asyncio task).

        Uses a JSON roundtrip to convert numpy types (float32, int64, etc.)
        to native Python types that Pydantic can serialize.
        """
        d = {
            "task_id": self.task_id,
            "market": self.market,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "results": self.results,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }
        return json.loads(json.dumps(d, default=_numpy_default))


# ---------------------------------------------------------------------------
# SQL queries (asyncpg parameterized: $1, $2, ...)
# ---------------------------------------------------------------------------

_SQL_CHECK_MODEL = """
SELECT id FROM prediction_models
WHERE market = $1 AND model_date = $2 AND forward_days = $3
LIMIT 1
"""

_SQL_INSERT_MODEL = """
INSERT INTO prediction_models (
    market, model_date, train_start, train_end, val_start, val_end,
    forward_days, feature_count, symbol_count, feature_sources,
    ic, icir, ndcg, model_path, metadata, quality_passed
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
ON CONFLICT (market, model_date, forward_days) DO UPDATE SET
    ic = EXCLUDED.ic,
    icir = EXCLUDED.icir,
    ndcg = EXCLUDED.ndcg,
    model_path = EXCLUDED.model_path,
    feature_count = EXCLUDED.feature_count,
    symbol_count = EXCLUDED.symbol_count,
    metadata = EXCLUDED.metadata,
    quality_passed = EXCLUDED.quality_passed
RETURNING id
"""

_SQL_INSERT_PREDICTIONS = """
INSERT INTO stock_predictions (
    market, prediction_date, model_id, symbol, predicted_score,
    percentile_rank, predicted_direction, forward_days
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (market, prediction_date, symbol, forward_days) DO UPDATE SET
    model_id = EXCLUDED.model_id,
    predicted_score = EXCLUDED.predicted_score,
    percentile_rank = EXCLUDED.percentile_rank,
    predicted_direction = EXCLUDED.predicted_direction
"""

_SQL_GET_LATEST_PREDICTIONS = """
SELECT symbol, predicted_score, percentile_rank, predicted_direction,
       prediction_date, forward_days
FROM stock_predictions
WHERE market = $1
  AND prediction_date = (
      SELECT MAX(prediction_date) FROM stock_predictions WHERE market = $1
  )
ORDER BY predicted_score DESC
LIMIT $2
"""

_SQL_GET_LATEST_PREDICTIONS_BY_SYMBOL = """
SELECT symbol, predicted_score, percentile_rank, predicted_direction,
       prediction_date, forward_days
FROM stock_predictions
WHERE market = $1 AND symbol = $2
  AND prediction_date = (
      SELECT MAX(prediction_date) FROM stock_predictions WHERE market = $1
  )
"""

_SQL_GET_MODELS = """
SELECT id, market, model_date, train_start, train_end, val_start, val_end,
       forward_days, feature_count, symbol_count, feature_sources,
       ic, icir, ndcg, model_path, metadata, quality_passed, created_at
FROM prediction_models
WHERE ($1::text IS NULL OR market = $1)
ORDER BY model_date DESC, created_at DESC
LIMIT 50
"""

_SQL_GET_PREDICTION_HISTORY = """
SELECT symbol, predicted_score, percentile_rank, predicted_direction,
       prediction_date, forward_days, actual_return
FROM stock_predictions
WHERE market = $1
  AND prediction_date >= CURRENT_DATE - $2 * INTERVAL '1 day'
ORDER BY prediction_date DESC, predicted_score DESC
"""

_SQL_BACKFILL_CANDIDATES = """
SELECT id, symbol, market, prediction_date, forward_days
FROM stock_predictions
WHERE actual_return IS NULL
  AND prediction_date <= CURRENT_DATE - forward_days * INTERVAL '1 day'
LIMIT 5000
"""

_SQL_UPDATE_ACTUAL_RETURN = """
UPDATE stock_predictions
SET actual_return = $1
WHERE id = $2
"""

_SQL_GET_LATEST_MODEL_PATH = """
SELECT id, model_path FROM prediction_models
WHERE market = $1 AND forward_days = $2 AND quality_passed = TRUE
ORDER BY model_date DESC, created_at DESC
LIMIT 1
"""

_SQL_GET_LATEST_MODEL_PATH_ANY = """
SELECT id, model_path FROM prediction_models
WHERE market = $1 AND forward_days = $2
ORDER BY model_date DESC, created_at DESC
LIMIT 1
"""

# _SQL_GET_LATEST_QUALITY_MODEL removed — identical to _SQL_GET_LATEST_MODEL_PATH

_SQL_UPDATE_MODEL_QUALITY = """
UPDATE prediction_models SET quality_passed = $1 WHERE id = $2
RETURNING id
"""

_SQL_GET_MODEL_DETAIL = """
SELECT id, market, model_date, forward_days, feature_count, symbol_count,
       ic, icir, ndcg, model_path, metadata, quality_passed, created_at
FROM prediction_models WHERE id = $1
"""

_SQL_PERFORMANCE_METRICS = """
SELECT
    prediction_date,
    symbol,
    predicted_score,
    percentile_rank,
    predicted_direction,
    actual_return
FROM stock_predictions
WHERE market = $1
  AND actual_return IS NOT NULL
  AND prediction_date >= CURRENT_DATE - $2 * INTERVAL '1 day'
ORDER BY prediction_date, predicted_score DESC
"""

_SQL_RECENT_PREDICTION_DATES = """
SELECT DISTINCT prediction_date
FROM stock_predictions
WHERE market = $1 AND forward_days = $2
ORDER BY prediction_date DESC
LIMIT $3
"""

_SQL_PREDICTIONS_BY_DATES = """
SELECT symbol, predicted_score, percentile_rank, predicted_direction,
       prediction_date, forward_days
FROM stock_predictions
WHERE market = $1 AND forward_days = $2
  AND prediction_date = ANY($3::date[])
ORDER BY prediction_date DESC, predicted_score DESC
"""

_SQL_GET_LATEST_PREDICTIONS_FD = """
SELECT symbol, predicted_score, percentile_rank, predicted_direction,
       prediction_date, forward_days
FROM stock_predictions
WHERE market = $1
  AND forward_days = $3
  AND prediction_date = (
      SELECT MAX(prediction_date) FROM stock_predictions
      WHERE market = $1 AND forward_days = $3
  )
ORDER BY predicted_score DESC
LIMIT $2
"""

_SQL_MARKET_CAP_LATEST = """
SELECT DISTINCT ON (symbol) symbol, market_cap
FROM stock_fundamentals
WHERE market = $1 AND record_type = 'daily_snapshot'
  AND market_cap IS NOT NULL
ORDER BY symbol, date DESC
"""


# ---------------------------------------------------------------------------
# PredictionService
# ---------------------------------------------------------------------------


class PredictionService:
    """LightGBM prediction service -- training and inference engine.

    Task lifecycle:
    1. run_prediction() -> creates PredictionTask, launches asyncio.Task
    2. _run_prediction_async() -> trains model (if needed), runs inference
    3. get_task() / get_latest_predictions() -> poll results
    """

    # Maximum age (seconds) before completed/failed tasks are pruned
    _TASK_MAX_AGE_SECONDS = 3600

    def __init__(self) -> None:
        self._tasks: dict[str, PredictionTask] = {}
        self._lock = asyncio.Lock()

    def _cleanup_old_tasks(self) -> None:
        """Remove completed/failed tasks older than 1 hour to prevent unbounded growth."""
        now = datetime.now()
        to_delete = [
            tid
            for tid, t in self._tasks.items()
            if t.status in ("completed", "failed")
            and t.completed_at is not None
            and (now - t.completed_at).total_seconds() > self._TASK_MAX_AGE_SECONDS
        ]
        for tid in to_delete:
            self._tasks.pop(tid, None)
        if to_delete:
            logger.debug("Cleaned up %d old prediction tasks", len(to_delete))

    # ------------------------------------------------------------------
    # Public API: task management
    # ------------------------------------------------------------------

    async def run_prediction(
        self,
        market: str,
        force_retrain: bool = False,
        forward_days: int = 5,
    ) -> str:
        """Trigger a prediction run (training + inference) for a market.

        Creates a PredictionTask and launches it as a background asyncio
        task. Returns the task_id immediately.

        Args:
            market: Market code (us, hk, cn, etc.).
            force_retrain: Force model retraining even if one exists for today.
            forward_days: Number of trading days to predict forward.

        Returns:
            task_id string.

        Raises:
            RuntimeError: If maximum concurrent predictions reached.
        """
        async with self._lock:
            self._cleanup_old_tasks()

            running = sum(
                1
                for t in self._tasks.values()
                if t.status in ("pending", "training", "predicting")
            )
            if running >= _MAX_CONCURRENT_PREDICTIONS:
                raise RuntimeError(
                    f"Maximum concurrent predictions ({_MAX_CONCURRENT_PREDICTIONS}) "
                    f"reached. Wait for existing task to complete."
                )

            task_id = uuid.uuid4().hex[:16]
            task = PredictionTask(task_id=task_id, market=market)
            self._tasks[task_id] = task

        logger.info(
            "Prediction task created: task_id=%s, market=%s, "
            "force_retrain=%s, forward_days=%d",
            task_id,
            market,
            force_retrain,
            forward_days,
        )

        # Launch background asyncio task
        coro = self._run_prediction_async(task, market, forward_days, force_retrain)
        bg_task = asyncio.create_task(coro, name=f"predict-{task_id}")
        task._asyncio_task = bg_task

        return task_id

    async def run_multi_horizon(
        self,
        market: str,
        force_retrain: bool = False,
    ) -> str:
        """Train models for all configured horizons sequentially, then combine.

        Creates a single PredictionTask that internally loops over horizons.
        Returns task_id immediately.
        """
        horizons = _get_prediction_horizons()

        async with self._lock:
            self._cleanup_old_tasks()

            running = sum(
                1
                for t in self._tasks.values()
                if t.status in ("pending", "training", "predicting")
            )
            if running >= _MAX_CONCURRENT_PREDICTIONS:
                raise RuntimeError(
                    f"Maximum concurrent predictions ({_MAX_CONCURRENT_PREDICTIONS}) "
                    f"reached. Wait for existing task to complete."
                )

            task_id = uuid.uuid4().hex[:16]
            task = PredictionTask(task_id=task_id, market=market)
            self._tasks[task_id] = task

        logger.info(
            "Multi-horizon prediction task created: task_id=%s, market=%s, "
            "horizons=%s, force_retrain=%s",
            task_id, market, horizons, force_retrain,
        )

        coro = self._run_multi_horizon_async(task, market, horizons, force_retrain)
        bg_task = asyncio.create_task(coro, name=f"predict-multi-{task_id}")
        task._asyncio_task = bg_task

        return task_id

    async def _run_multi_horizon_async(
        self,
        task: PredictionTask,
        market: str,
        horizons: list[int],
        force_retrain: bool,
    ) -> None:
        """Run full pipeline for multiple horizons, then combine signals."""
        n_horizons = len(horizons)
        try:
            for i, h in enumerate(horizons):
                pct_base = (i / n_horizons) * 90
                task.message = f"Horizon {h}d ({i + 1}/{n_horizons})"
                task.progress = pct_base

                logger.info(
                    "Multi-horizon: starting horizon %dd (%d/%d) for %s",
                    h, i + 1, n_horizons, market,
                )

                # Reuse existing single-horizon pipeline (synchronous within task)
                await self._run_prediction_async(
                    task, market, h, force_retrain,
                    _progress_base=pct_base,
                    _progress_range=90.0 / n_horizons,
                    _skip_completion=True,
                )

            # Combine signals if multiple horizons
            if n_horizons > 1:
                task.message = "Computing combined signal"
                task.progress = 92.0
                n_combined = await self.compute_combined_signal(market, horizons)
                logger.info(
                    "Combined signal: %d consensus predictions for %s",
                    n_combined, market,
                )

            task.status = "completed"
            task.progress = 100.0
            task.completed_at = datetime.now()
            task.message = f"Completed: {n_horizons} horizons"
            task.results = task.results or {}
            task.results["horizons"] = horizons

        except Exception as e:
            logger.error(
                "Multi-horizon prediction failed for %s: %s",
                market, e, exc_info=True,
            )
            task.status = "failed"
            task.error = str(e)
            task.completed_at = datetime.now()

    def get_task(self, task_id: str) -> Optional[dict]:
        """Return task status dict, or None if not found."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return task.to_dict()

    def list_tasks(self) -> list[dict]:
        """List all prediction tasks, newest first."""
        tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        return [t.to_dict() for t in tasks]

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running prediction task. Returns True if cancelled."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.status not in ("pending", "training", "predicting"):
            return False

        task.status = "failed"
        task.error = "Cancelled by user"
        task.completed_at = datetime.now()

        if task._asyncio_task is not None and not task._asyncio_task.done():
            task._asyncio_task.cancel()

        logger.info("Prediction task cancelled: task_id=%s", task_id)
        return True

    def delete_task(self, task_id: str) -> bool:
        """Delete a completed or failed task. Returns True if deleted."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.status in ("pending", "training", "predicting"):
            return False
        del self._tasks[task_id]
        logger.info("Prediction task deleted: task_id=%s", task_id)
        return True

    # ------------------------------------------------------------------
    # Public API: predictions query
    # ------------------------------------------------------------------

    async def get_latest_predictions(
        self,
        market: str,
        top_n: int = 50,
        symbol: Optional[str] = None,
        forward_days: Optional[int] = None,
    ) -> list[dict]:
        """Get the latest predictions for a market.

        Checks Redis cache first, falls back to PostgreSQL.

        Args:
            market: Market code.
            top_n: Maximum number of results (ignored if symbol is set).
            symbol: If set, return only this symbol's prediction.
            forward_days: If set, filter by prediction horizon.
                          0 = combined multi-horizon signal.

        Returns:
            List of prediction dicts sorted by predicted_score descending.
        """
        # 1. Try Redis cache (only for full-market queries without filters)
        if symbol is None and forward_days is None:
            cached = await self._read_prediction_cache(market)
            if cached is not None:
                logger.debug("Prediction cache hit: market=%s", market)
                return cached[:top_n]

        # 2. Query PostgreSQL
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.warning("DB pool not available for prediction query")
            return []

        try:
            async with pool.acquire(timeout=10) as conn:
                if symbol is not None:
                    rows = await conn.fetch(
                        _SQL_GET_LATEST_PREDICTIONS_BY_SYMBOL, market, symbol
                    )
                elif forward_days is not None:
                    rows = await conn.fetch(
                        _SQL_GET_LATEST_PREDICTIONS_FD, market, top_n, forward_days
                    )
                else:
                    rows = await conn.fetch(
                        _SQL_GET_LATEST_PREDICTIONS, market, top_n
                    )
        except Exception as e:
            logger.error("Failed to query latest predictions: %s", e)
            return []

        results = [self._row_to_prediction_dict(r) for r in rows]
        return results

    async def get_models(self, market: Optional[str] = None) -> list[dict]:
        """List trained models, optionally filtered by market."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return []

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(_SQL_GET_MODELS, market)
        except Exception as e:
            logger.error("Failed to query prediction models: %s", e)
            return []

        return [self._row_to_model_dict(r) for r in rows]

    async def get_prediction_history(
        self, market: str, days: int = 30
    ) -> list[dict]:
        """Get prediction history for the last N days."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return []

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(
                    _SQL_GET_PREDICTION_HISTORY, market, days
                )
        except Exception as e:
            logger.error("Failed to query prediction history: %s", e)
            return []

        return [self._row_to_prediction_dict(r) for r in rows]

    async def backfill_returns(self) -> dict:
        """Backfill actual returns for past predictions.

        Finds predictions where prediction_date + forward_days <= today
        and actual_return IS NULL, then computes the actual return from
        price data via BackendDataClient.

        Returns:
            Summary dict with counts of updated, failed, and skipped rows.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return {"error": "DB pool not available"}

        # 1. Find candidates
        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(_SQL_BACKFILL_CANDIDATES)
        except Exception as e:
            logger.error("Failed to query backfill candidates: %s", e)
            return {"error": str(e)}

        if not rows:
            logger.info("No predictions need backfilling")
            return {"updated": 0, "failed": 0, "total": 0}

        logger.info("Backfill: found %d predictions to update", len(rows))

        # 2. Group by (symbol, market) to batch price lookups
        symbol_groups: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            key = (row["symbol"], row["market"])
            if key not in symbol_groups:
                symbol_groups[key] = []
            symbol_groups[key].append(dict(row))

        # 3. Fetch prices and compute returns
        updated = 0
        failed = 0

        for (symbol, market), predictions in symbol_groups.items():
            try:
                actual_returns = await self._compute_actual_returns(
                    symbol, predictions, market=market
                )
            except Exception as e:
                logger.warning(
                    "Backfill price fetch failed for %s (%s): %s", symbol, market, e
                )
                failed += len(predictions)
                continue

            # 4. Write back to DB (batched)
            try:
                async with pool.acquire(timeout=10) as conn:
                    update_data = []
                    for pred in predictions:
                        ret = actual_returns.get(pred["id"])
                        if ret is not None:
                            update_data.append((float(ret), pred["id"]))
                            updated += 1
                        else:
                            failed += 1
                    if update_data:
                        await conn.executemany(_SQL_UPDATE_ACTUAL_RETURN, update_data)
            except Exception as e:
                logger.error(
                    "Backfill DB write failed for %s (%s): %s", symbol, market, e
                )
                failed += len(predictions)

        summary = {
            "updated": updated,
            "failed": failed,
            "total": len(rows),
        }
        logger.info("Backfill complete: %s", summary)

        # After backfilling, check for performance decay in each market
        for mkt in ("us", "hk", "cn"):
            try:
                decayed = await self._check_performance_decay(mkt)
                if decayed:
                    await self._flag_retrain_needed(mkt)
            except Exception as e:
                logger.debug("Decay check failed for %s: %s", mkt, e)

        return summary

    async def _check_performance_decay(
        self, market: str, lookback_days: int = 20,
    ) -> bool:
        """Check if recent prediction IC indicates performance decay.

        Computes rolling IC from backfilled predictions over the last
        N trading days. Returns True if mean IC is negative.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return False

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(
                    "SELECT prediction_date, "
                    "  corr(predicted_score, actual_return) AS daily_ic "
                    "FROM stock_predictions "
                    "WHERE market = $1 "
                    "  AND actual_return IS NOT NULL "
                    "  AND prediction_date >= CURRENT_DATE - $2 * INTERVAL '1 day' "
                    "GROUP BY prediction_date "
                    "HAVING COUNT(*) >= 10 "
                    "ORDER BY prediction_date",
                    market, lookback_days,
                )
        except Exception as e:
            logger.warning("Decay IC query failed for %s: %s", market, e)
            return False

        if len(rows) < 5:
            return False

        ics = [float(r["daily_ic"]) for r in rows if r["daily_ic"] is not None]
        if not ics:
            return False

        mean_ic = sum(ics) / len(ics)
        if mean_ic < -0.01:
            logger.warning(
                "Performance decay detected for %s: "
                "rolling IC=%.4f over %d days (threshold=-0.01)",
                market, mean_ic, len(ics),
            )
            return True

        return False

    async def _flag_retrain_needed(self, market: str) -> None:
        """Set Redis flag to force retrain on next scheduled prediction."""
        try:
            r = await _get_redis_client()
            key = f"prediction:retrain_needed:{market}"
            await r.set(key, b"1", ex=86400 * 2)  # 2-day TTL
            logger.info("Flagged %s for forced retrain", market)
        except Exception as e:
            logger.debug("Failed to set retrain flag for %s: %s", market, e)

    async def check_retrain_needed(self, market: str) -> bool:
        """Check if a market has been flagged for forced retraining."""
        try:
            r = await _get_redis_client()
            key = f"prediction:retrain_needed:{market}"
            val = await r.get(key)
            if val:
                await r.delete(key)  # Consume the flag
                return True
        except Exception:
            pass
        return False

    def shutdown(self) -> None:
        """Cancel any running prediction tasks. Called during app shutdown."""
        for task in self._tasks.values():
            if task._asyncio_task and not task._asyncio_task.done():
                task._asyncio_task.cancel()
                logger.info(
                    "Cancelled prediction task on shutdown: %s", task.task_id
                )

    # ------------------------------------------------------------------
    # Core async pipeline
    # ------------------------------------------------------------------

    async def _run_prediction_async(
        self,
        task: PredictionTask,
        market: str,
        forward_days: int,
        force_retrain: bool,
        _progress_base: float = 0.0,
        _progress_range: float = 100.0,
        _skip_completion: bool = False,
    ) -> None:
        """Full prediction pipeline: resolve symbols, train, infer.

        Runs as an asyncio.Task. Updates task.status/progress/message
        throughout for the polling API.

        When called from run_multi_horizon, _progress_base/_progress_range
        scale progress updates into a sub-range, and _skip_completion=True
        prevents the method from setting task to "completed" (the caller
        handles that).
        """

        def _p(pct: float) -> float:
            """Scale a 0-100 progress value into the assigned sub-range."""
            return _progress_base + (pct / 100.0) * _progress_range

        try:
            # Step 1: Resolve universe symbols
            task.status = "training"
            task.progress = _p(5)
            task.message = f"[{forward_days}d] Resolving universe symbols"
            logger.info(
                "Prediction pipeline start: market=%s, forward_days=%d",
                market, forward_days,
            )

            symbols = await self._resolve_symbols(market)
            if not symbols:
                raise RuntimeError(
                    f"No symbols resolved for market={market}. "
                    f"Configure a prediction universe or ensure BackendDataClient "
                    f"can return symbols."
                )

            logger.info(
                "Resolved %d symbols for market=%s", len(symbols), market
            )
            task.message = f"[{forward_days}d] Resolved {len(symbols)} symbols"
            task.progress = _p(10)

            # Step 1.5: Data freshness check
            is_fresh, freshness_msg = self._check_data_freshness(market)
            if not is_fresh:
                logger.warning(
                    "Skipping prediction for %s: %s", market, freshness_msg
                )
                if _skip_completion:
                    # Multi-horizon mode: just log and return, caller continues
                    task.message = f"[{forward_days}d] Skipped: {freshness_msg}"
                    return
                task.status = "completed"
                task.progress = 100.0
                task.completed_at = datetime.now()
                task.message = f"Skipped: {freshness_msg}"
                task.results = {
                    "market": market,
                    "skipped": True,
                    "reason": freshness_msg,
                }
                return
            logger.info("Data freshness OK for %s: %s", market, freshness_msg)

            # Step 2: Check if retraining is needed
            today = date.today()
            model_id: Optional[Any] = None
            model_path: Optional[str] = None
            trained_this_run = False

            if not force_retrain:
                existing = await self._check_existing_model(
                    market, today, forward_days
                )
                if existing is not None:
                    model_id = existing["id"]
                    model_path = existing.get("model_path")
                    logger.info(
                        "Existing model found for %s/%s, skipping training",
                        market,
                        today.isoformat(),
                    )
                    task.message = f"[{forward_days}d] Using existing model"
                    task.progress = _p(70)

            # Step 3: Train if needed
            quality_passed = True
            fallback: Optional[dict] = None
            if model_id is None:
                model_id, model_path, quality_passed = await self._train_model(
                    task, market, symbols, forward_days, today
                )
                trained_this_run = True

                if not quality_passed:
                    # Fall back to latest model that passed quality gate
                    fallback = await self._find_latest_quality_model(
                        market, forward_days
                    )
                    if fallback and os.path.exists(fallback["model_path"]):
                        logger.warning(
                            "Quality gate failed — falling back to previous model: id=%s",
                            fallback["id"],
                        )
                        model_id = fallback["id"]
                        model_path = fallback["model_path"]
                    elif fallback:
                        logger.error(
                            "Quality gate failed, fallback model file missing: %s",
                            fallback["model_path"],
                        )
                        # Fall through — use current model despite low quality
                    else:
                        logger.error(
                            "Quality gate failed and no previous quality model available. "
                            "Using current model despite low quality."
                        )

            # Step 4: Inference
            task.status = "predicting"
            task.progress = _p(75)
            task.message = f"[{forward_days}d] Running inference"
            logger.info("Starting inference: market=%s", market)

            prediction_count = await self._run_inference(
                task, market, symbols, model_id, model_path, forward_days, today
            )

            # Done
            if _skip_completion:
                # Multi-horizon mode: caller manages task lifecycle
                task.progress = _p(100)
                task.message = f"[{forward_days}d] Done: {prediction_count} predictions"
                logger.info(
                    "Horizon %dd completed: %d predictions for %s",
                    forward_days, prediction_count, market,
                )
                return

            task.status = "completed"
            task.progress = 100.0
            task.completed_at = datetime.now()
            task.message = (
                f"Completed: {prediction_count} predictions"
                + (" (model retrained)" if trained_this_run else "")
            )
            used_fallback = (
                not quality_passed and trained_this_run and fallback is not None
            )
            task.results = {
                "market": market,
                "model_id": str(model_id) if model_id else None,
                "prediction_count": prediction_count,
                "prediction_date": today.isoformat(),
                "forward_days": forward_days,
                "symbol_count": len(symbols),
                "retrained": trained_this_run,
                "trained_model_quality_passed": (
                    quality_passed if trained_this_run else None
                ),
                "used_fallback_model": used_fallback,
                "fallback_model_id": (
                    str(fallback["id"]) if used_fallback and fallback else None
                ),
            }
            # Merge PSI data from inference phase
            if task._psi_data is not None:
                task.results["feature_psi"] = task._psi_data

            logger.info(
                "Prediction pipeline completed: task_id=%s, market=%s, "
                "predictions=%d, retrained=%s",
                task.task_id,
                market,
                prediction_count,
                trained_this_run,
            )

        except asyncio.CancelledError:
            if _skip_completion:
                raise  # Let multi-horizon caller handle
            task.status = "failed"
            task.error = "Task was cancelled"
            task.completed_at = datetime.now()
            logger.info(
                "Prediction task cancelled: task_id=%s", task.task_id
            )
        except Exception as e:
            if _skip_completion:
                raise  # Let multi-horizon caller handle
            task.status = "failed"
            task.error = str(e)
            task.completed_at = datetime.now()
            logger.error(
                "Prediction task failed: task_id=%s, error=%s",
                task.task_id,
                e,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Step 1: Symbol resolution
    # ------------------------------------------------------------------

    async def _resolve_symbols(self, market: str) -> list[str]:
        """Resolve the prediction universe for a market.

        Priority:
        1. Universe with explicit symbols (custom type or pre-populated)
        2. Index-type universe → resolve via data-service constituent API
        3. BackendDataClient.get_symbols() full-market fallback (last resort)
        """
        from app.core.settings_cache import settings_cache

        universes = await settings_cache.get_universes(market=market)

        for universe in universes:
            # Priority 1: explicit symbols
            if universe.symbols and len(universe.symbols) > 0:
                logger.info(
                    "Using universe '%s' (%s): %d symbols",
                    universe.name,
                    universe.universe_type,
                    len(universe.symbols),
                )
                return universe.symbols

            # Priority 2: index-type → resolve constituents via data-service
            if universe.universe_type == "index" and universe.index_code:
                logger.info(
                    "Resolving index constituents for '%s' (index_code=%s, market=%s)",
                    universe.name, universe.index_code, market,
                )
                try:
                    symbols = await asyncio.to_thread(
                        self._get_index_constituents, universe.index_code, market,
                    )
                    if symbols:
                        logger.info(
                            "Resolved %d symbols from index %s for market=%s",
                            len(symbols), universe.index_code, market,
                        )
                        return symbols
                    logger.warning(
                        "Index resolution returned empty for %s, trying next universe",
                        universe.index_code,
                    )
                except Exception as e:
                    logger.warning(
                        "Index constituent resolution failed for %s: %s",
                        universe.index_code, e,
                    )

        # Priority 3: full-market fallback (may be very large!)
        logger.warning(
            "No configured universe or index resolution for market=%s, "
            "falling back to full market symbol list",
            market,
        )

        try:
            symbols = await asyncio.to_thread(self._get_backend_symbols, market)
            return symbols
        except Exception as e:
            logger.error(
                "BackendDataClient symbol fetch failed for market=%s: %s",
                market,
                e,
            )
            return []

    @staticmethod
    def _get_backend_symbols(market: str) -> list[str]:
        """Synchronous symbol fetch via BackendDataClient (for asyncio.to_thread)."""
        from app.services.backend_client import get_backend_client

        client = get_backend_client()
        return client.get_symbols(market)

    @staticmethod
    def _get_index_constituents(index_code: str, market: str) -> list[str]:
        """Synchronous index constituent fetch (for asyncio.to_thread)."""
        from app.services.backend_client import get_backend_client

        client = get_backend_client()
        return client.get_index_constituents(index_code, market)

    # ------------------------------------------------------------------
    # Step 2: Check existing model
    # ------------------------------------------------------------------

    async def _check_existing_model(
        self,
        market: str,
        model_date: date,
        forward_days: int,
    ) -> Optional[dict]:
        """Check if a quality-passed model already exists for today.

        Returns dict with 'id' and 'model_path' from the SAME row, else None.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return None

        try:
            async with pool.acquire(timeout=10) as conn:
                # Single query: today's model that also passed quality gate
                row = await conn.fetchrow(
                    """SELECT id, model_path FROM prediction_models
                    WHERE market = $1 AND model_date = $2 AND forward_days = $3
                      AND quality_passed = TRUE
                    LIMIT 1""",
                    market, model_date, forward_days,
                )
                if row is None:
                    return None

                return {"id": row["id"], "model_path": row["model_path"]}
        except Exception as e:
            logger.warning("Failed to check existing model: %s", e)
            return None

    # ------------------------------------------------------------------
    # Step 3: Training
    # ------------------------------------------------------------------

    async def _train_model(
        self,
        task: PredictionTask,
        market: str,
        symbols: list[str],
        forward_days: int,
        model_date: date,
    ) -> tuple[Any, str, bool]:
        """Train a LightGBM ranking model.

        Returns:
            Tuple of (model_id from DB, model_path on disk, quality_passed).

        Raises:
            RuntimeError on training failures.
        """
        task.message = "Building training feature matrix"
        task.progress = 15.0

        # Date ranges for training data
        train_end_date = model_date - timedelta(days=forward_days)
        train_start_date = model_date - timedelta(days=_TRAIN_LOOKBACK_DAYS)

        train_start_str = train_start_date.isoformat()
        train_end_str = model_date.isoformat()  # Include forward_days buffer for labels

        logger.info(
            "Building feature matrix: %s to %s, %d symbols",
            train_start_str, train_end_str, len(symbols),
        )

        # Build feature matrix (includes rank transform)
        feature_df = await feature_service.build_feature_matrix(
            market=market,
            symbols=symbols,
            start_date=train_start_str,
            end_date=train_end_str,
        )

        if feature_df.empty:
            raise RuntimeError(
                f"Feature matrix is empty for market={market}. "
                f"Ensure Qlib data is synced and symbols are valid."
            )

        task.message = f"Feature matrix: {len(feature_df)} rows"
        task.progress = 30.0

        # Get close prices for label computation
        logger.info("Fetching close prices for label computation")
        close_df = await self._fetch_close_prices(market, symbols, train_start_str, train_end_str)

        if close_df.empty:
            raise RuntimeError(
                "Close price data is empty. Cannot compute forward returns."
            )

        task.progress = 35.0

        # Merge close prices into feature matrix
        feature_df["date"] = pd.to_datetime(feature_df["date"])
        close_df["date"] = pd.to_datetime(close_df["date"])

        df = feature_df.merge(
            close_df[["symbol", "date", "close"]],
            on=["symbol", "date"],
            how="left",
        )

        # Compute forward returns
        df = df.sort_values(["symbol", "date"])
        df["forward_return"] = df.groupby("symbol")["close"].transform(
            lambda x: x.shift(-forward_days) / x - 1
        )

        # Winsorize: clip extreme returns (1st/99th percentile per date) to prevent
        # outliers from corporate events (M&A, earnings blowouts) distorting quintile bins.
        df["forward_return"] = df.groupby("date")["forward_return"].transform(
            lambda x: x.clip(x.quantile(0.01), x.quantile(0.99))
        )

        # Sector-neutral label construction: subtract sector mean return per date.
        # This teaches the model "which stocks outperform within their sector?"
        # rather than "buy tech, sell utilities" — separating alpha from beta.
        # Stocks without sector data keep raw forward_return (graceful fallback).
        # CN/HK: sector groups have only 3-10 stocks → sector mean is noise,
        # not a meaningful benchmark. Disabled via MarketConfig.
        cfg = get_market_config(market)
        if cfg.use_sector_neutral_labels:
            sector_map = await fundamental_service.get_sector_map(market, symbols)
            if sector_map:
                df["_sector"] = df["symbol"].map(sector_map)
                sector_coverage = df["_sector"].notna().mean()
                logger.info(
                    "Sector neutralization: %d/%d symbols mapped (%.0f%% coverage)",
                    len(sector_map), len(symbols), sector_coverage * 100,
                )
                if sector_coverage >= 0.3:  # Only neutralize if ≥30% coverage
                    # Compute sector mean return per (date, sector)
                    sector_mean = df.groupby(["date", "_sector"])["forward_return"].transform("mean")
                    # Subtract sector mean where available, keep raw where no sector
                    has_sector = df["_sector"].notna()
                    df.loc[has_sector, "forward_return"] = (
                        df.loc[has_sector, "forward_return"] - sector_mean[has_sector]
                    )
                    logger.info("Applied sector-neutral excess returns")
                else:
                    logger.info(
                        "Sector coverage too low (%.0f%%), using raw returns",
                        sector_coverage * 100,
                    )
                df = df.drop(columns=["_sector"])
            else:
                logger.info("No sector data available, using raw returns for labels")
        else:
            logger.info(
                "Sector-neutral labels disabled for market=%s (MarketConfig.use_sector_neutral_labels=False)",
                market,
            )

        # Drop rows without forward return (last forward_days dates per symbol)
        before_drop = len(df)
        df = df.dropna(subset=["forward_return"])
        logger.info(
            "Label computation: %d rows with forward returns "
            "(dropped %d without labels)",
            len(df),
            before_drop - len(df),
        )

        if len(df) < _MIN_TRAIN_DATES * _MIN_SYMBOLS_PER_DATE:
            raise RuntimeError(
                f"Insufficient labeled data: {len(df)} rows. "
                f"Need at least {_MIN_TRAIN_DATES * _MIN_SYMBOLS_PER_DATE}."
            )

        # Per-date percentile labels (0-4 scale for lambdarank).
        # Two labeling strategies depending on training mode:
        # - Cross-sectional (US): balanced quintiles via rank(method="first")
        #   to break ties → uniform 5-bin distribution. Tested: IC +61%.
        # - Legacy (CN/HK): original qcut(duplicates="drop") which may produce
        #   fewer bins when returns cluster. Tested: balanced labels hurt CN
        #   (IC -48%), likely because garbled groups pair better with sparser labels.
        use_legacy = not cfg.use_balanced_quintiles

        def _label_fn(x: "pd.Series") -> "pd.Series":
            if len(x) < _MIN_SYMBOLS_PER_DATE:
                return pd.Series([2] * len(x), index=x.index)
            if use_legacy:
                result = pd.qcut(
                    x, q=5, labels=False, duplicates="drop"
                )
                actual_bins = result.nunique()
                if actual_bins < 5:
                    logger.debug(
                        "qcut produced %d/5 bins for date group (size=%d) — "
                        "possible circuit-breaker or clustering",
                        actual_bins, len(x),
                    )
                return result
            # rank(method="first") produces unique sequential integers, so qcut
            # always succeeds. Safe as long as len(x) >= _MIN_SYMBOLS_PER_DATE (25).
            ranked = x.rank(method="first")
            return pd.qcut(ranked, q=5, labels=False).astype(float)

        df["label"] = df.groupby("date")["forward_return"].transform(_label_fn)
        df["label"] = df["label"].fillna(2).astype(float)

        task.message = "Splitting train/validation sets"
        task.progress = 40.0

        unique_dates = sorted(df["date"].unique())
        sort_cols = ["symbol", "date"] if cfg.use_temporal_sort else ["date", "symbol"]

        # Determine feature columns (everything except metadata + label + close)
        meta_cols = {"symbol", "date", "close", "forward_return", "label"}
        feature_cols = [c for c in df.columns if c not in meta_cols]
        if not feature_cols:
            raise RuntimeError("No feature columns found in the dataset")

        settings = get_settings()
        ensemble_size = settings.ENSEMBLE_SIZE
        n_folds = settings.WALKFORWARD_FOLDS

        # ----- Walk-forward evaluation across multiple OOS windows -----
        # Each fold trains an ensemble and evaluates IC on a held-out window.
        # The fold ICs give a confidence interval on model quality.
        # The final model is trained on the largest window (last fold's split).

        splits = self._walk_forward_splits(
            unique_dates, n_folds=n_folds, forward_days=forward_days,
        )

        if not splits:
            raise RuntimeError(
                f"Could not generate walk-forward splits "
                f"({len(unique_dates)} dates, {n_folds} folds, "
                f"forward_days={forward_days})"
            )

        fold_ics: list[float] = []
        fold_icirs: list[float] = []
        final_models: list[lgb.Booster] = []
        final_val_df: pd.DataFrame = pd.DataFrame()
        final_val_scores: np.ndarray = np.array([])
        final_train_dates: list = []
        final_val_dates: list = []
        final_train_df: pd.DataFrame = pd.DataFrame()

        for fold_idx, (tr_dates, va_dates) in enumerate(splits):
            is_final_fold = fold_idx == len(splits) - 1

            tr_mask = df["date"].isin(tr_dates)
            va_mask = df["date"].isin(va_dates)
            tr_df = df[tr_mask].copy().sort_values(sort_cols).reset_index(drop=True)
            va_df = df[va_mask].copy().sort_values(sort_cols).reset_index(drop=True)

            X_tr = tr_df[feature_cols].values
            y_tr = tr_df["label"].values
            X_va = va_df[feature_cols].values
            y_va = va_df["label"].values
            tr_group = tr_df.groupby("date", sort=True).size().values
            va_group = va_df.groupby("date", sort=True).size().values

            tr_set = lgb.Dataset(
                X_tr, label=y_tr, group=tr_group, feature_name=feature_cols,
            )
            va_set = lgb.Dataset(
                X_va, label=y_va, group=va_group, feature_name=feature_cols,
                reference=tr_set,
            )

            progress_base = 42.0 + fold_idx * (13.0 / len(splits))
            task.message = f"Walk-forward fold {fold_idx + 1}/{len(splits)}"
            task.progress = progress_base

            logger.info(
                "Walk-forward fold %d/%d: train=%d rows (%d dates), "
                "val=%d rows (%d dates), mode=%s",
                fold_idx + 1, len(splits),
                len(tr_df), len(tr_dates), len(va_df), len(va_dates),
                "temporal" if cfg.use_temporal_sort else "cross-sectional",
            )

            models = await asyncio.to_thread(
                self._train_ensemble_sync, tr_set, va_set, market, ensemble_size,
            )

            # Evaluate ensemble on this fold's validation set
            va_scores_list = [m.predict(X_va) for m in models]
            va_scores = np.mean(va_scores_list, axis=0)
            va_actual = va_df["forward_return"].values

            _, fold_ic, fold_icir = self._compute_ic_metrics(
                va_df, va_scores, va_actual,
            )
            fold_ics.append(fold_ic)
            fold_icirs.append(fold_icir)

            logger.info(
                "  Fold %d IC=%.4f, ICIR=%.4f", fold_idx + 1, fold_ic, fold_icir,
            )

            if is_final_fold:
                final_models = models
                final_val_df = va_df
                final_val_scores = va_scores
                final_train_dates = list(tr_dates)
                final_val_dates = list(va_dates)
                final_train_df = tr_df

        # Use final fold's models for deployment; fold ICs for quality gate
        models = final_models
        val_df = final_val_df
        val_scores = final_val_scores
        train_dates = final_train_dates
        val_dates = final_val_dates
        val_actual = val_df["forward_return"].values

        ic_mean = float(np.mean(fold_ics))
        icir = float(np.mean(fold_icirs)) if fold_icirs else 0.0

        task.message = "Ensemble trained, evaluating performance"
        task.progress = 55.0

        # Log per-member IC for the final fold's models
        for i, m in enumerate(models):
            member_scores = m.predict(val_df[feature_cols].values)
            _, member_ic, _ = self._compute_ic_metrics(
                val_df, member_scores, val_actual,
            )
            logger.info("  final member %d IC=%.4f", i, member_ic)

        # Best NDCG: average across final ensemble members
        ndcg_values = []
        for m in models:
            if m.best_score and "valid_0" in m.best_score:
                v = m.best_score["valid_0"]
                ndcg = v.get("ndcg@10", v.get("ndcg@5"))
                if ndcg is not None:
                    ndcg_values.append(ndcg)
        best_ndcg = float(np.mean(ndcg_values)) if ndcg_values else None

        best_iters = [
            m.best_iteration if m.best_iteration >= 0
            else _get_boost_round(market)
            for m in models
        ]
        logger.info(
            "Walk-forward summary (%d folds, %d members): "
            "fold_ICs=%s, mean_IC=%.4f, mean_ICIR=%.4f, "
            "NDCG@10=%s, best_iters=%s",
            len(splits), ensemble_size,
            [f"{ic:.4f}" for ic in fold_ics],
            ic_mean, icir,
            f"{best_ndcg:.4f}" if best_ndcg is not None else "N/A",
            best_iters,
        )

        # Quality gate: per-market thresholds from MarketConfig
        min_ic = cfg.min_ic_threshold
        min_icir = cfg.min_icir_threshold
        quality_passed = (ic_mean > min_ic and icir > min_icir)

        if not quality_passed:
            logger.warning(
                "Model quality gate FAILED: mean_IC=%.4f (min=%.4f), "
                "mean_ICIR=%.4f (min=%.4f). "
                "Model will be saved but marked as failed.",
                ic_mean, min_ic,
                icir, min_icir,
            )
        else:
            logger.info(
                "Model quality gate passed: mean_IC=%.4f, mean_ICIR=%.4f",
                ic_mean, icir,
            )

        # Extract feature importance (gain-based average across final ensemble)
        feature_importance: dict[str, float] = {}
        try:
            importance_arrays = [
                m.feature_importance(importance_type='gain') for m in models
            ]
            importance_values = np.mean(importance_arrays, axis=0)
            if len(importance_values) != len(feature_cols):
                logger.warning(
                    "Feature importance length mismatch: got %d, expected %d",
                    len(importance_values), len(feature_cols),
                )
            feature_importance = dict(
                sorted(
                    zip(feature_cols, (float(v) for v in importance_values)),
                    key=lambda x: x[1],
                    reverse=True,
                )
            )
            logger.info(
                "Top 5 features by gain (ensemble avg): %s",
                list(feature_importance.items())[:5],
            )
        except Exception as e:
            logger.warning("Failed to extract feature importance: %s", e)

        # Save ensemble to disk
        task.message = "Saving model"
        task.progress = 60.0

        model_path = self._save_model(
            models, market, model_date, feature_cols,
            feature_importance=feature_importance,
        )
        logger.info("Model saved to %s", model_path)

        # Save training feature distribution snapshot for PSI comparison at inference
        try:
            self._save_train_distribution(
                final_train_df, feature_cols, os.path.dirname(model_path),
            )
        except Exception as e:
            logger.warning("Failed to save training distribution: %s", e)

        # Record in DB
        task.message = "Recording model metadata"
        task.progress = 65.0

        # Determine feature source flags
        has_fundamental = any(c in feature_cols for c in FUNDAMENTAL_FEATURES)
        has_sentiment = any(c in feature_cols for c in SENTIMENT_FEATURES)
        feature_sources = ["alpha158"]
        if has_fundamental:
            feature_sources.append("fundamental")
        if has_sentiment:
            feature_sources.append("sentiment")

        model_id = await self._record_model(
            market=market,
            model_date=model_date,
            train_start=pd.Timestamp(train_dates[0]).date(),
            train_end=pd.Timestamp(train_dates[-1]).date(),
            val_start=pd.Timestamp(val_dates[0]).date(),
            val_end=pd.Timestamp(val_dates[-1]).date(),
            forward_days=forward_days,
            feature_count=len(feature_cols),
            symbol_count=final_train_df["symbol"].nunique(),
            feature_sources=feature_sources,
            ic=ic_mean,
            icir=icir,
            ndcg=best_ndcg,
            model_path=model_path,
            feature_importance=feature_importance,
            quality_passed=quality_passed,
            extra_metadata={
                "ensemble_size": ensemble_size,
                "walkforward_folds": len(splits),
                "fold_ics": [round(ic, 6) for ic in fold_ics],
                "fold_icirs": [round(ir, 6) for ir in fold_icirs],
            },
        )

        task.progress = 70.0
        logger.info(
            "Model recorded: model_id=%s, market=%s, quality_passed=%s",
            model_id, market, quality_passed,
        )

        return model_id, model_path, quality_passed

    @staticmethod
    def _walk_forward_splits(
        unique_dates: list,
        n_folds: int,
        forward_days: int,
    ) -> list[tuple[list, list]]:
        """Generate expanding-window walk-forward splits with purge gap.

        Layout for n_folds=3 over T dates (val_size = T // 5):
          Fold 1: [d0 .. d_t1] train | purge | [d_v1 .. d_v1+vs] val
          Fold 2: [d0 .. d_t2] train | purge | [d_v2 .. d_v2+vs] val  (expanding)
          Fold 3: [d0 .. d_t3] train | purge | [d_v3 .. d_v3+vs] val  (expanding)

        The last fold uses the most recent data as validation and has the
        largest training set.  Its trained model is deployed for inference.

        Returns:
            List of (train_dates, val_dates) tuples.
        """
        total = len(unique_dates)
        if n_folds <= 1:
            # Fallback: single 80/20 split
            split_idx = int(total * 0.8)
            if split_idx < _MIN_TRAIN_DATES:
                return []
            val_start = min(split_idx + forward_days, total - 1)
            train_dates = unique_dates[:split_idx]
            val_dates = unique_dates[val_start:]
            if len(val_dates) < 5:
                return []
            return [(train_dates, val_dates)]

        val_size = max(total // (n_folds + 2), 10)
        splits: list[tuple[list, list]] = []

        for i in range(n_folds):
            val_end_idx = total - (n_folds - 1 - i) * val_size
            val_start_idx = val_end_idx - val_size
            train_end_idx = val_start_idx - forward_days  # purge gap

            if train_end_idx < _MIN_TRAIN_DATES:
                continue
            if val_start_idx < 0 or val_end_idx > total:
                continue

            train_dates = unique_dates[:train_end_idx]
            val_dates = unique_dates[val_start_idx:val_end_idx]

            if len(val_dates) < 5:
                continue

            splits.append((train_dates, val_dates))

        return splits

    @staticmethod
    def _train_lgb_sync(
        train_set: lgb.Dataset,
        val_set: lgb.Dataset,
        market: str = "us",
    ) -> lgb.Booster:
        """Synchronous LightGBM training (runs in thread via asyncio.to_thread).

        LightGBM's C core releases the GIL during computation, so this
        does not meaningfully block other asyncio tasks.
        """
        params = _get_lgb_params(market)
        num_boost_round = _get_boost_round(market)
        early_stopping = _get_early_stopping(market)

        callbacks = [
            lgb.early_stopping(early_stopping),
            lgb.log_evaluation(period=50),
        ]

        model = lgb.train(
            params,
            train_set,
            valid_sets=[val_set],
            valid_names=["valid_0"],
            num_boost_round=num_boost_round,
            callbacks=callbacks,
        )
        return model

    @staticmethod
    def _train_ensemble_sync(
        train_set: lgb.Dataset,
        val_set: lgb.Dataset,
        market: str = "us",
        ensemble_size: int = 5,
    ) -> list[lgb.Booster]:
        """Train an ensemble of LightGBM models with different seeds.

        Each member uses a unique (seed, feature_fraction_seed, bagging_seed)
        triplet to diversify feature/bagging subsampling.  Predictions from all
        members should be averaged for more stable IC.

        lgb.Dataset objects are safe to reuse across multiple lgb.train() calls
        because LightGBM copies the data internally when constructing bins.
        """
        seeds = _ENSEMBLE_SEEDS[:ensemble_size]
        models: list[lgb.Booster] = []

        for i, seed in enumerate(seeds):
            logger.info(
                "Training ensemble member %d/%d (seed=%d) for %s",
                i + 1, ensemble_size, seed, market,
            )
            params = _get_lgb_params(market)
            params["seed"] = seed
            params["feature_fraction_seed"] = seed
            params["bagging_seed"] = seed

            num_boost_round = _get_boost_round(market)
            early_stopping = _get_early_stopping(market)
            callbacks = [
                lgb.early_stopping(early_stopping),
                lgb.log_evaluation(period=50),
            ]

            model = lgb.train(
                params,
                train_set,
                valid_sets=[val_set],
                valid_names=["valid_0"],
                num_boost_round=num_boost_round,
                callbacks=callbacks,
            )
            models.append(model)
            logger.info(
                "Ensemble member %d/%d done: best_iter=%d",
                i + 1, ensemble_size,
                model.best_iteration
                if model.best_iteration >= 0
                else num_boost_round,
            )

        return models

    @staticmethod
    def _compute_ic_metrics(
        val_df: pd.DataFrame,
        predicted_scores: np.ndarray,
        actual_returns: np.ndarray,
    ) -> tuple[pd.Series, float, float]:
        """Compute Information Coefficient and Information Ratio.

        IC = per-date Spearman rank correlation between predicted and actual.
        ICIR = IC.mean() / IC.std()

        Returns:
            (ic_series, ic_mean, icir)
        """
        temp = val_df[["date"]].copy()
        temp["pred"] = predicted_scores
        temp["actual"] = actual_returns

        # Per-date rank correlation
        ic_per_date = temp.groupby("date").apply(
            lambda g: g["pred"].corr(g["actual"], method="spearman")
            if len(g) >= 5
            else np.nan,
            include_groups=False,
        )
        ic_per_date = ic_per_date.dropna()

        if len(ic_per_date) == 0:
            return pd.Series(dtype=float), 0.0, 0.0

        ic_mean = float(ic_per_date.mean())
        ic_std = float(ic_per_date.std())
        icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0

        return ic_per_date, ic_mean, icir

    @staticmethod
    def _save_model(
        models: list[lgb.Booster] | lgb.Booster,
        market: str,
        model_date: date,
        feature_cols: list[str],
        feature_importance: dict[str, float] | None = None,
    ) -> str:
        """Save ensemble (or single model) + feature metadata to disk.

        Directory: {PREDICTION_DATA_DIR}/{market}/{YYYYMMDD}/
        Files: model.pkl, features.json (with optional feature_importance)

        Returns:
            Absolute path to model.pkl.
        """
        # Normalize to list for consistent serialization
        if isinstance(models, lgb.Booster):
            models = [models]

        settings = get_settings()
        date_str = model_date.strftime("%Y%m%d")
        model_dir = Path(settings.PREDICTION_DATA_DIR) / market / date_str
        model_dir.mkdir(parents=True, exist_ok=True)

        model_path = str(model_dir / "model.pkl")
        try:
            joblib.dump(models, model_path)
        except Exception as e:
            raise RuntimeError(
                f"Failed to save model to {model_path}: {e}"
            ) from e

        if not os.path.exists(model_path) or os.path.getsize(model_path) == 0:
            raise RuntimeError(
                f"Model file missing or empty after save: {model_path}"
            )

        # Save feature list alongside model for reproducibility
        features_meta: dict[str, Any] = {
            "features": feature_cols,
            "count": len(feature_cols),
            "ensemble_size": len(models),
        }
        if feature_importance is not None:
            features_meta["feature_importance"] = feature_importance

        features_path = str(model_dir / "features.json")
        try:
            with open(features_path, "w") as f:
                json.dump(features_meta, f, default=_numpy_default)
        except Exception as e:
            raise RuntimeError(
                f"Failed to save features.json to {features_path}: {e}"
            ) from e

        return model_path

    @staticmethod
    def _save_train_distribution(
        train_df: pd.DataFrame,
        feature_cols: list[str],
        model_dir: str,
    ) -> None:
        """Save training feature distribution snapshot for PSI comparison.

        Stores percentile-based bin edges per feature (10 bins) so that
        inference can compare its distribution against training.
        """
        dist: dict[str, list[float]] = {}
        percentiles = np.linspace(0, 100, 11).tolist()  # 0,10,20,...,100

        for col in feature_cols:
            if col not in train_df.columns:
                continue
            vals = train_df[col].dropna().values
            if len(vals) < 20:
                continue
            dist[col] = [float(v) for v in np.percentile(vals, percentiles)]

        path = os.path.join(model_dir, "train_distribution.json")
        with open(path, "w") as f:
            json.dump(dist, f, default=_numpy_default)
        logger.info("Saved training distribution snapshot: %d features", len(dist))

    @staticmethod
    def _compute_inference_psi(
        inference_df: pd.DataFrame,
        feature_cols: list[str],
        model_dir: str,
    ) -> dict[str, float] | None:
        """Compute PSI between training distribution and inference features.

        Returns:
            {feature: psi_score} dict, or None if no training snapshot.
        """
        dist_path = os.path.join(model_dir, "train_distribution.json")
        if not os.path.exists(dist_path):
            return None

        with open(dist_path) as f:
            train_dist = json.load(f)

        _EPS = 1e-6
        psi_scores: dict[str, float] = {}

        for col in feature_cols:
            if col not in train_dist or col not in inference_df.columns:
                continue

            bin_edges = train_dist[col]  # 11 percentile values (0,10,...,100)
            if len(bin_edges) < 3:
                continue

            infer_vals = inference_df[col].dropna().values
            if len(infer_vals) < 10:
                continue

            # Use training percentiles as bin edges
            edges = np.array(bin_edges)
            edges[0] = -np.inf
            edges[-1] = np.inf

            # Expected proportions: uniform 10% per bin (from training)
            n_bins = len(edges) - 1
            expected = np.full(n_bins, 1.0 / n_bins) + _EPS

            # Actual proportions from inference data
            counts = np.histogram(infer_vals, bins=edges)[0]
            actual = counts / len(infer_vals) + _EPS

            # PSI = Σ (actual - expected) × ln(actual / expected)
            psi = float(np.sum((actual - expected) * np.log(actual / expected)))
            psi_scores[col] = round(psi, 6)

        return psi_scores if psi_scores else None

    async def _record_model(
        self,
        market: str,
        model_date: date,
        train_start: date,
        train_end: date,
        val_start: date,
        val_end: date,
        forward_days: int,
        feature_count: int,
        symbol_count: int,
        feature_sources: list[str],
        ic: float,
        icir: float,
        ndcg: Optional[float],
        model_path: str,
        feature_importance: dict[str, float] | None = None,
        quality_passed: bool = True,
        extra_metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Write model metadata to prediction_models table. Returns model id."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            raise RuntimeError("DB pool not available for model recording")

        metadata: dict[str, Any] = {
            "lgb_params": _get_lgb_params(market),
            "num_boost_round": _get_boost_round(market),
            "early_stopping": _get_early_stopping(market),
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        # Include top 30 features by importance (avoid bloating JSONB)
        if feature_importance:
            top_items = list(feature_importance.items())[:30]
            metadata["feature_importance_top30"] = dict(top_items)

        metadata_json = json.dumps(metadata, default=_numpy_default)

        try:
            async with pool.acquire(timeout=10) as conn:
                model_id = await conn.fetchval(
                    _SQL_INSERT_MODEL,
                    market,             # $1
                    model_date,         # $2
                    train_start,        # $3
                    train_end,          # $4
                    val_start,          # $5
                    val_end,            # $6
                    forward_days,       # $7
                    feature_count,      # $8
                    symbol_count,       # $9
                    feature_sources,    # $10 TEXT[]
                    float(ic),          # $11
                    float(icir),        # $12
                    float(ndcg) if ndcg is not None else None,  # $13
                    model_path,         # $14
                    metadata_json,      # $15 JSONB
                    quality_passed,     # $16
                )
            return model_id
        except Exception as e:
            logger.error("Failed to record model in DB: %s", e)
            raise RuntimeError(f"Model DB recording failed: {e}") from e

    # ------------------------------------------------------------------
    # Step 4: Inference
    # ------------------------------------------------------------------

    async def _run_inference(
        self,
        task: PredictionTask,
        market: str,
        symbols: list[str],
        model_id: Any,
        model_path: Optional[str],
        forward_days: int,
        prediction_date: date,
    ) -> int:
        """Run inference: load model, predict, rank, write results.

        Returns:
            Number of predictions written.
        """
        # Load model
        if model_path is None or not os.path.exists(model_path):
            # Try to find latest model from DB
            model_path = await self._find_latest_model_path(market, forward_days)

        if model_path is None or not os.path.exists(model_path):
            raise RuntimeError(
                f"No model file found for market={market}, forward_days={forward_days}"
            )

        task.message = "Loading model"
        task.progress = 78.0

        loaded = await asyncio.to_thread(joblib.load, model_path)
        # Backward compatible: old single-model files are lgb.Booster,
        # new ensemble files are list[lgb.Booster]
        if isinstance(loaded, list):
            models = loaded
        else:
            models = [loaded]
        logger.info(
            "Model loaded from %s (%d ensemble members)", model_path, len(models),
        )

        # Load feature names
        features_path = os.path.join(os.path.dirname(model_path), "features.json")

        def _load_feature_meta() -> Optional[dict]:
            if not os.path.exists(features_path):
                return None
            with open(features_path) as f:
                return json.load(f)

        feature_meta = await asyncio.to_thread(_load_feature_meta)
        if feature_meta is not None:
            feature_cols = feature_meta["features"]
        else:
            # Fallback: use all available feature names from feature_service
            feature_cols = feature_service.get_feature_names()
            logger.warning(
                "features.json not found, using default feature list (%d)",
                len(feature_cols),
            )

        # Build feature matrix for the latest available date
        task.message = "Building inference features"
        task.progress = 82.0

        # Use a narrow window (last 90 days) to ensure we have the latest date
        inference_end = prediction_date.isoformat()
        inference_start = (prediction_date - timedelta(days=90)).isoformat()

        inference_df = await feature_service.build_feature_matrix(
            market=market,
            symbols=symbols,
            start_date=inference_start,
            end_date=inference_end,
        )

        if inference_df.empty:
            raise RuntimeError(
                "Inference feature matrix is empty. No data for latest date."
            )

        inference_df["date"] = pd.to_datetime(inference_df["date"])

        # Pick the latest date that has adequate symbol coverage.
        # Using max() alone is fragile: if Qlib data sync is partial,
        # a few symbols may have a newer date than the rest, causing
        # the filter to drop >99% of symbols.
        date_symbol_counts = (
            inference_df.groupby("date")["symbol"].nunique().sort_index()
        )
        max_date = date_symbol_counts.index.max()
        max_date_count = date_symbol_counts.loc[max_date]
        total_symbols = inference_df["symbol"].nunique()

        settings = get_settings()
        min_coverage = settings.INFERENCE_MIN_COVERAGE

        if max_date_count >= total_symbols * min_coverage:
            latest_date = max_date
        else:
            # Max date is sparse — fall back to the most recent date
            # that meets the minimum coverage threshold
            threshold = total_symbols * min_coverage
            candidates = date_symbol_counts[date_symbol_counts >= threshold]
            if candidates.empty:
                best_pct = max_date_count / total_symbols * 100
                raise RuntimeError(
                    f"Insufficient symbol coverage for inference: "
                    f"best date has {max_date_count}/{total_symbols} symbols "
                    f"({best_pct:.0f}%). Need >= {min_coverage * 100:.0f}%."
                )
            else:
                latest_date = candidates.index.max()
            logger.warning(
                "Max date %s has only %d/%d symbols (%.0f%%). "
                "Using %s (%d symbols) instead.",
                max_date.strftime("%Y-%m-%d"),
                max_date_count,
                total_symbols,
                max_date_count / total_symbols * 100,
                latest_date.strftime("%Y-%m-%d"),
                date_symbol_counts.loc[latest_date],
            )

        latest_df = inference_df[inference_df["date"] == latest_date].copy()

        logger.info(
            "Inference data: %d symbols for date %s (max_date=%s had %d symbols)",
            len(latest_df),
            latest_date.strftime("%Y-%m-%d"),
            max_date.strftime("%Y-%m-%d"),
            max_date_count,
        )

        if latest_df.empty:
            raise RuntimeError("No data for latest date after filtering")

        # Align feature columns (handle missing/extra columns gracefully)
        available_features = [c for c in feature_cols if c in latest_df.columns]
        missing_features = [c for c in feature_cols if c not in latest_df.columns]

        if missing_features:
            missing_pct = len(missing_features) / len(feature_cols)
            by_category = {
                "alpha158": [f for f in missing_features if f in ALPHA158_FEATURES],
                "fundamental": [f for f in missing_features if f in FUNDAMENTAL_FEATURES],
                "sentiment": [f for f in missing_features if f in SENTIMENT_FEATURES],
                "other": [
                    f for f in missing_features
                    if f not in ALPHA158_FEATURES
                    and f not in FUNDAMENTAL_FEATURES
                    and f not in SENTIMENT_FEATURES
                ],
            }
            if missing_pct > 0.25:
                logger.error(
                    "Inference: %.0f%% features missing (%d/%d) — by category: %s",
                    missing_pct * 100, len(missing_features), len(feature_cols), by_category,
                )
                await self._flag_retrain_needed(market)
            elif missing_pct > 0.10:
                logger.warning(
                    "Inference: %.0f%% features missing (%d/%d) — by category: %s",
                    missing_pct * 100, len(missing_features), len(feature_cols), by_category,
                )
            else:
                logger.warning(
                    "Missing %d features in inference data (will be filled with NaN): %s",
                    len(missing_features),
                    missing_features[:10],
                )
            for col in missing_features:
                latest_df[col] = np.nan

        X_inference = latest_df[feature_cols].values

        # Feature drift detection (PSI)
        model_dir = os.path.dirname(model_path)
        psi_scores = self._compute_inference_psi(latest_df, feature_cols, model_dir)
        if psi_scores:
            high_drift = {k: v for k, v in psi_scores.items() if v > 0.2}
            moderate_drift = {k: v for k, v in psi_scores.items() if 0.1 < v <= 0.2}
            if high_drift:
                logger.warning(
                    "HIGH feature drift (PSI>0.2) in %d features: %s",
                    len(high_drift),
                    dict(sorted(high_drift.items(), key=lambda x: -x[1])[:5]),
                )
            if moderate_drift:
                logger.info(
                    "Moderate feature drift (0.1<PSI≤0.2) in %d features",
                    len(moderate_drift),
                )
            task._psi_data = {
                "high_drift_count": len(high_drift),
                "moderate_drift_count": len(moderate_drift),
                "top_drifted": dict(
                    sorted(psi_scores.items(), key=lambda x: -x[1])[:10]
                ),
            }

        # Predict (ensemble average)
        task.message = "Generating predictions"
        task.progress = 88.0

        def _ensemble_predict() -> np.ndarray:
            scores_list = [m.predict(X_inference) for m in models]
            return np.mean(scores_list, axis=0)

        scores = await asyncio.to_thread(_ensemble_predict)

        # Compute percentile ranks (0.0 to 1.0)
        ranks = pd.Series(scores).rank(pct=True).values

        # Determine direction thresholds
        directions = []
        for rank_val in ranks:
            if rank_val >= DIRECTION_UP_THRESHOLD:
                directions.append("up")
            elif rank_val <= DIRECTION_DOWN_THRESHOLD:
                directions.append("down")
            else:
                directions.append("sideways")

        # Build results DataFrame
        results_df = pd.DataFrame(
            {
                "symbol": latest_df["symbol"].values,
                "predicted_score": scores,
                "percentile_rank": ranks,
                "predicted_direction": directions,
            }
        )
        results_df = results_df.sort_values("predicted_score", ascending=False)

        # Write to PostgreSQL
        task.message = "Writing predictions to database"
        task.progress = 92.0

        await self._write_predictions(
            market=market,
            prediction_date=prediction_date,
            model_id=model_id,
            results_df=results_df,
            forward_days=forward_days,
        )

        # Cache in Redis
        task.message = "Caching predictions"
        task.progress = 96.0
        await self._write_prediction_cache(market, results_df, prediction_date)

        return len(results_df)

    async def _find_latest_model_path(
        self, market: str, forward_days: int
    ) -> Optional[str]:
        """Find the latest model path from DB.

        Prefers models that passed quality gate, falls back to any model
        if no quality-passed model exists.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return None

        try:
            async with pool.acquire(timeout=10) as conn:
                # Prefer quality-passed model
                row = await conn.fetchrow(
                    _SQL_GET_LATEST_MODEL_PATH, market, forward_days
                )
                if row and row["model_path"]:
                    return row["model_path"]
                # Fallback: any model if no quality model exists
                row = await conn.fetchrow(
                    _SQL_GET_LATEST_MODEL_PATH_ANY, market, forward_days
                )
                if row and row["model_path"]:
                    logger.warning(
                        "No quality-passed model found, using latest available model"
                    )
                    return row["model_path"]
        except Exception as e:
            logger.warning("Failed to query latest model path: %s", e)

        return None

    async def _find_latest_quality_model(
        self, market: str, forward_days: int
    ) -> Optional[dict]:
        """Find the latest model that passed quality gate."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return None
        try:
            async with pool.acquire(timeout=10) as conn:
                row = await conn.fetchrow(
                    _SQL_GET_LATEST_MODEL_PATH, market, forward_days
                )
                if row:
                    return {"id": row["id"], "model_path": row["model_path"]}
        except Exception as e:
            logger.warning("Failed to find quality model: %s", e)
        return None

    async def update_model_quality(
        self, model_id: str, quality_passed: bool
    ) -> bool:
        """Admin override: update quality_passed flag for a model."""
        from app.core.settings_cache import settings_cache

        try:
            model_uuid = uuid.UUID(model_id)
        except ValueError:
            logger.warning("Invalid model_id format: %s", model_id)
            return False

        pool = settings_cache.pool
        if not pool:
            raise RuntimeError("DB pool not available")
        try:
            async with pool.acquire(timeout=10) as conn:
                result = await conn.fetchval(
                    _SQL_UPDATE_MODEL_QUALITY,
                    quality_passed,
                    model_uuid,
                )
                if result is None:
                    return False
                logger.info(
                    "Model quality updated: id=%s, quality_passed=%s",
                    model_id, quality_passed,
                )
                return True
        except Exception as e:
            logger.error("Failed to update model quality: %s", e)
            raise

    async def get_feature_importance(self, model_id: str) -> Optional[dict]:
        """Get feature importance for a specific model.

        Returns top-30 from DB metadata, plus full importance from disk if available.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return None

        try:
            model_uuid = uuid.UUID(model_id)
        except ValueError:
            return None

        try:
            async with pool.acquire(timeout=10) as conn:
                row = await conn.fetchrow(_SQL_GET_MODEL_DETAIL, model_uuid)

            if row is None:
                return None

            # Extract top-30 from metadata JSONB
            metadata = row["metadata"]
            top30 = {}
            if metadata:
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                top30 = metadata.get("feature_importance_top30", {})

            # Try to load full importance from disk (blocking I/O → offload)
            full_importance = None
            model_path = row["model_path"]
            if model_path:
                features_path = os.path.join(os.path.dirname(model_path), "features.json")

                def _read_features():
                    if not os.path.exists(features_path):
                        return None
                    with open(features_path) as f:
                        return json.load(f).get("feature_importance")

                try:
                    full_importance = await asyncio.to_thread(_read_features)
                except Exception as e:
                    logger.warning("Failed to read features.json: %s", e)

            return {
                "model_id": str(row["id"]),
                "market": row["market"],
                "model_date": row["model_date"].isoformat() if row["model_date"] else None,
                "feature_count": row["feature_count"],
                "top30": top30,
                "full": full_importance,
            }
        except Exception as e:
            logger.error("Failed to get feature importance for model %s: %s", model_id, e)
            return None

    async def get_performance_metrics(self, market: str, days: int = 90) -> dict:
        """Compute model performance metrics over time.

        Returns daily IC, hit rate, top/bottom-10 returns, and spread.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return {"market": market, "days": days, "data_points": 0, "metrics": [], "summary": {}}

        try:
            async with pool.acquire(timeout=30) as conn:
                rows = await conn.fetch(_SQL_PERFORMANCE_METRICS, market, days)
        except Exception as e:
            logger.error("Failed to fetch performance data: %s", e)
            return {"market": market, "days": days, "data_points": 0, "metrics": [], "summary": {}}

        if not rows:
            return {"market": market, "days": days, "data_points": 0, "metrics": [], "summary": {}}

        # Convert to DataFrame for efficient computation
        df = pd.DataFrame([dict(r) for r in rows])
        df["actual_return"] = df["actual_return"].astype(float)
        df["predicted_score"] = df["predicted_score"].astype(float)

        metrics_by_date = []
        for dt, group in df.groupby("prediction_date"):
            entry = {"date": dt.isoformat() if hasattr(dt, "isoformat") else str(dt)}

            # Daily IC: Spearman correlation
            if len(group) >= 5:
                ic_val = group["predicted_score"].corr(group["actual_return"], method="spearman")
                entry["ic"] = round(float(ic_val), 6) if pd.notna(ic_val) else None
            else:
                entry["ic"] = None

            # Hit Rate: % of "up" predictions with positive actual_return
            up_preds = group[group["predicted_direction"] == "up"]
            if len(up_preds) > 0:
                entry["hit_rate"] = round(float((up_preds["actual_return"] > 0).mean()), 4)
            else:
                entry["hit_rate"] = None

            # Top-10 and Bottom-10 average returns
            n_top = min(10, len(group))
            top_n = group.nlargest(n_top, "predicted_score")
            bottom_n = group.nsmallest(n_top, "predicted_score")

            entry["top10_return"] = _safe_round(float(top_n["actual_return"].mean()), 6)
            entry["bottom10_return"] = _safe_round(float(bottom_n["actual_return"].mean()), 6)
            entry["spread"] = _safe_round(
                (entry["top10_return"] or 0) - (entry["bottom10_return"] or 0), 6
            )
            entry["symbol_count"] = len(group)

            metrics_by_date.append(entry)

        # Summary statistics
        ic_values = [m["ic"] for m in metrics_by_date if m["ic"] is not None]
        hit_values = [m["hit_rate"] for m in metrics_by_date if m["hit_rate"] is not None]
        spread_values = [m["spread"] for m in metrics_by_date if m["spread"] is not None]

        # Long-Short Sharpe ratio and max drawdown
        ls_sharpe = None
        max_drawdown = None
        if spread_values and len(spread_values) >= 5:
            spreads_arr = np.array(spread_values)
            spread_std = float(np.std(spreads_arr))
            if spread_std > 1e-10:
                # Annualize assuming forward_days=5 ≈ weekly rebalancing
                ls_sharpe = _safe_round(
                    float(np.mean(spreads_arr)) / spread_std * np.sqrt(252 / 5),
                    4,
                )
            # Max drawdown of cumulative long-short spread
            cum_spread = np.cumsum(spreads_arr)
            running_max = np.maximum.accumulate(cum_spread)
            drawdowns = cum_spread - running_max
            max_drawdown = _safe_round(float(np.min(drawdowns)), 6)

        # Quintile return attribution
        quintile_returns = {}
        for q in range(5):
            lo = q * 0.2
            hi = (q + 1) * 0.2
            q_mask = (
                df["percentile_rank"].astype(float).between(lo, hi, inclusive="left")
                if q < 4
                else df["percentile_rank"].astype(float).between(lo, hi, inclusive="both")
            )
            q_rows = df[q_mask]
            if len(q_rows) > 0:
                quintile_returns[f"Q{q + 1}"] = _safe_round(
                    float(q_rows["actual_return"].mean()), 6,
                )

        summary = {
            "avg_ic": _safe_round(float(np.mean(ic_values)), 6) if ic_values else None,
            "ic_std": _safe_round(float(np.std(ic_values)), 6) if ic_values else None,
            "icir": _safe_round(
                float(np.mean(ic_values)) / float(np.std(ic_values)), 4,
            ) if ic_values and float(np.std(ic_values)) > 1e-10 else None,
            "avg_hit_rate": _safe_round(float(np.mean(hit_values)), 4) if hit_values else None,
            "avg_spread": _safe_round(float(np.mean(spread_values)), 6) if spread_values else None,
            "ls_sharpe": ls_sharpe,
            "max_drawdown": max_drawdown,
            "quintile_returns": quintile_returns,
            "total_dates": len(metrics_by_date),
            "total_predictions": len(df),
        }

        return {
            "market": market,
            "days": days,
            "data_points": len(metrics_by_date),
            "metrics": metrics_by_date,
            "summary": summary,
        }

    async def get_accuracy(self, market: str, days: int = 30) -> dict:
        """Compute prediction accuracy summary.

        Returns total predictions with actual returns, direction accuracy,
        and average IC/ICIR.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return {
                "market": market, "days": days,
                "total_predictions": 0, "correct_direction": 0,
                "accuracy": 0, "avg_ic": None, "avg_icir": None,
            }

        try:
            async with pool.acquire(timeout=30) as conn:
                rows = await conn.fetch(
                    _SQL_PERFORMANCE_METRICS, market, days,
                )
                # Count pending (no actual_return yet)
                pending_row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM stock_predictions "
                    "WHERE market = $1 AND actual_return IS NULL "
                    "AND prediction_date >= CURRENT_DATE - $2 * INTERVAL '1 day'",
                    market, days,
                )
                pending_count = int(pending_row["cnt"]) if pending_row else 0
        except Exception as e:
            logger.error("Failed to query accuracy data: %s", e)
            return {
                "market": market, "days": days,
                "total_predictions": 0, "correct_direction": 0,
                "accuracy": 0, "avg_ic": None, "avg_icir": None,
                "pending_count": 0,
            }

        if not rows:
            return {
                "market": market, "days": days,
                "total_predictions": 0, "correct_direction": 0,
                "accuracy": 0, "avg_ic": None, "avg_icir": None,
                "pending_count": pending_count,
            }

        total = len(rows)
        correct = sum(
            1 for r in rows
            if (r["predicted_direction"] == "up" and float(r["actual_return"]) > 0)
            or (r["predicted_direction"] == "down" and float(r["actual_return"]) < 0)
        )
        accuracy = correct / total if total > 0 else 0

        # Daily IC for avg/std
        df = pd.DataFrame([dict(r) for r in rows])
        df["actual_return"] = df["actual_return"].astype(float)
        df["predicted_score"] = df["predicted_score"].astype(float)
        ic_values = []
        for _, group in df.groupby("prediction_date"):
            if len(group) >= 5:
                ic_val = group["predicted_score"].corr(
                    group["actual_return"], method="spearman",
                )
                if pd.notna(ic_val):
                    ic_values.append(float(ic_val))

        avg_ic = _safe_round(float(np.mean(ic_values)), 6) if ic_values else None
        ic_std = float(np.std(ic_values)) if ic_values else 0
        avg_icir = (
            _safe_round(float(np.mean(ic_values)) / ic_std, 4)
            if ic_values and ic_std > 1e-10
            else None
        )

        return {
            "market": market,
            "days": days,
            "total_predictions": total,
            "correct_direction": correct,
            "accuracy": _safe_round(accuracy, 4),
            "avg_ic": avg_ic,
            "avg_icir": avg_icir,
            "pending_count": pending_count,
        }

    async def get_turnover_metrics(
        self, market: str, days: int = 90, top_n: int = 20,
    ) -> dict:
        """Compute prediction rank stability and top-N retention metrics.

        Measures how much the prediction rankings change between consecutive
        prediction dates.  High turnover implies high transaction costs.

        Returns:
            avg_rank_autocorr: Spearman correlation of ranks between t and t-1
            avg_topN_retention: fraction of yesterday's top-N still in today's top-N
            daily: per-date details
        """
        from scipy.stats import spearmanr
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return {"market": market, "days": days, "data_points": 0, "summary": {}, "daily": []}

        try:
            async with pool.acquire(timeout=30) as conn:
                rows = await conn.fetch(
                    "SELECT prediction_date, symbol, predicted_score "
                    "FROM stock_predictions "
                    "WHERE market = $1 AND prediction_date >= CURRENT_DATE - $2 * INTERVAL '1 day' "
                    "ORDER BY prediction_date, predicted_score DESC",
                    market, days,
                )
        except Exception as e:
            logger.error("Failed to fetch turnover data: %s", e)
            return {"market": market, "days": days, "data_points": 0, "summary": {}, "daily": []}

        if not rows:
            return {"market": market, "days": days, "data_points": 0, "summary": {}, "daily": []}

        df = pd.DataFrame([dict(r) for r in rows])
        df["predicted_score"] = df["predicted_score"].astype(float)

        dates_sorted = sorted(df["prediction_date"].unique())
        daily_metrics: list[dict] = []
        prev_date_data: dict | None = None

        for dt in dates_sorted:
            dt_df = df[df["prediction_date"] == dt].sort_values(
                "predicted_score", ascending=False,
            )
            symbols = dt_df["symbol"].tolist()
            scores = dt_df["predicted_score"].tolist()
            top_symbols = set(symbols[:top_n])

            entry: dict = {"date": dt.isoformat() if hasattr(dt, "isoformat") else str(dt)}

            if prev_date_data is not None:
                # Rank autocorrelation: align on common symbols
                prev_scores = prev_date_data["score_map"]
                common = set(scores_map := dict(zip(symbols, scores))) & set(prev_scores)
                if len(common) >= 5:
                    curr_vals = [scores_map[s] for s in common]
                    prev_vals = [prev_scores[s] for s in common]
                    corr, _ = spearmanr(curr_vals, prev_vals)
                    entry["rank_autocorr"] = _safe_round(float(corr), 4)
                else:
                    entry["rank_autocorr"] = None

                # Top-N retention
                prev_top = prev_date_data["top_symbols"]
                overlap = top_symbols & prev_top
                entry["topN_retention"] = _safe_round(
                    len(overlap) / top_n if top_n > 0 else 0.0, 4,
                )
            else:
                entry["rank_autocorr"] = None
                entry["topN_retention"] = None

            daily_metrics.append(entry)
            prev_date_data = {
                "score_map": dict(zip(symbols, scores)),
                "top_symbols": top_symbols,
            }

        autocorr_vals = [m["rank_autocorr"] for m in daily_metrics if m["rank_autocorr"] is not None]
        retention_vals = [m["topN_retention"] for m in daily_metrics if m["topN_retention"] is not None]

        summary = {
            "avg_rank_autocorr": _safe_round(float(np.mean(autocorr_vals)), 4) if autocorr_vals else None,
            "avg_topN_retention": _safe_round(float(np.mean(retention_vals)), 4) if retention_vals else None,
            "top_n": top_n,
            "total_dates": len(daily_metrics),
        }

        return {
            "market": market,
            "days": days,
            "data_points": len(daily_metrics),
            "summary": summary,
            "daily": daily_metrics,
        }

    async def get_ic_decay(self, market: str, days: int = 90) -> dict:
        """Compute IC at multiple forward horizons (alpha decay curve).

        For each prediction date, computes Spearman correlation between
        predicted scores and actual returns at t+1, t+3, t+5, t+10, t+20.
        The IC decay curve reveals signal half-life and optimal rebalancing freq.
        """
        from app.core.settings_cache import settings_cache

        _DECAY_HORIZONS = [1, 3, 5, 10, 20]
        pool = settings_cache.pool
        if not pool:
            return {"market": market, "horizons": {}, "data_points": 0}

        try:
            async with pool.acquire(timeout=30) as conn:
                # Get predictions with symbols and dates
                pred_rows = await conn.fetch(
                    "SELECT prediction_date, symbol, predicted_score "
                    "FROM stock_predictions "
                    "WHERE market = $1 "
                    "AND prediction_date >= CURRENT_DATE - ($2 + 30) * INTERVAL '1 day' "
                    "ORDER BY prediction_date",
                    market, days,
                )
                if not pred_rows:
                    return {"market": market, "horizons": {}, "data_points": 0}

                # Get price data for return computation
                price_rows = await conn.fetch(
                    "SELECT symbol, date, close "
                    "FROM stock_daily_bars "
                    "WHERE market = $1 "
                    "AND date >= (SELECT MIN(prediction_date) FROM stock_predictions "
                    "             WHERE market = $1) "
                    "ORDER BY symbol, date",
                    market,
                )
        except Exception as e:
            logger.error("Failed to fetch IC decay data: %s", e)
            return {"market": market, "horizons": {}, "data_points": 0}

        if not price_rows:
            return {"market": market, "horizons": {}, "data_points": 0}

        # Build price lookup: {symbol: [(date, close), ...]}
        price_df = pd.DataFrame([dict(r) for r in price_rows])
        price_df["close"] = price_df["close"].astype(float)
        price_df = price_df.sort_values(["symbol", "date"])

        # For each symbol, create a date-indexed series for fast lookups
        symbol_prices: dict[str, pd.Series] = {}
        for sym, grp in price_df.groupby("symbol"):
            symbol_prices[str(sym)] = grp.set_index("date")["close"]

        pred_df = pd.DataFrame([dict(r) for r in pred_rows])
        pred_df["predicted_score"] = pred_df["predicted_score"].astype(float)

        # Compute IC at each horizon
        horizon_ics: dict[int, list[float]] = {h: [] for h in _DECAY_HORIZONS}

        for dt, group in pred_df.groupby("prediction_date"):
            if len(group) < 10:
                continue

            for horizon in _DECAY_HORIZONS:
                returns = []
                scores = []
                for _, row in group.iterrows():
                    sym = str(row["symbol"])
                    if sym not in symbol_prices:
                        continue
                    prices = symbol_prices[sym]
                    # Find the price on prediction_date and prediction_date + horizon trading days
                    pred_date = row["prediction_date"]
                    future_dates = prices.index[prices.index > pred_date]
                    if len(future_dates) < horizon:
                        continue
                    p0_dates = prices.index[prices.index <= pred_date]
                    if len(p0_dates) == 0:
                        continue
                    p0 = prices.loc[p0_dates[-1]]
                    p1 = prices.loc[future_dates[horizon - 1]]
                    if p0 > 0:
                        returns.append(float(p1 / p0 - 1))
                        scores.append(float(row["predicted_score"]))

                if len(returns) >= 10:
                    from scipy.stats import spearmanr
                    corr, _ = spearmanr(scores, returns)
                    if not np.isnan(corr):
                        horizon_ics[horizon].append(float(corr))

        # Summarize
        horizons_summary = {}
        for h in _DECAY_HORIZONS:
            ics = horizon_ics[h]
            if ics:
                horizons_summary[h] = {
                    "avg_ic": _safe_round(float(np.mean(ics)), 6),
                    "ic_std": _safe_round(float(np.std(ics)), 6),
                    "n_dates": len(ics),
                }

        return {
            "market": market,
            "days": days,
            "horizons": horizons_summary,
            "data_points": sum(len(v) for v in horizon_ics.values()),
        }

    # ------------------------------------------------------------------
    # Return Attribution
    # ------------------------------------------------------------------

    async def get_return_attribution(
        self, market: str, days: int = 90, top_n: int = 20,
    ) -> dict:
        """Decompose Top-N portfolio returns into sector, size, and alpha.

        Uses a simplified Brinson-style attribution:
        - Sector attr = Σ (port_sector_wt - univ_sector_wt) × sector_return
        - Size attr   = Σ (port_size_wt - univ_size_wt) × size_return
        - Alpha       = portfolio_return - sector_attr - size_attr
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        empty = {
            "market": market, "days": days, "top_n": top_n,
            "data_points": 0, "daily": [], "summary": {},
        }
        if not pool:
            return empty

        try:
            async with pool.acquire(timeout=30) as conn:
                pred_rows = await conn.fetch(
                    _SQL_PERFORMANCE_METRICS, market, days,
                )
                cap_rows = await conn.fetch(_SQL_MARKET_CAP_LATEST, market)
        except Exception as e:
            logger.error("Failed to fetch attribution data: %s", e)
            return empty

        if not pred_rows:
            return empty

        # Build lookups
        cap_map: dict[str, float] = {
            r["symbol"]: float(r["market_cap"]) for r in cap_rows
            if r["market_cap"] is not None
        }
        all_symbols = list({str(r["symbol"]) for r in pred_rows})
        sector_map = await fundamental_service.get_sector_map(market, all_symbols)

        df = pd.DataFrame([dict(r) for r in pred_rows])
        df["actual_return"] = df["actual_return"].astype(float)
        df["predicted_score"] = df["predicted_score"].astype(float)

        # Size tercile classification
        if cap_map:
            all_caps = sorted(cap_map.values())
            n = len(all_caps)
            if n >= 3:
                t1 = all_caps[n // 3]
                t2 = all_caps[2 * n // 3]
            else:
                t1, t2 = 0, float("inf")

            def _size_bucket(sym: str) -> str:
                mc = cap_map.get(sym)
                if mc is None:
                    return "unknown"
                if mc <= t1:
                    return "small"
                if mc <= t2:
                    return "mid"
                return "large"
        else:
            def _size_bucket(sym: str) -> str:
                return "unknown"

        daily_attribution: list[dict] = []
        for dt, group in df.groupby("prediction_date"):
            n_group = len(group)
            if n_group < top_n:
                continue

            portfolio = group.nlargest(top_n, "predicted_score")
            port_ret = float(portfolio["actual_return"].mean())
            univ_ret = float(group["actual_return"].mean())

            # --- Sector attribution ---
            sect_attr = 0.0
            if sector_map:
                port_sectors = portfolio["symbol"].map(sector_map).dropna()
                univ_sectors = group["symbol"].map(sector_map).dropna()

                if len(port_sectors) > 0 and len(univ_sectors) > 0:
                    port_sw = port_sectors.value_counts(normalize=True)
                    univ_sw = univ_sectors.value_counts(normalize=True)
                    all_sects = set(port_sw.index) | set(univ_sw.index)

                    # Sector returns from universe
                    group_copy = group.copy()
                    group_copy["_sect"] = group_copy["symbol"].map(sector_map)
                    sect_rets = group_copy.groupby("_sect")["actual_return"].mean()

                    for s in all_sects:
                        pw = port_sw.get(s, 0.0)
                        uw = univ_sw.get(s, 0.0)
                        sr = sect_rets.get(s, 0.0)
                        sect_attr += (pw - uw) * sr

            # --- Size attribution ---
            size_attr = 0.0
            if cap_map:
                port_sizes = portfolio["symbol"].map(_size_bucket)
                univ_sizes = group["symbol"].map(_size_bucket)

                port_sw = port_sizes.value_counts(normalize=True)
                univ_sw = univ_sizes.value_counts(normalize=True)

                group_copy = group.copy()
                group_copy["_size"] = group_copy["symbol"].map(_size_bucket)
                size_rets = group_copy.groupby("_size")["actual_return"].mean()

                for sz in {"small", "mid", "large"}:
                    pw = port_sw.get(sz, 0.0)
                    uw = univ_sw.get(sz, 0.0)
                    sr = size_rets.get(sz, 0.0)
                    size_attr += (pw - uw) * sr

            alpha = port_ret - sect_attr - size_attr

            daily_attribution.append({
                "date": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
                "portfolio_return": _safe_round(port_ret, 6),
                "universe_return": _safe_round(univ_ret, 6),
                "sector_attr": _safe_round(sect_attr, 6),
                "size_attr": _safe_round(size_attr, 6),
                "alpha": _safe_round(alpha, 6),
            })

        # Summary
        summary: dict[str, Any] = {}
        if daily_attribution:
            port_rets = [d["portfolio_return"] or 0 for d in daily_attribution]
            sect_attrs = [d["sector_attr"] or 0 for d in daily_attribution]
            size_attrs = [d["size_attr"] or 0 for d in daily_attribution]
            alphas = [d["alpha"] or 0 for d in daily_attribution]

            total_ret = sum(port_rets)
            total_sect = sum(sect_attrs)
            total_size = sum(size_attrs)
            total_alpha = sum(alphas)

            # Percentages (of absolute total contribution)
            abs_total = abs(total_sect) + abs(total_size) + abs(total_alpha)
            summary = {
                "total_return": _safe_round(total_ret, 6),
                "sector_pct": _safe_round(
                    total_sect / abs_total * 100 if abs_total > 1e-10 else 0, 1,
                ),
                "size_pct": _safe_round(
                    total_size / abs_total * 100 if abs_total > 1e-10 else 0, 1,
                ),
                "alpha_pct": _safe_round(
                    total_alpha / abs_total * 100 if abs_total > 1e-10 else 0, 1,
                ),
                "avg_daily_alpha": _safe_round(float(np.mean(alphas)), 6),
                "sector_breakdown": {},
            }

            # Per-sector average contribution
            if sector_map:
                sect_contribs: dict[str, list[float]] = {}
                for d, group_data in zip(daily_attribution, df.groupby("prediction_date")):
                    dt_str, group = d["date"], group_data[1]
                    portfolio = group.nlargest(top_n, "predicted_score")
                    port_sects = portfolio["symbol"].map(sector_map).dropna()
                    for s in port_sects.unique():
                        mask = portfolio["symbol"].map(sector_map) == s
                        contrib = float(portfolio.loc[mask, "actual_return"].mean())
                        sect_contribs.setdefault(s, []).append(contrib)

                summary["sector_breakdown"] = {
                    s: _safe_round(float(np.mean(v)), 6)
                    for s, v in sorted(
                        sect_contribs.items(), key=lambda x: -abs(np.mean(x[1])),
                    )
                }

        return {
            "market": market,
            "days": days,
            "top_n": top_n,
            "data_points": len(daily_attribution),
            "daily": daily_attribution,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Recent predictions (for holdings change analysis)
    # ------------------------------------------------------------------

    async def get_recent_predictions(
        self, market: str, n_dates: int = 2, forward_days: int = 5,
    ) -> dict:
        """Get predictions for the last N prediction dates.

        Used for comparing today's top-N vs yesterday's top-N.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        empty = {"market": market, "forward_days": forward_days, "dates": [], "predictions": {}}
        if not pool:
            return empty

        try:
            async with pool.acquire(timeout=10) as conn:
                date_rows = await conn.fetch(
                    _SQL_RECENT_PREDICTION_DATES, market, forward_days, n_dates,
                )
                if not date_rows:
                    return empty

                dates = [r["prediction_date"] for r in date_rows]
                pred_rows = await conn.fetch(
                    _SQL_PREDICTIONS_BY_DATES, market, forward_days, dates,
                )
        except Exception as e:
            logger.error("Failed to fetch recent predictions: %s", e)
            return empty

        # Group by date
        by_date: dict[str, list[dict]] = {}
        for r in pred_rows:
            dt = r["prediction_date"].isoformat()
            if dt not in by_date:
                by_date[dt] = []
            by_date[dt].append({
                "symbol": r["symbol"],
                "predicted_score": float(r["predicted_score"]),
                "percentile_rank": float(r["percentile_rank"]),
                "predicted_direction": r["predicted_direction"],
                "forward_days": r["forward_days"],
            })

        return {
            "market": market,
            "forward_days": forward_days,
            "dates": [d.isoformat() for d in dates],
            "predictions": by_date,
        }

    # ------------------------------------------------------------------
    # Combined signal from multiple horizons
    # ------------------------------------------------------------------

    async def compute_combined_signal(
        self, market: str, horizons: list[int],
    ) -> int:
        """Average percentile ranks across horizons for the latest prediction date.

        Writes combined predictions with forward_days=0 (convention: 0 = combined).
        Only includes symbols present in ALL horizons (consensus filter).

        Returns number of combined predictions written.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return 0

        try:
            async with pool.acquire(timeout=30) as conn:
                rows = await conn.fetch(
                    "SELECT symbol, forward_days, percentile_rank, predicted_score "
                    "FROM stock_predictions "
                    "WHERE market = $1 "
                    "  AND forward_days = ANY($2::int[]) "
                    "  AND prediction_date = ("
                    "    SELECT MAX(prediction_date) FROM stock_predictions "
                    "    WHERE market = $1 AND forward_days = $3"
                    "  )",
                    market, horizons, horizons[0],
                )
        except Exception as e:
            logger.error("Failed to fetch predictions for combined signal: %s", e)
            return 0

        if not rows:
            logger.info("No predictions found for combined signal: %s", market)
            return 0

        df = pd.DataFrame([dict(r) for r in rows])
        df["percentile_rank"] = df["percentile_rank"].astype(float)
        df["predicted_score"] = df["predicted_score"].astype(float)

        # Average across horizons per symbol
        combined = df.groupby("symbol").agg(
            percentile_rank=("percentile_rank", "mean"),
            predicted_score=("predicted_score", "mean"),
            n_horizons=("forward_days", "nunique"),
        ).reset_index()

        # Only keep symbols present in ALL horizons
        combined = combined[combined["n_horizons"] == len(horizons)]

        if combined.empty:
            logger.info("No consensus symbols across all horizons for %s", market)
            return 0

        # Re-rank combined scores
        combined["percentile_rank"] = combined["predicted_score"].rank(pct=True)
        combined["predicted_direction"] = combined["percentile_rank"].apply(
            lambda x: "up" if x >= DIRECTION_UP_THRESHOLD
            else ("down" if x <= DIRECTION_DOWN_THRESHOLD else "sideways")
        )

        today = date.today()
        insert_rows = [
            (
                market,
                today,
                None,  # model_id: combined signal has no single model
                row["symbol"],
                float(row["predicted_score"]),
                float(row["percentile_rank"]),
                row["predicted_direction"],
                0,  # forward_days=0 = combined signal
            )
            for _, row in combined.iterrows()
        ]

        try:
            async with pool.acquire(timeout=30) as conn:
                await conn.executemany(_SQL_INSERT_PREDICTIONS, insert_rows)
        except Exception as e:
            logger.error("Failed to write combined signal: %s", e)
            return 0

        logger.info(
            "Wrote %d combined signal predictions for %s", len(insert_rows), market,
        )
        return len(insert_rows)

    async def _write_predictions(
        self,
        market: str,
        prediction_date: date,
        model_id: Any,
        results_df: pd.DataFrame,
        forward_days: int,
    ) -> None:
        """Write prediction results to stock_predictions table via asyncpg executemany."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            raise RuntimeError("DB pool not available for writing predictions")

        rows = [
            (
                market,
                prediction_date,
                model_id,
                row["symbol"],
                float(row["predicted_score"]),
                float(row["percentile_rank"]),
                row["predicted_direction"],
                forward_days,
            )
            for _, row in results_df.iterrows()
        ]

        try:
            async with pool.acquire(timeout=30) as conn:
                await conn.executemany(_SQL_INSERT_PREDICTIONS, rows)

            logger.info(
                "Wrote %d predictions to DB: market=%s, date=%s",
                len(rows),
                market,
                prediction_date.isoformat(),
            )
        except Exception as e:
            logger.error("Failed to write predictions to DB: %s", e)
            raise RuntimeError(f"Prediction DB write failed: {e}") from e

    # ------------------------------------------------------------------
    # Close price fetch (for labels)
    # ------------------------------------------------------------------

    async def _fetch_close_prices(
        self,
        market: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch close prices via BackendDataClient.

        Returns DataFrame with columns: symbol, date, close.
        """
        try:
            data = await asyncio.to_thread(
                self._fetch_close_sync, market, symbols, start_date, end_date
            )
        except Exception as e:
            logger.error("Close price fetch failed: %s", e)
            return pd.DataFrame()

        if not data:
            return pd.DataFrame()

        # Convert columnar format to flat DataFrame
        rows = []
        for symbol, bars in data.items():
            dates = bars.get("dates", [])
            closes = bars.get("close", [])
            for d, c in zip(dates, closes):
                if c is not None:
                    rows.append({"symbol": symbol, "date": d, "close": float(c)})

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return df

    @staticmethod
    def _fetch_close_sync(
        market: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> dict:
        """Synchronous close price fetch (for asyncio.to_thread).

        Batches symbols in groups of 30 to match BackendDataClient limits.
        """
        from app.services.backend_client import get_backend_client

        client = get_backend_client()
        batch_size = 30
        all_data: dict = {}

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            try:
                batch_data = client.get_history_batch(
                    symbols=batch,
                    market=market,
                    start_date=start_date,
                    end_date=end_date,
                )
                all_data.update(batch_data)
            except Exception as e:
                logger.warning(
                    "Close price batch %d-%d failed: %s",
                    i,
                    i + len(batch),
                    e,
                )

        return all_data

    # ------------------------------------------------------------------
    # Actual return computation (for backfill)
    # ------------------------------------------------------------------

    async def _compute_actual_returns(
        self,
        symbol: str,
        predictions: list[dict],
        market: str = "us",
    ) -> dict:
        """Compute actual returns for a list of predictions of the same symbol.

        Args:
            symbol: Stock symbol.
            predictions: List of prediction dicts (must include id, prediction_date, forward_days).
            market: Market code for price data retrieval.

        Returns:
            Dict mapping prediction id -> actual_return (float).
        """
        # Determine date range needed
        min_date = min(p["prediction_date"] for p in predictions)
        max_date = max(p["prediction_date"] for p in predictions)
        max_forward = max(p["forward_days"] for p in predictions)

        start_str = min_date.isoformat()
        # Convert trading days to calendar days with margin for weekends/holidays
        calendar_buffer = int(max_forward * 7 / 5) + 15
        end_str = (max_date + timedelta(days=calendar_buffer)).isoformat()

        # Fetch price data
        data = await asyncio.to_thread(
            self._fetch_close_sync,
            market,
            [symbol],
            start_str,
            end_str,
        )

        if symbol not in data:
            return {}

        dates = data[symbol].get("dates", [])
        closes = data[symbol].get("close", [])

        if not dates or not closes:
            return {}

        # Build date -> close mapping and a sorted list of trading dates
        price_map: dict[date, float] = {}
        for d_str, c in zip(dates, closes):
            if c is not None:
                d = pd.Timestamp(d_str).date()
                price_map[d] = float(c)
        trading_dates = sorted(price_map.keys())

        # Build index for O(1) lookup: trading_date -> position in trading_dates
        date_to_idx: dict[date, int] = {d: i for i, d in enumerate(trading_dates)}

        # Compute actual returns using trading-day indexing
        # This matches training which uses shift(-forward_days) on trading-day rows
        results: dict = {}
        for pred in predictions:
            pred_date = pred["prediction_date"]
            fwd = pred["forward_days"]

            base_price = price_map.get(pred_date)
            if base_price is None:
                continue

            # Find the Nth trading day after pred_date
            base_idx = date_to_idx.get(pred_date)
            if base_idx is None:
                continue
            target_idx = base_idx + fwd
            if target_idx >= len(trading_dates):
                continue  # Not enough future data yet

            future_price = price_map[trading_dates[target_idx]]

            if future_price is not None and base_price > 0:
                actual_return = (future_price / base_price) - 1.0
                results[pred["id"]] = actual_return

        return results

    # ------------------------------------------------------------------
    # Redis prediction cache
    # ------------------------------------------------------------------

    async def _read_prediction_cache(self, market: str) -> Optional[list[dict]]:
        """Read cached predictions from Redis."""
        key = _prediction_cache_key(market)
        try:
            r = await _get_redis_client()
            data = await r.get(key)
            if data is None:
                return None
            return msgpack.unpackb(data, raw=False)
        except Exception as e:
            logger.warning("Prediction cache read failed (non-fatal): %s", e)
            return None

    async def _write_prediction_cache(
        self,
        market: str,
        results_df: pd.DataFrame,
        prediction_date: date,
    ) -> None:
        """Write predictions to Redis cache with 24h TTL."""
        if results_df.empty:
            return

        key = _prediction_cache_key(market)
        try:
            records = []
            for _, row in results_df.iterrows():
                records.append(
                    {
                        "symbol": row["symbol"],
                        "predicted_score": float(row["predicted_score"]),
                        "percentile_rank": float(row["percentile_rank"]),
                        "predicted_direction": row["predicted_direction"],
                        "prediction_date": prediction_date.isoformat(),
                    }
                )

            packed = msgpack.packb(records, use_bin_type=True)
            r = await _get_redis_client()
            await r.setex(key, _PREDICTION_CACHE_TTL, packed)

            logger.info(
                "Cached %d predictions: key=%s, TTL=%ds",
                len(records),
                key,
                _PREDICTION_CACHE_TTL,
            )
        except Exception as e:
            logger.warning("Prediction cache write failed (non-fatal): %s", e)

    # ------------------------------------------------------------------
    # Row conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_prediction_dict(row) -> dict:
        """Convert an asyncpg Record to a prediction dict."""
        d = dict(row)
        # Convert date/datetime objects to ISO strings
        for key in ("prediction_date",):
            if key in d and d[key] is not None:
                d[key] = d[key].isoformat() if hasattr(d[key], "isoformat") else str(d[key])
        # Convert Decimal to float
        for key in ("predicted_score", "percentile_rank", "actual_return"):
            if key in d and d[key] is not None:
                d[key] = float(d[key])
        return d

    @staticmethod
    def _row_to_model_dict(row) -> dict:
        """Convert an asyncpg Record to a model metadata dict."""
        d = dict(row)
        # UUID to string
        if "id" in d and d["id"] is not None:
            d["id"] = str(d["id"])
        # Dates to ISO strings
        for key in (
            "model_date", "train_start", "train_end",
            "val_start", "val_end", "created_at",
        ):
            if key in d and d[key] is not None:
                d[key] = d[key].isoformat() if hasattr(d[key], "isoformat") else str(d[key])
        # Numeric to float
        for key in ("ic", "icir", "ndcg"):
            if key in d and d[key] is not None:
                d[key] = float(d[key])
        # Quality gate flag
        if "quality_passed" in d:
            d["quality_passed"] = (
                bool(d["quality_passed"]) if d["quality_passed"] is not None else True
            )
        # Extract feature importance from metadata if present
        if "metadata" in d and d["metadata"]:
            meta = d["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            d["feature_importance_top30"] = meta.get("feature_importance_top30")
            del d["metadata"]  # Don't expose raw metadata blob
        else:
            d["feature_importance_top30"] = None
            d.pop("metadata", None)
        return d


    # ------------------------------------------------------------------
    # Model file cleanup
    # ------------------------------------------------------------------

    async def cleanup_old_models(self) -> dict:
        """Delete model files older than MODEL_RETENTION_DAYS.

        Always keeps at least MODEL_MIN_QUALITY_KEEP quality-passed models
        per market, even if they exceed the retention period.

        Returns:
            Summary dict with counts of deleted/kept directories.
        """
        from app.core.settings_cache import settings_cache

        settings = get_settings()
        retention_days = settings.MODEL_RETENTION_DAYS
        min_keep = settings.MODEL_MIN_QUALITY_KEEP
        base_dir = settings.PREDICTION_DATA_DIR
        cutoff = date.today() - timedelta(days=retention_days)

        pool = settings_cache.pool
        deleted = 0
        kept = 0
        errors = 0

        for market_dir in ("cn", "us", "hk"):
            market_path = os.path.join(base_dir, market_dir)
            if not os.path.isdir(market_path):
                continue

            # Query DB for quality-passed model dates to protect
            protected_dates: set[str] = set()
            if pool:
                try:
                    async with pool.acquire(timeout=10) as conn:
                        rows = await conn.fetch(
                            """SELECT TO_CHAR(model_date, 'YYYYMMDD') as d
                            FROM prediction_models
                            WHERE market = $1 AND quality_passed = TRUE
                            ORDER BY model_date DESC
                            LIMIT $2""",
                            market_dir, min_keep,
                        )
                        protected_dates = {r["d"] for r in rows}
                except Exception as e:
                    logger.warning("Failed to query protected models for %s: %s", market_dir, e)

            # Scan date directories
            try:
                date_dirs = sorted(os.listdir(market_path))
            except OSError as e:
                logger.warning("Cannot list %s: %s", market_path, e)
                continue

            for dirname in date_dirs:
                dirpath = os.path.join(market_path, dirname)
                if not os.path.isdir(dirpath):
                    continue

                # Protect quality-passed models
                if dirname in protected_dates:
                    kept += 1
                    continue

                # Parse date from dirname (YYYYMMDD)
                try:
                    dir_date = date(int(dirname[:4]), int(dirname[4:6]), int(dirname[6:8]))
                except (ValueError, IndexError):
                    continue

                if dir_date < cutoff:
                    try:
                        import shutil
                        shutil.rmtree(dirpath)
                        deleted += 1
                        logger.debug("Deleted old model dir: %s", dirpath)
                    except OSError as e:
                        logger.warning("Failed to delete %s: %s", dirpath, e)
                        errors += 1
                else:
                    kept += 1

        logger.info(
            "Model cleanup: deleted=%d, kept=%d, errors=%d, retention=%dd",
            deleted, kept, errors, retention_days,
        )
        return {"deleted": deleted, "kept": kept, "errors": errors}

    # ------------------------------------------------------------------
    # Data freshness check
    # ------------------------------------------------------------------

    def _check_data_freshness(self, market: str) -> tuple[bool, str]:
        """Check if Qlib data is fresh enough for training.

        Reads the last date from the Qlib calendar file and compares
        with today. Returns (is_fresh, message).
        """
        settings = get_settings()
        max_stale = settings.PREDICTION_MAX_STALE_DAYS
        if max_stale <= 0:
            return True, "Freshness check disabled"

        # Map market to Qlib data dir
        market_map = {"cn": "cn_data", "us": "us_data", "hk": "hk_data"}
        qlib_market = market_map.get(market)
        if not qlib_market:
            return True, f"Unknown market {market}, skipping freshness check"

        calendar_path = os.path.join(
            settings.QLIB_DATA_DIR, qlib_market, "calendars", "day.txt"
        )

        if not os.path.exists(calendar_path):
            return False, f"Calendar file not found: {calendar_path}"

        try:
            with open(calendar_path) as f:
                lines = f.read().strip().splitlines()
            if not lines:
                return False, "Calendar file is empty"

            last_date_str = lines[-1].strip()
            last_date = date.fromisoformat(last_date_str)
        except Exception as e:
            return False, f"Failed to parse calendar: {e}"

        today = date.today()
        gap_days = (today - last_date).days

        # Convert calendar days to approximate trading days (5/7 ratio)
        approx_trading_gap = int(gap_days * 5 / 7)

        if approx_trading_gap > max_stale:
            return False, (
                f"Qlib data stale: last_date={last_date_str}, "
                f"gap={gap_days}d (~{approx_trading_gap} trading days), "
                f"threshold={max_stale} trading days"
            )

        if approx_trading_gap >= max_stale - 2:
            logger.info(
                "Qlib data approaching staleness: market=%s, last_date=%s, "
                "gap=%dd (~%d trading days)",
                market, last_date_str, gap_days, approx_trading_gap,
            )

        return True, f"Data fresh: last_date={last_date_str}, gap={gap_days}d"


# ---------------------------------------------------------------------------
# Module singleton + shutdown
# ---------------------------------------------------------------------------

prediction_service = PredictionService()


def shutdown_prediction_service() -> None:
    """Called during app shutdown."""
    prediction_service.shutdown()
