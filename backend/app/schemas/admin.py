"""Admin API Pydantic schemas."""

from datetime import datetime
from typing import Dict, Optional, List

from pydantic import Field, EmailStr

from app.models.user import AccountStatus, UserRole
from app.schemas.base import CamelModel


# ============== User Management Schemas ==============


class UserAdminResponse(CamelModel):
    """Admin view of a user with all details."""

    id: int
    email: EmailStr
    role: UserRole
    account_status: AccountStatus
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

    # Anthropic settings
    anthropic_api_key_set: bool  # Only returns whether set, not actual value
    anthropic_base_url: Optional[str] = None

    # AI processing models
    embedding_model: str = "text-embedding-3-small"
    news_filter_model: str = "gpt-4o-mini"
    news_retention_days: int = 30

    # External API keys (only show if set)
    finnhub_api_key_set: bool
    polygon_api_key_set: bool
    tavily_api_key_set: bool

    # User permission settings
    allow_user_custom_api_keys: bool = False

    # Registration approval settings
    require_registration_approval: bool = False

    # OpenAI Compatible / Local Model Configuration
    local_llm_base_url: Optional[str] = None
    analysis_model: str = "gpt-4o-mini"
    synthesis_model: str = "gpt-4o"
    use_local_models: bool = False

    # Clarification Settings
    max_clarification_rounds: int = 2
    clarification_confidence_threshold: float = 0.6

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

    # Anthropic settings
    anthropic_api_key: Optional[str] = Field(None, max_length=500)
    anthropic_base_url: Optional[str] = Field(None, max_length=500)

    # AI processing models
    embedding_model: Optional[str] = Field(None, max_length=100)
    news_filter_model: Optional[str] = Field(None, max_length=100)
    news_retention_days: Optional[int] = Field(None, ge=7, le=365)

    # External API keys
    finnhub_api_key: Optional[str] = Field(None, max_length=500)
    polygon_api_key: Optional[str] = Field(None, max_length=500)
    tavily_api_key: Optional[str] = Field(None, max_length=500)

    # User permission settings
    allow_user_custom_api_keys: Optional[bool] = None

    # Registration approval settings
    require_registration_approval: Optional[bool] = None

    # OpenAI Compatible / Local Model Configuration
    local_llm_base_url: Optional[str] = Field(None, max_length=500)
    analysis_model: Optional[str] = Field(None, max_length=100)
    synthesis_model: Optional[str] = Field(None, max_length=100)
    use_local_models: Optional[bool] = None

    # Clarification Settings
    max_clarification_rounds: Optional[int] = Field(None, ge=0, le=5)
    clarification_confidence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)


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


# ============== System Config Schemas (for frontend compatibility) ==============


class LlmConfig(CamelModel):
    """LLM configuration settings."""

    api_key: Optional[str] = None  # Masked as "***" if set
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    max_tokens: Optional[int] = None  # None means use model default
    temperature: Optional[float] = None  # None means use model default
    anthropic_api_key: Optional[str] = None  # Masked as "***" if set
    anthropic_base_url: Optional[str] = None


class LangGraphConfig(CamelModel):
    """LangGraph layered architecture configuration."""

    # OpenAI Compatible / Local Model Configuration
    local_llm_base_url: Optional[str] = None
    analysis_model: str = "gpt-4o-mini"
    synthesis_model: str = "gpt-4o"
    use_local_models: bool = False

    # Clarification Settings
    max_clarification_rounds: int = 2
    clarification_confidence_threshold: float = 0.6


class NewsConfig(CamelModel):
    """News processing configuration."""

    default_source: str = "trafilatura"
    retention_days: int = 30
    embedding_model: str = "text-embedding-3-small"
    filter_model: str = "gpt-4o-mini"
    auto_fetch_enabled: bool = True
    finnhub_api_key: Optional[str] = None  # Finnhub API key for news data
    tavily_api_key: Optional[str] = None  # Tavily API key for content extraction
    enable_mcp_extraction: bool = False  # Whether to use LLM+MCP for content extraction


class FeaturesConfig(CamelModel):
    """Feature flags configuration."""

    allow_user_api_keys: bool = True
    allow_user_custom_models: bool = False
    enable_news_analysis: bool = True
    enable_stock_analysis: bool = True
    require_registration_approval: bool = False
    enable_mcp_extraction: bool = False


class ModelAssignment(CamelModel):
    """A model assignment (provider + model name)."""

    provider_id: Optional[str] = None
    model: str = ""


class ModelAssignmentsConfig(CamelModel):
    """All model assignments."""

    chat: ModelAssignment = ModelAssignment(model="gpt-4o-mini")
    analysis: ModelAssignment = ModelAssignment(model="gpt-4o-mini")
    synthesis: ModelAssignment = ModelAssignment(model="gpt-4o")
    embedding: ModelAssignment = ModelAssignment(model="text-embedding-3-small")
    news_filter: ModelAssignment = ModelAssignment(model="gpt-4o-mini")
    content_extraction: ModelAssignment = ModelAssignment(model="gpt-4o-mini")


class Phase2ModelAssignment(CamelModel):
    """Phase 2 model assignment (provider + model)."""

    provider_id: Optional[str] = None
    model: str = ""


class Phase2Config(CamelModel):
    """Phase 2 multi-agent pipeline configuration."""

    enable_llm_pipeline: bool = False
    discard_threshold: int = 105
    layer1_scoring: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o-mini")
    layer15_cleaning: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o")
    layer2_scoring: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o-mini")
    layer2_analysis: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o")
    # L3 per-agent model overrides (null = use layer2Analysis fallback)
    news_entity: Optional[Phase2ModelAssignment] = None
    news_sentiment: Optional[Phase2ModelAssignment] = None
    news_summary: Optional[Phase2ModelAssignment] = None
    news_impact: Optional[Phase2ModelAssignment] = None
    news_report: Optional[Phase2ModelAssignment] = None
    cache_enabled: bool = True
    cache_ttl_minutes: int = 60


class DiscussionConfig(CamelModel):
    """Discussion group configuration."""

    enabled: Optional[bool] = None
    max_rounds: Optional[int] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None


class PredictionConfig(CamelModel):
    """ML prediction configuration."""

    enabled: Optional[bool] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    auto_retrain_enabled: Optional[bool] = None
    auto_retrain_interval_days: Optional[int] = None
    auto_tune_enabled: Optional[bool] = None
    auto_tune_interval_days: Optional[int] = None
    auto_tune_max_iterations: Optional[int] = None


class SystemConfigResponse(CamelModel):
    """System configuration response matching frontend SystemConfig type."""

    llm: LlmConfig
    news: NewsConfig
    features: FeaturesConfig
    langgraph: LangGraphConfig
    model_assignments: Optional[ModelAssignmentsConfig] = None
    phase2: Optional[Phase2Config] = None
    discussion: Optional[DiscussionConfig] = None
    prediction: Optional[PredictionConfig] = None


class UpdateSystemConfigRequest(CamelModel):
    """Request to update system configuration."""

    llm: Optional[LlmConfig] = None
    news: Optional[NewsConfig] = None
    features: Optional[FeaturesConfig] = None
    langgraph: Optional[LangGraphConfig] = None
    model_assignments: Optional[ModelAssignmentsConfig] = None
    phase2: Optional[Phase2Config] = None
    discussion: Optional[DiscussionConfig] = None
    prediction: Optional[PredictionConfig] = None


# ============== System Monitor Stats Schemas ==============


class UserStats(CamelModel):
    """User statistics for system monitor."""

    total: int = 0
    active: int = 0
    new_today: int = 0
    new_this_week: int = 0


class ActivityStats(CamelModel):
    """Activity statistics for system monitor."""

    today_logins: int = 0
    active_conversations: int = 0
    reports_generated: int = 0
    api_calls_today: int = 0


class SystemResourceStats(CamelModel):
    """System resource statistics."""

    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    disk_usage: float = 0.0
    uptime: int = 0


class ApiStats(CamelModel):
    """API usage statistics."""

    total_requests: int = 0
    average_latency: float = 0.0
    error_rate: float = 0.0
    rate_limit_hits: int = 0


class SystemMonitorStatsResponse(CamelModel):
    """System monitor statistics matching frontend SystemMonitorStats type."""

    users: UserStats
    activity: ActivityStats
    system: SystemResourceStats
    api: ApiStats


# ============== User Approval Schemas ==============


class ApproveUserRequest(CamelModel):
    """Request to approve a pending user."""

    send_notification: bool = False  # Optional: send email notification to user


class RejectUserRequest(CamelModel):
    """Request to reject a pending user."""

    reason: Optional[str] = Field(None, max_length=500)
    delete_account: bool = False  # If True, hard delete. If False, soft delete (is_active=False)


class CreateUserRequest(CamelModel):
    """Request to create a new user by admin."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = UserRole.USER


# ============== LLM Cost Tracking Schemas ==============


class CostBreakdownItem(CamelModel):
    """Single row in a purpose/model breakdown."""

    group: str
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    calls: int


class CostSummaryResponse(CamelModel):
    """Aggregated cost summary."""

    period_days: int
    total_cost_usd: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cached_tokens: int
    total_tokens: int
    total_calls: int
    by_purpose: List[CostBreakdownItem]
    by_model: List[CostBreakdownItem]


class DailyCostItem(CamelModel):
    """Single day in the daily cost chart."""

    date: str
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    calls: int


class CategoryBreakdownItem(CamelModel):
    """Single row in the purpose+sub-group breakdown."""

    purpose: str
    sub_group: str = ""
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    calls: int


class ModelPricingCreate(CamelModel):
    """Create/update model pricing."""

    model: str
    input_price: float
    cached_input_price: Optional[float] = None
    output_price: float
    effective_from: Optional[str] = None  # ISO date string, defaults to today


class ModelPricingResponse(CamelModel):
    """Model pricing row."""

    id: str
    model: str
    input_price: float
    cached_input_price: Optional[float] = None
    output_price: float
    effective_from: str
    created_at: str
