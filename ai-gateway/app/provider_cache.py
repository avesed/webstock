"""Provider credential cache backed by PostgreSQL.

Reads the llm_providers table directly via asyncpg (no SQLAlchemy ORM)
and caches results in-memory with configurable TTL.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional
from uuid import UUID

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

# Rate limit on force-refresh to prevent DB query storms
# when many concurrent requests hit a cache miss for unknown provider_ids
_FORCE_REFRESH_MIN_INTERVAL = 5.0  # seconds


@dataclass(frozen=True)
class ProviderRow:
    """Cached provider record from llm_providers table."""

    id: UUID
    name: str
    provider_type: str  # "openai" or "anthropic"
    api_key: str
    base_url: Optional[str]
    is_enabled: bool


class ProviderCache:
    """In-memory cache for llm_providers with TTL refresh."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._cache: Dict[UUID, ProviderRow] = {}
        self._last_refresh: float = 0
        self._last_force_refresh: float = 0
        self._lock = asyncio.Lock()

    async def init(self):
        """Create the asyncpg connection pool with startup retry."""
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
                logger.info(
                    "ProviderCache initialized: %d providers loaded",
                    len(self._cache),
                )
                return
            except Exception as e:
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "DB connection attempt %d/%d failed: %s — retrying in %ds",
                        attempt, max_retries, e, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "DB connection failed after %d attempts: %s", max_retries, e,
                    )
                    raise

    @staticmethod
    def _build_db_url() -> str:
        """Build asyncpg-compatible DB URL from settings."""
        db_url = settings.DATABASE_URL
        # Convert SQLAlchemy-style URL if needed
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        # Preserve query params (e.g. ?sslmode=require) — asyncpg supports them
        return db_url

    async def close(self):
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _refresh(self):
        """Reload all enabled providers from the database."""
        if not self._pool:
            return

        async with self._pool.acquire(timeout=10) as conn:
            rows = await conn.fetch(
                "SELECT id, name, provider_type, api_key, base_url, is_enabled "
                "FROM llm_providers WHERE is_enabled = true"
            )

        new_cache: Dict[UUID, ProviderRow] = {}
        for row in rows:
            provider = ProviderRow(
                id=row["id"],
                name=row["name"],
                provider_type=row["provider_type"],
                api_key=row["api_key"],
                base_url=row["base_url"],
                is_enabled=row["is_enabled"],
            )
            new_cache[provider.id] = provider

        self._cache = new_cache
        self._last_refresh = time.monotonic()
        logger.debug("Provider cache refreshed: %d entries", len(new_cache))

    async def _ensure_fresh(self):
        """Refresh cache if TTL expired."""
        if time.monotonic() - self._last_refresh > settings.PROVIDER_CACHE_TTL:
            async with self._lock:
                # Double-check after acquiring lock
                if time.monotonic() - self._last_refresh > settings.PROVIDER_CACHE_TTL:
                    await self._refresh()

    async def get_provider(self, provider_id: UUID) -> Optional[ProviderRow]:
        """Get a provider by ID, refreshing cache if stale."""
        await self._ensure_fresh()
        provider = self._cache.get(provider_id)
        if not provider:
            # Force refresh and retry (admin may have just added it)
            # Rate-limited to avoid DB query storms from many concurrent misses
            now = time.monotonic()
            if now - self._last_force_refresh >= _FORCE_REFRESH_MIN_INTERVAL:
                async with self._lock:
                    if now - self._last_force_refresh >= _FORCE_REFRESH_MIN_INTERVAL:
                        self._last_force_refresh = now
                        await self._refresh()
                provider = self._cache.get(provider_id)
        return provider

    async def get_all_providers(self) -> Dict[UUID, ProviderRow]:
        """Get all cached providers."""
        await self._ensure_fresh()
        return dict(self._cache)


# Singleton
provider_cache = ProviderCache()
