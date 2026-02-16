"""
Hang Seng Index constituent stocks service.

Provides HSI constituent list with two-layer fallback:
  1. akshare API (ak.index_stock_cons or ak.stock_hk_spot_em)
  2. Static hardcoded list (~82 constituents as of late 2025)

Results are cached in Redis for 24 hours.
"""

import asyncio
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


def _normalize_hk_symbol(code: str) -> str:
    """Normalize a raw HK stock code to WebStock format (e.g. '0700.HK').

    Handles codes like '00700', '0700', '700', '09988', '9988'.
    - 5-digit codes (e.g. '09988'): keep as-is, add .HK suffix.
    - 4-digit or shorter: zero-pad to 4 digits, add .HK suffix.
    """
    code = code.strip()
    # Remove any existing .HK suffix for uniform processing
    if code.upper().endswith(".HK"):
        code = code[:-3]

    # Strip leading zeros then re-pad based on length
    digits = code.lstrip("0") or "0"

    if len(digits) >= 5:
        # 5-digit stock code (e.g. 09988 -> keep as 9988 won't work;
        # the original had 5 digits, so keep the original digit count)
        return f"{digits}.HK"
    else:
        # Pad to 4 digits
        return f"{digits.zfill(4)}.HK"


def _fetch_via_index_cons() -> Optional[list[str]]:
    """Try fetching HSI constituents via ak.index_stock_cons('HSI').

    Returns a list of normalized symbols, or None on failure.
    """
    import akshare as ak

    df = ak.index_stock_cons("HSI")
    if df is None or df.empty:
        return None

    # The column name varies across akshare versions
    code_col = None
    for candidate in ("品种代码", "constituent_code", "code", "成分券代码"):
        if candidate in df.columns:
            code_col = candidate
            break

    if code_col is None:
        if df.columns.empty:
            logger.warning("index_stock_cons returned DataFrame with no columns")
            return None
        # Fall back to the first column if none of the known names match
        code_col = df.columns[0]
        logger.info(
            "HSI index_stock_cons: using first column '%s' as code column "
            "(available columns: %s)",
            code_col,
            list(df.columns),
        )

    symbols = []
    for raw_code in df[code_col].astype(str):
        sym = _normalize_hk_symbol(raw_code)
        symbols.append(sym)

    return sorted(set(symbols)) if symbols else None


def _fetch_via_hk_spot() -> Optional[list[str]]:
    """Fallback: try ak.stock_hk_spot_em() and return all HK stock codes.

    This returns ALL HK stocks, not just HSI constituents, so it is a
    last-resort data source. Returns None on failure.
    """
    import akshare as ak

    df = ak.stock_hk_spot_em()
    if df is None or df.empty:
        return None

    # stock_hk_spot_em uses Chinese column names: "代码" for code
    code_col = None
    for candidate in ("代码", "code", "symbol"):
        if candidate in df.columns:
            code_col = candidate
            break

    if code_col is None:
        logger.warning(
            "HSI stock_hk_spot_em: cannot find code column "
            "(available columns: %s)",
            list(df.columns),
        )
        return None

    # This endpoint returns all HK stocks; we cannot filter to HSI only,
    # so this fallback is of limited value. Return None to let the static
    # list take over instead.
    logger.warning(
        "HSI stock_hk_spot_em returned %d stocks but cannot filter to HSI "
        "constituents; skipping this fallback",
        len(df),
    )
    return None


class HSIConstituentService:
    """Service for fetching Hang Seng Index constituent stocks."""

    async def get_constituents(self) -> list[str]:
        """Get HSI constituent symbols in WebStock format (e.g. 0700.HK).

        Tries: Redis cache -> akshare API -> static fallback.
        Always returns a non-empty list.
        """
        # Layer 0: Redis cache
        cached = await self._get_from_cache()
        if cached is not None:
            return cached

        # Layer 1: akshare API
        symbols = await self._fetch_from_akshare()
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

    async def _fetch_from_akshare(self) -> Optional[list[str]]:
        """Fetch HSI constituents from akshare with fallback strategies."""
        # Strategy 1: index_stock_cons("HSI")
        try:
            symbols = await asyncio.to_thread(_fetch_via_index_cons)
            if symbols:
                logger.info(
                    "Fetched %d HSI constituents via index_stock_cons",
                    len(symbols),
                )
                return symbols
        except Exception as e:
            logger.warning("akshare index_stock_cons('HSI') failed: %s", e)

        # Strategy 2: stock_hk_spot_em (returns all HK stocks)
        try:
            symbols = await asyncio.to_thread(_fetch_via_hk_spot)
            if symbols:
                logger.info(
                    "Fetched %d HSI constituents via stock_hk_spot_em",
                    len(symbols),
                )
                return symbols
        except Exception as e:
            logger.warning("akshare stock_hk_spot_em() failed: %s", e)

        return None


async def get_hsi_constituents() -> list[str]:
    """Module-level convenience function to get HSI constituents.

    Returns:
        List of HSI constituent symbols in WebStock format (e.g. '0700.HK').
    """
    service = HSIConstituentService()
    return await service.get_constituents()
