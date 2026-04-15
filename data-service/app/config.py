"""Configuration for data-service."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Redis (DB 5 — separate from app/celery/qlib/rsshub on 0-4)
    REDIS_URL: str = "redis://redis:6379/5"

    # PostgreSQL (shared DB with main backend)
    DATABASE_URL: str = ""

    # Database pool
    DATABASE_POOL_MIN_SIZE: int = 2
    DATABASE_POOL_MAX_SIZE: int = 10
    DATABASE_COMMAND_TIMEOUT: int = 120

    # Service-to-service auth
    INTERNAL_API_TOKEN: str = ""

    # External API keys
    FINNHUB_API_KEY: str = ""
    TUSHARE_TOKEN: str = ""
    TIINGO_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    POLYGON_API_KEY: str = ""

    # Playwright service (for content extraction fallback, port 8002)
    PLAYWRIGHT_SERVICE_URL: str = "http://playwright-service:8002"

    # Qlib service URL (for triggering data sync after collection)
    QLIB_SERVICE_URL: str = "http://data-processor:8005"

    # NewsForge integration
    NEWSFORGE_URL: str = ""  # e.g., "http://newsforge:8000"
    NEWSFORGE_API_KEY: str = ""  # X-API-Key for NewsForge internal API
    NEWSFORGE_PUSH_ENABLED: bool = False
    NEWSFORGE_PUSH_BATCH_SIZE: int = 50

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8003
    LOG_LEVEL: str = "info"

    # Executor
    EXECUTOR_MAX_WORKERS: int = 20  # Frontend requests
    EXECUTOR_BACKGROUND_WORKERS: int = 10  # Daily bar + stock list collection
    EXECUTOR_PROFILE_WORKERS: int = 5  # Stock profile collection


@lru_cache()
def get_settings() -> Settings:
    return Settings()
