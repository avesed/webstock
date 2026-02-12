"""Configuration for Playwright extraction service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Service
    HOST: str = "0.0.0.0"
    PORT: int = 8002
    LOG_LEVEL: str = "info"

    # Playwright
    HEADLESS: bool = True
    BROWSER_TIMEOUT: int = 30000      # ms
    NAVIGATION_TIMEOUT: int = 15000   # ms
    MAX_CONTENT_LENGTH: int = 50000   # chars

    # MCP server port
    MCP_PORT: int = 8931


settings = Settings()
