"""Admin API endpoints for ML prediction management.

Provides admin-only endpoints for:
- Triggering prediction runs per market
- Viewing prediction results, models, and accuracy
- Managing RD-Agent research sessions
- Managing discovered factors and universes
- Viewing fundamental data collection status
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.database import get_db
from app.models.prediction import MLBacktest, PredictionUniverse
from app.models.user import User
from app.services.alphaforge_client import (
    AlphaForgeServiceError,
    get_alphaforge_client,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - Predictions"])

VALID_MARKETS = {"cn", "us", "hk"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_service_error(e: AlphaForgeServiceError) -> str:
    """Sanitize data-processor errors for frontend consumption.

    Strips internal URLs and container names while preserving the useful
    HTTP status code and error message.
    """
    msg = str(e)
    # Strip internal container URLs
    msg = re.sub(r"https?://[a-z0-9._-]+:\d+", "<service>", msg)
    return msg


def _validate_market(market: str) -> str:
    """Validate and return market string, raising 400 on invalid."""
    m = market.lower()
    if m not in VALID_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}",
        )
    return m


# ---------------------------------------------------------------------------
# Pydantic schemas for request bodies
# ---------------------------------------------------------------------------


class TriggerPredictionRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    force_retrain: bool = False
    forward_days: int = Field(default=5, ge=1, le=20)


class StartRdagentRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    universe_id: Optional[str] = None
    max_rounds: int = Field(default=30, ge=1, le=200)


class ModelQualityUpdateBody(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    quality_passed: bool


class ToggleFactorRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    is_active: bool


class CreateUniverseRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    market: str = Field(..., min_length=2, max_length=10)
    universe_type: str = Field(..., pattern=r"^(index|custom)$")
    index_code: Optional[str] = Field(None, max_length=20)
    symbols: Optional[List[str]] = None
    is_default: bool = False


class UpdateUniverseRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    market: Optional[str] = Field(None, min_length=2, max_length=10)
    universe_type: Optional[str] = Field(None, pattern=r"^(index|custom)$")
    index_code: Optional[str] = Field(None, max_length=20)
    symbols: Optional[List[str]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class AgentBacktestRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    cutoff_date: str = Field(..., description="Training cutoff date (YYYY-MM-DD)")
    validation_days: int = Field(default=60, ge=10, le=250)
    forward_days: int = Field(default=5, ge=1, le=30)
    max_iterations: int = Field(default=3, ge=1, le=10)


# ---------------------------------------------------------------------------
# GET /predictions/status — overall status per market
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/status",
    summary="Get prediction status per market",
    description="Returns overall prediction status including latest model, prediction count, and accuracy for each market.",
)
async def get_prediction_status(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Aggregate status from data-processor for all markets."""
    client = await get_alphaforge_client()
    result: Dict[str, Any] = {}

    for market in sorted(VALID_MARKETS):
        try:
            models_resp = await client.get_models(market=market)
            latest_resp = await client.get_latest_predictions(market=market, top_n=1)
            result[market] = {
                "models": models_resp,
                "latestPredictions": latest_resp,
            }
        except AlphaForgeServiceError as e:
            logger.warning("Failed to get prediction status for %s: %s", market, e)
            result[market] = {"error": _sanitize_service_error(e)}

    return {"status": "ok", "markets": result}


# ---------------------------------------------------------------------------
# POST /predictions/{market}/trigger — manual prediction trigger
# ---------------------------------------------------------------------------


@router.post(
    "/predictions/{market}/trigger",
    summary="Trigger prediction run",
    description="Manually trigger a prediction pipeline run for the specified market.",
)
async def trigger_prediction(
    market: str,
    request: TriggerPredictionRequest = Body(default=TriggerPredictionRequest()),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.trigger_prediction(
            market=market,
            force_retrain=request.force_retrain,
            forward_days=request.forward_days,
        )
        logger.info(
            "Admin %s triggered prediction for market=%s (force_retrain=%s, forward_days=%d)",
            current_user.email, market, request.force_retrain, request.forward_days,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# GET /predictions/{market}/latest — latest predictions
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/{market}/latest",
    summary="Get latest predictions",
    description="Returns the most recent prediction results for a market, sorted by predicted score.",
)
async def get_latest_predictions(
    market: str,
    top_n: int = Query(default=50, ge=1, le=500),
    symbol: Optional[str] = Query(default=None),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.get_latest_predictions(
            market=market,
            top_n=top_n,
            symbol=symbol,
        )
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# GET /predictions/models — model list with metrics
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/models",
    summary="List prediction models",
    description="Returns all trained prediction models with their IC/ICIR/NDCG metrics.",
)
async def get_prediction_models(
    market: Optional[str] = Query(default=None),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    if market:
        market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.get_models(market=market)
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# GET /predictions/models/{model_id}/feature-importance
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/models/{model_id}/feature-importance",
    summary="Get feature importance",
    description="Returns feature importance scores for a specific trained model.",
)
async def get_feature_importance(
    model_id: str,
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Get feature importance for a specific trained model."""
    try:
        client = await get_alphaforge_client()
        return await client.get_feature_importance(model_id)
    except AlphaForgeServiceError as e:
        status = e.status_code or 502
        raise HTTPException(status_code=status, detail=_sanitize_service_error(e))


# ---------------------------------------------------------------------------
# PUT /predictions/models/{model_id}/quality — admin quality override
# ---------------------------------------------------------------------------


@router.put(
    "/predictions/models/{model_id}/quality",
    summary="Override model quality",
    description="Admin override to approve or reject a trained model's quality flag.",
)
async def update_model_quality(
    model_id: str,
    request: ModelQualityUpdateBody,
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Admin override: approve or reject a model."""
    try:
        client = await get_alphaforge_client()
        return await client.update_model_quality(model_id, request.quality_passed)
    except AlphaForgeServiceError as e:
        status = e.status_code or 502
        raise HTTPException(status_code=status, detail=_sanitize_service_error(e))


# ---------------------------------------------------------------------------
# GET /predictions/{market}/accuracy — prediction accuracy
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/{market}/accuracy",
    summary="Get prediction accuracy",
    description="Returns prediction accuracy history (hit rate, rank correlation) over recent days.",
)
async def get_prediction_accuracy(
    market: str,
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.get_accuracy(market=market, days=days)
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# GET /predictions/{market}/direction/accuracy — direction model accuracy
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/{market}/direction/accuracy",
    summary="Get direction model accuracy",
    description="Returns direction model accuracy metrics (AUC, Brier, calibration, hit rate).",
)
async def get_direction_accuracy(
    market: str,
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.get_direction_accuracy(market=market, days=days)
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# GET /predictions/{market}/performance — model performance metrics
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/{market}/performance",
    summary="Get performance metrics",
    description="Returns model performance metrics (IC, ICIR, NDCG trends) over time for a market.",
)
async def get_performance_metrics(
    market: str,
    days: int = Query(90, ge=7, le=365),
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Get model performance metrics over time for a market."""
    market = _validate_market(market)
    try:
        client = await get_alphaforge_client()
        return await client.get_performance_metrics(market, days)
    except AlphaForgeServiceError as e:
        status = e.status_code or 502
        raise HTTPException(status_code=status, detail=_sanitize_service_error(e))


# ---------------------------------------------------------------------------
# Signal quality — IC decay, turnover, sectors
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/{market}/ic-decay",
    summary="Get IC decay curve",
    description="Returns IC decay across multiple horizons for a market.",
)
async def get_ic_decay(
    market: str,
    days: int = Query(60, ge=7, le=365),
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    try:
        client = await get_alphaforge_client()
        return await client.get_ic_decay(market, days)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get(
    "/predictions/{market}/turnover",
    summary="Get prediction turnover",
    description="Returns rank autocorrelation and top-N retention metrics.",
)
async def get_turnover(
    market: str,
    days: int = Query(60, ge=7, le=365),
    top_n: int = Query(20, ge=5, le=100),
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    try:
        client = await get_alphaforge_client()
        return await client.get_turnover(market, days, top_n)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get(
    "/predictions/sectors/{market}",
    summary="Get sector data summary",
    description="Returns sector data coverage and distribution for a market.",
)
async def get_sectors(
    market: str,
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    try:
        client = await get_alphaforge_client()
        return await client.get_sectors(market)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.post(
    "/predictions/sectors/{market}/collect",
    summary="Trigger sector collection",
    description="Trigger sector data collection for a market.",
)
async def collect_sectors(
    market: str,
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    try:
        client = await get_alphaforge_client()
        return await client.collect_sectors(market)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get(
    "/predictions/{market}/attribution",
    summary="Get return attribution",
    description="Decompose model returns into sector, size, and alpha components.",
)
async def get_attribution(
    market: str,
    days: int = Query(90, ge=7, le=365),
    top_n: int = Query(20, ge=5, le=100),
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    try:
        client = await get_alphaforge_client()
        return await client.get_attribution(market, days, top_n)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get(
    "/predictions/{market}/prediction-dates",
    summary="Get predictions for recent dates",
    description="Returns predictions for last N dates for holdings change analysis.",
)
async def get_prediction_dates(
    market: str,
    n_dates: int = Query(2, ge=1, le=10),
    forward_days: int = Query(5, ge=0, le=60),
    _user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    try:
        client = await get_alphaforge_client()
        return await client.get_prediction_dates(market, n_dates, forward_days)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


# ---------------------------------------------------------------------------
# GET /predictions/tasks/{task_id} — task polling
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/tasks/{task_id}",
    summary="Poll prediction task",
    description="Check the status and progress of a prediction task.",
)
async def get_prediction_task(
    task_id: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    client = await get_alphaforge_client()
    try:
        return await client.get_prediction_task(task_id)
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# RD-Agent endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/predictions/rdagent/{market}/start",
    summary="Start RD-Agent session",
    description="Start an RD-Agent research session that discovers alpha factors for the specified market.",
)
async def start_rdagent(
    market: str,
    request: StartRdagentRequest = Body(default=StartRdagentRequest()),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.start_rdagent(
            market=market,
            universe_id=request.universe_id,
            max_rounds=request.max_rounds,
        )
        logger.info(
            "Admin %s started RD-Agent for market=%s (max_rounds=%d)",
            current_user.email, market, request.max_rounds,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


@router.get(
    "/predictions/rdagent/{market}/status",
    summary="Get RD-Agent status",
    description="Returns the current status of the RD-Agent research session for a market.",
)
async def get_rdagent_status(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.get_rdagent_status(market=market)
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


@router.post(
    "/predictions/rdagent/{market}/stop",
    summary="Stop RD-Agent session",
    description="Stop a running RD-Agent research session for a market.",
)
async def stop_rdagent(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.stop_rdagent(market=market)
        logger.info("Admin %s stopped RD-Agent for market=%s", current_user.email, market)
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# Factor management
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/factors",
    summary="List discovered factors",
    description="Returns all LLM-discovered alpha factors with their IC/ICIR metrics.",
)
async def get_factors(
    market: Optional[str] = Query(default=None),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    if market:
        market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.get_rdagent_factors(market=market)
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


@router.put(
    "/predictions/factors/{factor_id}",
    summary="Toggle factor active status",
    description="Enable or disable a discovered factor for use in prediction models.",
)
async def toggle_factor(
    factor_id: str,
    request: ToggleFactorRequest,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    client = await get_alphaforge_client()
    try:
        resp = await client.toggle_factor(factor_id, request.is_active)
        logger.info(
            "Admin %s toggled factor %s to is_active=%s",
            current_user.email, factor_id, request.is_active,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# Universe CRUD (direct DB via async SQLAlchemy)
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/universes",
    summary="List prediction universes",
    description="Returns all configured stock universe definitions.",
)
async def list_universes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    result = await db.execute(
        select(PredictionUniverse).order_by(
            PredictionUniverse.market,
            PredictionUniverse.name,
        )
    )
    universes = result.scalars().all()
    return {
        "universes": [
            {
                "id": str(u.id),
                "name": u.name,
                "market": u.market,
                "universeType": u.universe_type,
                "indexCode": u.index_code,
                "symbols": u.symbols,
                "isDefault": u.is_default,
                "isActive": u.is_active,
                "createdAt": u.created_at.isoformat() if u.created_at else None,
                "updatedAt": u.updated_at.isoformat() if u.updated_at else None,
            }
            for u in universes
        ],
    }


@router.post(
    "/predictions/universes",
    summary="Create prediction universe",
    description="Create a new stock universe definition for prediction pipelines.",
    status_code=201,
)
async def create_universe(
    request: CreateUniverseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(request.market)

    # Validate: index type requires index_code, custom type requires symbols
    if request.universe_type == "index" and not request.index_code:
        raise HTTPException(status_code=400, detail="index_code is required for index-type universes")
    if request.universe_type == "custom" and not request.symbols:
        raise HTTPException(status_code=400, detail="symbols list is required for custom-type universes")

    # Check uniqueness
    existing = await db.execute(
        select(PredictionUniverse).where(
            PredictionUniverse.name == request.name,
            PredictionUniverse.market == market,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Universe '{request.name}' already exists for market '{market}'",
        )

    # If setting as default, clear existing default for this market
    if request.is_default:
        await db.execute(
            update(PredictionUniverse)
            .where(
                PredictionUniverse.market == market,
                PredictionUniverse.is_default == True,  # noqa: E712
            )
            .values(is_default=False)
        )

    universe = PredictionUniverse(
        name=request.name,
        market=market,
        universe_type=request.universe_type,
        index_code=request.index_code,
        symbols=request.symbols,
        is_default=request.is_default,
    )
    db.add(universe)
    await db.commit()
    await db.refresh(universe)

    logger.info(
        "Admin %s created universe '%s' for market=%s",
        current_user.email, request.name, market,
    )

    return {
        "id": str(universe.id),
        "name": universe.name,
        "market": universe.market,
        "universeType": universe.universe_type,
        "indexCode": universe.index_code,
        "symbols": universe.symbols,
        "isDefault": universe.is_default,
        "isActive": universe.is_active,
        "createdAt": universe.created_at.isoformat() if universe.created_at else None,
        "updatedAt": universe.updated_at.isoformat() if universe.updated_at else None,
    }


@router.put(
    "/predictions/universes/{universe_id}",
    summary="Update prediction universe",
    description="Update an existing stock universe definition.",
)
async def update_universe(
    universe_id: UUID,
    request: UpdateUniverseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    result = await db.execute(
        select(PredictionUniverse).where(PredictionUniverse.id == universe_id)
    )
    universe = result.scalar_one_or_none()
    if not universe:
        raise HTTPException(status_code=404, detail="Universe not found")

    # Apply partial updates
    if request.name is not None:
        universe.name = request.name
    if request.market is not None:
        universe.market = _validate_market(request.market)
    if request.universe_type is not None:
        universe.universe_type = request.universe_type
    if request.index_code is not None:
        universe.index_code = request.index_code
    if request.symbols is not None:
        universe.symbols = request.symbols
    if request.is_active is not None:
        universe.is_active = request.is_active

    # Handle default flag: clear others in same market when setting
    if request.is_default is not None:
        if request.is_default:
            await db.execute(
                update(PredictionUniverse)
                .where(
                    PredictionUniverse.market == universe.market,
                    PredictionUniverse.is_default == True,  # noqa: E712
                    PredictionUniverse.id != universe_id,
                )
                .values(is_default=False)
            )
        universe.is_default = request.is_default

    universe.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(universe)

    logger.info(
        "Admin %s updated universe %s ('%s')",
        current_user.email, universe_id, universe.name,
    )

    return {
        "id": str(universe.id),
        "name": universe.name,
        "market": universe.market,
        "universeType": universe.universe_type,
        "indexCode": universe.index_code,
        "symbols": universe.symbols,
        "isDefault": universe.is_default,
        "isActive": universe.is_active,
        "createdAt": universe.created_at.isoformat() if universe.created_at else None,
        "updatedAt": universe.updated_at.isoformat() if universe.updated_at else None,
    }


@router.delete(
    "/predictions/universes/{universe_id}",
    summary="Delete prediction universe",
    description="Delete a stock universe definition. Cannot delete a default universe.",
)
async def delete_universe(
    universe_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    result = await db.execute(
        select(PredictionUniverse).where(PredictionUniverse.id == universe_id)
    )
    universe = result.scalar_one_or_none()
    if not universe:
        raise HTTPException(status_code=404, detail="Universe not found")

    if universe.is_default:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a default universe. Set another universe as default first.",
        )

    name = universe.name
    market = universe.market
    await db.execute(
        delete(PredictionUniverse).where(PredictionUniverse.id == universe_id)
    )
    await db.commit()

    logger.info(
        "Admin %s deleted universe %s ('%s', market=%s)",
        current_user.email, universe_id, name, market,
    )

    return {"status": "ok", "message": f"Universe '{name}' deleted"}


# ---------------------------------------------------------------------------
# GET /predictions/fundamentals/status — fundamental collection status
# ---------------------------------------------------------------------------


@router.get(
    "/predictions/fundamentals/status",
    summary="Get fundamental data status",
    description="Returns the status of fundamental data collection from data-processor.",
)
async def get_fundamentals_status(
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    client = await get_alphaforge_client()
    try:
        return await client.get_fundamentals_status()
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# POST /predictions/fundamentals/{market}/collect — manual fundamental trigger
# ---------------------------------------------------------------------------


@router.post(
    "/predictions/fundamentals/{market}/collect",
    summary="Trigger fundamental data collection",
    description="Manually trigger fundamental data collection for a market via data-processor.",
)
async def collect_fundamentals(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.collect_fundamentals(market)
        logger.info(
            "Admin %s triggered fundamental collection for market=%s",
            current_user.email, market,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


# ---------------------------------------------------------------------------
# POST /predictions/fundamentals/backfill/{market} — historical backfill
# ---------------------------------------------------------------------------


@router.post(
    "/predictions/fundamentals/backfill/{market}",
    summary="Backfill historical quarterly fundamentals",
    description=(
        "Trigger historical quarterly fundamental backfill for US/HK markets. "
        "Fetches yfinance quarterly financial statements and computes derived "
        "metrics. Long-running background operation."
    ),
)
async def backfill_fundamentals(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    market = market.lower()
    if market not in ("us", "hk"):
        raise HTTPException(
            status_code=400,
            detail=f"Quarterly backfill only supported for US/HK markets, got: {market}",
        )
    client = await get_alphaforge_client()
    try:
        resp = await client.backfill_fundamentals(market)
        logger.info(
            "Admin %s triggered fundamental backfill for market=%s",
            current_user.email, market,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=_sanitize_service_error(e),
        )


@router.post("/predictions/earnings/collect/{market}", summary="Trigger EPS surprise event collection")
async def trigger_earnings_collection(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger EPS surprise event collection for a market.

    Fetches historical earnings dates and EPS actuals/estimates from yfinance
    and upserts into stock_earnings_events.  Non-blocking.
    """
    m = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.collect_earnings(m)
        logger.info(
            "Admin %s triggered earnings collection for market=%s",
            current_user.email, m,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.post("/predictions/analyst/collect/{market}", summary="Trigger analyst snapshot collection")
async def trigger_analyst_collection(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger analyst snapshot and insider activity collection for a market.

    Fetches analyst price targets, recommendations, EPS revisions, growth estimates,
    and insider transactions.  Non-blocking.
    """
    m = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.collect_analyst(m)
        logger.info(
            "Admin %s triggered analyst collection for market=%s",
            current_user.email, m,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.post("/predictions/options/collect/{market}", summary="Trigger options put/call ratio collection")
async def trigger_options_collection(
    market: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Trigger options put/call ratio collection for a market.

    US only.  Fetches options chains for the nearest ~30-day expiry per symbol
    and upserts into stock_options_flow.  Non-blocking.
    """
    m = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.collect_options(m)
        logger.info(
            "Admin %s triggered options collection for market=%s",
            current_user.email, m,
        )
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


# ---------------------------------------------------------------------------
# Backtest endpoints — proxy to data-processor
# ---------------------------------------------------------------------------


@router.post("/predictions/{market}/backtest", summary="Start historical cutoff backtest")
async def start_backtest(
    market: str,
    body: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    m = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        resp = await client.start_prediction_backtest(m, body)
        logger.info("Admin %s started backtest for market=%s", current_user.email, m)
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get("/predictions/backtests/tasks/{task_id}", summary="Poll backtest task status")
async def get_backtest_task(
    task_id: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    client = await get_alphaforge_client()
    try:
        return await client.get_prediction_backtest_task(task_id)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get("/predictions/{market}/backtests", summary="List backtests for market")
async def list_backtests(
    market: str,
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    m = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.list_prediction_backtests(m, limit=limit)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get("/predictions/backtests/{backtest_id}", summary="Get backtest detail")
async def get_backtest_detail(
    backtest_id: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    client = await get_alphaforge_client()
    try:
        return await client.get_prediction_backtest(backtest_id)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.delete("/predictions/backtests/{backtest_id}", summary="Delete backtest")
async def delete_backtest(
    backtest_id: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    client = await get_alphaforge_client()
    try:
        resp = await client.delete_prediction_backtest(backtest_id)
        logger.info("Admin %s deleted backtest %s", current_user.email, backtest_id)
        return resp
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


@router.get(
    "/predictions/ml-tools/tasks/{task_id}",
    summary="Poll ML tools sub-task status",
    description="Poll a data-processor ml-tools task (training or rolling backtest) for progress.",
)
async def get_ml_tools_task(
    task_id: str,
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    client = await get_alphaforge_client()
    try:
        return await client.ml_get_training_task(task_id)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_service_error(e))


# ---------------------------------------------------------------------------
# ML Agent -- LLM-driven backtest optimization
# ---------------------------------------------------------------------------


@router.post(
    "/predictions/{market}/agent-backtest",
    summary="Start agent-driven backtest",
    description=(
        "Start an LLM agent-driven backtest with iterative optimization. "
        "The agent profiles data, generates training configs, and iterates "
        "based on training results."
    ),
)
async def start_agent_backtest(
    market: str,
    request: AgentBacktestRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Start an agent-driven backtest with LLM optimization loop."""
    market = _validate_market(market)

    from datetime import date as date_type

    try:
        cutoff = date_type.fromisoformat(request.cutoff_date)
    except ValueError:
        raise HTTPException(400, "Invalid cutoff_date format, use YYYY-MM-DD")

    from app.services.ml_agent_service import ml_agent_service

    try:
        backtest_id = await ml_agent_service.create_session(
            market=market,
            cutoff_date=cutoff,
            validation_days=request.validation_days,
            forward_days=request.forward_days,
            max_iterations=request.max_iterations,
            db=db,
        )
    except Exception as e:
        logger.error("Agent backtest creation failed: %s", e, exc_info=True)
        raise HTTPException(500, f"Agent backtest failed: {str(e)[:500]}")

    # Dispatch the heavy agent loop to a Celery worker asynchronously
    from worker.tasks.ml_agent_tasks import run_ml_agent_session

    run_ml_agent_session.delay(
        backtest_id=backtest_id,
        market=market,
        cutoff_date=request.cutoff_date,
        validation_days=request.validation_days,
        forward_days=request.forward_days,
        max_iterations=request.max_iterations,
        user_id=current_user.id,
    )

    logger.info(
        "Admin %s started agent backtest for market=%s (cutoff=%s, max_iter=%d)",
        current_user.email, market, request.cutoff_date, request.max_iterations,
    )
    return {"backtest_id": backtest_id, "status": "pending"}


@router.get(
    "/predictions/agent-tasks/{backtest_id}",
    summary="Get agent backtest status",
    description="Returns the current status and metrics of an agent-driven backtest session.",
)
async def get_agent_task_status(
    backtest_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """Get agent backtest session status."""
    from uuid import UUID as UUIDType

    try:
        bt_id = UUIDType(backtest_id)
    except ValueError:
        raise HTTPException(400, "Invalid backtest_id format")

    backtest = await db.get(MLBacktest, bt_id)
    if not backtest:
        raise HTTPException(404, f"Backtest {backtest_id} not found")

    result: Dict[str, Any] = {
        "backtest_id": str(backtest.id),
        "market": backtest.market,
        "status": backtest.status,
        "cutoff_date": backtest.cutoff_date.isoformat() if backtest.cutoff_date else None,
        "agent_iteration": backtest.agent_iteration,
        "agent_run_id": backtest.agent_run_id,
        "train_ic": backtest.train_ic,
        "train_icir": backtest.train_icir,
        "val_ic": backtest.val_ic,
        "val_icir": backtest.val_icir,
        "val_spread": backtest.val_spread,
        "val_direction_accuracy": backtest.val_direction_accuracy,
        "val_hit_rate": backtest.val_hit_rate,
        "error": backtest.error,
        "created_at": backtest.created_at.isoformat() if backtest.created_at else None,
        "completed_at": backtest.completed_at.isoformat() if backtest.completed_at else None,
    }

    # effective_config stores both static config and live progress
    eff = backtest.effective_config or {}
    result["max_iterations"] = eff.get("max_iterations", 3)

    # Live progress from effective_config (updated during agent loop)
    result["iteration"] = eff.get("iteration", 0)
    result["phase"] = eff.get("phase", "")
    result["phase_detail"] = eff.get("phase_detail", "")
    result["tool_history"] = eff.get("tool_history", [])

    # Conversation state overrides (richer data when suspended/completed)
    conv = backtest.agent_conversation
    if conv and isinstance(conv, dict):
        result["pending_task_id"] = conv.get("pending_task_id")
        if "iteration" in conv:
            result["iteration"] = conv["iteration"]
        if conv.get("phase"):
            result["phase"] = conv["phase"]
        if conv.get("phase_detail"):
            result["phase_detail"] = conv["phase_detail"]
        if conv.get("tool_history"):
            result["tool_history"] = conv["tool_history"]
        if "max_iterations" in conv:
            result["max_iterations"] = conv["max_iterations"]

    return result
