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

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8003
    LOG_LEVEL: str = "info"

    # Executor
    EXECUTOR_MAX_WORKERS: int = 10


@lru_cache()
def get_settings() -> Settings:
    return Settings()
