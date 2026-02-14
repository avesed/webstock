"""Admin API Pydantic schemas."""

from datetime import datetime
from typing import Optional, List

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
    enable_llm_pipeline: bool = False
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

    enabled: bool = False
    score_threshold: int = 50
    discard_threshold: int = 105
    full_analysis_threshold: int = 195
    layer1_scoring: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o-mini")
    layer15_cleaning: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o")
    layer2_scoring: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o-mini")
    layer2_analysis: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o")
    layer2_lightweight: Phase2ModelAssignment = Phase2ModelAssignment(model="gpt-4o-mini")
    high_value_sources: list[str] = ["reuters", "bloomberg", "sec", "company_announcement"]
    high_value_pct: float = 0.20
    cache_enabled: bool = True
    cache_ttl_minutes: int = 60


class SystemConfigResponse(CamelModel):
    """System configuration response matching frontend SystemConfig type."""

    llm: LlmConfig
    news: NewsConfig
    features: FeaturesConfig
    langgraph: LangGraphConfig
    model_assignments: Optional[ModelAssignmentsConfig] = None
    phase2: Optional[Phase2Config] = None


class UpdateSystemConfigRequest(CamelModel):
    """Request to update system configuration."""

    llm: Optional[LlmConfig] = None
    news: Optional[NewsConfig] = None
    features: Optional[FeaturesConfig] = None
    langgraph: Optional[LangGraphConfig] = None
    model_assignments: Optional[ModelAssignmentsConfig] = None
    phase2: Optional[Phase2Config] = None


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


# ============== Filter Stats Schemas ==============


class TokenUsageResponse(CamelModel):
    """Token usage statistics with cost estimate."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class InitialFilterCountsResponse(CamelModel):
    """Counts for initial filter results."""

    useful: int
    uncertain: int
    skip: int
    total: int


class DeepFilterCountsResponse(CamelModel):
    """Counts for deep filter results."""

    keep: int
    delete: int
    total: int


class ErrorCountsResponse(CamelModel):
    """Counts for errors."""

    filter_error: int
    embedding_error: int


class EmbeddingCountsResponse(CamelModel):
    """Counts for embedding operations."""

    success: int
    error: int


class Layer1ScoringCountsResponse(CamelModel):
    """Counts for Layer 1 three-agent scoring results."""

    discard: int = 0
    lightweight: int = 0
    full_analysis: int = 0
    critical_event: int = 0
    total: int = 0


class FilterCountsResponse(CamelModel):
    """All filter counts grouped."""

    initial_filter: InitialFilterCountsResponse
    deep_filter: DeepFilterCountsResponse
    errors: ErrorCountsResponse
    embedding: EmbeddingCountsResponse
    layer1_scoring: Optional[Layer1ScoringCountsResponse] = None


class FilterRatesResponse(CamelModel):
    """Filter effectiveness rates as percentages."""

    initial_skip_rate: float
    initial_pass_rate: float
    deep_keep_rate: float
    deep_delete_rate: float
    filter_error_rate: float
    embedding_error_rate: float
    layer1_discard_rate: float = 0
    layer1_pass_rate: float = 0


class FilterTokensResponse(CamelModel):
    """Token usage summary for all filter stages."""

    initial_filter: TokenUsageResponse
    deep_filter: TokenUsageResponse
    total: TokenUsageResponse
    days: int
    layer1_macro: Optional[TokenUsageResponse] = None
    layer1_market: Optional[TokenUsageResponse] = None
    layer1_signal: Optional[TokenUsageResponse] = None


class FilterAlertResponse(CamelModel):
    """Alert for threshold violation."""

    stat: str
    rate: str
    level: str  # "warning" or "critical"
    message: str


class FilterStatsResponse(CamelModel):
    """Comprehensive filter statistics response."""

    period_days: int
    counts: FilterCountsResponse
    rates: FilterRatesResponse
    tokens: FilterTokensResponse
    alerts: List[FilterAlertResponse]


class DailyFilterStatsItemResponse(CamelModel):
    """Single day filter statistics."""

    date: str
    initial_useful: int
    initial_uncertain: int
    initial_skip: int
    fine_keep: int
    fine_delete: int
    filter_error: int
    embedding_success: int
    embedding_error: int
    initial_input_tokens: int
    initial_output_tokens: int
    deep_input_tokens: int
    deep_output_tokens: int


class DailyFilterStatsResponse(CamelModel):
    """Daily filter statistics list response."""

    days: int
    data: List[DailyFilterStatsItemResponse]


# ============== Pipeline Tracing Schemas ==============


class PipelineEventResponse(CamelModel):
    """Single pipeline event."""

    id: str
    news_id: str
    layer: str
    node: str
    status: str
    duration_ms: Optional[float] = None
    metadata: Optional[dict] = None
    error: Optional[str] = None
    created_at: datetime


class ArticleTimelineResponse(CamelModel):
    """Full pipeline timeline for a single article."""

    news_id: str
    title: Optional[str] = None
    symbol: Optional[str] = None
    events: List[PipelineEventResponse]
    total_duration_ms: Optional[float] = None


class NodeStatsResponse(CamelModel):
    """Aggregate stats for a single pipeline node."""

    layer: str
    node: str
    count: int
    success_count: int
    error_count: int
    avg_ms: Optional[float] = None
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[float] = None


class PipelineStatsResponse(CamelModel):
    """Aggregate pipeline statistics."""

    period_days: int
    nodes: List[NodeStatsResponse]


class PipelineEventSearchResponse(CamelModel):
    """Paginated pipeline event search results."""

    events: List[PipelineEventResponse]
    total: int


# ============== Source Stats Schemas ==============


class SourceStatsItemResponse(CamelModel):
    """Per-source article quality statistics."""

    source: str
    total: int
    initial_useful: int = 0
    initial_uncertain: int = 0
    fine_keep: int = 0
    fine_delete: int = 0
    embedded: int = 0
    fetch_failed: int = 0
    avg_entity_count: Optional[float] = None
    sentiment_distribution: Optional[dict] = None
    keep_rate: Optional[float] = None   # embedded / total (end-to-end retention)
    fetch_rate: Optional[float] = None  # (fine_keep + fine_delete) / total (Layer 1.5 throughput)


class SourceStatsResponse(CamelModel):
    """Source-level aggregate statistics."""

    period_days: int
    sources: List[SourceStatsItemResponse]
    total_sources: int


# ============== Layer 1.5 Stats Schemas ==============


class Layer15FetchStats(CamelModel):
    """Layer 1.5 content fetch statistics."""

    total: int = 0
    success: int = 0
    errors: int = 0
    avg_ms: Optional[float] = None
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    avg_images_found: float = 0
    avg_images_downloaded: float = 0
    articles_with_images: int = 0


class Layer15ProviderDistribution(CamelModel):
    """Provider usage distribution for content fetching."""

    provider: str
    count: int = 0


class Layer15CleaningStats(CamelModel):
    """Layer 1.5 content cleaning statistics."""

    total: int = 0
    success: int = 0
    errors: int = 0
    avg_ms: Optional[float] = None
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    avg_retention_rate: Optional[float] = None
    articles_with_visual_data: int = 0
    avg_image_count: float = 0
    avg_insights_length: float = 0


class Layer15StatsResponse(CamelModel):
    """Combined Layer 1.5 statistics response."""

    period_days: int
    fetch: Layer15FetchStats = Layer15FetchStats()
    provider_distribution: List[Layer15ProviderDistribution] = []
    cleaning: Layer15CleaningStats = Layer15CleaningStats()


# ============== News Pipeline Stats Schemas ==============


class NewsPipelineRoutingStats(CamelModel):
    """News pipeline routing decision counts from Redis."""

    total: int = 0
    full_analysis: int = 0
    lightweight: int = 0
    critical_events: int = 0
    scoring_errors: int = 0


class NewsPipelineTokenStage(CamelModel):
    """Token usage for a single news pipeline stage."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class NewsPipelineTokenStats(CamelModel):
    """Token breakdown by news pipeline processing stage."""

    scoring: NewsPipelineTokenStage = NewsPipelineTokenStage()
    multi_agent: NewsPipelineTokenStage = NewsPipelineTokenStage()
    lightweight: NewsPipelineTokenStage = NewsPipelineTokenStage()
    total: NewsPipelineTokenStage = NewsPipelineTokenStage()


class ScoreDistributionBucket(CamelModel):
    """Score distribution bucket for pipeline scoring."""

    bucket: str
    count: int
    full_analysis: int = 0
    lightweight: int = 0
    critical: int = 0


class NewsPipelineCacheStats(CamelModel):
    """Prompt caching statistics from news pipeline multi-agent analysis."""

    total: int = 0
    avg_cache_hit_rate: Optional[float] = None
    cache_hits: int = 0
    total_cached_tokens: int = 0
    total_prompt_tokens: int = 0


class NewsPipelineNodeLatency(CamelModel):
    """Per-node latency stats for news pipeline nodes."""

    node: str
    count: int = 0
    success: int = 0
    errors: int = 0
    avg_ms: Optional[float] = None
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None


class NewsPipelineStatsResponse(CamelModel):
    """Combined news pipeline statistics response."""

    period_days: int
    routing: NewsPipelineRoutingStats
    tokens: NewsPipelineTokenStats
    score_distribution: List[ScoreDistributionBucket] = []
    cache_stats: NewsPipelineCacheStats = NewsPipelineCacheStats()
    node_latency: List[NewsPipelineNodeLatency] = []
