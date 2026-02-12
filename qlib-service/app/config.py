"""Configuration for qlib-service."""
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
    PORT: int = 8001

    # Logging
    LOG_LEVEL: str = "info"

    # Expression engine limits
    MAX_EXPRESSION_LENGTH: int = 500

    # Backtest limits
    MAX_CONCURRENT_BACKTESTS: int = 1
    BACKTEST_TIMEOUT_SECONDS: int = 1800  # 30 minutes


@lru_cache()
def get_settings() -> Settings:
    return Settings()
