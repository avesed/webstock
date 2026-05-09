"""Skill: get ML prediction scores and rankings for stocks.

Retrieves the latest LightGBM prediction results from the data-processor
microservice. Returns predicted score, percentile rank, and directional
signal for one or more symbols.

Used by:
- Chat agents (function calling)
- Analysis LangGraph agents (shared_data or direct call)
- Discussion group agents
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult
from app.skills.utils import normalize_symbol

logger = logging.getLogger(__name__)


def _detect_market_code(symbol: str) -> str:
    """Detect market code string from symbol format.

    Returns the market string expected by PredictionClient (us/hk/cn).
    CN covers both Shanghai (.SS) and Shenzhen (.SZ) markets since the
    prediction service groups them under a single 'cn' market.
    """
    from app.services.stock_types import Market, detect_market

    market = detect_market(symbol)
    if market in (Market.SH, Market.SZ):
        return "cn"
    return market.value


def _format_direction(direction: Optional[str]) -> str:
    """Format direction string for display."""
    mapping = {
        "up": "UP (bullish)",
        "down": "DOWN (bearish)",
        "neutral": "NEUTRAL (sideways)",
    }
    if direction:
        return mapping.get(direction.lower(), direction)
    return "N/A"


def _format_percentile(rank: Any) -> str:
    """Format percentile rank as a human-readable string."""
    if rank is None:
        return "N/A"
    try:
        pct = float(rank) * 100
        return f"{pct:.1f}th percentile"
    except (TypeError, ValueError):
        return "N/A"


def _format_score(score: Any) -> str:
    """Format raw prediction score."""
    if score is None:
        return "N/A"
    try:
        return f"{float(score):.6f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_prediction(prediction: Dict[str, Any], model_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Format a single prediction record into a structured result.

    Args:
        prediction: Raw prediction dict from the data-processor API.
        model_info: Optional model metrics (IC, ICIR, etc.).

    Returns:
        Formatted dict with human-readable fields.
    """
    result: Dict[str, Any] = {
        "symbol": prediction.get("symbol", ""),
        "prediction_date": prediction.get("prediction_date", ""),
        "predicted_direction": _format_direction(prediction.get("predicted_direction")),
        "percentile_rank": _format_percentile(prediction.get("percentile_rank")),
        "percentile_rank_raw": prediction.get("percentile_rank"),
        "predicted_score": _format_score(prediction.get("predicted_score")),
        "predicted_score_raw": prediction.get("predicted_score"),
        "forward_days": prediction.get("forward_days", 5),
    }

    # Include actual return if already backfilled
    actual = prediction.get("actual_return")
    if actual is not None:
        try:
            result["actual_return"] = f"{float(actual) * 100:.2f}%"
            result["actual_return_raw"] = actual
        except (TypeError, ValueError):
            pass

    # Include model quality metrics when available
    if model_info:
        metrics: Dict[str, Any] = {}
        for key in ("ic", "icir", "ndcg"):
            val = model_info.get(key)
            if val is not None:
                try:
                    metrics[key] = float(val)
                except (TypeError, ValueError):
                    pass
        if model_info.get("feature_sources"):
            metrics["feature_sources"] = model_info["feature_sources"]
        if model_info.get("symbol_count"):
            metrics["universe_size"] = model_info["symbol_count"]
        if metrics:
            result["model_metrics"] = metrics

    return result


class StockPredictionSkill(BaseSkill):
    """Retrieve ML model prediction score and ranking for a stock."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_stock_prediction",
            description=(
                "Get ML model prediction score and ranking for a stock symbol. "
                "Returns predicted direction (up/down/sideways), percentile rank "
                "within the market, and model confidence metrics. Powered by "
                "LightGBM trained on Alpha158 factors."
            ),
            category="prediction",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock symbol (e.g., AAPL, 600519.SS, 0700.HK)",
                    required=True,
                ),
                SkillParameter(
                    name="market",
                    type="string",
                    description=(
                        "Market code: us, hk, cn. Auto-detected from symbol if not specified."
                    ),
                    required=False,
                    enum=["us", "hk", "cn"],
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        raw_symbol = kwargs.get("symbol", "")
        if not raw_symbol:
            return SkillResult(success=False, error="symbol parameter is required")

        symbol = normalize_symbol(raw_symbol)
        market = kwargs.get("market") or _detect_market_code(symbol)

        # Lazy import to avoid circular dependencies and to allow Celery
        # singleton reset to work correctly.
        from app.services.alphaforge_client import (
            AlphaForgeServiceError,
            get_alphaforge_client,
        )

        try:
            client = await get_alphaforge_client()
            data = await client.get_latest_predictions(
                market=market, symbol=symbol, top_n=1,
            )
        except AlphaForgeServiceError as exc:
            logger.warning(
                "get_stock_prediction failed for %s (market=%s): %s",
                symbol, market, exc,
            )
            return SkillResult(
                success=False,
                error=f"Prediction service unavailable: {exc}",
                metadata={"symbol": symbol, "market": market},
            )
        except Exception as exc:
            logger.error(
                "get_stock_prediction unexpected error for %s: %s",
                symbol, exc,
            )
            return SkillResult(
                success=False,
                error=f"Failed to retrieve prediction: {exc}",
                metadata={"symbol": symbol, "market": market},
            )

        # The API response shape is {"predictions": [...], "model": {...}, ...}
        predictions = data.get("predictions") or data.get("data") or []
        model_info = data.get("model")

        if not predictions:
            return SkillResult(
                success=False,
                error=(
                    f"No ML predictions available for {symbol} in the {market.upper()} market. "
                    "This may mean the prediction pipeline has not run yet for this market, "
                    "or the symbol is not in the prediction universe."
                ),
                metadata={"symbol": symbol, "market": market},
            )

        # Find the matching prediction (API may return the single symbol or a list)
        target = None
        for pred in predictions:
            pred_sym = pred.get("symbol", "")
            if pred_sym.upper() == symbol.upper():
                target = pred
                break

        # If exact match not found, use the first result (API was filtered by symbol)
        if target is None:
            target = predictions[0]

        formatted = _format_prediction(target, model_info)

        return SkillResult(
            success=True,
            data=formatted,
            metadata={
                "symbol": symbol,
                "market": market,
                "prediction_count": len(predictions),
            },
        )
