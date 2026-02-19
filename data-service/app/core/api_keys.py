"""API key manager — loads keys from DB, caches in memory.

On startup the data-service reads ``system_settings`` (id = 1) to obtain
API keys that are configured through the admin UI.  A background Redis
subscriber listens on ``data_service:reload_keys`` so the backend can
push instant updates whenever an admin saves new keys.

Usage in providers::

    from app.core.api_keys import get_api_key

    api_key = get_api_key("finnhub")  # DB value → env fallback
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------
_api_keys: Dict[str, str] = {}

# Column name in system_settings → short alias used by get_api_key()
_KEY_MAP = {
    "finnhub_api_key": "finnhub",
    "polygon_api_key": "polygon",
    "tavily_api_key": "tavily",
    # tiingo / tushare are env-only for now (not in system_settings)
}

# Reverse: alias → env var name on Settings
_ENV_FALLBACK = {
    "finnhub": "FINNHUB_API_KEY",
    "polygon": "POLYGON_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "tiingo": "TIINGO_API_KEY",
    "tushare": "TUSHARE_TOKEN",
}


def get_api_key(name: str) -> Optional[str]:
    """Return API key by short alias.  DB value takes priority over env."""
    val = _api_keys.get(name)
    if val:
        return val
    # Fallback to environment variable via Settings
    env_attr = _ENV_FALLBACK.get(name)
    if env_attr:
        return getattr(get_settings(), env_attr, None) or None
    return None


# ---------------------------------------------------------------------------
# DB loader (asyncpg, lightweight one-shot query)
# ---------------------------------------------------------------------------
async def load_api_keys_from_db() -> None:
    """Read API keys from ``system_settings`` (id=1) and cache in memory."""
    settings = get_settings()
    db_url = settings.DATABASE_URL
    if not db_url:
        logger.info("DATABASE_URL not configured, skipping DB key load")
        return

    # asyncpg needs postgresql:// scheme (strip +asyncpg if present)
    dsn = db_url.replace("+asyncpg", "").split("?")[0]

    try:
        import asyncpg  # noqa: local import — only needed here

        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            columns = ", ".join(_KEY_MAP.keys())
            row = await conn.fetchrow(
                f"SELECT {columns} FROM system_settings WHERE id = 1"
            )
            if row:
                loaded = 0
                for col, alias in _KEY_MAP.items():
                    val = row[col]
                    if val:
                        _api_keys[alias] = val
                        loaded += 1
                    else:
                        _api_keys.pop(alias, None)
                logger.info("Loaded %d API keys from DB", loaded)
            else:
                logger.warning("system_settings row not found (id=1)")
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("Failed to load API keys from DB: %s", e)


# ---------------------------------------------------------------------------
# Redis subscriber (background task)
# ---------------------------------------------------------------------------
_subscriber_task: Optional[asyncio.Task] = None


async def _redis_key_subscriber() -> None:
    """Listen on Redis pub/sub for key-reload signals from the backend."""
    from app.core.cache import get_redis

    channel = "data_service:reload_keys"
    logger.info("API key subscriber started on channel '%s'", channel)

    while True:
        try:
            redis = await get_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    logger.info("Received reload-keys signal, refreshing...")
                    await load_api_keys_from_db()
        except asyncio.CancelledError:
            logger.info("API key subscriber cancelled")
            return
        except Exception as e:
            logger.warning("API key subscriber error: %s — retrying in 5s", e)
            await asyncio.sleep(5)


def start_subscriber() -> None:
    """Launch the background Redis subscriber task."""
    global _subscriber_task
    if _subscriber_task is None or _subscriber_task.done():
        _subscriber_task = asyncio.create_task(_redis_key_subscriber())


async def stop_subscriber() -> None:
    """Cancel the background subscriber."""
    global _subscriber_task
    if _subscriber_task and not _subscriber_task.done():
        _subscriber_task.cancel()
        try:
            await _subscriber_task
        except asyncio.CancelledError:
            pass
    _subscriber_task = None
