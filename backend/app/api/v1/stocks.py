"""Stock data API endpoints."""

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.stock import (
    BatchQuoteRequest,
    BatchQuoteResponse,
    BollingerBandsResponse,
    ErrorResponse,
    HistoryInterval,
    HistoryPeriod,
    IndicatorDataPoint,
    MAIndicatorResponse,
    MACDIndicatorResponse,
    MarketType,
    SearchResponse,
    SearchResultResponse,
    StockFinancialsResponse,
    StockHistoryResponse,
    StockInfoResponse,
    StockQuoteResponse,
    TechnicalIndicatorsResponse,
)
from app.services.indicator_service import compute_indicator_series
from app.services.canonical_cache_service import (
    RESAMPLE_MAP,
    get_canonical_cache_service,
    resample_bars,
)
from app.services.providers import get_provider_router
from app.services.stock_service import (
    HistoryInterval as ServiceInterval,
    HistoryPeriod as ServicePeriod,
    Market,
    detect_market,
    get_stock_service,
    is_precious_metal,
)
from app.utils.symbol_validation import validate_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stocks", tags=["Stocks"])

# Rate limiting: 100 requests per minute for stock queries
STOCK_RATE_LIMIT = rate_limit(max_requests=100, window_seconds=60, key_prefix="stock_api")

# Lookback strategy: fetch more history than requested so indicators
# that need a long warm-up period (e.g. SMA 200) have enough data.
LOOKBACK_MAP: Dict[str, str] = {
    "1d": "3mo",
    "5d": "3mo",
    "1mo": "1y",
    "3mo": "2y",
    "6mo": "2y",
    "1y": "5y",
    "2y": "5y",
    "5y": "max",
    "max": "max",
}

# Approximate calendar days for each period value, used to compute the
# cutoff date when trimming indicator results back to the user's window.
PERIOD_DAYS: Dict[str, int] = {
    "1d": 1,
    "5d": 5,
    "1mo": 30,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "max": 99999,
}

# Valid indicator type identifiers accepted via the `types` query parameter.
VALID_INDICATOR_TYPES = {"sma", "ema", "rsi", "macd", "bb"}

# Intraday interval set for detection (indicators now supported for all intervals)
INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "1h"}

# Interval to approximate minutes mapping (for warm-up calculation)
INTERVAL_MINUTES: Dict[str, int] = {
    "1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
    "1d": 1440, "1wk": 10080, "1mo": 43200,
}

# Rate limiting for indicator endpoint (more expensive than quote lookups)
INDICATOR_RATE_LIMIT = rate_limit(max_requests=30, window_seconds=60, key_prefix="indicator_api")

_PERIOD_THRESHOLDS = [
    (1, "1d"), (5, "5d"), (30, "1mo"), (90, "3mo"),
    (180, "6mo"), (365, "1y"), (730, "2y"), (1825, "5y"),
]


def _pick_smallest_period(days: int) -> str:
    """Pick the smallest yfinance period string that covers the given number of days."""
    for threshold, period_str in _PERIOD_THRESHOLDS:
        if days <= threshold:
            return period_str
    return "max"


def _compute_min_warm_up_bars(
    type_list: List[str],
    parsed_ma_periods: List[int],
    rsi_period: int = 14,
    bb_period: int = 20,
) -> int:
    """Compute the minimum bars needed for indicator warm-up across ALL indicator types."""
    ma_max = max(parsed_ma_periods) if parsed_ma_periods else 0
    indicator_minimums = [ma_max]
    if "rsi" in type_list:
        indicator_minimums.append(rsi_period + 1)
    if "macd" in type_list:
        indicator_minimums.append(26 + 9)  # macd_slow + macd_signal defaults
    if "bb" in type_list:
        indicator_minimums.append(bb_period)
    result = max(indicator_minimums) if indicator_minimums else 200
    return result if result > 0 else 200


def _trim_series(
    series: List[Dict[str, Any]],
    cutoff_str: str,
) -> List[Dict[str, Any]]:
    """Keep only data points whose time >= cutoff_str."""
    # Normalize T separator to space for consistent lexicographic comparison
    # (ASCII 'T' > ' ', so "2025-03-10T09:30" > "2025-03-10 09:30" would mis-filter)
    normalized_cutoff = cutoff_str.replace("T", " ")
    return [p for p in series if p["time"].replace("T", " ") >= normalized_cutoff]


async def _compute_indicators_for_symbol(
    symbol: str,
    types: str,
    period: HistoryPeriod,
    interval: HistoryInterval,
    ma_periods: str,
    rsi_period: int,
    bb_period: int,
    bb_std: float,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> TechnicalIndicatorsResponse:
    """Shared implementation for both query-param and path-param indicator routes."""

    start_time = time.monotonic()
    is_intraday = interval.value in INTRADAY_INTERVALS

    # 1. Parse and validate indicator types
    type_list = [t.strip().lower() for t in types.split(",") if t.strip()]
    invalid = [t for t in type_list if t not in VALID_INDICATOR_TYPES]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid indicator type(s): {', '.join(invalid)}. "
                f"Valid types: {', '.join(sorted(VALID_INDICATOR_TYPES))}"
            ),
        )
    if not type_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one indicator type is required.",
        )

    # 2. Parse and validate MA periods
    try:
        parsed_ma_periods = [int(p.strip()) for p in ma_periods.split(",") if p.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ma_periods must be comma-separated integers (e.g. '20,50,200').",
        )
    if len(parsed_ma_periods) > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 5 MA periods allowed.",
        )
    for p in parsed_ma_periods:
        if p < 2 or p > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MA period {p} out of range. Must be between 2 and 500.",
            )

    stock_service = await get_stock_service()
    service_interval = ServiceInterval(interval.value)

    # 3. Determine how to fetch history: start/end mode vs period mode
    fetch_start: Optional[str] = None
    fetch_end: Optional[str] = None
    original_start_str: Optional[str] = None

    if start and end:
        # Start/end mode: extend start backwards for indicator warm-up
        original_start_str = start
        interval_mins = INTERVAL_MINUTES.get(interval.value, 1440)
        max_period = _compute_min_warm_up_bars(type_list, parsed_ma_periods, rsi_period, bb_period)
        # warm_up_bars * interval_minutes / minutes_per_day
        # Use 240 min (A-shares) for intraday to be conservative across all markets
        mins_per_day = 240 if is_intraday else (60 * 24)
        warm_up_days = max(math.ceil(max_period * interval_mins / mins_per_day) + 1, 2 if is_intraday else 30)
        # Cap warm-up to prevent excessively large lookback for weekly/monthly intervals
        warm_up_days = min(warm_up_days, 1825)  # Cap at 5 years

        try:
            # Parse start date (handle both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS" formats)
            start_dt = datetime.fromisoformat(start.replace("T", " ").split("+")[0].split("Z")[0])
            extended_start = start_dt - timedelta(days=warm_up_days)
            fetch_start = extended_start.strftime("%Y-%m-%d")
            fetch_end = end
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Failed to parse start date '%s' for warm-up extension: %s. Using as-is.",
                start, exc,
            )
            fetch_start = start
            fetch_end = end

        logger.info(
            "Indicators for %s: start/end mode, original_start=%s, extended_start=%s, end=%s, warm_up_days=%d",
            symbol, start, fetch_start, end, warm_up_days,
        )

        service_period = ServicePeriod.ONE_YEAR  # Ignored when start/end provided

        try:
            history = await stock_service.get_history(
                symbol,
                period=service_period,
                interval=service_interval,
                start=fetch_start,
                end=fetch_end,
            )
        except Exception as e:
            logger.error("Error fetching history for indicators (%s): %s", symbol, e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to fetch historical data for indicator computation.",
            )
    else:
        # Period mode: try original period first to reuse history endpoint's cache,
        # then expand only if bars are insufficient for indicator warm-up.
        max_period_val = _compute_min_warm_up_bars(type_list, parsed_ma_periods, rsi_period, bb_period)
        min_bars_needed = max_period_val + 10  # warm-up + small buffer

        # Step 1: fetch with original period (same params_hash as history endpoint)
        service_period = ServicePeriod(period.value)
        try:
            history = await stock_service.get_history(
                symbol,
                period=service_period,
                interval=service_interval,
            )
        except Exception as e:
            logger.warning(
                "Step 1 fetch failed for %s (period=%s, interval=%s): %s",
                symbol, period.value, interval.value, e,
            )
            history = None

        bars_count = len(history.get("bars") or []) if history else 0

        # Step 2: if bars insufficient for warm-up, compute minimal expanded period
        if bars_count < min_bars_needed:
            if is_intraday:
                interval_mins = INTERVAL_MINUTES.get(interval.value, 1)
                period_days = PERIOD_DAYS.get(period.value, 1)
                # Use conservative trading minutes (A-shares: 240min) to ensure
                # enough warm-up days across all markets (US: 390, HK: 330, CN: 240)
                trading_mins_per_day = 240
                warm_up_days = math.ceil(max_period_val * interval_mins / trading_mins_per_day) + 1
                total_days = period_days + warm_up_days
                expanded_period_str = _pick_smallest_period(total_days)
            else:
                expanded_period_str = LOOKBACK_MAP.get(period.value, period.value)

            logger.info(
                "Indicator warm-up: %d bars < %d needed for %s (period=%s, interval=%s, ma=%s), expanding → %s",
                bars_count, min_bars_needed, symbol, period.value, interval.value, ma_periods, expanded_period_str,
            )
            try:
                history = await stock_service.get_history(
                    symbol,
                    period=ServicePeriod(expanded_period_str),
                    interval=service_interval,
                )
            except Exception as e:
                logger.error("Error fetching expanded history for indicators (%s): %s", symbol, e)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Failed to fetch historical data for indicator computation.",
                )
        else:
            logger.info(
                "Indicator cache reuse: %d bars >= %d needed for %s (period=%s, interval=%s), skipping expanded fetch",
                bars_count, min_bars_needed, symbol, period.value, interval.value,
            )

    if history is None or not (history.get("bars") or []):
        logger.warning(
            "No bars returned for %s (period=%s, interval=%s, mode=%s)",
            symbol, period.value, interval.value,
            "start_end" if start and end else "period",
        )
        if is_precious_metal(symbol):
            raise _futures_market_closed_or_404(symbol)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No historical data available for symbol: {symbol}",
        )

    bars = history["bars"]
    logger.info("Fetched %d bars for %s indicator computation", len(bars), symbol)

    # 4. Convert bars to list of dicts
    bar_dicts: List[Dict[str, Any]] = [
        bar if isinstance(bar, dict) else {
            "date": getattr(bar, "date", None),
            "open": getattr(bar, "open", None),
            "high": getattr(bar, "high", None),
            "low": getattr(bar, "low", None),
            "close": getattr(bar, "close", None),
            "volume": getattr(bar, "volume", None),
        }
        for bar in bars
    ]

    # 5. Compute indicators on the full dataset
    raw = await asyncio.to_thread(
        compute_indicator_series,
        bars=bar_dicts,
        indicator_types=type_list,
        ma_periods=parsed_ma_periods,
        rsi_period=rsi_period,
        bb_period=bb_period,
        bb_std=bb_std,
        intraday=is_intraday,
    )

    # 6. Determine cutoff for trimming back to the user's requested window
    if original_start_str:
        # Start/end mode: trim to original start (before warm-up extension)
        cutoff_str = original_start_str
    else:
        # Period mode: trim by calendar days
        period_days = PERIOD_DAYS.get(period.value, 365)
        cutoff = datetime.now() - timedelta(days=period_days)
        if is_intraday:
            cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        else:
            cutoff_str = cutoff.strftime("%Y-%m-%d")

    # 7. Build response, trimming each series to the user's window
    ma_dict: Dict[str, MAIndicatorResponse] = {}
    rsi_resp: Optional[MAIndicatorResponse] = None
    macd_resp: Optional[MACDIndicatorResponse] = None
    bb_resp: Optional[BollingerBandsResponse] = None
    warnings: List[str] = raw.get("warnings", [])

    # Collect MA indicators (sma_* and ema_*)
    for key, value in raw.items():
        if key.startswith(("sma_", "ema_")) and isinstance(value, dict) and "series" in value:
            trimmed = _trim_series(value["series"], cutoff_str)
            if trimmed:
                ma_dict[key] = MAIndicatorResponse(
                    series=[IndicatorDataPoint(**p) for p in trimmed],
                    metadata=value["metadata"],
                )

    # RSI
    if "rsi" in raw and isinstance(raw["rsi"], dict) and "series" in raw["rsi"]:
        trimmed = _trim_series(raw["rsi"]["series"], cutoff_str)
        if trimmed:
            rsi_resp = MAIndicatorResponse(
                series=[IndicatorDataPoint(**p) for p in trimmed],
                metadata=raw["rsi"]["metadata"],
            )

    # MACD
    if "macd" in raw and isinstance(raw["macd"], dict) and "macd_line" in raw["macd"]:
        macd_data = raw["macd"]
        trimmed_macd = _trim_series(macd_data["macd_line"], cutoff_str)
        trimmed_signal = _trim_series(macd_data["signal_line"], cutoff_str)
        trimmed_hist = _trim_series(macd_data["histogram"], cutoff_str)
        if trimmed_macd and trimmed_signal and trimmed_hist:
            macd_resp = MACDIndicatorResponse(
                macd_line=[IndicatorDataPoint(**p) for p in trimmed_macd],
                signal_line=[IndicatorDataPoint(**p) for p in trimmed_signal],
                histogram=[IndicatorDataPoint(**p) for p in trimmed_hist],
                metadata=macd_data["metadata"],
            )

    # Bollinger Bands
    if "bb" in raw and isinstance(raw["bb"], dict) and "upper" in raw["bb"]:
        bb_data = raw["bb"]
        trimmed_upper = _trim_series(bb_data["upper"], cutoff_str)
        trimmed_middle = _trim_series(bb_data["middle"], cutoff_str)
        trimmed_lower = _trim_series(bb_data["lower"], cutoff_str)
        if trimmed_upper and trimmed_middle and trimmed_lower:
            bb_resp = BollingerBandsResponse(
                upper=[IndicatorDataPoint(**p) for p in trimmed_upper],
                middle=[IndicatorDataPoint(**p) for p in trimmed_middle],
                lower=[IndicatorDataPoint(**p) for p in trimmed_lower],
                metadata=bb_data["metadata"],
            )

    elapsed_ms = (time.monotonic() - start_time) * 1000
    logger.info(
        "Indicator endpoint for %s completed in %.1fms (interval=%s, cutoff=%s, intraday=%s)",
        symbol, elapsed_ms, interval.value, cutoff_str, is_intraday,
    )

    return TechnicalIndicatorsResponse(
        symbol=symbol,
        interval=interval.value,
        ma=ma_dict if ma_dict else None,
        rsi=rsi_resp,
        macd=macd_resp,
        bb=bb_resp,
        warnings=warnings,
    )


def _futures_market_closed_or_404(symbol: str) -> HTTPException:
    """Return appropriate error for futures with no history data."""
    from datetime import datetime, timezone as tz
    now = datetime.now(tz.utc)
    wd, hr = now.weekday(), now.hour
    is_closed = (
        wd == 5  # Saturday
        or (wd == 6 and hr < 23)  # Sunday before 23:00 UTC (6pm ET)
        or (wd == 4 and hr >= 22)  # Friday after 22:00 UTC (5pm ET)
    )
    if is_closed:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Futures market closed. {symbol} trades Sun 6PM–Fri 5PM ET.",
        )
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No historical data available for symbol: {symbol}",
    )


# =============================================================================
# Query parameter routes (preferred for symbols with special characters like GC=F)
# These routes are defined FIRST to ensure they match before path parameter routes
# =============================================================================


@router.get(
    "/quote",
    response_model=StockQuoteResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get real-time stock quote (query param)",
    description="Get current price, change, volume and other real-time data for a stock. Use this endpoint for symbols with special characters (e.g., GC=F).",
)
async def get_stock_quote_query(
    symbol: str = Query(..., description="Stock symbol (e.g., AAPL, 0700.HK, GC=F)"),
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """Get real-time quote for a stock using query parameter."""
    symbol = validate_symbol(symbol)
    logger.info(f"Getting quote for {symbol} (user: {current_user.id})")

    stock_service = await get_stock_service()

    try:
        quote = await stock_service.get_quote(symbol, force_refresh=refresh)

        if quote is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No quote data available for symbol: {symbol}",
            )

        return StockQuoteResponse(**quote)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quote for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch stock data. Please try again later.",
        )


@router.get(
    "/history",
    response_model=StockHistoryResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get historical OHLCV data (query param)",
    description="Get historical Open, High, Low, Close, Volume data for a stock. Use this endpoint for symbols with special characters (e.g., GC=F).",
)
async def get_stock_history_query(
    symbol: str = Query(..., description="Stock symbol (e.g., AAPL, 0700.HK, GC=F)"),
    period: HistoryPeriod = Query(
        HistoryPeriod.ONE_YEAR,
        description="Time period for historical data (ignored when start/end provided)",
    ),
    interval: HistoryInterval = Query(
        HistoryInterval.DAILY,
        description="Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)",
    ),
    start: Optional[str] = Query(None, description="Start date (e.g. 2025-03-01 or 2025-03-01T09:30:00)"),
    end: Optional[str] = Query(None, description="End date (e.g. 2025-03-15 or 2025-03-15T15:00:00)"),
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """Get historical OHLCV data for a stock using query parameter."""
    symbol = validate_symbol(symbol)
    logger.info(
        "Getting history for %s (period=%s, interval=%s, start=%s, end=%s)",
        symbol, period, interval, start, end,
    )

    stock_service = await get_stock_service()

    service_period = ServicePeriod(period.value)
    service_interval = ServiceInterval(interval.value)

    try:
        history = await stock_service.get_history(
            symbol,
            period=service_period,
            interval=service_interval,
            force_refresh=refresh,
            start=start if start and end else None,
            end=end if start and end else None,
        )

        if history is None:
            if is_precious_metal(symbol):
                raise _futures_market_closed_or_404(symbol)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No historical data available for symbol: {symbol}",
            )

        return StockHistoryResponse(**history)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting history for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch historical data. Please try again later.",
        )


@router.get(
    "/info",
    response_model=StockInfoResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get company information (query param)",
    description="Get company details including name, sector, description, and other information. Use this endpoint for symbols with special characters (e.g., GC=F).",
)
async def get_stock_info_query(
    symbol: str = Query(..., description="Stock symbol (e.g., AAPL, 0700.HK, GC=F)"),
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """Get company information for a stock using query parameter."""
    symbol = validate_symbol(symbol)
    logger.info(f"Getting info for {symbol}")

    stock_service = await get_stock_service()

    try:
        info = await stock_service.get_info(symbol, force_refresh=refresh)

        if info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No company information available for symbol: {symbol}",
            )

        return StockInfoResponse(**info)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting info for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch company information. Please try again later.",
        )


@router.get(
    "/financials",
    response_model=StockFinancialsResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get financial metrics (query param)",
    description="Get financial metrics including P/E ratio, EPS, dividend yield, and more. Use this endpoint for symbols with special characters (e.g., GC=F).",
)
async def get_stock_financials_query(
    symbol: str = Query(..., description="Stock symbol (e.g., AAPL, 0700.HK, GC=F)"),
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """Get financial metrics for a stock using query parameter."""
    symbol = validate_symbol(symbol)
    logger.info(f"Getting financials for {symbol}")

    stock_service = await get_stock_service()

    try:
        financials = await stock_service.get_financials(symbol, force_refresh=refresh)

        if financials is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No financial data available for symbol: {symbol}",
            )

        return StockFinancialsResponse(**financials)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting financials for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch financial data. Please try again later.",
        )


@router.get(
    "/indicators",
    response_model=TechnicalIndicatorsResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid parameters"},
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get technical indicators (query param)",
    description=(
        "Compute technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands) "
        "for a stock. Use this endpoint for symbols with special characters (e.g., GC=F)."
    ),
)
async def get_stock_indicators_query(
    symbol: str = Query(..., description="Stock symbol (e.g., AAPL, 0700.HK, GC=F)"),
    types: str = Query("sma", description="Comma-separated: sma,ema,rsi,macd,bb"),
    period: HistoryPeriod = Query(
        HistoryPeriod.ONE_YEAR,
        description="Time period for the indicator display window",
    ),
    interval: HistoryInterval = Query(
        HistoryInterval.DAILY,
        description="Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)",
    ),
    ma_periods: str = Query(
        "20,50,200",
        description="Comma-separated MA periods (max 5, each 2-500)",
    ),
    rsi_period: int = Query(14, ge=2, le=100, description="RSI period"),
    bb_period: int = Query(20, ge=2, le=100, description="Bollinger Bands period"),
    bb_std: float = Query(2.0, ge=0.5, le=5.0, description="Bollinger Bands std dev"),
    start: Optional[str] = Query(None, description="Start date (e.g. 2025-03-01 or 2025-03-01T09:30:00)"),
    end: Optional[str] = Query(None, description="End date (e.g. 2025-03-15 or 2025-03-15T15:00:00)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(INDICATOR_RATE_LIMIT),
):
    """Get technical indicators for a stock using query parameter."""
    symbol = validate_symbol(symbol)
    logger.info("Getting indicators for %s (user: %d)", symbol, current_user.id)

    return await _compute_indicators_for_symbol(
        symbol=symbol,
        types=types,
        period=period,
        interval=interval,
        ma_periods=ma_periods,
        rsi_period=rsi_period,
        bb_period=bb_period,
        bb_std=bb_std,
        start=start if start and end else None,
        end=end if start and end else None,
    )


@router.get(
    "/history/latest",
    summary="Get incremental bars since a timestamp",
    description=(
        "Fetch bars newer than the given Unix timestamp, useful for live chart "
        "updates. New bars are written back to the canonical disk cache (Layer 2) "
        "as a fire-and-forget operation."
    ),
)
async def get_latest_bars(
    symbol: str = Query(..., description="Stock symbol (e.g., AAPL, 0700.HK, GC=F)"),
    interval: HistoryInterval = Query(HistoryInterval.FIVE_MINUTES),
    since: int = Query(..., ge=946684800, description="Unix timestamp of last known bar (>= 2000-01-01)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """Get incremental bars since a timestamp. Writes back to canonical cache."""
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)

    since_dt = datetime.fromtimestamp(since, tz=timezone.utc)

    # Resolve to the canonical tier interval
    canonical_interval = RESAMPLE_MAP.get(interval.value, interval.value)
    interval_mins = INTERVAL_MINUTES.get(canonical_interval, 5)

    # Fetch slightly before 'since' to catch bars we might have missed
    start_str = (since_dt - timedelta(minutes=interval_mins * 2)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    end_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Fetch from provider at canonical precision
    router = await get_provider_router()
    try:
        history = await router.get_history(
            symbol,
            ServicePeriod.ONE_DAY,
            ServiceInterval(canonical_interval),
            market,
            start=start_str,
            end=end_str,
        )
    except Exception as e:
        logger.error("Error fetching latest bars for %s: %s", symbol, e)
        return {"symbol": symbol, "interval": interval.value, "bars": []}

    if not history or not history.bars:
        return {"symbol": symbol, "interval": interval.value, "bars": []}

    raw_bars = [
        {
            "date": (
                b.date.isoformat() if hasattr(b.date, "isoformat") else str(b.date)
            ),
            "open": round(float(b.open), 4),
            "high": round(float(b.high), 4),
            "low": round(float(b.low), 4),
            "close": round(float(b.close), 4),
            "volume": int(b.volume),
        }
        for b in history.bars
    ]

    # Fire-and-forget: write back to Layer 2 disk cache
    canonical_service = await get_canonical_cache_service()
    task = asyncio.create_task(
        canonical_service.append_bars(symbol, canonical_interval, raw_bars)
    )
    task.add_done_callback(
        lambda t: t.exception() and logger.error(
            "append_bars background task failed for %s/%s: %s",
            symbol, canonical_interval, t.exception(),
        )
    )

    # Downsample to user's requested interval if different from canonical
    if canonical_interval != interval.value:
        raw_bars = resample_bars(raw_bars, canonical_interval, interval.value)

    return {"symbol": symbol, "interval": interval.value, "bars": raw_bars}


# =============================================================================
# Path parameter routes (legacy, kept for backward compatibility)
# =============================================================================


@router.get(
    "/{symbol}/quote",
    response_model=StockQuoteResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get real-time stock quote",
    description="Get current price, change, volume and other real-time data for a stock.",
)
async def get_stock_quote(
    symbol: str,
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """
    Get real-time quote for a stock.

    - **symbol**: Stock symbol (AAPL, 0700.HK, 600519.SS, 000001.SZ)
    - **refresh**: Force refresh from data source, bypassing cache

    Symbol formats:
    - US stocks: AAPL, MSFT, GOOGL
    - HK stocks: 0700.HK, 9988.HK
    - Shanghai A-shares: 600519.SS, 600036.SS
    - Shenzhen A-shares: 000001.SZ, 000858.SZ
    """
    symbol = validate_symbol(symbol)
    logger.info(f"Getting quote for {symbol} (user: {current_user.id})")

    stock_service = await get_stock_service()

    try:
        quote = await stock_service.get_quote(symbol, force_refresh=refresh)

        if quote is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No quote data available for symbol: {symbol}",
            )

        return StockQuoteResponse(**quote)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quote for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch stock data. Please try again later.",
        )


@router.get(
    "/{symbol}/history",
    response_model=StockHistoryResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get historical OHLCV data",
    description="Get historical Open, High, Low, Close, Volume data for a stock.",
)
async def get_stock_history(
    symbol: str,
    period: HistoryPeriod = Query(
        HistoryPeriod.ONE_YEAR,
        description="Time period for historical data (ignored when start/end provided)",
    ),
    interval: HistoryInterval = Query(
        HistoryInterval.DAILY,
        description="Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)",
    ),
    start: Optional[str] = Query(None, description="Start date (e.g. 2025-03-01 or 2025-03-01T09:30:00)"),
    end: Optional[str] = Query(None, description="End date (e.g. 2025-03-15 or 2025-03-15T15:00:00)"),
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """
    Get historical OHLCV data for a stock.

    - **symbol**: Stock symbol
    - **period**: Time period (1mo, 3mo, 6mo, 1y, 2y, 5y, max) - ignored when start/end provided
    - **interval**: Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)
    - **start**: Optional start date/datetime (e.g. 2025-03-01)
    - **end**: Optional end date/datetime (e.g. 2025-03-15)
    - **refresh**: Force refresh from data source
    """
    symbol = validate_symbol(symbol)
    logger.info(
        "Getting history for %s (period=%s, interval=%s, start=%s, end=%s)",
        symbol, period, interval, start, end,
    )

    stock_service = await get_stock_service()

    # Convert schema enums to service enums
    service_period = ServicePeriod(period.value)
    service_interval = ServiceInterval(interval.value)

    try:
        history = await stock_service.get_history(
            symbol,
            period=service_period,
            interval=service_interval,
            force_refresh=refresh,
            start=start if start and end else None,
            end=end if start and end else None,
        )

        if history is None:
            if is_precious_metal(symbol):
                raise _futures_market_closed_or_404(symbol)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No historical data available for symbol: {symbol}",
            )

        return StockHistoryResponse(**history)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting history for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch historical data. Please try again later.",
        )


@router.get(
    "/{symbol}/info",
    response_model=StockInfoResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get company information",
    description="Get company details including name, sector, description, and other information.",
)
async def get_stock_info(
    symbol: str,
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """
    Get company information for a stock.

    - **symbol**: Stock symbol
    - **refresh**: Force refresh from data source

    Returns company name, description, sector, industry, website, and other details.
    """
    symbol = validate_symbol(symbol)
    logger.info(f"Getting info for {symbol}")

    stock_service = await get_stock_service()

    try:
        info = await stock_service.get_info(symbol, force_refresh=refresh)

        if info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No company information available for symbol: {symbol}",
            )

        return StockInfoResponse(**info)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting info for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch company information. Please try again later.",
        )


@router.get(
    "/{symbol}/financials",
    response_model=StockFinancialsResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get financial metrics",
    description="Get financial metrics including P/E ratio, EPS, dividend yield, and more.",
)
async def get_stock_financials(
    symbol: str,
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """
    Get financial metrics for a stock.

    - **symbol**: Stock symbol
    - **refresh**: Force refresh from data source

    Returns P/E ratio, EPS, dividend yield, book value, profit margin, ROE, and other metrics.
    """
    symbol = validate_symbol(symbol)
    logger.info(f"Getting financials for {symbol}")

    stock_service = await get_stock_service()

    try:
        financials = await stock_service.get_financials(symbol, force_refresh=refresh)

        if financials is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No financial data available for symbol: {symbol}",
            )

        return StockFinancialsResponse(**financials)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting financials for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch financial data. Please try again later.",
        )


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search stocks",
    description="Search for stocks by symbol or name across multiple markets.",
)
async def search_stocks(
    q: str = Query(..., min_length=1, max_length=50, description="Search query"),
    markets: Optional[List[MarketType]] = Query(
        None,
        description="Markets to search (default: all)",
    ),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """
    Search for stocks by symbol or name.

    - **q**: Search query (symbol or company name)
    - **markets**: Optional list of markets to search (us, hk, sh, sz, metal)

    Uses local in-memory search for fast response (<10ms).
    Falls back to API search if local data is not available.

    Returns matching stocks from all requested markets.
    """
    logger.info(f"Searching stocks: {q} (markets: {markets})")

    # Convert schema enum to market strings for local search
    market_list = None
    if markets:
        market_list = [m.value for m in markets]

    # Try local search first
    try:
        from app.services.stock_list_service import get_stock_list_service

        stock_list_service = await get_stock_list_service(auto_load=True)

        if stock_list_service.is_loaded:
            # Use fast local search
            results = stock_list_service.search(q, markets=market_list, limit=50)

            if results:
                logger.debug(f"Local search found {len(results)} results for '{q}'")
                # Filter to only include fields expected by SearchResultResponse
                allowed_fields = {'symbol', 'name', 'exchange', 'market', 'match_field', 'name_zh'}
                filtered_results = [
                    {k: v for k, v in r.items() if k in allowed_fields}
                    for r in results
                ]
                return SearchResponse(
                    results=[SearchResultResponse(**r) for r in filtered_results],
                    count=len(results),
                    source="local",
                )
            else:
                logger.debug(f"Local search found no results for '{q}', falling back to API")

    except Exception as e:
        logger.warning(f"Local search failed, falling back to API: {e}")

    # Fall back to API search
    stock_service = await get_stock_service()

    # Convert schema enum to service enum
    service_markets = None
    if markets:
        service_markets = [Market(m.value) for m in markets]

    try:
        results = await stock_service.search(q, markets=service_markets)

        # Debug: log first few results
        if results:
            logger.debug(f"API search results sample: {results[:3]}")

        return SearchResponse(
            results=[SearchResultResponse(**r) for r in results],
            count=len(results),
            source="api",
        )

    except Exception as e:
        logger.error(f"Error searching stocks: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search service temporarily unavailable. Please try again later.",
        )


@router.post(
    "/batch/quotes",
    response_model=BatchQuoteResponse,
    summary="Get quotes for multiple stocks",
    description="Get real-time quotes for multiple stocks in a single request.",
)
async def get_batch_quotes(
    request: BatchQuoteRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """
    Get quotes for multiple stocks efficiently.

    - **symbols**: List of stock symbols (max 50)

    Returns a dictionary mapping each symbol to its quote (or null if unavailable).
    """
    logger.info(f"Getting batch quotes for {len(request.symbols)} symbols")

    # Validate and normalize symbols
    symbols = [validate_symbol(s) for s in request.symbols]

    stock_service = await get_stock_service()

    try:
        quotes = await stock_service.get_batch_quotes(symbols)

        # Convert to response format
        response_quotes = {}
        for symbol, quote in quotes.items():
            if quote:
                response_quotes[symbol] = StockQuoteResponse(**quote)
            else:
                response_quotes[symbol] = None

        return BatchQuoteResponse(quotes=response_quotes)

    except Exception as e:
        logger.error(f"Error getting batch quotes: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch stock data. Please try again later.",
        )


@router.get(
    "/{symbol}/indicators",
    response_model=TechnicalIndicatorsResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid parameters"},
        404: {"model": ErrorResponse, "description": "Stock not found"},
        503: {"model": ErrorResponse, "description": "Data source unavailable"},
    },
    summary="Get technical indicators",
    description=(
        "Compute technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands) "
        "for a stock."
    ),
)
async def get_stock_indicators(
    symbol: str,
    types: str = Query("sma", description="Comma-separated: sma,ema,rsi,macd,bb"),
    period: HistoryPeriod = Query(
        HistoryPeriod.ONE_YEAR,
        description="Time period for the indicator display window",
    ),
    interval: HistoryInterval = Query(
        HistoryInterval.DAILY,
        description="Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)",
    ),
    ma_periods: str = Query(
        "20,50,200",
        description="Comma-separated MA periods (max 5, each 2-500)",
    ),
    rsi_period: int = Query(14, ge=2, le=100, description="RSI period"),
    bb_period: int = Query(20, ge=2, le=100, description="Bollinger Bands period"),
    bb_std: float = Query(2.0, ge=0.5, le=5.0, description="Bollinger Bands std dev"),
    start: Optional[str] = Query(None, description="Start date (e.g. 2025-03-01 or 2025-03-01T09:30:00)"),
    end: Optional[str] = Query(None, description="End date (e.g. 2025-03-15 or 2025-03-15T15:00:00)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(INDICATOR_RATE_LIMIT),
):
    """
    Get technical indicators for a stock.

    - **symbol**: Stock symbol (AAPL, 0700.HK, 600519.SS, 000001.SZ)
    - **types**: Comma-separated indicator types: sma, ema, rsi, macd, bb
    - **period**: Display window for results (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max)
    - **interval**: Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)
    - **ma_periods**: Comma-separated moving average periods (max 5, each 2-500)
    - **rsi_period**: RSI period (2-100, default 14)
    - **bb_period**: Bollinger Bands period (2-100, default 20)
    - **bb_std**: Bollinger Bands standard deviation (0.5-5.0, default 2.0)
    - **start**: Optional start date/datetime for indicator window
    - **end**: Optional end date/datetime for indicator window

    Automatically fetches extended history for indicator warm-up,
    then trims results to the requested period.
    """
    symbol = validate_symbol(symbol)
    logger.info("Getting indicators for %s (user: %d)", symbol, current_user.id)

    return await _compute_indicators_for_symbol(
        symbol=symbol,
        types=types,
        period=period,
        interval=interval,
        ma_periods=ma_periods,
        rsi_period=rsi_period,
        bb_period=bb_period,
        bb_std=bb_std,
        start=start if start and end else None,
        end=end if start and end else None,
    )
