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
import redis

from app.config import get_settings
from app.services.feature_service import (
    ALPHA158_FEATURES,
    FUNDAMENTAL_FEATURES,
    SENTIMENT_FEATURES,
    feature_service,
)

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

# Minimum number of symbols per date for valid ranking labels
_MIN_SYMBOLS_PER_DATE = 10

# LightGBM default hyperparameters (lambdarank objective)
_DEFAULT_LGB_PARAMS: dict[str, Any] = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "ndcg_eval_at": [5, 10, 20],
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
}

_DEFAULT_NUM_BOOST_ROUND = 500
_DEFAULT_EARLY_STOPPING = 50


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


_redis_client: Optional[redis.Redis] = None


def _get_redis_client() -> redis.Redis:
    """Return a module-level shared Redis client (lazy singleton)."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=False)
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
    ic, icir, ndcg, model_path, metadata
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
ON CONFLICT (market, model_date, forward_days) DO UPDATE SET
    ic = EXCLUDED.ic,
    icir = EXCLUDED.icir,
    ndcg = EXCLUDED.ndcg,
    model_path = EXCLUDED.model_path,
    feature_count = EXCLUDED.feature_count,
    symbol_count = EXCLUDED.symbol_count,
    metadata = EXCLUDED.metadata
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
       ic, icir, ndcg, model_path, created_at
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
WHERE market = $1 AND forward_days = $2
ORDER BY model_date DESC, created_at DESC
LIMIT 1
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
            del self._tasks[tid]
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
    ) -> list[dict]:
        """Get the latest predictions for a market.

        Checks Redis cache first, falls back to PostgreSQL.

        Args:
            market: Market code.
            top_n: Maximum number of results (ignored if symbol is set).
            symbol: If set, return only this symbol's prediction.

        Returns:
            List of prediction dicts sorted by predicted_score descending.
        """
        # 1. Try Redis cache (only for full-market queries without symbol filter)
        if symbol is None:
            cached = self._read_prediction_cache(market)
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
        return summary

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
    ) -> None:
        """Full prediction pipeline: resolve symbols, train, infer.

        Runs as an asyncio.Task. Updates task.status/progress/message
        throughout for the polling API.
        """
        try:
            # Step 1: Resolve universe symbols
            task.status = "training"
            task.progress = 5.0
            task.message = "Resolving universe symbols"
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
            task.message = f"Resolved {len(symbols)} symbols"
            task.progress = 10.0

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
                    task.message = "Using existing model, skipping training"
                    task.progress = 70.0

            # Step 3: Train if needed
            if model_id is None:
                model_id, model_path = await self._train_model(
                    task, market, symbols, forward_days, today
                )
                trained_this_run = True

            # Step 4: Inference
            task.status = "predicting"
            task.progress = 75.0
            task.message = "Running inference"
            logger.info("Starting inference: market=%s", market)

            prediction_count = await self._run_inference(
                task, market, symbols, model_id, model_path, forward_days, today
            )

            # Done
            task.status = "completed"
            task.progress = 100.0
            task.completed_at = datetime.now()
            task.message = (
                f"Completed: {prediction_count} predictions"
                + (" (model retrained)" if trained_this_run else "")
            )
            task.results = {
                "market": market,
                "model_id": str(model_id) if model_id else None,
                "prediction_count": prediction_count,
                "prediction_date": today.isoformat(),
                "forward_days": forward_days,
                "symbol_count": len(symbols),
                "retrained": trained_this_run,
            }

            logger.info(
                "Prediction pipeline completed: task_id=%s, market=%s, "
                "predictions=%d, retrained=%s",
                task.task_id,
                market,
                prediction_count,
                trained_this_run,
            )

        except asyncio.CancelledError:
            task.status = "failed"
            task.error = "Task was cancelled"
            task.completed_at = datetime.now()
            logger.info(
                "Prediction task cancelled: task_id=%s", task.task_id
            )
        except Exception as e:
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
        1. settings_cache universes (DB-configured)
        2. BackendDataClient.get_symbols() (fallback)
        """
        from app.core.settings_cache import settings_cache

        universes = await settings_cache.get_universes(market=market)

        # Try default universe first, then any universe for this market
        for universe in universes:
            if universe.symbols and len(universe.symbols) > 0:
                logger.info(
                    "Using universe '%s' (%s): %d symbols",
                    universe.name,
                    universe.universe_type,
                    len(universe.symbols),
                )
                return universe.symbols

        # Fallback to BackendDataClient
        logger.info(
            "No configured universe for market=%s, falling back to BackendDataClient",
            market,
        )

        try:
            client = await asyncio.to_thread(self._get_backend_symbols, market)
            return client
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

    # ------------------------------------------------------------------
    # Step 2: Check existing model
    # ------------------------------------------------------------------

    async def _check_existing_model(
        self,
        market: str,
        model_date: date,
        forward_days: int,
    ) -> Optional[dict]:
        """Check if a model already exists for today.

        Returns dict with 'id' and 'model_path' if found, else None.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return None

        try:
            async with pool.acquire(timeout=10) as conn:
                row = await conn.fetchrow(
                    _SQL_GET_LATEST_MODEL_PATH, market, forward_days
                )
                if row is None:
                    return None

                # Also check if there is a model specifically for today
                today_row = await conn.fetchrow(
                    _SQL_CHECK_MODEL, market, model_date, forward_days
                )
                if today_row is None:
                    return None

                return {"id": today_row["id"], "model_path": row["model_path"]}
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
    ) -> tuple[Any, str]:
        """Train a LightGBM ranking model.

        Returns:
            Tuple of (model_id from DB, model_path on disk).

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

        # Per-date percentile labels (0-4 scale for lambdarank)
        df["label"] = df.groupby("date")["forward_return"].transform(
            lambda x: pd.qcut(
                x, q=5, labels=False, duplicates="drop"
            )
            if len(x) >= _MIN_SYMBOLS_PER_DATE
            else pd.Series([2] * len(x), index=x.index)  # neutral if too few
        )
        df["label"] = df["label"].fillna(2).astype(float)

        task.message = "Splitting train/validation sets"
        task.progress = 40.0

        # Purged time-series split
        unique_dates = sorted(df["date"].unique())
        split_idx = int(len(unique_dates) * 0.8)

        if split_idx < _MIN_TRAIN_DATES:
            raise RuntimeError(
                f"Not enough dates for training split: {split_idx} < {_MIN_TRAIN_DATES}"
            )

        train_dates = unique_dates[:split_idx]
        # Purge gap: skip forward_days between train and val
        val_start_idx = split_idx + forward_days
        if val_start_idx >= len(unique_dates):
            # Not enough dates for validation after purge gap, use smaller gap
            val_start_idx = min(split_idx + 1, len(unique_dates) - 1)
            logger.warning(
                "Purge gap reduced: not enough dates for full gap of %d days",
                forward_days,
            )
        val_dates = unique_dates[val_start_idx:]

        if len(val_dates) < 5:
            raise RuntimeError(
                f"Not enough validation dates: {len(val_dates)}. "
                f"Need at least 5 dates after purge gap."
            )

        train_mask = df["date"].isin(train_dates)
        val_mask = df["date"].isin(val_dates)

        train_df = df[train_mask].copy()
        val_df = df[val_mask].copy()

        logger.info(
            "Train/Val split: train=%d rows (%d dates), val=%d rows (%d dates), "
            "purge gap=%d days",
            len(train_df),
            len(train_dates),
            len(val_df),
            len(val_dates),
            forward_days,
        )

        # Determine feature columns (everything except metadata + label + close)
        meta_cols = {"symbol", "date", "close", "forward_return", "label"}
        feature_cols = [c for c in df.columns if c not in meta_cols]

        if not feature_cols:
            raise RuntimeError("No feature columns found in the dataset")

        X_train = train_df[feature_cols].values
        y_train = train_df["label"].values
        X_val = val_df[feature_cols].values
        y_val = val_df["label"].values

        # Group sizes for lambdarank (number of stocks per date)
        train_group = train_df.groupby("date").size().values
        val_group = val_df.groupby("date").size().values

        task.message = f"Training LightGBM ({len(feature_cols)} features)"
        task.progress = 45.0

        # Train LightGBM (CPU-bound but releases GIL)
        logger.info(
            "Starting LightGBM training: %d features, %d train rows, %d val rows",
            len(feature_cols),
            len(X_train),
            len(X_val),
        )

        train_set = lgb.Dataset(
            X_train, label=y_train, group=train_group, feature_name=feature_cols
        )
        val_set = lgb.Dataset(
            X_val, label=y_val, group=val_group, feature_name=feature_cols,
            reference=train_set,
        )

        # Run training in thread to avoid blocking event loop during callbacks
        model = await asyncio.to_thread(
            self._train_lgb_sync, train_set, val_set
        )

        task.message = "Model trained, evaluating performance"
        task.progress = 55.0

        # Evaluate: IC, ICIR, NDCG
        val_scores = model.predict(X_val)
        val_actual = val_df["forward_return"].values

        ic_series, ic_mean, icir = self._compute_ic_metrics(
            val_df, val_scores, val_actual
        )

        # Best NDCG from LightGBM training log
        best_ndcg = None
        if model.best_score and "valid_0" in model.best_score:
            valid_scores = model.best_score["valid_0"]
            # Pick ndcg@10 as primary metric
            best_ndcg = valid_scores.get(
                "ndcg@10", valid_scores.get("ndcg@5")
            )

        logger.info(
            "Model evaluation: IC=%.4f, ICIR=%.4f, NDCG@10=%s, best_iter=%d",
            ic_mean,
            icir,
            f"{best_ndcg:.4f}" if best_ndcg is not None else "N/A",
            model.best_iteration if model.best_iteration >= 0 else _DEFAULT_NUM_BOOST_ROUND,
        )

        # Save model to disk
        task.message = "Saving model"
        task.progress = 60.0

        model_path = self._save_model(model, market, model_date, feature_cols)
        logger.info("Model saved to %s", model_path)

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
            symbol_count=train_df["symbol"].nunique(),
            feature_sources=feature_sources,
            ic=ic_mean,
            icir=icir,
            ndcg=best_ndcg,
            model_path=model_path,
        )

        task.progress = 70.0
        logger.info(
            "Model recorded: model_id=%s, market=%s", model_id, market
        )

        return model_id, model_path

    @staticmethod
    def _train_lgb_sync(
        train_set: lgb.Dataset,
        val_set: lgb.Dataset,
    ) -> lgb.Booster:
        """Synchronous LightGBM training (runs in thread via asyncio.to_thread).

        LightGBM's C core releases the GIL during computation, so this
        does not meaningfully block other asyncio tasks.
        """
        callbacks = [
            lgb.early_stopping(_DEFAULT_EARLY_STOPPING),
            lgb.log_evaluation(period=50),
        ]

        model = lgb.train(
            _DEFAULT_LGB_PARAMS,
            train_set,
            valid_sets=[val_set],
            valid_names=["valid_0"],
            num_boost_round=_DEFAULT_NUM_BOOST_ROUND,
            callbacks=callbacks,
        )
        return model

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
        model: lgb.Booster,
        market: str,
        model_date: date,
        feature_cols: list[str],
    ) -> str:
        """Save model + feature metadata to disk.

        Directory: {PREDICTION_DATA_DIR}/{market}/{YYYYMMDD}/
        Files: model.pkl, features.json

        Returns:
            Absolute path to model.pkl.
        """
        settings = get_settings()
        date_str = model_date.strftime("%Y%m%d")
        model_dir = Path(settings.PREDICTION_DATA_DIR) / market / date_str
        model_dir.mkdir(parents=True, exist_ok=True)

        model_path = str(model_dir / "model.pkl")
        joblib.dump(model, model_path)

        # Save feature list alongside model for reproducibility
        features_path = str(model_dir / "features.json")
        with open(features_path, "w") as f:
            json.dump(
                {"features": feature_cols, "count": len(feature_cols)}, f
            )

        return model_path

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
    ) -> Any:
        """Write model metadata to prediction_models table. Returns model id."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            raise RuntimeError("DB pool not available for model recording")

        metadata_json = json.dumps(
            {
                "lgb_params": _DEFAULT_LGB_PARAMS,
                "num_boost_round": _DEFAULT_NUM_BOOST_ROUND,
                "early_stopping": _DEFAULT_EARLY_STOPPING,
            },
            default=_numpy_default,
        )

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

        model = await asyncio.to_thread(joblib.load, model_path)
        logger.info("Model loaded from %s", model_path)

        # Load feature names
        features_path = os.path.join(os.path.dirname(model_path), "features.json")
        if os.path.exists(features_path):
            with open(features_path) as f:
                feature_meta = json.load(f)
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

        # Use only the latest date
        latest_date = inference_df["date"].max()
        latest_df = inference_df[inference_df["date"] == latest_date].copy()

        logger.info(
            "Inference data: %d symbols for date %s",
            len(latest_df),
            latest_date.strftime("%Y-%m-%d"),
        )

        if latest_df.empty:
            raise RuntimeError("No data for latest date after filtering")

        # Align feature columns (handle missing/extra columns gracefully)
        available_features = [c for c in feature_cols if c in latest_df.columns]
        missing_features = [c for c in feature_cols if c not in latest_df.columns]

        if missing_features:
            logger.warning(
                "Missing %d features in inference data (will be filled with NaN): %s",
                len(missing_features),
                missing_features[:10],
            )
            for col in missing_features:
                latest_df[col] = np.nan

        X_inference = latest_df[feature_cols].values

        # Predict
        task.message = "Generating predictions"
        task.progress = 88.0

        scores = await asyncio.to_thread(model.predict, X_inference)

        # Compute percentile ranks (0.0 to 1.0)
        ranks = pd.Series(scores).rank(pct=True).values

        # Determine direction thresholds
        directions = []
        for rank_val in ranks:
            if rank_val >= 0.70:
                directions.append("up")
            elif rank_val <= 0.30:
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
        self._write_prediction_cache(market, results_df, prediction_date)

        return len(results_df)

    async def _find_latest_model_path(
        self, market: str, forward_days: int
    ) -> Optional[str]:
        """Find the latest model path from DB."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return None

        try:
            async with pool.acquire(timeout=10) as conn:
                row = await conn.fetchrow(
                    _SQL_GET_LATEST_MODEL_PATH, market, forward_days
                )
                if row and row["model_path"]:
                    return row["model_path"]
        except Exception as e:
            logger.warning("Failed to query latest model path: %s", e)

        return None

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
        end_str = (max_date + timedelta(days=max_forward + 10)).isoformat()

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

        # Build date -> close mapping
        price_map: dict[date, float] = {}
        for d_str, c in zip(dates, closes):
            if c is not None:
                d = pd.Timestamp(d_str).date()
                price_map[d] = float(c)

        # Compute actual returns
        results: dict = {}
        for pred in predictions:
            pred_date = pred["prediction_date"]
            fwd = pred["forward_days"]

            # Find actual future date (approximate: calendar days as proxy for trading days)
            # In practice, we look for the closest available price
            base_price = price_map.get(pred_date)
            if base_price is None:
                continue

            # Search for future price within a window
            target_date = pred_date + timedelta(days=fwd)
            future_price = None
            for offset in range(0, 5):  # Allow up to 5 calendar days slack
                candidate = target_date + timedelta(days=offset)
                if candidate in price_map:
                    future_price = price_map[candidate]
                    break

            if future_price is not None and base_price > 0:
                actual_return = (future_price / base_price) - 1.0
                results[pred["id"]] = actual_return

        return results

    # ------------------------------------------------------------------
    # Redis prediction cache
    # ------------------------------------------------------------------

    def _read_prediction_cache(self, market: str) -> Optional[list[dict]]:
        """Read cached predictions from Redis."""
        key = _prediction_cache_key(market)
        try:
            r = _get_redis_client()
            data = r.get(key)
            if data is None:
                return None
            return msgpack.unpackb(data, raw=False)
        except Exception as e:
            logger.debug("Prediction cache read failed (non-fatal): %s", e)
            return None

    def _write_prediction_cache(
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
            r = _get_redis_client()
            r.setex(key, _PREDICTION_CACHE_TTL, packed)

            logger.info(
                "Cached %d predictions: key=%s, TTL=%ds",
                len(records),
                key,
                _PREDICTION_CACHE_TTL,
            )
        except Exception as e:
            logger.debug("Prediction cache write failed (non-fatal): %s", e)

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
        return d


# ---------------------------------------------------------------------------
# Module singleton + shutdown
# ---------------------------------------------------------------------------

prediction_service = PredictionService()


def shutdown_prediction_service() -> None:
    """Called during app shutdown."""
    prediction_service.shutdown()
