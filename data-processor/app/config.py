"""Configuration for data-processor microservice.

Extends qlib-service configuration with ML prediction capabilities,
AI gateway integration, and PostgreSQL settings cache.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Qlib data directory
    QLIB_DATA_DIR: str = "/app/data/qlib"
    DEFAULT_MARKET: str = "us"

    # Redis
    REDIS_URL: str = "redis://redis:6379/3"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8005

    # Logging
    LOG_LEVEL: str = "info"

    # Expression engine limits
    MAX_EXPRESSION_LENGTH: int = 500

    # Backtest limits
    MAX_CONCURRENT_BACKTESTS: int = 1
    BACKTEST_TIMEOUT_SECONDS: int = 1800  # 30 minutes

    # Backend data source configuration
    DATA_SERVICE_URL: str = "http://data-service:8003"
    WEBSTOCK_BACKEND_URL: str = "http://app:80"  # Legacy fallback
    INTERNAL_API_TOKEN: str = ""

    # AI Gateway (for LLM proxy to RD-Agent)
    AI_GATEWAY_URL: str = "http://ai-gateway:8004"

    # PostgreSQL (raw asyncpg, NOT SQLAlchemy — only for settings cache)
    DATABASE_URL: str = "postgresql://webstock:webstock@postgres:5432/webstock"
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 5

    # Prediction data
    PREDICTION_DATA_DIR: str = "/app/data/predictions"

    # Settings cache TTL
    SETTINGS_CACHE_TTL: int = 60  # seconds


@lru_cache()
def get_settings() -> Settings:
    return Settings()
