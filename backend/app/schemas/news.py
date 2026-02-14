"""Pydantic schemas for news operations."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import CamelModel


class NewsBase(CamelModel):
    """Base schema for news article."""

    symbol: str = Field(..., min_length=1, max_length=20)
    title: str = Field(..., min_length=1, max_length=500)
    summary: Optional[str] = Field(None, max_length=5000)
    source: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., max_length=1024)
    published_at: datetime
    market: str = Field(..., min_length=1, max_length=10)


class NewsCreate(NewsBase):
    """Schema for creating news (internal use)."""

    sentiment_score: Optional[float] = Field(None, ge=-1.0, le=1.0)
    ai_analysis: Optional[str] = None


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


# News Alert Schemas
class NewsAlertBase(CamelModel):
    """Base schema for news alert."""

    symbol: Optional[str] = Field(None, max_length=20)
    keywords: List[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True


class NewsAlertCreate(NewsAlertBase):
    """Schema for creating a news alert."""

    pass


class NewsAlertUpdate(BaseModel):
    """Schema for updating a news alert."""

    symbol: Optional[str] = Field(None, max_length=20)
    keywords: Optional[List[str]] = Field(None, max_length=20)
    is_active: Optional[bool] = None


class NewsAlertResponse(NewsAlertBase):
    """Response schema for news alert."""

    id: UUID
    user_id: int
    created_at: datetime


class NewsAlertListResponse(BaseModel):
    """Response schema for list of news alerts."""

    alerts: List[NewsAlertResponse]
    total: int


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


class NewsFullContentResponse(CamelModel):
    """Response schema for news full content."""

    id: str
    title: str
    full_content: Optional[str] = None
    content_status: str
    language: Optional[str] = None
    authors: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    word_count: int = 0
    is_fetching: bool = False
    fetched_at: Optional[datetime] = None
    error: Optional[str] = None
    investment_summary: Optional[str] = None  # 1句话概况
    detailed_summary: Optional[str] = None    # 完整细节总结
    ai_analysis: Optional[str] = None         # Markdown分析报告


class BatchFetchRequest(CamelModel):
    """Request schema for batch content fetching."""

    news_ids: List[str] = Field(..., min_length=1, max_length=50)


class BatchFetchResponse(CamelModel):
    """Response schema for batch content fetching."""

    queued: int
    message: str


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
