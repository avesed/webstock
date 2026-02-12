"""Pydantic schemas for RSS Feed management."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from app.schemas.base import CamelModel


class RssFeedCreate(CamelModel):
    """Request to create a new RSS feed."""

    name: str = Field(..., min_length=1, max_length=200)
    rsshub_route: str = Field(..., max_length=500)
    description: Optional[str] = None
    category: str = Field(default="media", pattern=r"^(media|exchange|social)$")
    symbol: Optional[str] = Field(None, max_length=20)
    market: str = Field(default="US", max_length=10)
    poll_interval_minutes: int = Field(default=15, ge=5, le=1440)
    fulltext_mode: bool = False

    @field_validator("rsshub_route")
    @classmethod
    def validate_route(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("RSSHub route must start with '/'")
        return v


class RssFeedUpdate(CamelModel):
    """Request to update an RSS feed."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    rsshub_route: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    category: Optional[str] = Field(None, pattern=r"^(media|exchange|social)$")
    symbol: Optional[str] = Field(None, max_length=20)
    market: Optional[str] = Field(None, max_length=10)
    poll_interval_minutes: Optional[int] = Field(None, ge=5, le=1440)
    fulltext_mode: Optional[bool] = None
    is_enabled: Optional[bool] = None

    @field_validator("rsshub_route")
    @classmethod
    def validate_route(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("/"):
            raise ValueError("RSSHub route must start with '/'")
        return v


class RssFeedResponse(CamelModel):
    """Response for a single RSS feed."""

    id: str
    name: str
    rsshub_route: str
    description: Optional[str] = None
    category: str = "media"
    symbol: Optional[str] = None
    market: str = "US"
    poll_interval_minutes: int = 15
    fulltext_mode: bool = False
    is_enabled: bool = True
    last_polled_at: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    article_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RssFeedListResponse(CamelModel):
    """Response for listing RSS feeds."""

    feeds: List[RssFeedResponse]
    total: int


class RssFeedTestArticle(CamelModel):
    """A single article from a feed test."""

    title: str
    url: str
    summary: Optional[str] = None
    published_at: Optional[str] = None
    source: Optional[str] = None


class RssFeedTestRequest(CamelModel):
    """Request to test an RSSHub route."""

    rsshub_route: str = Field(..., max_length=500)
    fulltext_mode: bool = False

    @field_validator("rsshub_route")
    @classmethod
    def validate_route(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("RSSHub route must start with '/'")
        return v


class RssFeedTestResponse(CamelModel):
    """Response from testing an RSSHub route."""

    route: str
    article_count: int = 0
    articles: List[RssFeedTestArticle] = Field(default_factory=list)
    error: Optional[str] = None


class RssFeedStatsItem(CamelModel):
    """Per-feed statistics."""

    feed_id: str
    feed_name: str
    rsshub_route: str
    category: str
    is_enabled: bool
    article_count: int = 0
    last_polled_at: Optional[datetime] = None
    consecutive_errors: int = 0
    recent_articles: int = 0  # 最近7天新增文章数


class RssFeedStatsResponse(CamelModel):
    """Response for feed statistics."""

    total_feeds: int = 0
    enabled_feeds: int = 0
    total_articles: int = 0
    feeds: List[RssFeedStatsItem] = Field(default_factory=list)
