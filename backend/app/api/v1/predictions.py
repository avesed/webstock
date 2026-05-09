"""User-facing prediction endpoints (read-only).

Provides authenticated (non-admin) users with:
- Latest predictions per market (top/bottom ranked stocks)
- Model summary (quality, freshness, accuracy)
- Performance trends over time
- Per-symbol prediction lookup
"""

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.models.user import User
from app.services.alphaforge_client import (
    AlphaForgeServiceError,
    get_alphaforge_client,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictions", tags=["Predictions"])

VALID_MARKETS = {"cn", "us", "hk"}


def _validate_market(market: str) -> str:
    m = market.lower()
    if m not in VALID_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}",
        )
    return m


def _sanitize_error(e: AlphaForgeServiceError) -> str:
    msg = str(e)
    msg = re.sub(r"https?://[a-z0-9._-]+:\d+", "<service>", msg)
    return msg


def _detect_market_from_symbol(symbol: str) -> Optional[str]:
    """Guess market from symbol suffix."""
    s = symbol.upper()
    if s.endswith(".SS") or s.endswith(".SZ"):
        return "cn"
    if s.endswith(".HK"):
        return "hk"
    # Default: US (no suffix or common US patterns)
    return "us"


# ---------------------------------------------------------------------------
# GET /predictions/{market}/latest
# ---------------------------------------------------------------------------


@router.get(
    "/{market}/latest",
    summary="Latest predictions",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_latest_predictions(
    market: str,
    top_n: int = Query(default=50, ge=1, le=500),
    _user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Top-ranked stock predictions for a market."""
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        data = await client.get_latest_predictions(market=market, top_n=top_n)
        # Strip actualReturn — admin-only field
        for p in data.get("predictions", []):
            p.pop("actual_return", None)
            p.pop("actualReturn", None)
        return data
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_error(e))


# ---------------------------------------------------------------------------
# GET /predictions/{market}/summary
# ---------------------------------------------------------------------------


@router.get(
    "/{market}/summary",
    summary="Model summary",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_summary(
    market: str,
    _user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Combined model metadata + accuracy in a single response."""
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        models_data, accuracy_data = await asyncio.gather(
            client.get_models(market=market),
            client.get_accuracy(market=market, days=30),
            return_exceptions=True,
        )

        # Build model summary
        model_info = None
        if isinstance(models_data, dict):
            models = models_data.get("models", [])
            if models:
                latest = models[0]
                model_info = {
                    "model_date": latest.get("model_date"),
                    "quality_passed": latest.get("quality_passed", False),
                    "feature_count": latest.get("feature_count"),
                    "symbol_count": latest.get("symbol_count"),
                }

        # Build accuracy summary
        accuracy_info = None
        if isinstance(accuracy_data, dict):
            accuracy_info = {
                "hit_rate": accuracy_data.get("accuracy"),
                "avg_ic": accuracy_data.get("avg_ic"),
                "total_predictions": accuracy_data.get("total_predictions"),
                "days": accuracy_data.get("days", 30),
            }

        # Get prediction date from latest predictions
        prediction_date = None
        try:
            latest_data = await client.get_latest_predictions(market=market, top_n=1)
            preds = latest_data.get("predictions", [])
            if preds:
                prediction_date = preds[0].get("prediction_date")
        except Exception:
            pass

        return {
            "market": market,
            "model": model_info,
            "accuracy": accuracy_info,
            "prediction_date": prediction_date,
        }
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_error(e))


# ---------------------------------------------------------------------------
# GET /predictions/{market}/performance
# ---------------------------------------------------------------------------


@router.get(
    "/{market}/performance",
    summary="Performance trends",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def get_performance(
    market: str,
    days: int = Query(default=60, ge=7, le=180),
    _user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """IC and hit rate trends over time."""
    market = _validate_market(market)
    client = await get_alphaforge_client()
    try:
        return await client.get_performance_metrics(market=market, days=days)
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_error(e))


# ---------------------------------------------------------------------------
# GET /predictions/symbol/{symbol}
# ---------------------------------------------------------------------------


@router.get(
    "/symbol/{symbol}",
    summary="Single symbol prediction",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_symbol_prediction(
    symbol: str,
    _user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get latest prediction for a single stock symbol."""
    market = _detect_market_from_symbol(symbol)
    client = await get_alphaforge_client()
    try:
        data = await client.get_latest_predictions(
            market=market, top_n=500, symbol=symbol
        )
        preds = data.get("predictions", [])
        if not preds:
            raise HTTPException(status_code=404, detail=f"No prediction for {symbol}")
        pred = preds[0]
        pred.pop("actual_return", None)
        pred.pop("actualReturn", None)
        return {"market": market, "prediction": pred}
    except AlphaForgeServiceError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=_sanitize_error(e))
