"""Stock data API endpoints."""

import logging
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
from app.utils.symbol_validation import validate_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stocks", tags=["Stocks"])

# Rate limiting: 100 requests per minute for stock queries
STOCK_RATE_LIMIT = rate_limit(max_requests=100, window_seconds=60, key_prefix="stock_api")


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
    """Get historical OHLCV data for a stock using query parameter."""
    symbol = validate_symbol(symbol)
    logger.info(f"Getting history for {symbol} (period={period}, interval={interval})")

    stock_service = await get_stock_service()

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
