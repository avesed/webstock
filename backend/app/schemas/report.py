"""Pydantic schemas for report schedules and reports."""

from datetime import datetime, time
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import CamelModel


class ReportFrequency(str, Enum):
    """Report frequency enumeration."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReportFormat(str, Enum):
    """Report format enumeration."""

    JSON = "json"
    HTML = "html"


class ReportStatus(str, Enum):
    """Report status enumeration."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


# ============== Report Schedule Schemas ==============


class ReportScheduleBase(CamelModel):
    """Base schema for report schedule."""

    name: str = Field(..., min_length=1, max_length=100)
    frequency: ReportFrequency
    time_of_day: time = Field(..., description="Time of day to generate report (UTC)")
    day_of_week: Optional[int] = Field(
        None,
        ge=0,
        le=6,
        description="Day of week for weekly schedules (0=Monday, 6=Sunday)",
    )
    day_of_month: Optional[int] = Field(
        None,
        ge=1,
        le=31,
        description="Day of month for monthly schedules (1-31)",
    )
    symbols: List[str] = Field(
        default_factory=list,
        description="Stock symbols to include (empty for all watchlist)",
    )
    include_portfolio: bool = Field(
        False,
        description="Include portfolio summary in report",
    )
    include_news: bool = Field(
        True,
        description="Include news summary in report",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: List[str]) -> List[str]:
        """Normalize symbols to uppercase."""
        return [s.strip().upper() for s in v if s.strip()]

    @model_validator(mode="after")
    def validate_schedule_params(self) -> "ReportScheduleBase":
        """Validate schedule parameters based on frequency."""
        if self.frequency == ReportFrequency.WEEKLY:
            if self.day_of_week is None:
                raise ValueError(
                    "day_of_week is required for weekly schedules"
                )
        elif self.frequency == ReportFrequency.MONTHLY:
            if self.day_of_month is None:
                raise ValueError(
                    "day_of_month is required for monthly schedules"
                )
        return self


class ReportScheduleCreate(ReportScheduleBase):
    """Schema for creating a report schedule."""

    pass


class ReportScheduleUpdate(BaseModel):
    """Schema for updating a report schedule."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    frequency: Optional[ReportFrequency] = None
    time_of_day: Optional[time] = None
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    symbols: Optional[List[str]] = None
    include_portfolio: Optional[bool] = None
    include_news: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Normalize symbols to uppercase."""
        if v is None:
            return None
        return [s.strip().upper() for s in v if s.strip()]


class ReportScheduleResponse(CamelModel):
    """Response schema for report schedule."""

    id: str
    user_id: int
    name: str
    frequency: str
    time_of_day: time
    day_of_week: Optional[int]
    day_of_month: Optional[int]
    symbols: List[str]
    include_portfolio: bool
    include_news: bool
    is_active: bool
    last_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ReportScheduleListResponse(BaseModel):
    """Response schema for list of report schedules."""

    schedules: List[ReportScheduleResponse]
    total: int


# ============== Report Schemas ==============


class ReportResponse(CamelModel):
    """Response schema for report."""

    id: str
    schedule_id: Optional[str]
    user_id: int
    title: str
    content: Optional[Dict[str, Any]]
    format: str
    status: str
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]


class ReportListResponse(BaseModel):
    """Response schema for list of reports."""

    reports: List[ReportResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class ReportDownloadResponse(BaseModel):
    """Response schema for report download."""

    content: str
    content_type: str
    filename: str


# ============== Report Content Schemas ==============


class StockPerformanceSummary(BaseModel):
    """Stock performance summary for report."""

    symbol: str
    name: Optional[str] = None
    current_price: Optional[float] = None
    day_change: Optional[float] = None
    day_change_percent: Optional[float] = None
    week_change_percent: Optional[float] = None
    month_change_percent: Optional[float] = None
    volume: Optional[int] = None
    market_cap: Optional[float] = None


class TechnicalSummary(BaseModel):
    """Technical analysis summary for report."""

    symbol: str
    trend: Optional[str] = None  # bullish, bearish, neutral
    support_level: Optional[float] = None
    resistance_level: Optional[float] = None
    rsi: Optional[float] = None
    macd_signal: Optional[str] = None  # buy, sell, hold
    moving_average_signal: Optional[str] = None


class NewsSummary(BaseModel):
    """News summary for report."""

    symbol: str
    total_articles: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    top_headlines: List[str] = Field(default_factory=list)


class PortfolioSummaryForReport(BaseModel):
    """Portfolio summary for report."""

    total_value: Optional[float] = None
    total_cost: Optional[float] = None
    total_profit_loss: Optional[float] = None
    total_profit_loss_percent: Optional[float] = None
    day_change: Optional[float] = None
    day_change_percent: Optional[float] = None
    holdings_count: int = 0
    top_gainers: List[str] = Field(default_factory=list)
    top_losers: List[str] = Field(default_factory=list)


class ReportContent(BaseModel):
    """Full report content structure."""

    generated_at: datetime
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    symbols: List[str]
    stock_performance: List[StockPerformanceSummary] = Field(default_factory=list)
    technical_analysis: List[TechnicalSummary] = Field(default_factory=list)
    news_summary: List[NewsSummary] = Field(default_factory=list)
    portfolio_summary: Optional[PortfolioSummaryForReport] = None
    ai_summary: Optional[str] = None


# ============== Message Response ==============


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
