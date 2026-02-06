"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "WebStock"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://webstock:webstock@postgres:5432/webstock"
    DATABASE_POOL_SIZE: int = 30
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_POOL_SIZE: int = 30

    # JWT Configuration
    # IMPORTANT: JWT_SECRET_KEY must be set via environment variable in production
    # Generate a secure key with: openssl rand -hex 32
    JWT_SECRET_KEY: str = Field(
        default="CHANGE_ME_INSECURE_DEFAULT_KEY",
        description="JWT secret key - MUST be overridden in production"
    )
    # Previous keys for smooth rotation (comma-separated)
    # During rotation, new tokens use primary key, old tokens verified with previous keys
    JWT_SECRET_KEY_PREVIOUS: str = Field(
        default="",
        description="Previous JWT keys for smooth key rotation (comma-separated)"
    )

    @property
    def jwt_previous_keys(self) -> list[str]:
        """Parse comma-separated previous keys into a list."""
        if not self.JWT_SECRET_KEY_PREVIOUS.strip():
            return []
        return [k.strip() for k in self.JWT_SECRET_KEY_PREVIOUS.split(",") if k.strip()]

    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Security
    BCRYPT_ROUNDS: int = 12
    MAX_LOGIN_ATTEMPTS: int = 5
    ACCOUNT_LOCK_MINUTES: int = 15

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # CORS - 允许所有来源（生产环境应限制为特定域名）
    CORS_ORIGINS: list[str] = ["*"]

    # External APIs
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: Optional[str] = None  # For OpenAI-compatible APIs
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536
    OPENAI_MAX_TOKENS: int = 2000
    FINNHUB_API_KEY: Optional[str] = None
    TUSHARE_TOKEN: Optional[str] = None
    ALPHA_VANTAGE_API_KEY: Optional[str] = None

    # News Full Content Settings
    FULL_CONTENT_ENABLED: bool = True
    FULL_CONTENT_DEFAULT_SOURCE: str = "scraper"  # scraper / polygon
    POLYGON_API_KEY: Optional[str] = None
    SCRAPER_RATE_LIMIT: int = 10  # requests per minute per domain
    SCRAPER_TIMEOUT: int = 30  # seconds
    NEWS_RETENTION_DAYS_DEFAULT: int = 30  # default retention days
    NEWS_CONTENT_BASE_PATH: str = "data/news_content"  # JSON storage path

    # OpenAI Rate Limiting (layered)
    # Global rate limit for all OpenAI API calls combined
    OPENAI_RATE_LIMIT: int = 200  # total requests per minute across all features
    # Per-feature rate limits (must sum to <= OPENAI_RATE_LIMIT)
    OPENAI_RATE_LIMIT_ANALYSIS: int = 80  # analysis agents (fundamental/technical/sentiment/news)
    OPENAI_RATE_LIMIT_CHAT: int = 80  # AI chat conversations
    OPENAI_RATE_LIMIT_EMBEDDING: int = 30  # RAG embedding generation
    OPENAI_RATE_LIMIT_BACKGROUND: int = 10  # background tasks (news monitoring, etc.)
    # Per-user rate limits
    AI_ANALYSIS_RATE_LIMIT: int = 10  # analysis requests per minute per user
    AI_CHAT_RATE_LIMIT: int = 20  # chat messages per minute per user

    @model_validator(mode="after")
    def _validate_rate_limits(self) -> "Settings":
        """Ensure per-feature rate limits don't exceed global limit."""
        feature_sum = (
            self.OPENAI_RATE_LIMIT_ANALYSIS
            + self.OPENAI_RATE_LIMIT_CHAT
            + self.OPENAI_RATE_LIMIT_EMBEDDING
            + self.OPENAI_RATE_LIMIT_BACKGROUND
        )
        if feature_sum > self.OPENAI_RATE_LIMIT:
            import warnings
            warnings.warn(
                f"Per-feature OpenAI rate limits sum ({feature_sum}) exceeds "
                f"global limit ({self.OPENAI_RATE_LIMIT}). "
                f"The global limit will be the effective bottleneck.",
                stacklevel=2,
            )
        return self

    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # Email (SMTP)
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: str = "noreply@webstock.local"

    # Web Push
    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_CLAIMS_EMAIL: str = "admin@webstock.local"

    # First admin configuration
    # If set, this email will be promoted to admin role on first startup
    # Only takes effect if no admin users exist yet
    FIRST_ADMIN_EMAIL: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
