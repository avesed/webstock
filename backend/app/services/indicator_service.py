"""Technical indicator computation service.

Computes time-series technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands)
from OHLCV bar data using the `ta` library.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import ta.momentum
import ta.trend
import ta.volatility

logger = logging.getLogger(__name__)


def _format_time(value: Any, intraday: bool = False) -> str:
    """Format a time value for indicator data points.

    For daily data (intraday=False): returns YYYY-MM-DD.
    For intraday data (intraday=True): returns full datetime string YYYY-MM-DD HH:MM:SS.

    Handles datetime objects, pd.Timestamp, and ISO datetime strings.
    """
    if intraday:
        if isinstance(value, (datetime, pd.Timestamp)):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        # For strings, return as-is (already contains time info)
        return str(value)

    # Daily mode: extract date portion only
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    s = str(value)
    # Extract YYYY-MM-DD from ISO datetime strings (e.g. "2025-02-11T00:00:00-05:00")
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        return s[:10]
    return s


def _series_to_points(
    dates: pd.Index,
    values: pd.Series,
    intraday: bool = False,
) -> List[Dict[str, Any]]:
    """Convert a pandas Series to a list of {time, value} dicts.

    Drops NaN values and rounds to 4 decimal places.
    """
    points: List[Dict[str, Any]] = []
    for date, val in zip(dates, values):
        if pd.notna(val):
            points.append({
                "time": _format_time(date, intraday=intraday),
                "value": round(float(val), 4),
            })
    return points


def compute_indicator_series(
    bars: List[Dict[str, Any]],
    indicator_types: List[str],
    ma_periods: Optional[List[int]] = None,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    intraday: bool = False,
) -> Dict[str, Any]:
    """Compute technical indicator series from OHLCV bars.

    Args:
        bars: List of bar dicts with keys: date, open, high, low, close, volume.
        indicator_types: Which indicators to compute. Valid values:
            "sma", "ema", "rsi", "macd", "bb".
        ma_periods: Periods for SMA/EMA moving averages. Defaults to [20, 50, 200].
        rsi_period: Period for RSI. Defaults to 14.
        macd_fast: Fast EMA period for MACD. Defaults to 12.
        macd_slow: Slow EMA period for MACD. Defaults to 26.
        macd_signal: Signal line period for MACD. Defaults to 9.
        bb_period: Period for Bollinger Bands. Defaults to 20.
        bb_std: Standard deviation multiplier for Bollinger Bands. Defaults to 2.0.

    Returns:
        Dictionary with indicator keys mapped to series data and a "warnings" list.
    """
    if ma_periods is None:
        ma_periods = [20, 50, 200]

    start_time = time.monotonic()
    result: Dict[str, Any] = {}
    warnings: List[str] = []
    num_bars = len(bars)

    logger.info(
        "Computing indicators %s for %d bars (ma_periods=%s)",
        indicator_types, num_bars, ma_periods,
    )

    if num_bars < 2:
        warnings.append(f"Insufficient data: only {num_bars} bar(s) provided")
        result["warnings"] = warnings
        return result

    # Build DataFrame from bars — validate required columns
    try:
        df = pd.DataFrame(bars)
        if "close" not in df.columns or "date" not in df.columns:
            warnings.append("Bar data missing required 'close' or 'date' columns")
            result["warnings"] = warnings
            return result
        close = df["close"].astype(float)
        dates = df["date"]
    except (KeyError, ValueError, TypeError) as e:
        logger.error("Invalid bar data structure: %s", e)
        warnings.append(f"Invalid bar data: {e}")
        result["warnings"] = warnings
        return result

    # SMA
    if "sma" in indicator_types:
        for period in ma_periods:
            key = f"sma_{period}"
            if num_bars < period:
                warnings.append(
                    f"SMA {period} needs {period} bars, only have {num_bars}"
                )
                continue
            try:
                indicator = ta.trend.SMAIndicator(close=close, window=period)
                series = indicator.sma_indicator()
                points = _series_to_points(dates, series, intraday=intraday)
                if points:
                    result[key] = {
                        "series": points,
                        "metadata": {"period": period, "type": "sma"},
                    }
                else:
                    warnings.append(f"SMA {period} produced no valid data points")
            except Exception as e:
                logger.error("Error computing SMA %d: %s", period, e)
                warnings.append(f"SMA {period} computation failed: {e}")

    # EMA
    if "ema" in indicator_types:
        for period in ma_periods:
            key = f"ema_{period}"
            if num_bars < period:
                warnings.append(
                    f"EMA {period} needs {period} bars, only have {num_bars}"
                )
                continue
            try:
                indicator = ta.trend.EMAIndicator(close=close, window=period)
                series = indicator.ema_indicator()
                points = _series_to_points(dates, series, intraday=intraday)
                if points:
                    result[key] = {
                        "series": points,
                        "metadata": {"period": period, "type": "ema"},
                    }
                else:
                    warnings.append(f"EMA {period} produced no valid data points")
            except Exception as e:
                logger.error("Error computing EMA %d: %s", period, e)
                warnings.append(f"EMA {period} computation failed: {e}")

    # RSI
    if "rsi" in indicator_types:
        if num_bars < rsi_period + 1:
            warnings.append(
                f"RSI {rsi_period} needs at least {rsi_period + 1} bars, "
                f"only have {num_bars}"
            )
        else:
            try:
                indicator = ta.momentum.RSIIndicator(close=close, window=rsi_period)
                series = indicator.rsi()
                points = _series_to_points(dates, series, intraday=intraday)
                if points:
                    result["rsi"] = {
                        "series": points,
                        "metadata": {"period": rsi_period},
                    }
                else:
                    warnings.append("RSI produced no valid data points")
            except Exception as e:
                logger.error("Error computing RSI: %s", e)
                warnings.append(f"RSI computation failed: {e}")

    # MACD
    if "macd" in indicator_types:
        min_bars_needed = macd_slow + macd_signal
        if num_bars < min_bars_needed:
            warnings.append(
                f"MACD needs at least {min_bars_needed} bars, only have {num_bars}"
            )
        else:
            try:
                indicator = ta.trend.MACD(
                    close=close,
                    window_slow=macd_slow,
                    window_fast=macd_fast,
                    window_sign=macd_signal,
                )
                macd_line = indicator.macd()
                signal_line = indicator.macd_signal()
                histogram = indicator.macd_diff()
                result["macd"] = {
                    "macd_line": _series_to_points(dates, macd_line, intraday=intraday),
                    "signal_line": _series_to_points(dates, signal_line, intraday=intraday),
                    "histogram": _series_to_points(dates, histogram, intraday=intraday),
                    "metadata": {
                        "fast": macd_fast,
                        "slow": macd_slow,
                        "signal": macd_signal,
                    },
                }
            except Exception as e:
                logger.error("Error computing MACD: %s", e)
                warnings.append(f"MACD computation failed: {e}")

    # Bollinger Bands
    if "bb" in indicator_types:
        if num_bars < bb_period:
            warnings.append(
                f"Bollinger Bands need {bb_period} bars, only have {num_bars}"
            )
        else:
            try:
                indicator = ta.volatility.BollingerBands(
                    close=close,
                    window=bb_period,
                    window_dev=bb_std,
                )
                upper = indicator.bollinger_hband()
                middle = indicator.bollinger_mavg()
                lower = indicator.bollinger_lband()
                result["bb"] = {
                    "upper": _series_to_points(dates, upper, intraday=intraday),
                    "middle": _series_to_points(dates, middle, intraday=intraday),
                    "lower": _series_to_points(dates, lower, intraday=intraday),
                    "metadata": {"period": bb_period, "std_dev": bb_std},
                }
            except Exception as e:
                logger.error("Error computing Bollinger Bands: %s", e)
                warnings.append(f"Bollinger Bands computation failed: {e}")

    result["warnings"] = warnings

    elapsed_ms = (time.monotonic() - start_time) * 1000
    logger.info(
        "Indicator computation completed in %.1fms: %d indicators, %d warnings",
        elapsed_ms,
        len([k for k in result if k != "warnings"]),
        len(warnings),
    )

    return result
