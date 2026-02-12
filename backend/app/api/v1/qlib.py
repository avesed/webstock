"""Qlib quantitative analysis API endpoints.

These endpoints proxy requests to the qlib-service microservice,
adding user authentication and rate limiting.

Phase 0: health proxy only
Phase 1A: factors, data sync
Phase 1B: expression engine
Phase 2: backtests
Phase 3: portfolio optimization
"""
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.qlib import (
    BacktestCreateRequest,
    BacktestListResponse,
    BacktestResponse,
    EfficientFrontierRequest,
    EfficientFrontierResponse,
    ExpressionBatchRequest,
    ExpressionBatchResultResponse,
    ExpressionEvaluateRequest,
    ExpressionResultResponse,
    ExpressionValidateRequest,
    FactorResultResponse,
    FactorSummaryResponse,
    ICAnalysisRequest,
    ICAnalysisResponse,
    DataSyncStatusResponse,
    PortfolioOptimizeRequest,
    PortfolioOptimizeResponse,
    QlibServiceStatusResponse,
    RiskDecompositionRequest,
    RiskDecompositionResponse,
    ValidationResultResponse,
)
from app.services.backtest_service import BacktestManagementService
from app.services.qlib_client import get_qlib_client, QlibServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qlib", tags=["qlib"])

# Rate limits for portfolio optimization endpoints
PORTFOLIO_RATE_LIMIT = rate_limit(
    max_requests=10, window_seconds=60, key_prefix="qlib_portfolio",
)


def _sanitize_qlib_error(e: QlibServiceError) -> str:
    """Sanitize qlib-service errors for frontend consumption.

    Strips internal URLs and container names while preserving the useful
    HTTP status code and error message.
    """
    msg = str(e)
    # Remove internal container URLs
    for pattern in ["http://qlib-service:8001", "http://localhost:8001"]:
        msg = msg.replace(pattern, "qlib-service")
    return msg


@router.get("/status", response_model=QlibServiceStatusResponse)
async def qlib_status(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get qlib-service status."""
    client = await get_qlib_client()
    try:
        health = await client.health()
        return {
            "available": True,
            **health,
        }
    except Exception as e:
        logger.warning("qlib-service unavailable: %s", e)
        return {
            "available": False,
            "error": str(e),
        }


# === Factor Endpoints ===


@router.get("/factors/{symbol}", response_model=FactorResultResponse)
async def get_factors(
    symbol: str,
    market: str = "us",
    alpha_type: str = "alpha158",
    start_date: str | None = None,
    end_date: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Get Alpha158/360 factors for a stock symbol."""
    client = await get_qlib_client()
    try:
        return await client.get_factors(symbol, market, alpha_type, start_date, end_date)
    except QlibServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_qlib_error(e))


@router.get("/factors/{symbol}/summary", response_model=FactorSummaryResponse)
async def get_factor_summary(
    symbol: str,
    market: str = "us",
    current_user: User = Depends(get_current_user),
):
    """Get factor summary (top factors by z-score) for a stock symbol."""
    client = await get_qlib_client()
    try:
        return await client.get_factor_summary(symbol, market)
    except QlibServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_qlib_error(e))


@router.post("/factors/ic", response_model=ICAnalysisResponse)
async def compute_ic(
    request: ICAnalysisRequest,
    current_user: User = Depends(get_current_user),
):
    """Compute IC/ICIR for factors across a universe of stocks."""
    client = await get_qlib_client()
    try:
        return await client.compute_ic(
            universe=request.universe,
            factor_names=request.factor_names or None,
            market=request.market.value,
            start_date=str(request.start_date) if request.start_date else None,
            end_date=str(request.end_date) if request.end_date else None,
            forward_days=request.forward_days,
        )
    except QlibServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_qlib_error(e))


# === Data Endpoints ===


@router.get("/data/status", response_model=DataSyncStatusResponse)
async def get_data_status(
    current_user: User = Depends(get_current_user),
):
    """Get data sync status for all markets in qlib-service."""
    client = await get_qlib_client()
    try:
        return await client.get_data_status()
    except QlibServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_qlib_error(e))


# === Expression Engine Endpoints ===


@router.post("/expression/evaluate", response_model=ExpressionResultResponse)
async def evaluate_expression(
    request: ExpressionEvaluateRequest,
    current_user: User = Depends(get_current_user),
):
    """Evaluate a Qlib expression for a stock symbol."""
    client = await get_qlib_client()
    try:
        return await client.evaluate_expression(
            symbol=request.symbol,
            expression=request.expression,
            market=request.market.value,
            start_date=str(request.start_date) if request.start_date else None,
            end_date=str(request.end_date) if request.end_date else None,
            period=request.period,
        )
    except QlibServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_qlib_error(e))


@router.post("/expression/batch", response_model=ExpressionBatchResultResponse)
async def evaluate_expression_batch(
    request: ExpressionBatchRequest,
    current_user: User = Depends(get_current_user),
):
    """Evaluate a Qlib expression across multiple symbols (cross-sectional)."""
    client = await get_qlib_client()
    try:
        return await client.evaluate_expression_batch(
            symbols=request.symbols,
            expression=request.expression,
            market=request.market.value,
            target_date=str(request.target_date) if request.target_date else None,
        )
    except QlibServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_qlib_error(e))


@router.post("/expression/validate", response_model=ValidationResultResponse)
async def validate_expression(
    request: ExpressionValidateRequest,
    current_user: User = Depends(get_current_user),
):
    """Validate a Qlib expression syntax without executing."""
    client = await get_qlib_client()
    try:
        return await client.validate_expression(request.expression)
    except QlibServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_qlib_error(e))


# === Backtest Endpoints ===


@router.post("/backtests", response_model=BacktestResponse, status_code=201)
async def create_backtest(
    request: BacktestCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new backtest.

    Creates a local DB record and forwards the configuration to qlib-service
    for execution. The returned record includes a backtest id that can be
    polled for progress.

    Raises:
        409 Conflict: If user exceeds concurrent or daily backtest quotas
    """
    try:
        backtest = await BacktestManagementService.create_backtest(
            db, current_user.id, request,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return BacktestResponse.from_model(backtest)


@router.get("/backtests", response_model=BacktestListResponse)
async def list_backtests(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's backtests, newest first."""
    items, total = await BacktestManagementService.list_backtests(
        db, current_user.id, limit=limit, offset=offset,
    )
    return BacktestListResponse(
        items=[BacktestResponse.from_model(bt) for bt in items],
        total=total,
    )


@router.get("/backtests/{backtest_id}", response_model=BacktestResponse)
async def get_backtest(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single backtest by id.

    If the backtest is still running, the backend transparently polls
    qlib-service for the latest progress and caches completed results
    in the local database.
    """
    backtest = await BacktestManagementService.get_backtest(
        db, current_user.id, backtest_id,
    )
    if backtest is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestResponse.from_model(backtest)


@router.post("/backtests/{backtest_id}/cancel", response_model=BacktestResponse)
async def cancel_backtest(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a running backtest."""
    try:
        backtest = await BacktestManagementService.cancel_backtest(
            db, current_user.id, backtest_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if backtest is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestResponse.from_model(backtest)


@router.delete("/backtests/{backtest_id}", status_code=204)
async def delete_backtest(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a backtest record.

    If the backtest is still running, it will be cancelled first.
    """
    deleted = await BacktestManagementService.delete_backtest(
        db, current_user.id, backtest_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Backtest not found")


# === Portfolio Optimization Endpoints ===


@router.post("/portfolio/optimize", response_model=PortfolioOptimizeResponse)
async def optimize_portfolio(
    request: PortfolioOptimizeRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(PORTFOLIO_RATE_LIMIT),
):
    """Optimize a portfolio using PyPortfolioOpt.

    Methods: max_sharpe, min_volatility, risk_parity, efficient_return.
    Uses historical daily close prices from CanonicalCache.
    """
    from app.services.portfolio_optimization import (
        PortfolioOptimizationError,
        PortfolioOptimizationService,
    )

    try:
        result = await PortfolioOptimizationService.optimize(
            symbols=request.symbols,
            method=request.method.value,
            constraints=request.constraints.model_dump(),
            lookback_days=request.lookback_days,
        )
        return result
    except PortfolioOptimizationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("Portfolio optimization error: %s", e)
        raise HTTPException(status_code=500, detail="Portfolio optimization failed")


@router.post("/portfolio/efficient-frontier", response_model=EfficientFrontierResponse)
async def efficient_frontier(
    request: EfficientFrontierRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(PORTFOLIO_RATE_LIMIT),
):
    """Compute the efficient frontier for a set of stocks."""
    from app.services.portfolio_optimization import (
        PortfolioOptimizationError,
        PortfolioOptimizationService,
    )

    try:
        result = await PortfolioOptimizationService.efficient_frontier(
            symbols=request.symbols,
            n_points=request.n_points,
            constraints=request.constraints.model_dump(),
            lookback_days=request.lookback_days,
        )
        return result
    except PortfolioOptimizationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("Efficient frontier error: %s", e)
        raise HTTPException(status_code=500, detail="Efficient frontier computation failed")


@router.post("/portfolio/risk-decomposition", response_model=RiskDecompositionResponse)
async def risk_decomposition(
    request: RiskDecompositionRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(PORTFOLIO_RATE_LIMIT),
):
    """Compute risk decomposition for a portfolio with given weights."""
    from app.services.portfolio_optimization import (
        PortfolioOptimizationError,
        PortfolioOptimizationService,
    )

    try:
        result = await PortfolioOptimizationService.risk_decomposition(
            symbols=request.symbols,
            weights=request.weights,
            lookback_days=request.lookback_days,
        )
        return result
    except PortfolioOptimizationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("Risk decomposition error: %s", e)
        raise HTTPException(status_code=500, detail="Risk decomposition computation failed")
