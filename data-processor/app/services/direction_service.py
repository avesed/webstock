"""Binary direction prediction service -- LightGBM classification + calibration.

Trains a separate binary classification model that predicts whether each stock
will go up or down over the forward horizon, outputting a calibrated probability.

This complements the ranking model in prediction_service.py:
- Ranking model: optimises stock ordering (lambdarank objective).
- Direction model: predicts binary up/down probability (binary objective).

Training pipeline:
1. Reuse feature_service.build_feature_matrix() for per-stock features.
2. Merge market_features_service.build_market_features() (market-level features).
3. Label construction: forward_return > 0 -> 1, else -> 0 (binary).
4. Walk-forward cross-validation (same expanding window pattern as ranking).
5. Ensemble training (multi-seed, binary objective, is_unbalance=True).
6. Probability calibration via isotonic regression (>1000 samples) or Platt scaling.
7. Quality gate: AUC > 0.52, Brier score < 0.25 (per-market thresholds).

Inference pipeline:
1. Load direction model ensemble + calibrator from disk.
2. Build features (individual + market-level) for prediction_date.
3. Ensemble average raw probability -> calibrate -> up_probability.
4. UPDATE existing stock_predictions rows (ranking model already INSERTed them).

Model storage (alongside ranking model):
    {PREDICTION_DATA_DIR}/{market}/{YYYYMMDD}/
        direction_model.pkl    - LightGBM ensemble (list[Booster])
        direction_features.json - feature column names
        calibrator.pkl         - sklearn calibrator object

All orchestration is async. LightGBM training runs in native C code that
releases the GIL, so it does not block the event loop significantly.
"""

import asyncio
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.config import get_settings
from app.services.feature_service import (
    ALPHA158_FEATURES,
    FUNDAMENTAL_FEATURES,
    SENTIMENT_FEATURES,
    feature_service,
)
from app.services.market_config import MarketConfig, get_market_config
from app.services.market_features_service import (
    MARKET_FEATURE_COLUMNS,
    build_market_features,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Training history lookback (calendar days -> ~2 years of trading days)
_TRAIN_LOOKBACK_DAYS = 730

# Minimum number of dates required for a valid train/val split
_MIN_TRAIN_DATES = 60

# Minimum samples per date for valid binary labels
_MIN_SYMBOLS_PER_DATE = 25

# Ensemble seeds -- same as ranking model for consistency
_ENSEMBLE_SEEDS: list[int] = [42, 137, 271, 419, 503, 631, 769, 887, 953, 1031]

# LightGBM base hyperparameters for binary classification.
# is_unbalance=True auto-adjusts class weights when up/down ratio is skewed.
_DIRECTION_LGB_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": ["auc", "binary_logloss"],
    "is_unbalance": True,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
}

# Minimum calibration samples to use isotonic regression (more flexible).
# Below this, fall back to Platt scaling (logistic sigmoid) which needs fewer.
_ISOTONIC_MIN_SAMPLES = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _numpy_default(obj: Any) -> Any:
    """JSON serializer fallback for numpy types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        import math
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _get_direction_lgb_params(
    market: str, cfg: MarketConfig | None = None,
) -> dict[str, Any]:
    """Return merged LightGBM params for binary direction model."""
    params = dict(_DIRECTION_LGB_PARAMS)
    resolved = cfg or get_market_config(market)
    # Apply per-market ranking overrides that also apply to direction model:
    # learning_rate, num_leaves, min_child_samples, lambda_l2.
    # These are shared hyperparameters -- data complexity is market-dependent,
    # not objective-dependent.
    for key in ("learning_rate", "num_leaves", "min_child_samples", "lambda_l2"):
        if key in resolved.lgb_overrides:
            params[key] = resolved.lgb_overrides[key]
    # Apply direction-specific overrides on top (highest priority)
    params.update(resolved.direction_lgb_overrides)
    return params


# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

_SQL_UPDATE_UP_PROBABILITY = """
UPDATE stock_predictions
SET up_probability = $1
WHERE market = $2 AND prediction_date = $3 AND symbol = $4 AND forward_days = $5
"""

_SQL_INSERT_DIRECTION_MODEL = """
INSERT INTO prediction_models (
    market, model_date, train_start, train_end, val_start, val_end,
    forward_days, feature_count, symbol_count, feature_sources,
    ic, icir, ndcg, auc, brier_score, model_path, metadata,
    quality_passed, model_type
) VALUES (
    $1, $2, $3, $4, $5, $6,
    $7, $8, $9, $10,
    $11, $12, $13, $14, $15, $16, $17,
    $18, 'direction'
)
ON CONFLICT (market, model_date, forward_days, model_type) DO UPDATE SET
    auc = EXCLUDED.auc,
    brier_score = EXCLUDED.brier_score,
    model_path = EXCLUDED.model_path,
    feature_count = EXCLUDED.feature_count,
    symbol_count = EXCLUDED.symbol_count,
    metadata = EXCLUDED.metadata,
    quality_passed = EXCLUDED.quality_passed
RETURNING id
"""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


async def train_and_predict_direction(
    market: str,
    db_pool,  # asyncpg.Pool
    forward_days: int = 5,
    force_retrain: bool = False,
    prediction_date: Optional[date] = None,
) -> Optional[dict]:
    """Full direction model pipeline: train (if needed) + inference.

    Designed to run AFTER the ranking model has completed and INSERTed
    rows into stock_predictions. This function only UPDATEs the
    up_probability column on those existing rows.

    Args:
        prediction_date: The date used by the ranking model for INSERT.
            Must match to ensure the UPDATE finds the correct rows.
            Defaults to date.today() if not provided.

    Returns a summary dict on success, None on failure.
    The function never raises -- direction model failure should not
    block ranking predictions.
    """
    try:
        return await _direction_pipeline(
            market, db_pool, forward_days, force_retrain, prediction_date,
        )
    except Exception as e:
        logger.error(
            "Direction model pipeline failed for market=%s (non-fatal): %s",
            market, e, exc_info=True,
        )
        return None


async def _direction_pipeline(
    market: str,
    db_pool,
    forward_days: int,
    force_retrain: bool,
    prediction_date: Optional[date] = None,
) -> dict:
    """Internal direction pipeline -- may raise on failure."""
    settings = get_settings()
    cfg = get_market_config(market)
    today = prediction_date or date.today()

    logger.info(
        "Direction model pipeline start: market=%s, forward_days=%d",
        market, forward_days,
    )

    # Step 1: Check for existing direction model
    model_dir = _get_model_dir(market, today)
    direction_model_path = os.path.join(model_dir, "direction_model.pkl")
    calibrator_path = os.path.join(model_dir, "calibrator.pkl")

    trained_this_run = False
    quality_passed = True
    auc_score = 0.0
    brier = 0.0

    if not force_retrain and os.path.exists(direction_model_path):
        logger.info(
            "Existing direction model found at %s, skipping training",
            direction_model_path,
        )
    else:
        # Step 2: Train the direction model
        train_result = await _train_direction_model(
            market, today, forward_days, db_pool, cfg,
        )
        trained_this_run = True
        quality_passed = train_result["quality_passed"]
        auc_score = train_result["auc"]
        brier = train_result["brier_score"]

        if not quality_passed:
            logger.warning(
                "Direction model quality gate FAILED: AUC=%.4f (min=%.4f), "
                "Brier=%.4f (max=%.4f). Running inference with low-quality model.",
                auc_score, cfg.direction_min_auc,
                brier, cfg.direction_max_brier,
            )

    # Step 3: Inference -- update up_probability
    # Always run inference even if quality gate failed, so users see
    # up_probability values (the quality_passed flag signals low confidence).
    n_updated = await _run_direction_inference(
        market, today, forward_days, db_pool, cfg,
    )

    summary = {
        "market": market,
        "trained": trained_this_run,
        "quality_passed": quality_passed,
        "auc": auc_score if trained_this_run else None,
        "brier_score": brier if trained_this_run else None,
        "predictions_updated": n_updated,
    }

    logger.info(
        "Direction model pipeline completed: market=%s, trained=%s, "
        "quality_passed=%s, predictions_updated=%d",
        market, trained_this_run, quality_passed, n_updated,
    )

    return summary


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


async def _train_direction_model(
    market: str,
    model_date: date,
    forward_days: int,
    db_pool,
    cfg: MarketConfig,
    persist: bool = True,
) -> dict:
    """Train a binary direction model with walk-forward validation.

    Args:
        persist: If True (default), save model to disk and record in DB.
            Set to False for ML tools backtesting to avoid polluting
            production models and disk with backtest artifacts.

    Returns dict with keys: quality_passed, auc, brier_score, model_path,
    calibration_method, ensemble_size, feature_count, symbol_count.
    """
    settings = get_settings()
    ensemble_size = settings.ENSEMBLE_SIZE
    n_folds = settings.WALKFORWARD_FOLDS

    # Date ranges
    train_end_date = model_date - timedelta(days=forward_days)
    train_start_date = model_date - timedelta(days=_TRAIN_LOOKBACK_DAYS)

    train_start_str = train_start_date.isoformat()
    train_end_str = model_date.isoformat()

    logger.info(
        "Direction model training: market=%s, period=%s~%s, ensemble=%d, folds=%d",
        market, train_start_str, train_end_str, ensemble_size, n_folds,
    )

    # Step 1: Build per-stock feature matrix
    feature_df = await feature_service.build_feature_matrix(
        market=market,
        symbols=await _resolve_symbols(market),
        start_date=train_start_str,
        end_date=train_end_str,
    )

    if feature_df.empty:
        raise RuntimeError(
            f"Feature matrix is empty for direction model (market={market})"
        )

    feature_df["date"] = pd.to_datetime(feature_df["date"])

    # Step 2: Build market-level features and merge
    market_features_df = await build_market_features(
        market=market,
        start_date=train_start_date,
        end_date=model_date,
        db_pool=db_pool,
    )

    if not market_features_df.empty:
        market_features_df["date"] = pd.to_datetime(market_features_df["date"])
        feature_df = feature_df.merge(
            market_features_df, on="date", how="left",
        )
        logger.info(
            "Merged %d market features (%d dates) into direction training data",
            len(MARKET_FEATURE_COLUMNS), len(market_features_df),
        )
    else:
        logger.warning(
            "Market features unavailable, training direction model "
            "without market-level features"
        )
        for col in MARKET_FEATURE_COLUMNS:
            feature_df[col] = np.nan

    # Step 2b: Stacking — feed ranking model's predicted scores as a feature.
    # The ranking model (IC~0.04) captures relative stock quality (alpha);
    # direction model needs both alpha + market regime (beta) to predict
    # absolute direction. Stacking injects the alpha signal directly.
    try:
        from app.services.prediction_service import prediction_service

        ranking_history = await prediction_service.get_prediction_history(
            market=market, days=_TRAIN_LOOKBACK_DAYS,
        )
        if ranking_history:
            ranking_df = pd.DataFrame(ranking_history)
            # Normalise column names (API returns snake_case)
            if "prediction_date" in ranking_df.columns:
                ranking_df.rename(columns={"prediction_date": "date"}, inplace=True)
            if "predicted_score" in ranking_df.columns:
                ranking_df.rename(columns={"predicted_score": "ranking_score"}, inplace=True)
            if "date" in ranking_df.columns and "ranking_score" in ranking_df.columns:
                ranking_df["date"] = pd.to_datetime(ranking_df["date"])
                ranking_df = ranking_df[["symbol", "date", "ranking_score"]].drop_duplicates(
                    subset=["symbol", "date"],
                )
                n_before = len(feature_df)
                feature_df = feature_df.merge(ranking_df, on=["symbol", "date"], how="left")
                n_matched = int(feature_df["ranking_score"].notna().sum())
                coverage = n_matched / n_before if n_before > 0 else 0
                logger.info(
                    "Stacking: merged ranking scores, %d/%d rows matched (%.1f%%)",
                    n_matched, n_before, coverage * 100,
                )
                if coverage < 0.05:
                    # Less than 5% coverage → ranking_score is mostly NaN → noise.
                    # Drop the column to avoid hurting the model.
                    feature_df.drop(columns=["ranking_score"], inplace=True)
                    logger.info(
                        "Stacking: dropped ranking_score (coverage %.1f%% < 5%%)",
                        coverage * 100,
                    )
            else:
                logger.info("Stacking: ranking history columns not usable, skipping")
        else:
            logger.info("Stacking: no ranking history available, skipping")
    except Exception as e:
        logger.warning("Stacking: failed to fetch ranking scores (non-fatal): %s", e)

    # Step 3: Fetch close prices for label computation
    close_df = await _fetch_close_prices(
        market, train_start_str, train_end_str,
    )

    if close_df.empty:
        raise RuntimeError("Close price data is empty for direction model")

    close_df["date"] = pd.to_datetime(close_df["date"])

    df = feature_df.merge(
        close_df[["symbol", "date", "close"]],
        on=["symbol", "date"],
        how="left",
    )

    # Step 4: Compute forward returns and binary labels
    df = df.sort_values(["symbol", "date"])
    df["forward_return"] = df.groupby("symbol")["close"].transform(
        lambda x: x.shift(-forward_days) / x - 1
    )

    # Winsorize extreme returns (consistent with ranking model)
    df["forward_return"] = df.groupby("date")["forward_return"].transform(
        lambda x: x.clip(x.quantile(0.01), x.quantile(0.99))
    )

    # Binary label: 1 if positive return, 0 otherwise
    df = df.dropna(subset=["forward_return"])
    df["label"] = (df["forward_return"] > 0).astype(float)

    # Filter dates with sufficient symbols
    date_counts = df.groupby("date")["symbol"].nunique()
    valid_dates = date_counts[date_counts >= _MIN_SYMBOLS_PER_DATE].index
    df = df[df["date"].isin(valid_dates)]

    if len(df) < _MIN_TRAIN_DATES * _MIN_SYMBOLS_PER_DATE:
        raise RuntimeError(
            f"Insufficient labeled data for direction model: {len(df)} rows"
        )

    class_balance = df["label"].mean()
    logger.info(
        "Direction labels: %d rows, %.1f%% positive (up), %.1f%% negative (down)",
        len(df), class_balance * 100, (1 - class_balance) * 100,
    )

    # Step 5: Determine feature columns
    meta_cols = {"symbol", "date", "close", "forward_return", "label"}
    feature_cols = [c for c in df.columns if c not in meta_cols]
    if not feature_cols:
        raise RuntimeError("No feature columns found for direction model")

    # Step 6: Walk-forward training with ensemble
    unique_dates = sorted(df["date"].unique())
    sort_cols = (
        ["symbol", "date"] if cfg.use_temporal_sort else ["date", "symbol"]
    )

    splits = _walk_forward_splits(
        unique_dates, n_folds=n_folds, forward_days=forward_days,
    )

    if not splits:
        raise RuntimeError(
            f"Could not generate walk-forward splits for direction model "
            f"({len(unique_dates)} dates, {n_folds} folds)"
        )

    fold_aucs: list[float] = []
    fold_briers: list[float] = []
    final_models: list[lgb.Booster] = []
    final_val_df: pd.DataFrame = pd.DataFrame()
    final_val_probs: np.ndarray = np.array([])
    final_train_dates: list = []
    final_val_dates: list = []

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

        tr_set = lgb.Dataset(X_tr, label=y_tr, feature_name=feature_cols)
        va_set = lgb.Dataset(
            X_va, label=y_va, feature_name=feature_cols, reference=tr_set,
        )

        logger.info(
            "Direction walk-forward fold %d/%d: train=%d rows (%d dates), "
            "val=%d rows (%d dates)",
            fold_idx + 1, len(splits),
            len(tr_df), len(tr_dates), len(va_df), len(va_dates),
        )

        models = await asyncio.to_thread(
            _train_direction_ensemble_sync,
            tr_set, va_set, market, ensemble_size, cfg,
        )

        # Evaluate ensemble on validation set
        va_probs_list = [m.predict(X_va) for m in models]
        va_probs = np.mean(va_probs_list, axis=0)

        try:
            fold_auc = float(roc_auc_score(y_va, va_probs))
        except ValueError:
            fold_auc = 0.5
        fold_brier = float(brier_score_loss(y_va, va_probs))

        fold_aucs.append(fold_auc)
        fold_briers.append(fold_brier)

        logger.info(
            "  Fold %d AUC=%.4f, Brier=%.4f",
            fold_idx + 1, fold_auc, fold_brier,
        )

        if is_final_fold:
            final_models = models
            final_val_df = va_df
            final_val_probs = va_probs
            final_train_dates = list(tr_dates)
            final_val_dates = list(va_dates)

    # Step 7: Probability calibration on final fold's validation set
    y_val_final = final_val_df["label"].values
    calibrator, calibration_method = _fit_calibrator(
        final_val_probs, y_val_final,
    )

    # Re-evaluate after calibration
    calibrated_probs = _apply_calibrator(calibrator, final_val_probs)
    try:
        calibrated_auc = float(roc_auc_score(y_val_final, calibrated_probs))
    except ValueError:
        calibrated_auc = 0.5
    calibrated_brier = float(brier_score_loss(y_val_final, calibrated_probs))

    # Use mean across folds for quality gate (uncalibrated, more conservative)
    mean_auc = float(np.mean(fold_aucs))
    mean_brier = float(np.mean(fold_briers))

    logger.info(
        "Direction walk-forward summary (%d folds, %d members): "
        "fold_AUCs=%s, mean_AUC=%.4f, mean_Brier=%.4f, "
        "calibrated_AUC=%.4f, calibrated_Brier=%.4f, method=%s",
        len(splits), ensemble_size,
        [f"{a:.4f}" for a in fold_aucs],
        mean_auc, mean_brier,
        calibrated_auc, calibrated_brier,
        calibration_method,
    )

    # Quality gate
    quality_passed = (
        mean_auc > cfg.direction_min_auc
        and mean_brier < cfg.direction_max_brier
    )

    if not quality_passed:
        logger.warning(
            "Direction model quality gate FAILED: "
            "mean_AUC=%.4f (min=%.4f), mean_Brier=%.4f (max=%.4f)",
            mean_auc, cfg.direction_min_auc,
            mean_brier, cfg.direction_max_brier,
        )
    else:
        logger.info(
            "Direction model quality gate passed: "
            "mean_AUC=%.4f, mean_Brier=%.4f",
            mean_auc, mean_brier,
        )

    # Step 8-9: Save model + record in DB (skip for backtests)
    model_dir = _get_model_dir(market, model_date)
    model_id = None

    if persist:
        _save_direction_model(
            final_models, calibrator, feature_cols, model_dir,
        )

        model_id = await _record_direction_model(
            db_pool=db_pool,
            market=market,
            model_date=model_date,
            train_start=pd.Timestamp(final_train_dates[0]).date(),
            train_end=pd.Timestamp(final_train_dates[-1]).date(),
            val_start=pd.Timestamp(final_val_dates[0]).date(),
            val_end=pd.Timestamp(final_val_dates[-1]).date(),
            forward_days=forward_days,
            feature_count=len(feature_cols),
            symbol_count=final_val_df["symbol"].nunique(),
            auc=mean_auc,
            brier_score=mean_brier,
            model_path=os.path.join(model_dir, "direction_model.pkl"),
            quality_passed=quality_passed,
            extra_metadata={
                "ensemble_size": ensemble_size,
                "walkforward_folds": len(splits),
                "fold_aucs": [round(a, 6) for a in fold_aucs],
                "fold_briers": [round(b, 6) for b in fold_briers],
                "calibrated_auc": round(calibrated_auc, 6),
                "calibrated_brier": round(calibrated_brier, 6),
                "calibration_method": calibration_method,
                "class_balance": round(class_balance, 4),
                "has_market_features": not market_features_df.empty,
            },
        )

        logger.info(
            "Direction model recorded: model_id=%s, market=%s, "
            "AUC=%.4f, Brier=%.4f, quality_passed=%s",
            model_id, market, mean_auc, mean_brier, quality_passed,
        )

    return {
        "quality_passed": quality_passed,
        "auc": mean_auc,
        "brier_score": mean_brier,
        "calibrated_auc": calibrated_auc,
        "calibrated_brier": calibrated_brier,
        "calibration_method": calibration_method,
        "model_path": model_dir,
        "model_id": str(model_id) if model_id else None,
        "feature_count": len(feature_cols),
        "symbol_count": final_val_df["symbol"].nunique(),
        "ensemble_size": ensemble_size,
        # Non-serializable objects for ml_tools_service caching
        "models": final_models,
        "feature_cols": feature_cols,
        "calibrator": calibrator,
    }


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


async def _run_direction_inference(
    market: str,
    prediction_date: date,
    forward_days: int,
    db_pool,
    cfg: MarketConfig,
) -> int:
    """Run direction model inference and UPDATE stock_predictions.up_probability.

    Returns number of predictions updated.
    """
    # Find the latest direction model (prefer today's, fall back to recent)
    model_dir, models, calibrator, feature_cols = await _load_direction_model(
        market, prediction_date,
    )

    if models is None:
        logger.warning(
            "No direction model found for market=%s, skipping inference",
            market,
        )
        return 0

    logger.info(
        "Direction inference: market=%s, %d ensemble members, %d features",
        market, len(models), len(feature_cols),
    )

    # Build per-stock features for the latest date
    inference_end = prediction_date.isoformat()
    inference_start = (prediction_date - timedelta(days=90)).isoformat()

    inference_df = await feature_service.build_feature_matrix(
        market=market,
        symbols=await _resolve_symbols(market),
        start_date=inference_start,
        end_date=inference_end,
    )

    if inference_df.empty:
        logger.warning("Inference feature matrix is empty for direction model")
        return 0

    inference_df["date"] = pd.to_datetime(inference_df["date"])

    # Build and merge market features
    market_features_df = await build_market_features(
        market=market,
        start_date=prediction_date - timedelta(days=90),
        end_date=prediction_date,
        db_pool=db_pool,
    )

    if not market_features_df.empty:
        market_features_df["date"] = pd.to_datetime(market_features_df["date"])
        inference_df = inference_df.merge(
            market_features_df, on="date", how="left",
        )
    else:
        for col in MARKET_FEATURE_COLUMNS:
            if col not in inference_df.columns:
                inference_df[col] = np.nan

    # Stacking: merge latest ranking scores for inference
    try:
        from app.services.prediction_service import prediction_service

        ranking_latest = await prediction_service.get_latest_predictions(
            market=market, top_n=500,
        )
        if ranking_latest:
            rdf = pd.DataFrame(ranking_latest)
            if "predicted_score" in rdf.columns:
                rdf.rename(columns={"predicted_score": "ranking_score"}, inplace=True)
            if "ranking_score" in rdf.columns and "symbol" in rdf.columns:
                # Latest predictions are for a single date — broadcast to all dates
                rdf = rdf[["symbol", "ranking_score"]].drop_duplicates(subset=["symbol"])
                inference_df = inference_df.merge(rdf, on="symbol", how="left")
    except Exception as e:
        logger.warning("Stacking inference: failed to fetch ranking scores: %s", e)

    # Pick the latest date with adequate coverage
    settings = get_settings()
    date_symbol_counts = (
        inference_df.groupby("date")["symbol"].nunique().sort_index()
    )
    if date_symbol_counts.empty:
        logger.warning("No inference dates available for direction model")
        return 0

    max_date = date_symbol_counts.index.max()
    max_date_count = date_symbol_counts.loc[max_date]
    total_symbols = inference_df["symbol"].nunique()
    min_coverage = settings.INFERENCE_MIN_COVERAGE

    if max_date_count >= total_symbols * min_coverage:
        latest_date = max_date
    else:
        threshold = total_symbols * min_coverage
        candidates = date_symbol_counts[date_symbol_counts >= threshold]
        if candidates.empty:
            logger.warning(
                "Direction inference: insufficient symbol coverage "
                "(best: %d/%d symbols)", max_date_count, total_symbols,
            )
            return 0
        latest_date = candidates.index.max()

    latest_df = inference_df[inference_df["date"] == latest_date].copy()

    logger.info(
        "Direction inference: %d symbols for date %s",
        len(latest_df), latest_date.strftime("%Y-%m-%d"),
    )

    # Align feature columns
    available_features = [c for c in feature_cols if c in latest_df.columns]
    missing_features = [c for c in feature_cols if c not in latest_df.columns]

    if missing_features:
        missing_pct = len(missing_features) / len(feature_cols)
        if missing_pct > 0.25:
            logger.error(
                "Direction inference: %.0f%% features missing (%d/%d)",
                missing_pct * 100, len(missing_features), len(feature_cols),
            )
        elif missing_pct > 0.10:
            logger.warning(
                "Direction inference: %.0f%% features missing (%d/%d)",
                missing_pct * 100, len(missing_features), len(feature_cols),
            )
        else:
            logger.info(
                "Direction inference: %d features missing (filled with NaN)",
                len(missing_features),
            )
        for col in missing_features:
            latest_df[col] = np.nan

    X_inference = latest_df[feature_cols].values

    # Predict -- ensemble average raw probability, then calibrate
    def _ensemble_predict() -> np.ndarray:
        probs_list = [m.predict(X_inference) for m in models]
        return np.mean(probs_list, axis=0)

    raw_probs = await asyncio.to_thread(_ensemble_predict)

    raw_mean = float(np.mean(raw_probs))
    raw_std = float(np.std(raw_probs))

    logger.info(
        "Raw ensemble output: mean=%.4f, std=%.4f, min=%.4f, max=%.4f",
        raw_mean, raw_std, float(np.min(raw_probs)), float(np.max(raw_probs)),
    )

    # Calibrate the market-level probability (the mean direction signal).
    if calibrator is not None:
        calibrated_mean = float(
            _apply_calibrator(calibrator, np.array([raw_mean]))[0]
        )
    else:
        calibrated_mean = raw_mean

    # When all predictions are from a single date, market features are
    # constant → raw probabilities cluster in a very narrow range.
    # Standard calibration (isotonic) maps the entire cluster to one value,
    # erasing per-stock differentiation.
    # Fix: use rank-based rescaling that preserves per-stock ordering around
    # the calibrated market-level probability.
    if raw_std < 0.02 and len(raw_probs) > 10:
        from scipy.stats import rankdata
        # Use 'ordinal' to break ties — ensures every stock gets a unique rank.
        # With low best_iter, LightGBM produces many tied raw predictions;
        # 'average' would give tied stocks the same rank = same output.
        n = len(raw_probs)
        ranks = rankdata(raw_probs, method="ordinal")  # [1, n]
        # Normalise to [0, 1] with endpoints included
        normalised = (ranks - 1) / max(n - 1, 1)  # [0.0, 1.0]
        # Spread ±0.15 around calibrated mean (wider = more differentiated)
        half_spread = 0.15
        calibrated_probs = calibrated_mean + half_spread * (2.0 * normalised - 1.0)
        calibrated_probs = np.clip(calibrated_probs, 0.05, 0.95)
        logger.info(
            "Rank-based spread: raw_std=%.4f (< 0.02), calibrated_mean=%.4f, "
            "output range=[%.4f, %.4f]",
            raw_std, calibrated_mean,
            float(calibrated_probs.min()), float(calibrated_probs.max()),
        )
    elif calibrator is not None:
        calibrated_probs = _apply_calibrator(calibrator, raw_probs)
        logger.info(
            "Applied calibration: raw mean=%.4f -> calibrated mean=%.4f",
            raw_mean, float(np.mean(calibrated_probs)),
        )
    else:
        calibrated_probs = raw_probs
        logger.info(
            "No calibrator available, using raw probabilities (mean=%.4f)",
            raw_mean,
        )

    # UPDATE stock_predictions rows
    n_updated = await _update_up_probabilities(
        db_pool=db_pool,
        market=market,
        prediction_date=prediction_date,
        forward_days=forward_days,
        symbols=latest_df["symbol"].values,
        up_probabilities=calibrated_probs,
    )

    logger.info(
        "Direction inference complete: market=%s, %d/%d predictions updated, "
        "mean up_probability=%.4f",
        market, n_updated, len(latest_df),
        float(np.mean(calibrated_probs)),
    )

    return n_updated


# ---------------------------------------------------------------------------
# Ensemble training (synchronous, runs via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _train_direction_ensemble_sync(
    train_set: lgb.Dataset,
    val_set: lgb.Dataset,
    market: str = "us",
    ensemble_size: int = 5,
    cfg: MarketConfig | None = None,
) -> list[lgb.Booster]:
    """Train an ensemble of binary classification LightGBM models.

    Each member uses a unique seed triplet for feature/bagging subsampling.
    The binary objective produces raw log-odds; .predict() returns probabilities.
    """
    resolved = cfg or get_market_config(market)
    seeds = _ENSEMBLE_SEEDS[:ensemble_size]
    models: list[lgb.Booster] = []

    num_boost_round = resolved.num_boost_round
    early_stopping = resolved.early_stopping_rounds

    for i, seed in enumerate(seeds):
        logger.info(
            "Direction ensemble member %d/%d (seed=%d) for %s",
            i + 1, ensemble_size, seed, market,
        )
        params = _get_direction_lgb_params(market, resolved)
        params["seed"] = seed
        params["feature_fraction_seed"] = seed
        params["bagging_seed"] = seed

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
            "Direction ensemble member %d/%d done: best_iter=%d",
            i + 1, ensemble_size,
            model.best_iteration
            if model.best_iteration >= 0
            else num_boost_round,
        )

    return models


# ---------------------------------------------------------------------------
# Walk-forward splits (same logic as prediction_service)
# ---------------------------------------------------------------------------


def _walk_forward_splits(
    unique_dates: list,
    n_folds: int,
    forward_days: int,
) -> list[tuple[list, list]]:
    """Generate expanding-window walk-forward splits with purge gap.

    Identical logic to PredictionService._walk_forward_splits() to ensure
    consistent evaluation methodology between ranking and direction models.
    """
    total = len(unique_dates)
    if n_folds <= 1:
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
        train_end_idx = val_start_idx - forward_days

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


# ---------------------------------------------------------------------------
# Probability calibration
# ---------------------------------------------------------------------------


def _fit_calibrator(
    predicted_probs: np.ndarray,
    true_labels: np.ndarray,
) -> tuple[Any, str]:
    """Fit a probability calibrator on validation predictions.

    Uses isotonic regression when sufficient samples (>1000) for a
    non-parametric flexible mapping. Falls back to Platt scaling
    (logistic sigmoid) for smaller validation sets.

    Returns:
        (calibrator_object, method_name)
    """
    n_samples = len(true_labels)

    if n_samples >= _ISOTONIC_MIN_SAMPLES:
        # Isotonic regression: non-parametric, monotone increasing mapping
        calibrator = IsotonicRegression(
            y_min=0.0, y_max=1.0, out_of_bounds="clip",
        )
        calibrator.fit(predicted_probs, true_labels)
        method = "isotonic"
        logger.info(
            "Fitted isotonic calibrator on %d validation samples",
            n_samples,
        )
    else:
        # Platt scaling: logistic regression on predicted probabilities
        calibrator = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
        calibrator.fit(
            predicted_probs.reshape(-1, 1),
            true_labels.astype(int),
        )
        method = "platt"
        logger.info(
            "Fitted Platt scaling calibrator on %d validation samples",
            n_samples,
        )

    return calibrator, method


def _apply_calibrator(
    calibrator: Any,
    raw_probs: np.ndarray,
) -> np.ndarray:
    """Apply a fitted calibrator to raw probabilities.

    Handles both IsotonicRegression and LogisticRegression calibrators.
    Returns calibrated probabilities clipped to [0, 1].
    """
    if isinstance(calibrator, IsotonicRegression):
        calibrated = calibrator.predict(raw_probs)
    elif isinstance(calibrator, LogisticRegression):
        calibrated = calibrator.predict_proba(
            raw_probs.reshape(-1, 1)
        )[:, 1]
    else:
        logger.warning(
            "Unknown calibrator type: %s, returning raw probabilities",
            type(calibrator).__name__,
        )
        return raw_probs

    return np.clip(calibrated, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------


def _get_model_dir(market: str, model_date: date) -> str:
    """Return the model directory path for a given market and date."""
    settings = get_settings()
    date_str = model_date.strftime("%Y%m%d")
    model_dir = str(Path(settings.PREDICTION_DATA_DIR) / market / date_str)
    os.makedirs(model_dir, exist_ok=True)
    return model_dir


def _save_direction_model(
    models: list[lgb.Booster],
    calibrator: Any,
    feature_cols: list[str],
    model_dir: str,
) -> None:
    """Save direction model ensemble + calibrator + feature metadata to disk.

    Files written:
        direction_model.pkl    - list[lgb.Booster]
        calibrator.pkl         - sklearn calibrator object
        direction_features.json - feature column names and count
    """
    # Save ensemble
    model_path = os.path.join(model_dir, "direction_model.pkl")
    joblib.dump(models, model_path)
    logger.info(
        "Saved direction model ensemble (%d members) to %s",
        len(models), model_path,
    )

    # Save calibrator
    calibrator_path = os.path.join(model_dir, "calibrator.pkl")
    joblib.dump(calibrator, calibrator_path)
    logger.info("Saved calibrator to %s", calibrator_path)

    # Save feature names
    features_meta = {
        "features": feature_cols,
        "count": len(feature_cols),
        "ensemble_size": len(models),
    }
    features_path = os.path.join(model_dir, "direction_features.json")
    with open(features_path, "w") as f:
        json.dump(features_meta, f, default=_numpy_default)
    logger.info(
        "Saved direction features metadata: %d features", len(feature_cols),
    )


async def _load_direction_model(
    market: str,
    target_date: date,
) -> tuple[Optional[str], Optional[list[lgb.Booster]], Any, list[str]]:
    """Load the latest direction model for a market.

    Searches backwards from target_date for up to 30 days to find
    the most recent direction model that exists on disk.

    Returns:
        (model_dir, models, calibrator, feature_cols) or
        (None, None, None, []) if not found.
    """
    settings = get_settings()
    base_dir = Path(settings.PREDICTION_DATA_DIR) / market

    # Search backwards from target_date
    for days_back in range(31):
        check_date = target_date - timedelta(days=days_back)
        date_str = check_date.strftime("%Y%m%d")
        model_dir = str(base_dir / date_str)
        model_path = os.path.join(model_dir, "direction_model.pkl")

        if not os.path.exists(model_path):
            continue

        # Found a model -- load it
        try:
            loaded = await asyncio.to_thread(joblib.load, model_path)
            if isinstance(loaded, list):
                models = loaded
            else:
                models = [loaded]

            # Load calibrator
            calibrator_path = os.path.join(model_dir, "calibrator.pkl")
            calibrator = None
            if os.path.exists(calibrator_path):
                calibrator = await asyncio.to_thread(
                    joblib.load, calibrator_path,
                )

            # Load feature names
            features_path = os.path.join(
                model_dir, "direction_features.json",
            )
            feature_cols: list[str] = []
            if os.path.exists(features_path):
                def _read_features():
                    with open(features_path) as f:
                        return json.load(f)

                meta = await asyncio.to_thread(_read_features)
                feature_cols = meta.get("features", [])
            else:
                # Fallback: use default feature names
                feature_cols = feature_service.get_feature_names()
                feature_cols.extend(MARKET_FEATURE_COLUMNS)
                logger.warning(
                    "direction_features.json not found, "
                    "using default feature list (%d)", len(feature_cols),
                )

            logger.info(
                "Loaded direction model from %s: %d members, "
                "%d features, calibrator=%s",
                model_path, len(models), len(feature_cols),
                type(calibrator).__name__ if calibrator else "None",
            )

            return model_dir, models, calibrator, feature_cols

        except Exception as e:
            logger.warning(
                "Failed to load direction model from %s: %s",
                model_path, e,
            )
            continue

    return None, None, None, []


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


async def _record_direction_model(
    db_pool,
    market: str,
    model_date: date,
    train_start: date,
    train_end: date,
    val_start: date,
    val_end: date,
    forward_days: int,
    feature_count: int,
    symbol_count: int,
    auc: float,
    brier_score: float,
    model_path: str,
    quality_passed: bool,
    extra_metadata: dict[str, Any] | None = None,
) -> Any:
    """Write direction model metadata to prediction_models table.

    Uses model_type='direction' to distinguish from ranking models.
    Returns model id.
    """
    metadata: dict[str, Any] = {
        "model_type": "direction",
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    metadata_json = json.dumps(metadata, default=_numpy_default)

    try:
        async with db_pool.acquire(timeout=10) as conn:
            model_id = await conn.fetchval(
                _SQL_INSERT_DIRECTION_MODEL,
                market,              # $1
                model_date,          # $2
                train_start,         # $3
                train_end,           # $4
                val_start,           # $5
                val_end,             # $6
                forward_days,        # $7
                feature_count,       # $8
                symbol_count,        # $9
                ["alpha158", "fundamental", "sentiment", "market"],  # $10
                None,                # $11 ic (not applicable)
                None,                # $12 icir (not applicable)
                None,                # $13 ndcg (not applicable)
                float(auc),          # $14
                float(brier_score),  # $15
                model_path,          # $16
                metadata_json,       # $17
                quality_passed,      # $18
            )
        return model_id
    except Exception as e:
        logger.error("Failed to record direction model in DB: %s", e)
        raise RuntimeError(f"Direction model DB recording failed: {e}") from e


async def _update_up_probabilities(
    db_pool,
    market: str,
    prediction_date: date,
    forward_days: int,
    symbols: np.ndarray,
    up_probabilities: np.ndarray,
) -> int:
    """Batch-UPDATE stock_predictions.up_probability for all symbols.

    Returns number of rows updated.
    """
    rows = [
        (
            float(prob),
            market,
            prediction_date,
            str(symbol),
            forward_days,
        )
        for symbol, prob in zip(symbols, up_probabilities)
    ]

    try:
        async with db_pool.acquire(timeout=30) as conn:
            await conn.executemany(_SQL_UPDATE_UP_PROBABILITY, rows)

            # Verify how many rows were actually updated.
            # executemany doesn't return per-row affected counts, so we
            # query the DB to confirm how many predictions got up_probability.
            actual_updated = await conn.fetchval(
                "SELECT COUNT(*) FROM stock_predictions "
                "WHERE market = $1 AND prediction_date = $2 "
                "AND forward_days = $3 AND up_probability IS NOT NULL",
                market, prediction_date, forward_days,
            )

        if actual_updated != len(rows):
            logger.warning(
                "Direction update mismatch: attempted=%d, "
                "verified=%d (market=%s, date=%s). "
                "Some ranking rows may not exist yet.",
                len(rows), actual_updated, market,
                prediction_date.isoformat(),
            )
        else:
            logger.info(
                "Updated %d/%d up_probability values: market=%s, date=%s",
                actual_updated, len(rows), market,
                prediction_date.isoformat(),
            )
        return actual_updated
    except Exception as e:
        logger.error(
            "Failed to update up_probability: %s", e,
        )
        return 0


# ---------------------------------------------------------------------------
# Symbol resolution (reuse from prediction_service)
# ---------------------------------------------------------------------------


async def _resolve_symbols(market: str) -> list[str]:
    """Resolve prediction universe symbols for a market.

    Delegates to PredictionService._resolve_symbols() to keep universe
    resolution logic in one place.
    """
    from app.services.prediction_service import prediction_service
    return await prediction_service._resolve_symbols(market)


# ---------------------------------------------------------------------------
# Close price fetch (reuse from prediction_service)
# ---------------------------------------------------------------------------


async def _fetch_close_prices(
    market: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Fetch close prices for all universe symbols.

    Delegates to PredictionService to reuse batched BackendDataClient logic.
    """
    from app.services.prediction_service import prediction_service
    symbols = await _resolve_symbols(market)
    return await prediction_service._fetch_close_prices(
        market, symbols, start_date, end_date,
    )
