"""Indicator computation API routes.

These endpoints do NOT require Qlib initialization. They operate on raw OHLCV
bars passed in the request body, using pure pandas/numpy computations.

A separate thread pool is used to avoid blocking the Qlib query executor.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, HTTPException

from app.models.schemas import IndicatorComputeRequest, IndicatorComputeResponse
from app.services.indicator_compute import compute_indicator_series

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/indicators", tags=["indicators"])

# Separate executor for indicator computation (does NOT need Qlib)
_indicator_executor = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="indicator"
)

COMPUTE_TIMEOUT = 30  # seconds


def shutdown_indicator_executor() -> None:
    """Gracefully shut down the indicator executor. Called from app lifespan."""
    logger.info("Shutting down indicator executor...")
    _indicator_executor.shutdown(wait=True, cancel_futures=True)
    logger.info("Indicator executor shut down")


@router.post("/compute", response_model=IndicatorComputeResponse)
async def compute_indicators(request: IndicatorComputeRequest):
    """Compute technical indicators from OHLCV bars.

    Accepts raw bar data and indicator configuration.
    Computation runs in a dedicated thread pool (separate from Qlib).
    """
    if not request.bars:
        raise HTTPException(status_code=400, detail="No bars provided")
    if not request.indicator_types:
        raise HTTPException(status_code=400, detail="No indicator types specified")
    if len(request.bars) > 10000:
        raise HTTPException(status_code=400, detail="Too many bars (max 10000)")

    logger.info(
        "Computing indicators %s for %d bars (intraday=%s)",
        request.indicator_types, len(request.bars), request.intraday,
    )

    loop = asyncio.get_running_loop()
    call = partial(
        compute_indicator_series,
        bars=request.bars,
        indicator_types=request.indicator_types,
        ma_periods=request.ma_periods,
        rsi_period=request.rsi_period,
        macd_fast=request.macd_fast,
        macd_slow=request.macd_slow,
        macd_signal=request.macd_signal,
        bb_period=request.bb_period,
        bb_std=request.bb_std,
        atr_period=request.atr_period,
        kdj_k_period=request.kdj_k_period,
        kdj_d_period=request.kdj_d_period,
        williams_r_period=request.williams_r_period,
        cci_period=request.cci_period,
        sar_af_start=request.sar_af_start,
        sar_af_step=request.sar_af_step,
        sar_af_max=request.sar_af_max,
        intraday=request.intraday,
    )

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_indicator_executor, call),
            timeout=COMPUTE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("Indicator computation timed out after %ds", COMPUTE_TIMEOUT)
        raise HTTPException(
            status_code=504, detail=f"Computation timed out after {COMPUTE_TIMEOUT}s"
        )
    except Exception as e:
        logger.error("Indicator computation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Computation failed: {e}")

    # Separate warnings from indicator data
    warnings = result.pop("warnings", [])
    return IndicatorComputeResponse(indicators=result, warnings=warnings)
