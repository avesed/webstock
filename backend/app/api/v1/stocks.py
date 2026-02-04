"""Stock data API endpoints."""

import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.stock import (
    BatchQuoteRequest,
    BatchQuoteResponse,
    ErrorResponse,
    HistoryInterval,
    HistoryPeriod,
    MarketType,
    SearchResponse,
    SearchResultResponse,
    StockFinancialsResponse,
    StockHistoryResponse,
    StockInfoResponse,
    StockQuoteResponse,
)
from app.services.stock_service import (
    HistoryInterval as ServiceInterval,
    HistoryPeriod as ServicePeriod,
    Market,
    get_stock_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stocks", tags=["Stocks"])

# Rate limiting: 100 requests per minute for stock queries
STOCK_RATE_LIMIT = rate_limit(max_requests=100, window_seconds=60, key_prefix="stock_api")

# Symbol validation patterns
SYMBOL_PATTERNS = {
    "US": re.compile(r"^[A-Z]{1,5}$"),           # US: 1-5 uppercase letters
    "HK": re.compile(r"^[0-9]{4,5}\.HK$"),       # HK: 4-5 digits followed by .HK
    "A_SHARE": re.compile(r"^[0-9]{6}\.(SS|SZ)$"),  # A-Share: 6 digits followed by .SS or .SZ
    "A_SHARE_BARE": re.compile(r"^[0-9]{6}$"),    # A-Share without suffix
    "HK_BARE": re.compile(r"^[0-9]{4,5}$"),       # HK without .HK suffix
}

# Shanghai: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx (STAR Market)
_SHANGHAI_PREFIXES = ("600", "601", "603", "605", "688")
# Shenzhen: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx (ChiNext), 301xxx
_SHENZHEN_PREFIXES = ("000", "001", "002", "003", "300", "301")


def validate_symbol(symbol: str) -> str:
    """Validate and normalize stock symbol with regex pattern matching."""
    symbol = symbol.strip().upper()
    if not symbol or len(symbol) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid symbol format: symbol is empty or too long",
        )

    # Auto-append exchange suffix for bare 6-digit A-share codes
    if SYMBOL_PATTERNS["A_SHARE_BARE"].match(symbol):
        if symbol.startswith(_SHANGHAI_PREFIXES):
            symbol = f"{symbol}.SS"
        elif symbol.startswith(_SHENZHEN_PREFIXES):
            symbol = f"{symbol}.SZ"

    # Auto-append .HK for bare 4-5 digit codes that look like HK stocks
    if SYMBOL_PATTERNS["HK_BARE"].match(symbol):
        symbol = f"{symbol}.HK"

    # Check against valid patterns
    is_valid = (
        SYMBOL_PATTERNS["US"].match(symbol) or
        SYMBOL_PATTERNS["HK"].match(symbol) or
        SYMBOL_PATTERNS["A_SHARE"].match(symbol)
    )

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid symbol format. Valid formats: "
                "US (e.g., AAPL), HK (e.g., 0700.HK), "
                "Shanghai (e.g., 600519.SS), Shenzhen (e.g., 000001.SZ)"
            ),
        )

    # Normalize HK symbols: 01810.HK → 1810.HK (yfinance uses 4-digit codes)
    if symbol.endswith(".HK"):
        code = symbol[:-3]  # strip ".HK"
        code = str(int(code)).zfill(4)  # 01810 → 1810, 00700 → 0700
        symbol = f"{code}.HK"

    return symbol


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
        description="Time period for historical data",
    ),
    interval: HistoryInterval = Query(
        HistoryInterval.DAILY,
        description="Data interval (daily, weekly, monthly)",
    ),
    refresh: bool = Query(False, description="Force refresh from source"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(STOCK_RATE_LIMIT),
):
    """
    Get historical OHLCV data for a stock.

    - **symbol**: Stock symbol
    - **period**: Time period (1mo, 3mo, 6mo, 1y, 2y, 5y, max)
    - **interval**: Data interval (1d for daily, 1wk for weekly, 1mo for monthly)
    - **refresh**: Force refresh from data source
    """
    symbol = validate_symbol(symbol)
    logger.info(f"Getting history for {symbol} (period={period}, interval={interval})")

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
        )

        if history is None:
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
    - **markets**: Optional list of markets to search (us, hk, sh, sz)

    Returns matching stocks from all requested markets.
    """
    logger.info(f"Searching stocks: {q} (markets: {markets})")

    stock_service = await get_stock_service()

    # Convert schema enum to service enum
    service_markets = None
    if markets:
        service_markets = [Market(m.value) for m in markets]

    try:
        results = await stock_service.search(q, markets=service_markets)

        return SearchResponse(
            results=[SearchResultResponse(**r) for r in results],
            count=len(results),
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
