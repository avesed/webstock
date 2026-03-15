"""Market-level features for direction prediction.

Builds 7 features that are identical for all stocks on a given trading date:

    index_return_5d   - 5-day rolling return of the market benchmark index
    index_return_10d  - 10-day rolling return
    index_return_20d  - 20-day rolling return
    market_breadth    - fraction of stocks with positive daily return
    market_volatility - 20-day rolling std-dev of daily index returns
    sector_momentum   - mean 5-day return of top-3 performing sectors
    volume_ma10       - 10-day MA of market-wide mean daily volume

These features complement per-stock Alpha158/fundamental/sentiment features
for binary direction classification. They capture market regime (trending /
mean-reverting), breadth (broad rally vs narrow), and liquidity conditions.

Unlike per-stock features which are rank-transformed, market features are
z-score normalised because every stock on a given date receives the same
value -- rank transform within a single value is meaningless.

Data sources:
    - Index prices: BackendDataClient.get_history_batch() via data-service
    - stock_daily_bars: asyncpg direct query for breadth and volume
    - stock_sectors: asyncpg direct query for sector momentum proxy

Graceful degradation:
    Each feature group is computed independently. If a data source is
    unavailable the corresponding columns are filled with NaN -- never
    raises exceptions for missing data.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from app.services.backend_client import get_backend_client
from app.services.market_config import get_market_config

logger = logging.getLogger(__name__)

# Feature column names exported for use by feature_service / prediction_service
MARKET_FEATURE_COLUMNS: list[str] = [
    "index_return_5d",
    "index_return_10d",
    "index_return_20d",
    "market_breadth",
    "market_volatility",
    "sector_momentum_mean",
    "volume_ma10",
    # Trend/regime indicators for direction prediction
    "up_ratio_5d",
    "up_ratio_20d",
    "breadth_momentum_5d",
]

# Extra calendar-day lookback to ensure rolling windows have enough history.
# 60 calendar days covers ~40 trading days which is sufficient for the
# longest rolling window (20-day) plus a safety margin.
_LOOKBACK_BUFFER_DAYS = 60


@dataclass
class MarketFeatures:
    """Market-level features for a single date."""

    date: date
    index_return_5d: Optional[float] = None
    index_return_10d: Optional[float] = None
    index_return_20d: Optional[float] = None
    market_breadth: Optional[float] = None
    market_volatility: Optional[float] = None
    sector_momentum_mean: Optional[float] = None
    volume_ma10: Optional[float] = None
    up_ratio_5d: Optional[float] = None
    up_ratio_20d: Optional[float] = None
    breadth_momentum_5d: Optional[float] = None


# ---------------------------------------------------------------------------
# SQL queries (asyncpg $N placeholders)
# ---------------------------------------------------------------------------

# Daily return and total stock count per date for market breadth.
# Returns (date, positive_count, total_count) aggregated across all
# symbols in the market.
_SQL_BREADTH = """
    SELECT date,
           COUNT(*) FILTER (
               WHERE close > 0 AND lag_close > 0 AND close > lag_close
           ) AS positive_count,
           COUNT(*) FILTER (
               WHERE close > 0 AND lag_close > 0
           ) AS total_count
    FROM (
        SELECT date, close,
               LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS lag_close
        FROM stock_daily_bars
        WHERE market = $1
          AND date >= $2 AND date <= $3
    ) sub
    WHERE lag_close IS NOT NULL
    GROUP BY date
    ORDER BY date
"""

# Market-wide average daily volume per date.
_SQL_VOLUME = """
    SELECT date,
           AVG(volume) AS avg_volume
    FROM stock_daily_bars
    WHERE market = $1
      AND date >= $2 AND date <= $3
      AND volume > 0
    GROUP BY date
    ORDER BY date
"""

# Per-stock 5-day returns with sector labels.
# Uses LAG(close, 5) window function -- efficient because PostgreSQL
# only needs a single sequential scan of the partition.  Sector
# aggregation (mean of top-3) is done in Python for simplicity.
_SQL_SECTOR_STOCK_RETURNS = """
    SELECT b.date,
           s.sector,
           b.close,
           LAG(b.close, 5) OVER (
               PARTITION BY b.symbol ORDER BY b.date
           ) AS close_lag5
    FROM stock_daily_bars b
    JOIN stock_sectors s
        ON s.symbol = b.symbol AND s.market = $1
    WHERE b.market = $1
      AND b.date >= $2 AND b.date <= $3
      AND s.sector IS NOT NULL
    ORDER BY b.date
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_market_features(
    market: str,
    start_date: date,
    end_date: date,
    db_pool,  # asyncpg.Pool
) -> pd.DataFrame:
    """Build market-level features for the given date range.

    Returns a DataFrame with columns ``[date] + MARKET_FEATURE_COLUMNS``.
    All feature values are z-score normalised (zero mean, unit variance).
    Dates without sufficient data have NaN for the affected features.

    Args:
        market: Market code (``us``, ``cn``, ``hk``).
        start_date: First date of the requested output range.
        end_date: Last date of the requested output range.
        db_pool: asyncpg connection pool (from ``settings_cache.pool``).

    Returns:
        DataFrame ready for left-join onto per-stock features on ``date``.
        Empty DataFrame (with correct columns) on total failure.
    """
    market = market.lower()
    cfg = get_market_config(market)

    # Extend lookback to fill rolling windows at the start of the range.
    fetch_start = start_date - timedelta(days=_LOOKBACK_BUFFER_DAYS)

    logger.info(
        "Building market features: market=%s, range=%s~%s, index=%s, "
        "fetch_start=%s (buffer=%d days)",
        market, start_date, end_date, cfg.index_symbol,
        fetch_start, _LOOKBACK_BUFFER_DAYS,
    )

    # Fetch all data sources concurrently-safe (sequential because
    # BackendDataClient is synchronous httpx and DB queries must not
    # share an asyncpg connection concurrently).
    index_df = _fetch_index_prices(cfg.index_symbol, market, fetch_start, end_date)
    breadth_df = await _fetch_breadth(market, fetch_start, end_date, db_pool)
    volume_df = await _fetch_volume(market, fetch_start, end_date, db_pool)
    sector_df = await _fetch_sector_momentum(market, fetch_start, end_date, db_pool)

    # ---- Compute rolling features ----

    result_frames: list[pd.DataFrame] = []

    # 1. Index-based features (returns + volatility)
    idx_features = _compute_index_features(index_df)
    if idx_features is not None:
        result_frames.append(idx_features)

    # 2. Market breadth
    if breadth_df is not None:
        result_frames.append(breadth_df)

    # 3. Volume MA
    vol_features = _compute_volume_ma(volume_df)
    if vol_features is not None:
        result_frames.append(vol_features)

    # 4. Sector momentum
    if sector_df is not None:
        result_frames.append(sector_df)

    # ---- Merge all feature groups ----

    if not result_frames:
        logger.warning(
            "No market features could be computed for market=%s", market,
        )
        return _empty_result()

    merged = result_frames[0]
    for frame in result_frames[1:]:
        merged = merged.merge(frame, on="date", how="outer")

    # Breadth momentum: 5-day change in market breadth.
    # Positive = breadth expanding (more stocks rising), negative = narrowing.
    if "market_breadth" in merged.columns:
        merged = merged.sort_values("date")
        merged["breadth_momentum_5d"] = merged["market_breadth"].diff(5)

    # Trim to requested date range (rolling windows needed the buffer).
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    merged = merged[(merged["date"] >= start_ts) & (merged["date"] <= end_ts)]

    if merged.empty:
        logger.warning(
            "Market features empty after date trim for market=%s", market,
        )
        return _empty_result()

    # Ensure all expected columns exist (fill missing groups with NaN).
    for col in MARKET_FEATURE_COLUMNS:
        if col not in merged.columns:
            merged[col] = np.nan

    # ---- Z-score normalisation ----
    # All stocks on a given date get the same market feature value, so
    # rank transform is meaningless.  Z-score normalises across the time
    # dimension so that the tree model sees standardised magnitude.
    for col in MARKET_FEATURE_COLUMNS:
        series = merged[col].astype("float64")
        non_null = series.notna().sum()
        if non_null == 0:
            # Entire column is NaN (data source unavailable).
            # Preserve NaN so LightGBM uses its native missing-value splits.
            continue
        mean = series.mean()
        std = series.std()
        if std > 0:
            merged[col] = (series - mean) / std
        else:
            # Constant column (all same value) -- set to 0 (no information).
            merged[col] = 0.0

    merged = merged[["date"] + MARKET_FEATURE_COLUMNS].reset_index(drop=True)

    logger.info(
        "Market features built: market=%s, %d dates, non-null counts: %s",
        market,
        len(merged),
        {col: int(merged[col].notna().sum()) for col in MARKET_FEATURE_COLUMNS},
    )

    return merged


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------


def _fetch_index_prices(
    index_symbol: str,
    market: str,
    start_date: date,
    end_date: date,
) -> Optional[pd.DataFrame]:
    """Fetch daily close prices for the benchmark index.

    Uses ``BackendDataClient.get_index_history()`` which calls data-service's
    ``/v1/stock/history/{symbol}`` endpoint.  This endpoint falls through to
    yfinance for symbols not in ``stock_daily_bars`` (like index symbols
    ``^GSPC``, ``^HSI``, ``000300.SS``).

    Returns a DataFrame with columns ``[date, close]`` sorted by date,
    or None on failure.
    """
    try:
        client = get_backend_client()
        bars = client.get_index_history(
            symbol=index_symbol,
            market=market,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
    except Exception as e:
        logger.warning(
            "Failed to fetch index prices for %s (market=%s): %s",
            index_symbol, market, e,
        )
        return None

    if not bars:
        logger.warning(
            "No index data returned for %s (market=%s)", index_symbol, market,
        )
        return None

    # get_index_history returns [{date, open, high, low, close, volume}, ...]
    df = pd.DataFrame(bars)
    if "date" not in df.columns or "close" not in df.columns:
        logger.warning(
            "Incomplete index data for %s: columns=%s",
            index_symbol, list(df.columns),
        )
        return None

    # yfinance returns tz-aware strings like "2025-03-13T00:00:00-04:00".
    # Extract just the date part to get tz-naive timestamps that match
    # DB-sourced dates (breadth, volume, sectors).
    df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[["date", "close"]].dropna(subset=["close"]).sort_values("date").reset_index(drop=True)

    logger.info(
        "Index prices fetched: %s, %d bars, %s ~ %s",
        index_symbol, len(df),
        df["date"].min().date() if len(df) > 0 else "N/A",
        df["date"].max().date() if len(df) > 0 else "N/A",
    )

    return df


async def _fetch_breadth(
    market: str,
    start_date: date,
    end_date: date,
    db_pool,
) -> Optional[pd.DataFrame]:
    """Compute market breadth from stock_daily_bars.

    Returns a DataFrame with columns ``[date, market_breadth]`` where
    market_breadth = (stocks with positive return) / (total stocks).
    """
    if not db_pool:
        logger.warning("DB pool unavailable for market breadth query")
        return None

    try:
        async with db_pool.acquire(timeout=30) as conn:
            rows = await conn.fetch(_SQL_BREADTH, market, start_date, end_date)
    except Exception as e:
        logger.warning("Market breadth query failed for market=%s: %s", market, e)
        return None

    if not rows:
        logger.warning("No breadth data for market=%s", market)
        return None

    records = []
    for row in rows:
        total = row["total_count"]
        if total and total > 0:
            records.append({
                "date": pd.Timestamp(row["date"]),
                "market_breadth": float(row["positive_count"]) / float(total),
            })

    if not records:
        return None

    df = pd.DataFrame(records)
    logger.info("Market breadth computed: market=%s, %d dates", market, len(df))
    return df


async def _fetch_volume(
    market: str,
    start_date: date,
    end_date: date,
    db_pool,
) -> Optional[pd.DataFrame]:
    """Fetch market-wide average daily volume from stock_daily_bars.

    Returns a DataFrame with columns ``[date, avg_volume]``.
    """
    if not db_pool:
        logger.warning("DB pool unavailable for volume query")
        return None

    try:
        async with db_pool.acquire(timeout=30) as conn:
            rows = await conn.fetch(_SQL_VOLUME, market, start_date, end_date)
    except Exception as e:
        logger.warning("Volume query failed for market=%s: %s", market, e)
        return None

    if not rows:
        logger.warning("No volume data for market=%s", market)
        return None

    df = pd.DataFrame([
        {"date": pd.Timestamp(row["date"]), "avg_volume": float(row["avg_volume"])}
        for row in rows
    ])
    logger.info("Volume data fetched: market=%s, %d dates", market, len(df))
    return df


async def _fetch_sector_momentum(
    market: str,
    start_date: date,
    end_date: date,
    db_pool,
) -> Optional[pd.DataFrame]:
    """Compute sector momentum from stock_daily_bars + stock_sectors.

    For each date, computes the mean 5-day return of the top-3 performing
    sectors.  Returns a DataFrame with columns ``[date, sector_momentum_mean]``.

    Falls back to None if stock_sectors data is unavailable (e.g. fresh
    deployment before sector collection has run).
    """
    if not db_pool:
        logger.warning("DB pool unavailable for sector momentum query")
        return None

    # First check if stock_sectors table has data for this market.
    try:
        async with db_pool.acquire(timeout=10) as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM stock_sectors WHERE market = $1 "
                "AND sector IS NOT NULL",
                market,
            )
    except Exception as e:
        logger.warning(
            "Failed to check stock_sectors availability for market=%s: %s",
            market, e,
        )
        return None

    if not count or count < 3:
        logger.info(
            "Insufficient sector data for market=%s (%d sectors) "
            "-- skipping sector momentum",
            market, count or 0,
        )
        return None

    try:
        async with db_pool.acquire(timeout=60) as conn:
            rows = await conn.fetch(
                _SQL_SECTOR_STOCK_RETURNS, market, start_date, end_date,
            )
    except Exception as e:
        logger.warning(
            "Sector momentum query failed for market=%s: %s", market, e,
        )
        return None

    if not rows:
        logger.info("No sector return data for market=%s", market)
        return None

    # Step 1: compute per-stock 5-day returns, aggregate by (date, sector).
    # {date: {sector: [returns]}}
    date_sector_returns: dict[pd.Timestamp, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        close = row["close"]
        close_lag5 = row["close_lag5"]
        if close is None or close_lag5 is None:
            continue
        close_f = float(close)
        lag_f = float(close_lag5)
        if close_f <= 0 or lag_f <= 0:
            continue
        ret = (close_f - lag_f) / lag_f
        dt = pd.Timestamp(row["date"])
        date_sector_returns[dt][row["sector"]].append(ret)

    # Step 2: for each date, compute mean return per sector, take top 3.
    records = []
    for dt in sorted(date_sector_returns.keys()):
        sector_means = []
        for sector, rets in date_sector_returns[dt].items():
            if rets:
                sector_means.append(float(np.mean(rets)))
        if len(sector_means) >= 3:
            sector_means.sort(reverse=True)
            top3 = sector_means[:3]
            records.append({
                "date": dt,
                "sector_momentum_mean": float(np.mean(top3)),
            })

    if not records:
        return None

    df = pd.DataFrame(records)
    logger.info("Sector momentum computed: market=%s, %d dates", market, len(df))
    return df


# ---------------------------------------------------------------------------
# Feature computation helpers
# ---------------------------------------------------------------------------


def _compute_index_features(index_df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Compute index return and volatility features from daily close prices.

    Produces columns:
        index_return_5d, index_return_10d, index_return_20d, market_volatility

    Returns None if input data is insufficient.
    """
    if index_df is None or len(index_df) < 2:
        logger.warning("Insufficient index data for feature computation")
        return None

    df = index_df.copy()
    df = df.sort_values("date").reset_index(drop=True)

    # Daily return (simple, not log -- consistent with Alpha158)
    df["daily_return"] = df["close"].pct_change()

    # Rolling returns: (close_t / close_{t-N}) - 1
    for window in (5, 10, 20):
        col = f"index_return_{window}d"
        df[col] = df["close"].pct_change(periods=window)

    # 20-day rolling volatility of daily returns
    df["market_volatility"] = df["daily_return"].rolling(window=20, min_periods=10).std()

    # Up ratio: fraction of recent trading days with positive index returns.
    # Captures trend direction — 0.8 means 4 out of 5 recent days were up.
    daily_up = (df["daily_return"] > 0).astype(float)
    df["up_ratio_5d"] = daily_up.rolling(5, min_periods=3).mean()
    df["up_ratio_20d"] = daily_up.rolling(20, min_periods=10).mean()

    result = df[["date", "index_return_5d", "index_return_10d",
                 "index_return_20d", "market_volatility",
                 "up_ratio_5d", "up_ratio_20d"]].copy()

    non_null = result.dropna(how="all", subset=[
        "index_return_5d", "index_return_10d",
        "index_return_20d", "market_volatility",
        "up_ratio_5d", "up_ratio_20d",
    ])

    if non_null.empty:
        logger.warning("All index features are NaN after rolling computation")
        return None

    logger.info(
        "Index features computed: %d dates with data",
        len(non_null),
    )
    return result


def _compute_volume_ma(volume_df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Compute 10-day moving average of market-wide mean volume.

    Produces column: volume_ma10

    Returns None if input data is insufficient.
    """
    if volume_df is None or volume_df.empty:
        return None

    df = volume_df.copy().sort_values("date").reset_index(drop=True)

    df["volume_ma10"] = df["avg_volume"].rolling(window=10, min_periods=5).mean()

    result = df[["date", "volume_ma10"]].dropna(subset=["volume_ma10"])

    if result.empty:
        logger.warning("Volume MA10 all NaN after rolling computation")
        return None

    logger.info("Volume MA10 computed: %d dates", len(result))
    return result


def _empty_result() -> pd.DataFrame:
    """Return an empty DataFrame with the correct column schema."""
    return pd.DataFrame(columns=["date"] + MARKET_FEATURE_COLUMNS)
