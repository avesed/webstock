"""Technical indicator computation service.

Delegates computation to qlib-service via HTTP. The qlib-service computes
indicators using pure pandas/numpy, replacing the previous in-process `ta` library.
"""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def compute_indicator_series(
    bars: List[Dict[str, Any]],
    indicator_types: List[str],
    ma_periods: Optional[List[int]] = None,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
    kdj_k_period: int = 9,
    kdj_d_period: int = 3,
    williams_r_period: int = 14,
    cci_period: int = 20,
    sar_af_start: float = 0.02,
    sar_af_step: float = 0.02,
    sar_af_max: float = 0.2,
    intraday: bool = False,
) -> Dict[str, Any]:
    """Compute technical indicator series from OHLCV bars via qlib-service.

    Args:
        bars: List of bar dicts with keys: date, open, high, low, close, volume.
        indicator_types: Which indicators to compute. Valid values:
            "sma", "ema", "rsi", "macd", "bb", "atr", "obv", "kdj",
            "williams_r", "cci", "vwap", "sar".
        ma_periods: Periods for SMA/EMA moving averages. Defaults to [20, 50, 200].
        (other params: see qlib-service IndicatorComputeRequest)
        intraday: True for intraday time formatting.

    Returns:
        Dictionary with indicator keys mapped to series data and a "warnings" list.
    """
    from app.services.alphaforge_client import AlphaForgeServiceError, get_alphaforge_client

    if ma_periods is None:
        ma_periods = [20, 50, 200]

    start_time = time.monotonic()

    try:
        client = await get_alphaforge_client()
        result = await client.compute_indicators(
            bars=bars,
            indicator_types=indicator_types,
            ma_periods=ma_periods,
            rsi_period=rsi_period,
            macd_fast=macd_fast,
            macd_slow=macd_slow,
            macd_signal=macd_signal,
            bb_period=bb_period,
            bb_std=bb_std,
            atr_period=atr_period,
            kdj_k_period=kdj_k_period,
            kdj_d_period=kdj_d_period,
            williams_r_period=williams_r_period,
            cci_period=cci_period,
            sar_af_start=sar_af_start,
            sar_af_step=sar_af_step,
            sar_af_max=sar_af_max,
            intraday=intraday,
        )
        # qlib-service returns {"indicators": {...}, "warnings": [...]}
        indicators = result.get("indicators", {})
        warnings = result.get("warnings", [])
        indicators["warnings"] = warnings

        elapsed_ms = (time.monotonic() - start_time) * 1000
        computed_keys = [k for k in indicators if k != "warnings"]
        logger.info(
            "Indicator computation OK: %d types requested, %d computed in %.0fms (%d bars, intraday=%s)",
            len(indicator_types), len(computed_keys), elapsed_ms, len(bars), intraday,
        )
        return indicators

    except AlphaForgeServiceError as e:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.error(
            "qlib-service indicator computation failed after %.0fms: %s",
            elapsed_ms, e,
        )
        return {"warnings": [f"Indicator service unavailable: {e}"]}
    except Exception as e:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.error(
            "Unexpected error in indicator computation after %.0fms: %s",
            elapsed_ms, e, exc_info=True,
        )
        return {"warnings": [f"Indicator computation error: {e}"]}
