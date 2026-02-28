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
):
    """Get the latest prediction results for a market."""
    market = market.lower()
    results = await prediction_service.get_latest_predictions(
        market=market, top_n=top_n, symbol=symbol,
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
