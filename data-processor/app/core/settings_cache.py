"""Settings cache backed by PostgreSQL via asyncpg.

Reads prediction-related configuration from system_settings and
prediction_universes tables, caching in-memory with configurable TTL.
Pattern adapted from ai-gateway/app/provider_cache.py.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from uuid import UUID

import asyncpg

from app.config import get_settings

logger = logging.getLogger(__name__)

# Rate limit on force-refresh to prevent DB query storms
_FORCE_REFRESH_MIN_INTERVAL = 5.0  # seconds


@dataclass(frozen=True)
class AutoTuneConfig:
    """Auto-retrain and auto-tune scheduling configuration."""

    auto_retrain_enabled: bool = False
    auto_retrain_interval_days: int = 7
    auto_tune_enabled: bool = False
    auto_tune_interval_days: int = 30
    auto_tune_max_iterations: int = 3


@dataclass(frozen=True)
class PredictionLLMConfig:
    """LLM configuration for prediction workflows."""

    provider_id: Optional[UUID]
    model: Optional[str]
    enabled: bool


@dataclass(frozen=True)
class UniverseConfig:
    """Stock universe configuration for prediction targets."""

    id: UUID
    name: str
    market: str
    universe_type: str  # "index" or "custom"
    index_code: Optional[str]
    symbols: Optional[list[str]]
    is_default: bool


@dataclass(frozen=True)
class PredictionConfig:
    """Aggregated prediction configuration."""

    llm: PredictionLLMConfig
    universes: list[UniverseConfig]
    auto_tune: AutoTuneConfig = field(default_factory=AutoTuneConfig)


class SettingsCache:
    """In-memory cache for prediction settings with TTL refresh.

    Connects directly to PostgreSQL via asyncpg (no SQLAlchemy ORM)
    and refreshes on configurable TTL or explicit force-refresh.
    """

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self._config: Optional[PredictionConfig] = None
        self._last_refresh: float = 0
        self._last_force_refresh: float = 0
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Create the asyncpg connection pool with startup retry."""
        settings = get_settings()
        db_url = self._build_db_url()
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    db_url,
                    min_size=settings.DB_POOL_MIN,
                    max_size=settings.DB_POOL_MAX,
                    command_timeout=30,
                )
                await self._refresh()
                llm_status = "enabled" if self._config and self._config.llm.enabled else "disabled"
                universe_count = len(self._config.universes) if self._config else 0
                logger.info(
                    "SettingsCache initialized: prediction=%s, universes=%d",
                    llm_status,
                    universe_count,
                )
                return
            except Exception as e:
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "DB connection attempt %d/%d failed: %s -- retrying in %ds",
                        attempt,
                        max_retries,
                        e,
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "DB connection failed after %d attempts: %s",
                        max_retries,
                        e,
                    )
                    raise

    @staticmethod
    def _build_db_url() -> str:
        """Build asyncpg-compatible DB URL from settings."""
        settings = get_settings()
        db_url = settings.DATABASE_URL
        # Convert SQLAlchemy-style URL if needed
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        # Preserve query params (e.g. ?sslmode=require) -- asyncpg supports them
        return db_url

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("SettingsCache connection pool closed")

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        """Expose pool for health checks."""
        return self._pool

    async def _refresh(self) -> None:
        """Reload prediction config from the database."""
        if not self._pool:
            return

        async with self._pool.acquire(timeout=10) as conn:
            # Fetch LLM settings + auto-tune config from system_settings
            settings_row = await conn.fetchrow(
                "SELECT prediction_provider_id, prediction_model, prediction_enabled, "
                "auto_retrain_enabled, auto_retrain_interval_days, "
                "auto_tune_enabled, auto_tune_interval_days, auto_tune_max_iterations "
                "FROM system_settings WHERE id = 1"
            )

            # Fetch active universes
            universe_rows = await conn.fetch(
                "SELECT id, name, market, universe_type, index_code, symbols, is_default "
                "FROM prediction_universes WHERE is_active = true "
                "ORDER BY is_default DESC, name ASC"
            )

        # Build LLM config (handle missing columns gracefully for fresh deployments)
        if settings_row:
            llm_config = PredictionLLMConfig(
                provider_id=settings_row["prediction_provider_id"],
                model=settings_row["prediction_model"],
                enabled=bool(settings_row["prediction_enabled"]),
            )
            # Auto-tune config (columns may not exist on older schema)
            try:
                auto_tune_config = AutoTuneConfig(
                    auto_retrain_enabled=bool(settings_row["auto_retrain_enabled"]),
                    auto_retrain_interval_days=int(settings_row["auto_retrain_interval_days"]),
                    auto_tune_enabled=bool(settings_row["auto_tune_enabled"]),
                    auto_tune_interval_days=int(settings_row["auto_tune_interval_days"]),
                    auto_tune_max_iterations=int(settings_row["auto_tune_max_iterations"]),
                )
            except (KeyError, TypeError):
                auto_tune_config = AutoTuneConfig()
        else:
            llm_config = PredictionLLMConfig(
                provider_id=None,
                model=None,
                enabled=False,
            )
            auto_tune_config = AutoTuneConfig()

        # Build universe configs
        universes: list[UniverseConfig] = []
        for row in universe_rows:
            # symbols column may be TEXT[] or JSONB depending on migration
            raw_symbols = row["symbols"]
            if isinstance(raw_symbols, str):
                try:
                    raw_symbols = json.loads(raw_symbols)
                except (json.JSONDecodeError, TypeError):
                    raw_symbols = None
            elif isinstance(raw_symbols, list):
                pass  # Already a list (TEXT[] from asyncpg)
            else:
                raw_symbols = None

            universes.append(
                UniverseConfig(
                    id=row["id"],
                    name=row["name"],
                    market=row["market"],
                    universe_type=row["universe_type"],
                    index_code=row["index_code"],
                    symbols=raw_symbols,
                    is_default=bool(row["is_default"]),
                )
            )

        self._config = PredictionConfig(
            llm=llm_config, universes=universes, auto_tune=auto_tune_config,
        )
        self._last_refresh = time.monotonic()
        logger.debug(
            "Settings cache refreshed: prediction_enabled=%s, universes=%d",
            llm_config.enabled,
            len(universes),
        )

    async def _ensure_fresh(self) -> None:
        """Refresh cache if TTL expired."""
        settings = get_settings()
        if time.monotonic() - self._last_refresh > settings.SETTINGS_CACHE_TTL:
            async with self._lock:
                # Double-check after acquiring lock
                if time.monotonic() - self._last_refresh > settings.SETTINGS_CACHE_TTL:
                    try:
                        await self._refresh()
                    except Exception as e:
                        logger.warning("Settings cache refresh failed: %s", e)
                        # Serve stale data rather than failing

    async def get_config(self) -> PredictionConfig:
        """Get the full prediction configuration, refreshing if stale.

        Returns a default disabled config if cache has never been populated.
        """
        await self._ensure_fresh()
        if self._config is None:
            return PredictionConfig(
                llm=PredictionLLMConfig(provider_id=None, model=None, enabled=False),
                universes=[],
                auto_tune=AutoTuneConfig(),
            )
        return self._config

    async def get_llm_config(self) -> PredictionLLMConfig:
        """Convenience: get just the LLM configuration."""
        config = await self.get_config()
        return config.llm

    async def get_universes(self, market: Optional[str] = None) -> list[UniverseConfig]:
        """Get active universes, optionally filtered by market.

        Args:
            market: If provided, filter universes to this market only.

        Returns:
            List of active universe configurations.
        """
        config = await self.get_config()
        if market is None:
            return config.universes
        return [u for u in config.universes if u.market == market]

    async def force_refresh(self) -> bool:
        """Force an immediate cache refresh, rate-limited.

        Returns True if refresh was performed, False if rate-limited.
        """
        now = time.monotonic()
        if now - self._last_force_refresh < _FORCE_REFRESH_MIN_INTERVAL:
            return False
        async with self._lock:
            if now - self._last_force_refresh < _FORCE_REFRESH_MIN_INTERVAL:
                return False
            self._last_force_refresh = now
            try:
                await self._refresh()
                return True
            except Exception as e:
                logger.warning("Force refresh failed: %s", e)
                return False


# Singleton
settings_cache = SettingsCache()
