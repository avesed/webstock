"""Utility to convert pandas DataFrames to Qlib .bin format.

Qlib stores data as flat binary files: one .bin file per feature per symbol.

Binary format (little-endian float32):
    [start_index (1 x float32)] [data values (N x float32)]

- start_index: 0-based calendar position of the first data value
- data values: compact array from start_index to end_index (NaN for gaps)
- Qlib reads the header via FileFeatureStorage.start_index to align data to calendar

Structure:
  data/{market}_data/features/{symbol_lower}/{feature}.day.bin

This module provides the conversion without requiring qlib.init(),
making it usable in ProcessPoolExecutor for incremental updates.
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FEATURES = ["open", "high", "low", "close", "volume", "factor"]

# Qlib .bin uses little-endian float32 throughout (including the header)
BIN_DTYPE = "<f"
# numpy float32 for intermediate computation
NP_FLOAT32 = np.float32


def read_calendar(market_data_dir: str) -> List[str]:
    """Read existing trading calendar from disk.

    Returns sorted list of date strings (YYYY-MM-DD), or empty list if not found.
    """
    cal_path = Path(market_data_dir) / "calendars" / "day.txt"
    if not cal_path.exists():
        logger.debug("No calendar found at %s, returning empty", cal_path)
        return []
    try:
        lines = cal_path.read_text().strip().split("\n")
        return sorted(l.strip() for l in lines if l.strip())
    except Exception as e:
        logger.warning("Failed to read calendar at %s: %s", cal_path, e)
        return []


# ------------------------------------------------------------------
# Internal helpers for Qlib .bin I/O
# ------------------------------------------------------------------

def _read_bin_to_calendar(
    bin_path: Path,
    cal_len: int,
    symbol: str,
    feature: str,
) -> Optional[np.ndarray]:
    """Read a Qlib .bin file and expand to full calendar-length array.

    Parses the start_index header, then places compact data at the correct
    calendar positions. Returns None if the file is corrupt or unreadable.
    """
    try:
        file_size = bin_path.stat().st_size
        if file_size == 0:
            logger.warning(
                "Merge: %s/%s.day.bin is empty (0 bytes), skipping",
                symbol, feature,
            )
            return None
        if file_size < 8:
            # Need at least header (4 bytes) + one value (4 bytes)
            logger.warning(
                "Merge: %s/%s.day.bin too small (%d bytes), skipping",
                symbol, feature, file_size,
            )
            return None
        if file_size % 4 != 0:
            logger.warning(
                "Merge: %s/%s.day.bin has non-aligned size %d bytes "
                "(not a multiple of float32), skipping",
                symbol, feature, file_size,
            )
            return None

        raw = np.fromfile(str(bin_path), dtype=BIN_DTYPE)
        start_idx = int(raw[0])
        data = raw[1:]

        # Sanity check: start_index should be a reasonable calendar position
        if start_idx < 0 or start_idx >= cal_len:
            logger.warning(
                "Merge: %s/%s.day.bin has invalid start_index=%d "
                "(calendar length=%d). File may be in old format or corrupt, "
                "existing data will not be preserved",
                symbol, feature, start_idx, cal_len,
            )
            return None

        result = np.full(cal_len, np.nan, dtype=NP_FLOAT32)
        end_pos = min(start_idx + len(data), cal_len)
        result[start_idx:end_pos] = data[:end_pos - start_idx]

        if start_idx + len(data) > cal_len:
            logger.debug(
                "Merge: %s/%s.day.bin data extends %d beyond calendar, truncated",
                symbol, feature, start_idx + len(data) - cal_len,
            )

        return result
    except Exception as e:
        logger.warning(
            "Merge: failed to read %s/%s.day.bin: %s", symbol, feature, e,
        )
        return None


def _write_bin_with_header(
    bin_path: Path,
    data: np.ndarray,
    symbol: str,
    feature: str,
) -> None:
    """Write data to Qlib .bin format with start_index header.

    Finds the compact range (first/last non-NaN position) and writes:
        [start_index (float32)] [compact_data (float32...)]

    If all values are NaN, writes nothing (skips the file).
    """
    non_nan_indices = np.where(~np.isnan(data))[0]
    if len(non_nan_indices) == 0:
        logger.debug(
            "All NaN data for %s/%s, skipping write", symbol, feature,
        )
        return

    start_idx = int(non_nan_indices[0])
    end_idx = int(non_nan_indices[-1])
    compact = data[start_idx:end_idx + 1]

    # Qlib format: [start_index as float32] + [compact data as float32]
    header = np.array([start_idx], dtype=NP_FLOAT32)
    np.hstack([header, compact]).astype(BIN_DTYPE).tofile(str(bin_path))


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def dataframe_to_bin(
    df: pd.DataFrame,
    symbol: str,
    market_data_dir: str,
    calendar: Optional[List[str]] = None,
    merge: bool = False,
) -> bool:
    """Convert a DataFrame of OHLCV+factor to Qlib .bin format.

    Writes one .bin file per feature, using Qlib's binary format:
        [start_index (float32)] [compact_data (float32...)]

    Args:
        df: DataFrame with DatetimeIndex and columns: open, high, low, close, volume, factor.
            'factor' is the adjustment factor (for splits/dividends). If missing, defaults to 1.0.
        symbol: Qlib-format symbol (e.g., SH600000, AAPL)
        market_data_dir: Path to market data directory (e.g., /app/data/qlib/us_data)
        calendar: Global calendar dates. Required for correct calendar-aligned output.
        merge: If True and calendar is provided, read existing .bin files and merge new
            data on top (non-NaN values overwrite). Prevents data loss during incremental sync.

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
                "Cannot convert index to DatetimeIndex for %s: %s", symbol, e,
                exc_info=True,
            )
            return False

    # Sort by date
    df = df.sort_index()

    # Add factor column if missing
    if "factor" not in df.columns:
        df = df.copy()
        df["factor"] = 1.0

    # Create feature directory (Qlib's FileFeatureStorage does instrument.lower())
    feature_dir = Path(market_data_dir) / "features" / symbol.lower()
    feature_dir.mkdir(parents=True, exist_ok=True)

    try:
        for feature in FEATURES:
            if feature not in df.columns:
                logger.warning(
                    "Feature '%s' not in DataFrame for %s, filling with NaN",
                    feature,
                    symbol,
                )
                values = np.full(len(df), np.nan, dtype=NP_FLOAT32)
            else:
                values = df[feature].values.astype(NP_FLOAT32)

            bin_path = feature_dir / f"{feature}.day.bin"

            if calendar is not None:
                cal_len = len(calendar)
                aligned = align_to_calendar(df.index, values, calendar)

                if merge and bin_path.exists():
                    existing = _read_bin_to_calendar(
                        bin_path, cal_len, symbol, feature,
                    )
                    if existing is not None:
                        # New non-NaN values take precedence over existing
                        mask = ~np.isnan(aligned)
                        existing[mask] = aligned[mask]
                        aligned = existing

                _write_bin_with_header(bin_path, aligned, symbol, feature)
            else:
                # No calendar — write with start_index=0
                _write_bin_with_header(bin_path, values, symbol, feature)

        logger.debug(
            "Wrote .bin files for %s (%d days, merge=%s)", symbol, len(df), merge,
        )
        return True
    except Exception as e:
        logger.error("Failed to write .bin for %s: %s", symbol, e, exc_info=True)
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
        np.ndarray of length len(calendar), aligned by date
    """
    cal_dates = pd.to_datetime(calendar)
    result = np.full(len(cal_dates), np.nan, dtype=NP_FLOAT32)

    # Create date-to-index mapping for calendar
    cal_idx = {d: i for i, d in enumerate(cal_dates)}

    dropped = 0
    for date_val, val in zip(dates, values):
        normalized = pd.Timestamp(date_val.date())
        if normalized in cal_idx:
            result[cal_idx[normalized]] = val
        else:
            dropped += 1
    if dropped:
        logger.debug(
            "align_to_calendar: dropped %d/%d dates not in calendar",
            dropped, len(dates),
        )

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
        existing = set(
            l.strip() for l in cal_path.read_text().strip().split("\n") if l.strip()
        )

    merged = sorted(existing | set(d for d in new_dates if d.strip()))
    cal_path.write_text("\n".join(merged) + "\n")

    logger.debug("Calendar updated: %d total dates at %s", len(merged), cal_path)
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
            else:
                logger.debug("Skipping malformed instruments line: %r", line.strip())

    # Update or add
    if symbol in entries:
        old_start, old_end = entries[symbol]
        entries[symbol] = (min(old_start, start_date), max(old_end, end_date))
    else:
        entries[symbol] = (start_date, end_date)

    # Write back
    lines = [f"{sym}\t{s}\t{e}" for sym, (s, e) in sorted(entries.items())]
    inst_path.write_text("\n".join(lines) + "\n")
    logger.debug("Instruments updated: %s [%s, %s] at %s", symbol, start_date, end_date, inst_path)
