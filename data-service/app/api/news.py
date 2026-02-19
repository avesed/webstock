"""News API endpoints for the data-service.

Provides raw news fetching from multiple providers (Finnhub, YFinance, AKShare)
with market-based routing. No LLM processing or database writes.

All endpoints require X-Internal-Token authentication.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.core.auth import verify_internal_token
from app.models.base import ApiResponse
from app.models.news import NewsArticle
from app.services.news_service import NewsService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/news",
    tags=["news"],
    dependencies=[Depends(verify_internal_token)],
)

# Singleton service instance (stateless, safe to share)
_news_service = NewsService()


@router.get(
    "/company/{symbol}",
    response_model=ApiResponse[List[NewsArticle]],
    summary="Get company news by symbol",
)
async def get_company_news(
    symbol: str,
    market: Optional[str] = Query(
        None,
        description="Market override (us, hk, sh, sz, metal). Auto-detected if omitted.",
    ),
    source: Optional[str] = Query(
        None,
        description="Preferred source (finnhub, yfinance, akshare, auto).",
    ),
    from_date: Optional[str] = Query(
        None,
        alias="from",
        description="Start date YYYY-MM-DD (Finnhub only). Defaults to 7 days ago.",
    ),
    to_date: Optional[str] = Query(
        None,
        alias="to",
        description="End date YYYY-MM-DD (Finnhub only). Defaults to today.",
    ),
) -> ApiResponse[List[NewsArticle]]:
    """Get news for a specific stock symbol.

    Routes to the appropriate provider based on market detection:
    - US: YFinance (primary) + Finnhub (fallback)
    - HK: YFinance Ticker (primary) + Finnhub (fallback)
    - A-shares: AKShare/Eastmoney
    - Metals: YFinance Ticker
    """
    start = time.monotonic()

    articles = await _news_service.get_company_news(
        symbol=symbol,
        market=market,
        from_date=from_date,
        to_date=to_date,
        source=source,
    )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Determine which provider was used from the first article
    provider = None
    if articles:
        provider = articles[0].get("provider")

    return ApiResponse(
        success=True,
        data=articles,
        source=provider,
        elapsed_ms=elapsed_ms,
    )


@router.get(
    "/general",
    response_model=ApiResponse[List[NewsArticle]],
    summary="Get general market news",
)
async def get_general_news(
    category: str = Query(
        "general",
        description="News category (general, forex, crypto, merger).",
    ),
) -> ApiResponse[List[NewsArticle]]:
    """Get general market news from Finnhub."""
    start = time.monotonic()

    articles = await _news_service.get_general_news(category=category)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    return ApiResponse(
        success=True,
        data=articles,
        source="finnhub",
        elapsed_ms=elapsed_ms,
    )


@router.get(
    "/trending-cn",
    response_model=ApiResponse[List[NewsArticle]],
    summary="Get trending Chinese market news",
)
async def get_trending_cn_news() -> ApiResponse[List[NewsArticle]]:
    """Get trending/hot A-share market news from AKShare (Eastmoney source)."""
    start = time.monotonic()

    articles = await _news_service.get_trending_cn_news()

    elapsed_ms = int((time.monotonic() - start) * 1000)

    return ApiResponse(
        success=True,
        data=articles,
        source="akshare",
        elapsed_ms=elapsed_ms,
    )
