"""Pydantic schemas for news operations."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.base import CamelModel


class NewsResponse(CamelModel):
    """Response schema for news article."""

    # Use str for id since news_service returns hash-based string IDs
    id: str
    symbol: str
    title: str
    summary: Optional[str] = None
    source: str
    url: str
    published_at: datetime
    market: str
    sentiment_score: Optional[float] = None
    sentiment_tag: Optional[str] = None
    investment_summary: Optional[str] = None  # 1句话概况，卡片预览
    detailed_summary: Optional[str] = None    # 完整细节总结，"阅读更多"展示
    ai_analysis: Optional[str] = None         # Markdown分析报告，"分析"展示
    related_entities: Optional[List[dict]] = None
    industry_tags: Optional[List[str]] = None
    event_tags: Optional[List[str]] = None
    content_score: Optional[int] = None
    processing_path: Optional[str] = None
    score_details: Optional[dict] = None
    content_status: Optional[str] = None
    filter_status: Optional[str] = None
    # created_at is optional since external news may not have it
    created_at: Optional[datetime] = None


class NewsAnalysisRequest(BaseModel):
    """Request schema for AI news analysis."""

    news_id: Optional[str] = Field(None, description="Optional news ID for reference")
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock symbol")
    title: str = Field(..., min_length=1, max_length=500, description="News headline")
    summary: Optional[str] = Field(None, max_length=5000, description="News content/summary")
    source: Optional[str] = Field(None, max_length=100, description="News source")
    published_at: Optional[datetime] = Field(None, description="Publication timestamp")
    market: Optional[str] = Field(None, max_length=10, description="Market (US, HK, SH, SZ)")
    language: Optional[str] = Field(None, max_length=10, description="Language code (en, zh)")


class NewsAnalysisResponse(BaseModel):
    """Response schema for AI news analysis."""

    news_id: str
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    sentiment_label: str  # "positive", "negative", "neutral"
    impact_prediction: str
    key_points: List[str]
    summary: str
    analyzed_at: datetime


class NewsFeedResponse(CamelModel):
    """Paginated response schema for news feed."""

    news: List[NewsResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class TrendingNewsResponse(BaseModel):
    """Response schema for trending news."""

    news: List[NewsResponse]
    market: Optional[str] = None
    fetched_at: datetime


class SentimentTimelineItemResponse(CamelModel):
    """Single day sentiment aggregation data."""

    date: str  # YYYY-MM-DD
    bullish: int
    bearish: int
    neutral: int
    total: int
    score: float  # (bullish - bearish) / total, range -1.0 ~ 1.0


class SentimentTimelineResponse(CamelModel):
    """Sentiment trend timeline."""

    symbol: str
    days: int
    data: List[SentimentTimelineItemResponse]
