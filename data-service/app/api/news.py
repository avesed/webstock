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


@router.post(
    "/push-to-newsforge",
    response_model=ApiResponse,
    summary="Push collected news to NewsForge",
)
async def push_to_newsforge(
    symbols: Optional[str] = Query(None, description="Comma-separated symbols to push news for"),
    category: str = Query("general", description="News category for general news"),
    include_trending_cn: bool = Query(True, description="Include trending CN news"),
) -> ApiResponse:
    """Manually trigger a push of collected news to NewsForge.

    Collects news from all configured providers and pushes to NewsForge
    for LLM processing (classification, entity extraction, etc.).
    """
    from app.core.cache import get_redis
    from app.services.newsforge_push_service import NewsForgePushService

    push_service = NewsForgePushService()
    if not push_service.enabled:
        return ApiResponse(success=False, error="NewsForge push is not enabled")

    start = time.monotonic()
    all_articles = []

    # Collect general news
    general = await _news_service.get_general_news(category=category)
    all_articles.extend(general)

    # Collect trending CN news
    if include_trending_cn:
        try:
            cn_news = await _news_service.get_trending_cn_news()
            all_articles.extend(cn_news)
        except Exception:
            logger.warning("Failed to fetch trending CN news for push", exc_info=True)

    # Collect news for specific symbols
    if symbols:
        for sym in symbols.split(","):
            sym = sym.strip()
            if sym:
                try:
                    sym_news = await _news_service.get_company_news(symbol=sym)
                    all_articles.extend(sym_news)
                except Exception:
                    logger.warning("Failed to fetch news for %s", sym, exc_info=True)

    # Push to NewsForge
    redis = await get_redis()
    result = await push_service.push_articles(all_articles, redis=redis)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    return ApiResponse(success=True, data=result, elapsed_ms=elapsed_ms)


@router.get(
    "/newsforge-status",
    response_model=ApiResponse,
    summary="Get NewsForge push statistics",
)
async def newsforge_push_status() -> ApiResponse:
    """Get statistics about news articles pushed to NewsForge."""
    from app.core.cache import get_redis
    from app.services.newsforge_push_service import NewsForgePushService

    push_service = NewsForgePushService()
    redis = await get_redis()
    stats = await push_service.get_push_stats(redis)

    return ApiResponse(success=True, data={
        "enabled": push_service.enabled,
        **stats,
    })
