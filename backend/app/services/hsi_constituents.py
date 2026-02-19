"""
Hang Seng Index constituent stocks service.

Provides HSI constituent list with fallback:
  1. Redis cache (24h TTL)
  2. data-service API (akshare-based, via DataServiceClient)
  3. Static hardcoded list (~82 constituents as of late 2025)
"""

import json
import logging
from typing import Optional

from app.db.redis import get_redis

logger = logging.getLogger(__name__)

# Redis cache settings
_CACHE_KEY = "hsi:constituents"
_CACHE_TTL = 86400  # 24 hours

# Static fallback list of HSI constituents (as of late 2025)
_STATIC_HSI = [
    "0001.HK", "0002.HK", "0003.HK", "0005.HK", "0006.HK",
    "0011.HK", "0012.HK", "0016.HK", "0017.HK", "0027.HK",
    "0066.HK", "0101.HK", "0175.HK", "0241.HK", "0267.HK",
    "0288.HK", "0291.HK", "0316.HK", "0386.HK", "0388.HK",
    "0669.HK", "0688.HK", "0700.HK", "0762.HK", "0823.HK",
    "0857.HK", "0868.HK", "0881.HK", "0883.HK", "0939.HK",
    "0941.HK", "0960.HK", "0968.HK", "0981.HK", "1038.HK",
    "1044.HK", "1093.HK", "1109.HK", "1113.HK", "1177.HK",
    "1209.HK", "1211.HK", "1299.HK", "1378.HK", "1398.HK",
    "1810.HK", "1876.HK", "1928.HK", "1997.HK", "2007.HK",
    "2018.HK", "2269.HK", "2313.HK", "2318.HK", "2319.HK",
    "2331.HK", "2382.HK", "2388.HK", "2628.HK", "2688.HK",
    "3311.HK", "3328.HK", "3690.HK", "3692.HK", "3968.HK",
    "3988.HK", "6060.HK", "6078.HK", "6098.HK", "6618.HK",
    "6690.HK", "6862.HK", "9618.HK", "9626.HK", "9633.HK",
    "9888.HK", "9961.HK", "9988.HK", "9999.HK",
]


class HSIConstituentService:
    """Service for fetching Hang Seng Index constituent stocks."""

    async def get_constituents(self) -> list[str]:
        """Get HSI constituent symbols in WebStock format (e.g. 0700.HK).

        Tries: Redis cache -> data-service API -> static fallback.
        Always returns a non-empty list.
        """
        # Layer 0: Redis cache
        cached = await self._get_from_cache()
        if cached is not None:
            return cached

        # Layer 1: data-service API
        symbols = await self._fetch_from_data_service()
        if symbols:
            await self._save_to_cache(symbols)
            return symbols

        # Layer 2: Static fallback
        logger.info("Using static HSI constituent list (%d stocks)", len(_STATIC_HSI))
        await self._save_to_cache(_STATIC_HSI)
        return list(_STATIC_HSI)

    async def _get_from_cache(self) -> Optional[list[str]]:
        """Try to load constituents from Redis cache."""
        try:
            redis_client = await get_redis()
            cached = await redis_client.get(_CACHE_KEY)
            if cached:
                symbols = json.loads(cached)
                logger.debug(
                    "Loaded %d HSI constituents from cache", len(symbols)
                )
                return symbols
        except Exception as e:
            logger.warning("Failed to read HSI constituents from cache: %s", e)
        return None

    async def _save_to_cache(self, symbols: list[str]) -> None:
        """Save constituents to Redis cache."""
        try:
            redis_client = await get_redis()
            await redis_client.setex(
                _CACHE_KEY, _CACHE_TTL, json.dumps(symbols)
            )
            logger.debug(
                "Cached %d HSI constituents for %d seconds",
                len(symbols),
                _CACHE_TTL,
            )
        except Exception as e:
            logger.warning("Failed to cache HSI constituents: %s", e)

    async def _fetch_from_data_service(self) -> Optional[list[str]]:
        """Fetch HSI constituents from data-service."""
        try:
            from app.services.data_service_client import get_data_service_client
            client = await get_data_service_client()
            result = await client.get_hsi_constituents()
            if result and isinstance(result.get("symbols"), list):
                symbols = result["symbols"]
                logger.info(
                    "Fetched %d HSI constituents from data-service",
                    len(symbols),
                )
                return symbols
        except Exception as e:
            logger.warning("data-service HSI constituents failed: %s", e)
        return None


async def get_hsi_constituents() -> list[str]:
    """Module-level convenience function to get HSI constituents.

    Returns:
        List of HSI constituent symbols in WebStock format (e.g. '0700.HK').
    """
    service = HSIConstituentService()
    return await service.get_constituents()
