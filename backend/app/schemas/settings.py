"""Settings Pydantic schemas."""

from typing import Optional
from pydantic import BaseModel, Field


class NotificationSettings(BaseModel):
    """Notification preferences."""
    price_alerts: bool = True
    news_alerts: bool = True
    report_notifications: bool = True
    email_notifications: bool = False


class ApiKeySettings(BaseModel):
    """API key and AI configuration."""
    finnhub_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = "https://api.openai.com/v1"
    openai_model: Optional[str] = None
    openai_max_tokens: Optional[int] = None
    openai_temperature: Optional[float] = None
    openai_system_prompt: Optional[str] = None
    anthropic_api_key: Optional[str] = None


class NewsSourceSettings(BaseModel):
    """News source preferences."""
    source: str = "yfinance"  # Options: "yfinance", "finnhub", "auto"


class NewsContentSettings(BaseModel):
    """News full content settings."""
    source: str = "trafilatura"  # Options: "trafilatura", "polygon", "tavily", "playwright"
    polygon_api_key: Optional[str] = None
    retention_days: int = 30


class UserSettingsResponse(BaseModel):
    """User settings response."""
    notifications: NotificationSettings
    api_keys: ApiKeySettings
    news_source: Optional[NewsSourceSettings] = None  # Only visible to admins
    news_content: Optional[NewsContentSettings] = None  # Only visible to admins
    can_customize_api: bool = False  # Whether user has permission to customize API settings
    is_admin: bool = False  # Whether user is an administrator

    class Config:
        from_attributes = True


class UpdateNotificationSettings(BaseModel):
    """Update notification preferences request."""
    price_alerts: Optional[bool] = None
    news_alerts: Optional[bool] = None
    report_notifications: Optional[bool] = None
    email_notifications: Optional[bool] = None


class UpdateApiKeySettings(BaseModel):
    """Update API key and AI settings request."""
    finnhub_api_key: Optional[str] = Field(None, max_length=1000)
    openai_api_key: Optional[str] = Field(None, max_length=1000)
    openai_base_url: Optional[str] = Field(None, max_length=500)
    openai_model: Optional[str] = Field(None, max_length=100)
    openai_max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    openai_temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    openai_system_prompt: Optional[str] = Field(None, max_length=10000)
    anthropic_api_key: Optional[str] = Field(None, max_length=1000)


class UpdateNewsSourceSettings(BaseModel):
    """Update news source settings request."""
    source: Optional[str] = Field(None, pattern="^(yfinance|finnhub|auto)$")


class UpdateNewsContentSettings(BaseModel):
    """Update news content settings request."""
    source: Optional[str] = Field(None, pattern="^(trafilatura|polygon|tavily|playwright)$")
    polygon_api_key: Optional[str] = Field(None, max_length=1000)
    retention_days: Optional[int] = Field(None, ge=7, le=365)


class UpdateSettingsRequest(BaseModel):
    """Update user settings request."""
    notifications: Optional[UpdateNotificationSettings] = None
    api_keys: Optional[UpdateApiKeySettings] = None
    news_source: Optional[UpdateNewsSourceSettings] = None
    news_content: Optional[UpdateNewsContentSettings] = None
