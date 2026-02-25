"""AI Gateway configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8004

    # PostgreSQL (raw asyncpg, NOT SQLAlchemy — we only read llm_providers)
    # The URL should be in asyncpg format: postgresql://user:pass@host:port/db
    DATABASE_URL: str = "postgresql://webstock:webstock@postgres:5432/webstock"
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 10

    # Auth
    INTERNAL_API_TOKEN: str = ""

    # Cache TTL for provider data
    PROVIDER_CACHE_TTL: int = 60  # seconds

    # Logging
    LOG_LEVEL: str = "info"


settings = Settings()
