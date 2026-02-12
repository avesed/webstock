"""Utility to convert pandas DataFrames to Qlib .bin format.

Qlib stores data as flat binary files: one .bin file per feature per symbol.
Each .bin is an array of float32 values aligned to the global trading calendar.

Structure:
  data/{market}_data/features/{SYMBOL}/{feature}.day.bin

This module provides the conversion without requiring qlib's dump_bin.py,
making it usable for incremental updates.
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FEATURES = ["open", "high", "low", "close", "volume", "factor"]

# Qlib .bin format: raw float32 array, calendar alignment is implicit
BIN_DTYPE = np.float32


def dataframe_to_bin(
    df: pd.DataFrame,
    symbol: str,
    market_data_dir: str,
    calendar: Optional[List[str]] = None,
) -> bool:
    """Convert a DataFrame of OHLCV+factor to Qlib .bin format.

    Args:
        df: DataFrame with DatetimeIndex and columns: open, high, low, close, volume, factor.
            'factor' is the adjustment factor (for splits/dividends). If missing, defaults to 1.0.
        symbol: Qlib-format symbol (e.g., SH600000, AAPL)
        market_data_dir: Path to market data directory (e.g., /app/data/qlib/us_data)
        calendar: Optional global calendar dates. If provided, data is aligned to it.

    Returns:
        True if successful, False otherwise.
    """
    if df.empty:
        logger.warning("Empty DataFrame for symbol %s, skipping", symbol)
        return False

    # Ensure DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception as e:
            logger.error(
                "Cannot convert index to DatetimeIndex for %s: %s", symbol, e
            )
            return False

    # Sort by date
    df = df.sort_index()

    # Add factor column if missing
    if "factor" not in df.columns:
        df = df.copy()
        df["factor"] = 1.0

    # Create feature directory
    feature_dir = Path(market_data_dir) / "features" / symbol
    feature_dir.mkdir(parents=True, exist_ok=True)

    try:
        for feature in FEATURES:
            if feature not in df.columns:
                logger.warning(
                    "Feature '%s' not in DataFrame for %s, filling with NaN",
                    feature,
                    symbol,
                )
                values = np.full(len(df), np.nan, dtype=BIN_DTYPE)
            else:
                values = df[feature].values.astype(BIN_DTYPE)

            # If calendar provided, align data to global calendar
            if calendar is not None:
                aligned = align_to_calendar(df.index, values, calendar)
            else:
                aligned = values

            bin_path = feature_dir / f"{feature}.day.bin"
            aligned.tofile(str(bin_path))

        logger.debug("Wrote .bin files for %s (%d days)", symbol, len(df))
        return True
    except Exception as e:
        logger.error("Failed to write .bin for %s: %s", symbol, e)
        return False


def align_to_calendar(
    dates: pd.DatetimeIndex,
    values: np.ndarray,
    calendar: List[str],
) -> np.ndarray:
    """Align data to global calendar, filling gaps with NaN.

    Args:
        dates: DatetimeIndex of available data
        values: Corresponding values
        calendar: Global trading calendar as list of date strings (YYYY-MM-DD)

    Returns:
        np.ndarray aligned to calendar length
    """
    cal_dates = pd.to_datetime(calendar)
    result = np.full(len(cal_dates), np.nan, dtype=BIN_DTYPE)

    # Create date-to-index mapping for calendar
    cal_idx = {d: i for i, d in enumerate(cal_dates)}

    for date_val, val in zip(dates, values):
        normalized = pd.Timestamp(date_val.date())
        if normalized in cal_idx:
            result[cal_idx[normalized]] = val

    return result


def update_calendar(
    market_data_dir: str,
    new_dates: List[str],
) -> List[str]:
    """Read existing calendar, merge new dates, write back.

    Returns the full sorted calendar.
    """
    cal_path = Path(market_data_dir) / "calendars" / "day.txt"
    cal_path.parent.mkdir(parents=True, exist_ok=True)

    existing: set = set()
    if cal_path.exists():
        existing = set(cal_path.read_text().strip().split("\n"))

    merged = sorted(existing | set(new_dates))
    cal_path.write_text("\n".join(merged) + "\n")

    return merged


def update_instruments(
    market_data_dir: str,
    symbol: str,
    start_date: str,
    end_date: str,
) -> None:
    """Add or update a symbol entry in instruments/all.txt.

    Format: symbol\\tstart_date\\tend_date (one per line)
    """
    inst_path = Path(market_data_dir) / "instruments" / "all.txt"
    inst_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing entries
    entries: Dict[str, tuple] = {}
    if inst_path.exists():
        for line in inst_path.read_text().strip().split("\n"):
            if not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                entries[parts[0]] = (parts[1], parts[2])

    # Update or add
    if symbol in entries:
        old_start, old_end = entries[symbol]
        entries[symbol] = (min(old_start, start_date), max(old_end, end_date))
    else:
        entries[symbol] = (start_date, end_date)

    # Write back
    lines = [f"{sym}\t{s}\t{e}" for sym, (s, e) in sorted(entries.items())]
    inst_path.write_text("\n".join(lines) + "\n")
