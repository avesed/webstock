"""News API endpoints."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.news import News, NewsAlert
from app.models.user import User
from app.models.user_settings import UserSettings
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.news import (
    MessageResponse,
    NewsAlertCreate,
    NewsAlertListResponse,
    NewsAlertResponse,
    NewsAlertUpdate,
    NewsAnalysisRequest,
    NewsAnalysisResponse,
    NewsFeedResponse,
    NewsResponse,
    TrendingNewsResponse,
)
from app.services.news_service import get_news_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["News"])

# Rate limiting configurations for different endpoints
# Symbol news: 100 requests per minute
SYMBOL_NEWS_RATE_LIMIT = rate_limit(max_requests=100, window_seconds=60, key_prefix="news_symbol")
# Feed/Trending: 30 requests per minute
FEED_RATE_LIMIT = rate_limit(max_requests=30, window_seconds=60, key_prefix="news_feed")
# Analyze: 10 requests per minute (uses AI)
ANALYZE_RATE_LIMIT = rate_limit(max_requests=10, window_seconds=60, key_prefix="news_analyze")
# Alerts CRUD: 60 requests per minute
ALERTS_RATE_LIMIT = rate_limit(max_requests=60, window_seconds=60, key_prefix="news_alerts")


@router.get(
    "/trending",
    response_model=TrendingNewsResponse,
    summary="Get trending news",
    description="Get hot/trending market news, optionally filtered by market.",
)
async def get_trending_news(
    market: Optional[str] = Query(
        None,
        description="Filter by market (US, HK, SH, SZ)",
        regex="^(US|HK|SH|SZ)$",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """
    Get trending/hot market news.

    - **market**: Optional market filter (US, HK, SH, SZ)
    """
    # Load user with settings
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(User).where(User.id == current_user.id).options(selectinload(User.settings))
    )
    user = result.scalar_one_or_none()
    
    news_service = await get_news_service()
    news = await news_service.get_trending_news(market=market, user=user)

    return TrendingNewsResponse(
        news=[NewsResponse(**n) for n in news],
        market=market,
        fetched_at=datetime.now(timezone.utc),
    )


@router.get(
    "/feed",
    response_model=NewsFeedResponse,
    summary="Get user's news feed",
    description="Get aggregated news for stocks in user's watchlists.",
)
async def get_news_feed(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """
    Get personalized news feed based on user's watchlist stocks.

    - **page**: Page number (1-indexed)
    - **page_size**: Number of items per page (max 100)
    """
    # Get all symbols from user's watchlists
    query = (
        select(WatchlistItem.symbol)
        .join(Watchlist)
        .where(Watchlist.user_id == current_user.id)
        .distinct()
    )
    result = await db.execute(query)
    symbols = [row[0] for row in result.fetchall()]

    if not symbols:
        return NewsFeedResponse(
            news=[],
            total=0,
            page=page,
            page_size=page_size,
            has_more=False,
        )

    # Load user with settings
    from sqlalchemy.orm import selectinload
    user_result = await db.execute(
        select(User).where(User.id == current_user.id).options(selectinload(User.settings))
    )
    user = user_result.scalar_one_or_none()
    
    # Get news for these symbols
    news_service = await get_news_service()
    feed_data = await news_service.get_news_feed(
        symbols=symbols,
        page=page,
        page_size=page_size,
        user=user,
    )

    return NewsFeedResponse(
        news=[NewsResponse(**n) for n in feed_data["news"]],
        total=feed_data["total"],
        page=feed_data["page"],
        page_size=feed_data["page_size"],
        has_more=feed_data["has_more"],
    )


@router.get(
    "/alerts",
    response_model=NewsAlertListResponse,
    summary="Get user's news alerts",
    description="Get all news alerts configured by the user.",
)
async def get_news_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Get all news alerts for the current user.
    """
    query = (
        select(NewsAlert)
        .where(NewsAlert.user_id == current_user.id)
        .order_by(NewsAlert.created_at.desc())
    )
    result = await db.execute(query)
    alerts = result.scalars().all()

    return NewsAlertListResponse(
        alerts=[NewsAlertResponse.model_validate(a) for a in alerts],
        total=len(alerts),
    )


@router.post(
    "/alerts",
    response_model=NewsAlertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create news alert",
    description="Create a new news alert for keyword monitoring.",
)
async def create_news_alert(
    data: NewsAlertCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Create a new news alert.

    - **symbol**: Stock symbol (optional, None means all watchlist stocks)
    - **keywords**: List of keywords to monitor
    - **is_active**: Whether the alert is active
    """
    # Validate keywords
    if not data.keywords or len(data.keywords) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one keyword is required",
        )

    # Check alert limit (max 20 per user)
    count_query = select(func.count(NewsAlert.id)).where(
        NewsAlert.user_id == current_user.id
    )
    result = await db.execute(count_query)
    existing_count = result.scalar()

    if existing_count >= 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum number of news alerts (20) reached",
        )

    alert = NewsAlert(
        user_id=current_user.id,
        symbol=data.symbol.upper() if data.symbol else None,
        keywords=[k.lower().strip() for k in data.keywords],
        is_active=data.is_active,
    )

    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    logger.info(f"Created news alert {alert.id} for user {current_user.id}")

    return NewsAlertResponse.model_validate(alert)


@router.put(
    "/alerts/{alert_id}",
    response_model=NewsAlertResponse,
    summary="Update news alert",
    description="Update an existing news alert.",
)
async def update_news_alert(
    alert_id: UUID,
    data: NewsAlertUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Update a news alert.

    - **alert_id**: ID of the alert to update
    - **symbol**: New stock symbol (optional)
    - **keywords**: New keywords (optional)
    - **is_active**: New active status (optional)
    """
    # Get alert
    query = select(NewsAlert).where(
        NewsAlert.id == alert_id,
        NewsAlert.user_id == current_user.id,
    )
    result = await db.execute(query)
    alert = result.scalar_one_or_none()

    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News alert not found",
        )

    # Update fields
    if data.symbol is not None:
        alert.symbol = data.symbol.upper() if data.symbol else None
    if data.keywords is not None:
        if len(data.keywords) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one keyword is required",
            )
        alert.keywords = [k.lower().strip() for k in data.keywords]
    if data.is_active is not None:
        alert.is_active = data.is_active

    await db.commit()
    await db.refresh(alert)

    logger.info(f"Updated news alert {alert_id}")

    return NewsAlertResponse.model_validate(alert)


@router.delete(
    "/alerts/{alert_id}",
    response_model=MessageResponse,
    summary="Delete news alert",
    description="Delete a news alert.",
)
async def delete_news_alert(
    alert_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Delete a news alert.

    - **alert_id**: ID of the alert to delete
    """
    # Delete alert
    delete_query = delete(NewsAlert).where(
        NewsAlert.id == alert_id,
        NewsAlert.user_id == current_user.id,
    )
    result = await db.execute(delete_query)

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News alert not found",
        )

    await db.commit()

    logger.info(f"Deleted news alert {alert_id}")

    return MessageResponse(message="News alert deleted successfully")


@router.get(
    "/{symbol}",
    response_model=List[NewsResponse],
    summary="Get news for a stock",
    description="Get recent news articles for a specific stock symbol.",
)
async def get_stock_news(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(SYMBOL_NEWS_RATE_LIMIT),
):
    """
    Get news for a specific stock.

    - **symbol**: Stock symbol (e.g., AAPL, 0700.HK, 600519.SS)
    """
    symbol = symbol.strip().upper()

    # Auto-append exchange suffix for bare 6-digit A-share codes
    if re.match(r"^[0-9]{6}$", symbol):
        if symbol.startswith(("600", "601", "603", "605", "688")):
            symbol = f"{symbol}.SS"
        elif symbol.startswith(("000", "001", "002", "003", "300", "301")):
            symbol = f"{symbol}.SZ"

    # Auto-append .HK for bare 4-5 digit codes
    if re.match(r"^[0-9]{4,5}$", symbol):
        symbol = f"{symbol}.HK"

    # Normalize HK symbols: 01810.HK → 1810.HK (yfinance uses 4-digit codes)
    if symbol.endswith(".HK"):
        code = symbol[:-3]
        try:
            code = str(int(code)).zfill(4)
        except ValueError:
            pass
        symbol = f"{code}.HK"

    # Load user with settings
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(User).where(User.id == current_user.id).options(selectinload(User.settings))
    )
    user = result.scalar_one_or_none()

    news_service = await get_news_service()
    news = await news_service.get_news_by_symbol(symbol, user=user)

    return [NewsResponse(**n) for n in news]


@router.post(
    "/analyze",
    response_model=NewsAnalysisResponse,
    summary="AI analyze news article",
    description="Get AI analysis of a news article's impact on stock price.",
)
async def analyze_news(
    data: NewsAnalysisRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ANALYZE_RATE_LIMIT),
):
    """
    Get AI analysis for a news article.

    Accepts news content directly in the request body for analysis.

    Returns sentiment score, impact prediction, and key points.
    """
    # Check if OpenAI is configured
    if not settings.OPENAI_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI analysis is not available. OpenAI API key not configured.",
        )

    from app.agents.prompts.news_prompt import (
        build_news_analysis_prompt,
        get_news_analysis_system_prompt,
    )
    from openai import AsyncOpenAI

    try:
        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE,
        )

        # Build prompt from request data
        system_prompt = get_news_analysis_system_prompt()
        user_prompt = build_news_analysis_prompt(
            symbol=data.symbol,
            title=data.title,
            summary=data.summary or "",
            source=data.source or "unknown",
            published_at=data.published_at.isoformat() if data.published_at else datetime.now(timezone.utc).isoformat(),
            market=data.market or "US",
        )

        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1000,
            temperature=0.3,
        )

        content = response.choices[0].message.content

        # Parse JSON from response
        try:
            # Try to find JSON in the response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                analysis = json.loads(content[start:end])
            else:
                raise ValueError("No JSON found in response")
        except (json.JSONDecodeError, ValueError):
            # Return default analysis if parsing fails
            analysis = {
                "sentiment_score": 0.0,
                "sentiment_label": "neutral",
                "impact_prediction": {
                    "direction": "neutral",
                    "magnitude": "low",
                },
                "key_points": ["Analysis parsing failed"],
                "summary": content[:500] if content else "Analysis unavailable",
            }

        return NewsAnalysisResponse(
            news_id=data.news_id or "generated",
            sentiment_score=float(analysis.get("sentiment_score", 0)),
            sentiment_label=analysis.get("sentiment_label", "neutral"),
            impact_prediction=json.dumps(analysis.get("impact_prediction", {})),
            key_points=analysis.get("key_points", []),
            summary=analysis.get("summary", ""),
            analyzed_at=datetime.now(timezone.utc),
        )

    except Exception as e:
        logger.exception(f"News analysis error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to analyze news article",
        )
