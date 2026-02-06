"""Admin API Pydantic schemas."""

from datetime import datetime
from typing import Optional, List

from pydantic import Field, EmailStr

from app.models.user import UserRole
from app.schemas.base import CamelModel


# ============== User Management Schemas ==============


class UserAdminResponse(CamelModel):
    """Admin view of a user with all details."""

    id: int
    email: EmailStr
    role: UserRole
    is_active: bool
    is_locked: bool
    failed_login_attempts: int
    locked_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    can_use_custom_api_key: bool = False


class UserListResponse(CamelModel):
    """Paginated list of users for admin."""

    users: List[UserAdminResponse]
    total: int


class UpdateUserRequest(CamelModel):
    """Request to update user attributes by admin."""

    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    is_locked: Optional[bool] = None
    can_use_custom_api_key: Optional[bool] = None


class ResetPasswordRequest(CamelModel):
    """Request to reset a user's password by admin."""

    new_password: str = Field(..., min_length=8, max_length=128)


# ============== System Settings Schemas ==============


class SystemSettingsResponse(CamelModel):
    """System settings response for admin (masks sensitive values)."""

    # OpenAI settings
    openai_api_key_set: bool  # Only returns whether set, not actual value
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens: Optional[int] = None
    openai_temperature: Optional[float] = None

    # AI processing models
    embedding_model: str = "text-embedding-3-small"
    news_filter_model: str = "gpt-4o-mini"
    news_retention_days: int = 30

    # External API keys (only show if set)
    finnhub_api_key_set: bool
    polygon_api_key_set: bool

    # User permission settings
    allow_user_custom_api_keys: bool = False

    # Audit fields
    updated_at: datetime
    updated_by: Optional[int] = None


class UpdateSystemSettingsRequest(CamelModel):
    """Request to update system settings by admin."""

    # OpenAI settings
    openai_api_key: Optional[str] = Field(None, max_length=500)
    openai_base_url: Optional[str] = Field(None, max_length=500)
    openai_model: Optional[str] = Field(None, max_length=100)
    openai_max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    openai_temperature: Optional[float] = Field(None, ge=0.0, le=2.0)

    # AI processing models
    embedding_model: Optional[str] = Field(None, max_length=100)
    news_filter_model: Optional[str] = Field(None, max_length=100)
    news_retention_days: Optional[int] = Field(None, ge=7, le=365)

    # External API keys
    finnhub_api_key: Optional[str] = Field(None, max_length=500)
    polygon_api_key: Optional[str] = Field(None, max_length=500)

    # User permission settings
    allow_user_custom_api_keys: Optional[bool] = None


# ============== System Statistics Schemas ==============


class ApiCallStats(CamelModel):
    """API call statistics for monitoring."""

    chat_requests_today: int = 0
    analysis_requests_today: int = 0
    total_tokens_today: int = 0


class SystemStatsResponse(CamelModel):
    """System-wide statistics for admin dashboard."""

    total_users: int
    total_admins: int
    active_users: int
    logins_24h: int
    api_stats: ApiCallStats
