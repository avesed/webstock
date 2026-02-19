"""Stock data API routes -- quotes, history, info, financials, search, batch.

All routes are protected by X-Internal-Token and return ApiResponse envelopes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.auth import verify_internal_token
from app.models.base import ApiResponse
from app.models.stock import (
    FinancialsData,
    HistoryData,
    InfoData,
    OHLCVBar,
    QuoteData,
    SearchItem,
)
from app.services.stock_router import get_stock_router

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/v1",
    tags=["stock"],
    dependencies=[Depends(verify_internal_token)],
)


# --- Request models ---

class BatchQuotesRequest(BaseModel):
    symbols: list[str]


# --- Routes ---


@router.get("/quote/{symbol}", response_model=ApiResponse[QuoteData])
async def get_quote(
    symbol: str,
    market: str = Query("us"),
):
    """Get real-time quote for a symbol."""
    t0 = time.monotonic()
    sr = await get_stock_router()
    data = await sr.get_quote(symbol, market=market)
    elapsed = int((time.monotonic() - t0) * 1000)

    if data is None:
        return ApiResponse(
            success=False,
            error=f"No quote data for {symbol}",
            elapsed_ms=elapsed,
        )

    source = data.get("source", "unknown")
    quote = QuoteData(
        symbol=data.get("symbol", symbol),
        name=data.get("name"),
        price=data.get("price", 0),
        change=data.get("change", 0),
        change_percent=data.get("change_percent", 0),
        volume=data.get("volume"),
        market_cap=data.get("market_cap"),
        day_high=data.get("high") or data.get("day_high"),
        day_low=data.get("low") or data.get("day_low"),
        open=data.get("open"),
        previous_close=data.get("prev_close") or data.get("previous_close"),
        timestamp=data.get("timestamp"),
        market=data.get("market", market),
        currency=data.get("currency"),
        source=source,
    )

    return ApiResponse(
        data=quote,
        source=source,
        elapsed_ms=elapsed,
    )


@router.get("/history/{symbol}", response_model=ApiResponse[HistoryData])
async def get_history(
    symbol: str,
    period: str = Query("1y"),
    interval: str = Query("1d"),
    market: str = Query("us"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """Get historical OHLCV data for a symbol."""
    t0 = time.monotonic()
    sr = await get_stock_router()
    data = await sr.get_history(
        symbol, period=period, interval=interval,
        market=market, start=start, end=end,
    )
    elapsed = int((time.monotonic() - t0) * 1000)

    if data is None:
        return ApiResponse(
            success=False,
            error=f"No history data for {symbol}",
            elapsed_ms=elapsed,
        )

    bars = [
        OHLCVBar(
            date=b.get("date", ""),
            open=b.get("open", 0),
            high=b.get("high", 0),
            low=b.get("low", 0),
            close=b.get("close", 0),
            volume=b.get("volume"),
        )
        for b in data.get("bars", [])
    ]

    history = HistoryData(
        symbol=data.get("symbol", symbol),
        bars=bars,
        interval=data.get("interval", interval),
        market=data.get("market", market),
    )

    return ApiResponse(
        data=history,
        source=data.get("source"),
        elapsed_ms=elapsed,
    )


@router.get("/info/{symbol}", response_model=ApiResponse[InfoData])
async def get_info(
    symbol: str,
    market: str = Query("us"),
):
    """Get company / instrument information."""
    t0 = time.monotonic()
    sr = await get_stock_router()
    data = await sr.get_info(symbol, market=market)
    elapsed = int((time.monotonic() - t0) * 1000)

    if data is None:
        return ApiResponse(
            success=False,
            error=f"No info data for {symbol}",
            elapsed_ms=elapsed,
        )

    source = data.get("source", "unknown")
    info = InfoData(
        symbol=data.get("symbol", symbol),
        name=data.get("name", ""),
        description=data.get("description"),
        sector=data.get("sector"),
        industry=data.get("industry"),
        website=data.get("website"),
        employees=data.get("employees"),
        market_cap=data.get("market_cap"),
        currency=data.get("currency"),
        exchange=data.get("exchange"),
        market=data.get("market", market),
        source=source,
    )

    return ApiResponse(
        data=info,
        source=source,
        elapsed_ms=elapsed,
    )


@router.get(
    "/financials/{symbol}",
    response_model=ApiResponse[FinancialsData],
)
async def get_financials(
    symbol: str,
    market: str = Query("us"),
):
    """Get key financial metrics and ratios."""
    t0 = time.monotonic()
    sr = await get_stock_router()
    data = await sr.get_financials(symbol, market=market)
    elapsed = int((time.monotonic() - t0) * 1000)

    if data is None:
        return ApiResponse(
            success=False,
            error=f"No financials data for {symbol}",
            elapsed_ms=elapsed,
        )

    source = data.get("source", "unknown")
    financials = FinancialsData(
        symbol=data.get("symbol", symbol),
        pe_ratio=data.get("pe_ratio"),
        forward_pe=data.get("forward_pe"),
        eps=data.get("eps"),
        dividend_yield=data.get("dividend_yield"),
        dividend_rate=data.get("dividend_rate"),
        book_value=data.get("book_value"),
        price_to_book=data.get("price_to_book"),
        revenue=data.get("revenue"),
        revenue_growth=data.get("revenue_growth"),
        net_income=data.get("net_income"),
        profit_margin=data.get("profit_margin"),
        gross_margin=data.get("gross_margin"),
        operating_margin=data.get("operating_margin"),
        roe=data.get("roe"),
        roa=data.get("roa"),
        debt_to_equity=data.get("debt_to_equity"),
        current_ratio=data.get("current_ratio"),
        eps_growth=data.get("eps_growth"),
        payout_ratio=data.get("payout_ratio"),
        market=data.get("market", market),
        source=source,
    )

    return ApiResponse(
        data=financials,
        source=source,
        elapsed_ms=elapsed,
    )


@router.get(
    "/search",
    response_model=ApiResponse[list[SearchItem]],
)
async def search_stocks(
    q: str = Query(..., min_length=1),
    markets: str = Query("us,hk,sh,sz,metal"),
):
    """Search for stocks across markets."""
    t0 = time.monotonic()
    market_list = [m.strip().lower() for m in markets.split(",") if m.strip()]
    sr = await get_stock_router()
    results = await sr.search(q, markets=market_list)
    elapsed = int((time.monotonic() - t0) * 1000)

    items = [
        SearchItem(
            symbol=r.get("symbol", ""),
            name=r.get("name", ""),
            exchange=r.get("exchange"),
            market=r.get("market", "us"),
        )
        for r in results
    ]

    return ApiResponse(
        data=items,
        source="mixed",
        elapsed_ms=elapsed,
    )


@router.post(
    "/batch/quotes",
    response_model=ApiResponse[dict],
)
async def batch_quotes(body: BatchQuotesRequest):
    """Get quotes for multiple symbols in parallel."""
    t0 = time.monotonic()
    sr = await get_stock_router()

    async def _fetch_one(sym: str):
        try:
            return sym, await sr.get_quote(sym)
        except Exception as e:
            logger.warning("Batch quote error for %s: %s", sym, e)
            return sym, None

    tasks = [_fetch_one(s) for s in body.symbols[:50]]  # cap at 50
    results = await asyncio.gather(*tasks)
    elapsed = int((time.monotonic() - t0) * 1000)

    quotes = {}
    for sym, data in results:
        if data is not None:
            quotes[sym] = data

    return ApiResponse(
        data={"quotes": quotes, "count": len(quotes)},
        source="mixed",
        elapsed_ms=elapsed,
    )
