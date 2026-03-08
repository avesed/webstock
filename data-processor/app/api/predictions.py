"""Prediction API endpoints.

Supports ML prediction workflows: trigger training/inference,
retrieve results, query models, and backfill actual returns.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.prediction_schemas import (
    PredictionRunRequest,
    PredictionRunResponse,
    PredictionResult,
    PredictionTaskStatus,
    ModelInfo,
    ModelQualityUpdateRequest,
)
from app.models.backtest_schemas import (
    BacktestRequest,
    BacktestStartResponse,
    BacktestTaskStatus,
    BacktestSummary,
    BacktestDetail,
    BacktestListResponse,
)
from app.services.ml_backtest_service import backtest_service
from app.services.prediction_service import prediction_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.post("/{market}/run")
async def run_prediction(market: str, request: PredictionRunRequest = PredictionRunRequest()):
    """Trigger a prediction run (train + predict) for the given market.

    Non-blocking: returns task_id immediately. Poll /tasks/{task_id} for status.
    """
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")

    try:
        task_id = await prediction_service.run_prediction(
            market=market,
            force_retrain=request.force_retrain,
            forward_days=request.forward_days,
        )
    except RuntimeError as e:
        raise HTTPException(429, str(e))
    return PredictionRunResponse(task_id=task_id, market=market, status="pending")


@router.get("/{market}/latest")
async def get_latest_predictions(
    market: str,
    top_n: int = Query(50, ge=1, le=500),
    symbol: Optional[str] = Query(None),
    forward_days: Optional[int] = Query(None, ge=0, le=60),
):
    """Get the latest prediction results for a market.

    When forward_days is specified, only return predictions for that horizon.
    forward_days=0 returns combined multi-horizon signal.
    """
    market = market.lower()
    results = await prediction_service.get_latest_predictions(
        market=market, top_n=top_n, symbol=symbol, forward_days=forward_days,
    )
    return {"market": market, "count": len(results), "predictions": results}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get the status of a prediction task."""
    task = prediction_service.get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    return PredictionTaskStatus(
        task_id=task["task_id"],
        status=task["status"],
        progress=task["progress"],
        message=task["message"],
    )


@router.get("/models")
async def list_models(market: Optional[str] = Query(None)):
    """List available prediction models with metrics."""
    models = await prediction_service.get_models(market=market)
    return {"models": models}


@router.get("/{market}/history")
async def get_prediction_history(
    market: str,
    days: int = Query(30, ge=1, le=365),
):
    """Get historical prediction results with actual returns."""
    market = market.lower()
    history = await prediction_service.get_prediction_history(market=market, days=days)
    return {"market": market, "days": days, "count": len(history), "predictions": history}


@router.get("/{market}/accuracy")
async def get_accuracy(
    market: str,
    days: int = Query(30, ge=1, le=365),
):
    """Get prediction accuracy summary (direction accuracy, IC, ICIR)."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")
    return await prediction_service.get_accuracy(market=market, days=days)


@router.get("/models/{model_id}/feature-importance")
async def get_feature_importance(model_id: str):
    """Get feature importance for a specific model."""
    result = await prediction_service.get_feature_importance(model_id)
    if result is None:
        raise HTTPException(404, f"Model not found: {model_id}")
    return result


@router.put("/models/{model_id}/quality")
async def update_model_quality(model_id: str, request: ModelQualityUpdateRequest):
    """Admin override: mark model as approved/rejected."""
    try:
        success = await prediction_service.update_model_quality(
            model_id, request.quality_passed
        )
    except Exception as e:
        logger.error("Failed to update model quality for %s: %s", model_id, e, exc_info=True)
        raise HTTPException(500, "Failed to update model quality")
    if not success:
        raise HTTPException(404, f"Model not found: {model_id}")
    return {"model_id": model_id, "quality_passed": request.quality_passed}


@router.get("/{market}/performance")
async def get_performance_metrics(
    market: str,
    days: int = Query(90, ge=7, le=365),
):
    """Get model performance metrics over time (IC trend, hit rate, spread)."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")
    result = await prediction_service.get_performance_metrics(market=market, days=days)
    return result


@router.get("/{market}/turnover")
async def get_turnover_metrics(
    market: str,
    days: int = Query(90, ge=7, le=365),
    top_n: int = Query(20, ge=5, le=100),
):
    """Prediction rank stability: rank autocorrelation and top-N retention."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")
    result = await prediction_service.get_turnover_metrics(
        market=market, days=days, top_n=top_n,
    )
    return result


@router.get("/{market}/ic-decay")
async def get_ic_decay(
    market: str,
    days: int = Query(90, ge=7, le=365),
):
    """IC at multiple forward horizons (alpha decay curve)."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")
    result = await prediction_service.get_ic_decay(market=market, days=days)
    return result


@router.get("/{market}/attribution")
async def get_return_attribution(
    market: str,
    days: int = Query(90, ge=7, le=365),
    top_n: int = Query(20, ge=5, le=100),
):
    """Return attribution decomposition: sector, size, and alpha components."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")
    return await prediction_service.get_return_attribution(
        market=market, days=days, top_n=top_n,
    )


@router.get("/{market}/prediction-dates")
async def get_prediction_dates(
    market: str,
    n_dates: int = Query(2, ge=1, le=10),
    forward_days: int = Query(5, ge=0, le=60),
):
    """Get predictions for the last N prediction dates (for holdings change)."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")
    return await prediction_service.get_recent_predictions(
        market=market, n_dates=n_dates, forward_days=forward_days,
    )


@router.post("/backfill-returns")
async def backfill_returns():
    """Backfill actual returns for past predictions whose forward period has elapsed."""
    result = await prediction_service.backfill_returns()
    return result


@router.get("/fundamentals/status")
async def get_fundamentals_status():
    """Return fundamental data collection status.

    Reports the last update time and total symbol count in stock_fundamentals.
    """
    from app.core.settings_cache import settings_cache

    pool = settings_cache.pool
    if not pool:
        return {"last_updated": None, "total_symbols": 0}

    try:
        async with pool.acquire(timeout=10) as conn:
            row = await conn.fetchrow(
                "SELECT MAX(created_at) AS last_updated, "
                "COUNT(DISTINCT symbol) AS total_symbols "
                "FROM stock_fundamentals"
            )
    except Exception as e:
        logger.error("Failed to query fundamentals status: %s", e)
        raise HTTPException(status_code=500, detail="Failed to query fundamentals status")

    return {
        "last_updated": row["last_updated"].isoformat() if row["last_updated"] else None,
        "total_symbols": row["total_symbols"] or 0,
    }


# In-memory set tracking running fundamental collections
_running_fundamental_collections: set[str] = set()

# In-memory set tracking running backfill operations
_running_backfill_operations: set[str] = set()


@router.post("/fundamentals/{market}/collect")
async def collect_fundamentals(market: str):
    """Trigger fundamental data collection for a market.

    Non-blocking: starts collection in background and returns immediately.
    """
    market = market.lower()
    if market not in ("cn", "us", "hk"):
        raise HTTPException(status_code=400, detail=f"Invalid market: {market}")

    if market in _running_fundamental_collections:
        raise HTTPException(status_code=409, detail=f"Fundamental collection already running for {market}")

    async def _run():
        _running_fundamental_collections.add(market)
        try:
            from app.services.fundamental_service import fundamental_service
            await fundamental_service.collect_market(market)
        except Exception as e:
            logger.error("Background fundamental collection failed for %s: %s", market, e, exc_info=True)
        finally:
            _running_fundamental_collections.discard(market)

    asyncio.create_task(_run())
    return {"status": "started", "market": market}


# In-memory guards for new signal collection endpoints
_running_signal_collections: set[str] = set()


@router.post("/earnings/collect/{market}")
async def collect_earnings(market: str):
    """Trigger EPS surprise event collection for a market.

    Fetches ticker.earnings_dates for all universe symbols and upserts
    into stock_earnings_events.  Non-blocking: returns immediately.
    """
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(status_code=400, detail=f"Invalid market: {market}")

    guard_key = f"earnings:{market}"
    if guard_key in _running_signal_collections:
        raise HTTPException(status_code=409, detail=f"Earnings collection already running for {market}")

    _running_signal_collections.add(guard_key)

    async def _run():
        try:
            from app.services.fundamental_service import fundamental_service
            from app.services.earnings_service import earnings_service
            symbols = await fundamental_service._resolve_symbols(market)
            if symbols:
                await earnings_service.collect_earnings_events(market, symbols)
        except Exception as e:
            logger.error("Background earnings collection failed for %s: %s", market, e, exc_info=True)
        finally:
            _running_signal_collections.discard(guard_key)

    asyncio.create_task(_run())
    return {"status": "started", "market": market}


@router.post("/analyst/collect/{market}")
async def collect_analyst(market: str):
    """Trigger analyst snapshot + insider activity collection for a market.

    Fetches analyst_price_targets, recommendations_summary, eps_revisions,
    growth_estimates, and insider_purchases for all universe symbols.
    Non-blocking: returns immediately.
    """
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(status_code=400, detail=f"Invalid market: {market}")

    guard_key = f"analyst:{market}"
    if guard_key in _running_signal_collections:
        raise HTTPException(status_code=409, detail=f"Analyst collection already running for {market}")

    _running_signal_collections.add(guard_key)

    async def _run():
        try:
            from app.services.fundamental_service import fundamental_service
            from app.services.analyst_service import analyst_service
            symbols = await fundamental_service._resolve_symbols(market)
            if symbols:
                await analyst_service.collect_analyst_snapshots(market, symbols)
        except Exception as e:
            logger.error("Background analyst collection failed for %s: %s", market, e, exc_info=True)
        finally:
            _running_signal_collections.discard(guard_key)

    asyncio.create_task(_run())
    return {"status": "started", "market": market}


@router.post("/options/collect/{market}")
async def collect_options(market: str):
    """Trigger options put/call ratio collection for a market.

    US only.  Fetches option chains for the nearest ~30-day expiry per symbol
    and upserts into stock_options_flow.  Non-blocking: returns immediately.
    """
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(status_code=400, detail=f"Invalid market: {market}")

    guard_key = f"options:{market}"
    if guard_key in _running_signal_collections:
        raise HTTPException(status_code=409, detail=f"Options collection already running for {market}")

    _running_signal_collections.add(guard_key)

    async def _run():
        try:
            from app.services.fundamental_service import fundamental_service
            from app.services.options_service import options_service
            symbols = await fundamental_service._resolve_symbols(market)
            if symbols:
                await options_service.collect_options_flow(market, symbols)
        except Exception as e:
            logger.error("Background options collection failed for %s: %s", market, e, exc_info=True)
        finally:
            _running_signal_collections.discard(guard_key)

    asyncio.create_task(_run())
    return {"status": "started", "market": market}


@router.post("/sectors/{market}/collect")
async def collect_sectors(market: str):
    """Trigger sector/industry classification collection for a market.

    US/HK: yfinance sector/industry (GICS).
    CN: akshare industry classification via data-service.

    Only re-fetches stale entries (>7 days old). Non-blocking.
    """
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(status_code=400, detail=f"Invalid market: {market}")

    guard_key = f"sectors:{market}"
    if guard_key in _running_signal_collections:
        raise HTTPException(status_code=409, detail=f"Sector collection already running for {market}")

    _running_signal_collections.add(guard_key)

    async def _run():
        try:
            from app.services.fundamental_service import fundamental_service
            await fundamental_service.collect_sector_data(market)
        except Exception as e:
            logger.error("Background sector collection failed for %s: %s", market, e, exc_info=True)
        finally:
            _running_signal_collections.discard(guard_key)

    asyncio.create_task(_run())
    return {"status": "started", "market": market}


@router.get("/sectors/{market}")
async def get_sectors(market: str):
    """Get sector classification data for a market."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(status_code=400, detail=f"Invalid market: {market}")

    from app.services.fundamental_service import fundamental_service
    sector_map = await fundamental_service.get_sector_map(market)
    # Count unique sectors
    sector_counts: dict[str, int] = {}
    for sector in sector_map.values():
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    return {
        "market": market,
        "total_symbols": len(sector_map),
        "unique_sectors": len(sector_counts),
        "sector_counts": sector_counts,
    }


@router.post("/fundamentals/backfill/{market}")
async def backfill_fundamentals(market: str):
    """Backfill historical quarterly fundamental data for US/HK markets.

    Fetches quarterly income statements and balance sheets from yfinance,
    computes derived metrics using daily close prices from stock_daily_bars,
    and writes to stock_fundamentals table.

    Non-blocking: starts backfill in background and returns immediately.
    Long-running operation: progress reported via Redis key
    ``fundamentals:backfill:{market}:progress``.
    """
    market = market.lower()
    if market not in ("us", "hk"):
        raise HTTPException(
            status_code=400,
            detail=f"Quarterly backfill only supported for US/HK markets, got: {market}",
        )

    if market in _running_backfill_operations:
        logger.warning("Backfill already running for %s, rejecting duplicate request", market)
        raise HTTPException(
            status_code=409,
            detail=f"Fundamental backfill already running for {market}",
        )

    # Guard before create_task to prevent TOCTOU race
    _running_backfill_operations.add(market)

    async def _run():
        try:
            from app.services.fundamental_service import fundamental_service
            await fundamental_service.backfill_us_hk_quarterly(market)
        except Exception as e:
            logger.error(
                "Background fundamental backfill failed for %s: %s",
                market, e, exc_info=True,
            )
        finally:
            _running_backfill_operations.discard(market)

    asyncio.create_task(_run())
    return {"status": "started", "market": market}


# ---------------------------------------------------------------------------
# Backtest endpoints
# ---------------------------------------------------------------------------

@router.post("/{market}/backtest")
async def start_backtest(market: str, request: BacktestRequest):
    """Start a historical cutoff backtest.

    Non-blocking: returns task_id + backtest_id immediately.
    Poll /backtests/tasks/{task_id} for progress.
    """
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")

    try:
        task_id, backtest_id = await backtest_service.start_backtest(
            market=market,
            cutoff_date=request.cutoff_date,
            validation_days=request.validation_days,
            forward_days=request.forward_days,
            config_override=request.config_override,
            use_llm_agents=request.use_llm_agents,
            max_iterations=request.max_iterations,
        )
    except RuntimeError as e:
        raise HTTPException(429, str(e))

    return BacktestStartResponse(
        task_id=task_id,
        backtest_id=backtest_id,
        market=market,
        status="pending",
    )


@router.get("/backtests/tasks/{task_id}")
async def get_backtest_task_status(task_id: str):
    """Get real-time backtest task progress with structured observability."""
    task = backtest_service.get_task(task_id)
    if not task:
        raise HTTPException(404, f"Backtest task not found: {task_id}")
    return task


@router.get("/{market}/backtests")
async def list_backtests(
    market: str,
    limit: int = Query(50, ge=1, le=200),
):
    """List historical backtests for a market."""
    market = market.lower()
    if market not in ("us", "hk", "cn"):
        raise HTTPException(400, f"Unsupported market: {market}")

    backtests = await backtest_service.list_backtests(market=market, limit=limit)
    return BacktestListResponse(
        backtests=[BacktestSummary(
            id=str(b["id"]),
            market=b["market"],
            cutoff_date=b["cutoff_date"],
            validation_days=b["validation_days"],
            forward_days=b["forward_days"],
            status=b["status"],
            train_ic=b.get("train_ic"),
            train_icir=b.get("train_icir"),
            val_ic=b.get("val_ic"),
            val_icir=b.get("val_icir"),
            val_direction_accuracy=b.get("val_direction_accuracy"),
            val_spread=b.get("val_spread"),
            agent_iteration=b.get("agent_iteration"),
            duration_seconds=b.get("duration_seconds"),
            created_at=b.get("created_at"),
            completed_at=b.get("completed_at"),
        ) for b in backtests],
        total=len(backtests),
    )


@router.get("/backtests/{backtest_id}")
async def get_backtest_detail(backtest_id: str):
    """Get full backtest detail including validation metrics and results."""
    result = await backtest_service.get_backtest(backtest_id)
    if not result:
        raise HTTPException(404, f"Backtest not found: {backtest_id}")

    return BacktestDetail(
        id=str(result["id"]),
        market=result["market"],
        cutoff_date=result["cutoff_date"],
        validation_days=result["validation_days"],
        forward_days=result["forward_days"],
        status=result["status"],
        config_override=result.get("config_override"),
        effective_config=result.get("effective_config", {}),
        train_ic=result.get("train_ic"),
        train_icir=result.get("train_icir"),
        train_ndcg=result.get("train_ndcg"),
        fold_ics=result.get("fold_ics"),
        ensemble_size=result.get("ensemble_size"),
        feature_count=result.get("feature_count"),
        symbol_count=result.get("symbol_count"),
        val_ic=result.get("val_ic"),
        val_icir=result.get("val_icir"),
        val_direction_accuracy=result.get("val_direction_accuracy"),
        val_spread=result.get("val_spread"),
        val_q1_return=result.get("val_q1_return"),
        val_q5_return=result.get("val_q5_return"),
        val_hit_rate=result.get("val_hit_rate"),
        val_max_drawdown=result.get("val_max_drawdown"),
        results=result.get("results", {}),
        error=result.get("error"),
        agent_run_id=result.get("agent_run_id"),
        agent_iteration=result.get("agent_iteration"),
        duration_seconds=result.get("duration_seconds"),
        created_at=result.get("created_at"),
        completed_at=result.get("completed_at"),
    )


@router.delete("/backtests/{backtest_id}")
async def delete_backtest(backtest_id: str):
    """Delete a backtest record."""
    success = await backtest_service.delete_backtest(backtest_id)
    if not success:
        raise HTTPException(404, f"Backtest not found: {backtest_id}")
    return {"deleted": True, "backtest_id": backtest_id}
