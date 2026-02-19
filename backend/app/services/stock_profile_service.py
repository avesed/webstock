"""Stock profile data collection service for building a vectorized knowledge base.

Collects enriched stock profiles (description, industry, concepts, main business)
from the data-service microservice across CN/US/HK markets. Each profile is
converted to an embedding-friendly text string via ``to_embedding_text()``.

Uses **granular batch endpoints** in data-service (max 50 symbols per HTTP call)
to avoid HTTP timeout issues with large markets:
- CN: two-step — concept mapping (1 call) + stock info batches (N calls)
- US/HK: batched yfinance collection (N calls of 50 symbols each)

Data sources (handled by data-service):
- A-shares (CN): akshare concept boards (inverted mapping) + individual stock info
- US stocks: yfinance Ticker info (industry, sector, description)
- HK stocks: yfinance Ticker info (major stocks only)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Batch size for granular profile collection
_BATCH_SIZE = 50


@dataclass
class StockProfile:
    """Enriched stock profile for vectorized knowledge base."""

    symbol: str            # e.g. AAPL, 600519.SS, 0700.HK
    name: str              # English name
    name_zh: str = ""      # Chinese name (A-shares, HK)
    market: str = ""       # us, sh, sz, hk
    description: str = ""  # Company business description
    industry: str = ""     # Industry classification
    sector: str = ""       # Sector classification
    concepts: List[str] = field(default_factory=list)  # Concept board labels (A-shares)
    main_business: str = ""  # Main products / business lines

    def to_embedding_text(self) -> str:
        """Combine all fields into a single string for embedding.

        Produces a compact text (~150-400 chars) that captures the stock's
        identity, business scope, and thematic associations.
        """
        parts = [self.symbol]
        if self.name:
            parts.append(self.name)
        if self.name_zh:
            parts.append(self.name_zh)
        if self.market:
            parts.append(f"市场:{self.market}")
        if self.industry:
            parts.append(f"行业:{self.industry}")
        if self.sector and self.sector != self.industry:
            parts.append(f"板块:{self.sector}")
        if self.concepts:
            parts.append(f"概念:{','.join(self.concepts[:15])}")
        if self.main_business:
            parts.append(f"主营:{self.main_business[:200]}")
        # Only add description if it differs from main_business
        if self.description and self.description != self.main_business:
            parts.append(self.description[:300])
        return " ".join(parts)


def _profile_from_dict(p: Dict[str, Any], **overrides: Any) -> StockProfile:
    """Create a StockProfile from a data-service response dict."""
    kwargs: Dict[str, Any] = {
        "symbol": p.get("symbol", ""),
        "name": p.get("name", ""),
        "name_zh": p.get("name_zh", ""),
        "market": p.get("market", ""),
        "description": p.get("description", ""),
        "industry": p.get("industry", ""),
        "sector": p.get("sector", ""),
        "concepts": p.get("concepts", []),
        "main_business": p.get("main_business", ""),
    }
    kwargs.update(overrides)
    return StockProfile(**kwargs)


class StockProfileService:
    """Collects stock profile data across CN/US/HK markets via data-service.

    Uses granular batch endpoints (``fetch_cn_concept_mapping`` +
    ``fetch_stock_profiles_batch``) to keep each HTTP call under 60s,
    orchestrating multiple calls in a loop with rate-limit delays.
    """

    # -----------------------------------------------------------------------
    # A-shares: two-step — concept mapping + batched stock info
    # -----------------------------------------------------------------------

    async def collect_cn_profiles(self) -> List[StockProfile]:
        """Collect A-share profiles via data-service (granular batching).

        Step 1: Fetch concept board → stock mapping (single HTTP call, ~200s).
        Step 2: Batch fetch individual stock info (50 codes per call, ~40s each).
        Merge concept data from step 1 into each profile.
        """
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Starting CN profile collection (granular)")
        t0 = time.monotonic()

        client = await get_data_service_client()

        # Step 1: Get concept mapping
        mapping = await client.fetch_cn_concept_mapping()
        if not mapping:
            logger.warning("[StockProfile] CN concept mapping returned no data")
            return []

        concepts_map = mapping.get("concepts", {})  # code -> [concept_names]
        names_map = mapping.get("names", {})         # code -> name_zh

        mapping_elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] CN concept mapping: %d stocks in %.0fs, "
            "starting batched info fetch",
            len(concepts_map), mapping_elapsed,
        )

        # Step 2: Batch fetch individual stock info
        codes = list(concepts_map.keys())
        profiles: List[StockProfile] = []
        failed_batches = 0

        for i in range(0, len(codes), _BATCH_SIZE):
            batch = codes[i : i + _BATCH_SIZE]
            result = await client.fetch_stock_profiles_batch("cn", batch)
            # Single retry on failure
            if not result:
                await asyncio.sleep(5.0)
                result = await client.fetch_stock_profiles_batch("cn", batch)
            if result:
                for p in result.get("profiles", []):
                    code = p.get("symbol", "").split(".")[0]
                    profiles.append(_profile_from_dict(
                        p,
                        name_zh=p.get("name_zh") or names_map.get(code, ""),
                        concepts=concepts_map.get(code, []),
                    ))
            else:
                # Fallback: create concept-only profiles from mapping data
                failed_batches += 1
                logger.warning(
                    "[StockProfile] CN batch %d-%d failed after retry, "
                    "creating %d concept-only profiles",
                    i, min(i + _BATCH_SIZE, len(codes)), len(batch),
                )
                for code in batch:
                    profiles.append(StockProfile(
                        symbol=code,
                        name="",
                        name_zh=names_map.get(code, ""),
                        concepts=concepts_map.get(code, []),
                    ))
            # Log progress every 500 stocks
            done = min(i + _BATCH_SIZE, len(codes))
            if done % 500 < _BATCH_SIZE or done == len(codes):
                logger.info(
                    "[StockProfile] CN info: %d/%d codes processed, "
                    "%d profiles so far",
                    done, len(codes), len(profiles),
                )
            # Rate limit courtesy between batches
            if i + _BATCH_SIZE < len(codes):
                await asyncio.sleep(2.0)

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] CN collection complete: %d profiles in %.0fs "
            "(failed_batches=%d)",
            len(profiles), elapsed, failed_batches,
        )
        return profiles

    # -----------------------------------------------------------------------
    # US stocks: batched yfinance collection
    # -----------------------------------------------------------------------

    async def collect_us_profiles(self) -> List[StockProfile]:
        """Collect US stock profiles via data-service (granular batching)."""
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Starting US profile collection (granular)")
        t0 = time.monotonic()

        us_symbols = await self._get_symbols_by_market("us")
        if not us_symbols:
            logger.warning("[StockProfile] No US symbols found")
            return []
        us_symbols = us_symbols[:5000]

        client = await get_data_service_client()
        profiles: List[StockProfile] = []
        failed_batches = 0

        for i in range(0, len(us_symbols), _BATCH_SIZE):
            batch = us_symbols[i : i + _BATCH_SIZE]
            result = await client.fetch_stock_profiles_batch("us", batch)
            if not result:
                await asyncio.sleep(5.0)
                result = await client.fetch_stock_profiles_batch("us", batch)
            if result:
                for p in result.get("profiles", []):
                    profiles.append(_profile_from_dict(p, market="us"))
            else:
                failed_batches += 1
                logger.warning(
                    "[StockProfile] US batch %d-%d failed after retry",
                    i, min(i + _BATCH_SIZE, len(us_symbols)),
                )
            # Log progress every 500 symbols
            done = min(i + _BATCH_SIZE, len(us_symbols))
            if done % 500 < _BATCH_SIZE or done == len(us_symbols):
                logger.info(
                    "[StockProfile] US: %d/%d symbols processed, "
                    "%d profiles so far",
                    done, len(us_symbols), len(profiles),
                )
            # yfinance rate limit courtesy between batches
            if i + _BATCH_SIZE < len(us_symbols):
                await asyncio.sleep(3.0)

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] US collection complete: %d profiles in %.0fs "
            "(failed_batches=%d)",
            len(profiles), elapsed, failed_batches,
        )
        return profiles

    # -----------------------------------------------------------------------
    # HK stocks: batched yfinance collection
    # -----------------------------------------------------------------------

    async def collect_hk_profiles(self) -> List[StockProfile]:
        """Collect HK stock profiles via data-service (granular batching)."""
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Starting HK profile collection (granular)")
        t0 = time.monotonic()

        hk_symbols = await self._get_symbols_by_market("hk")
        if not hk_symbols:
            logger.warning("[StockProfile] No HK symbols found")
            return []
        hk_symbols = hk_symbols[:500]

        client = await get_data_service_client()
        profiles: List[StockProfile] = []
        failed_batches = 0

        for i in range(0, len(hk_symbols), _BATCH_SIZE):
            batch = hk_symbols[i : i + _BATCH_SIZE]
            result = await client.fetch_stock_profiles_batch("hk", batch)
            if not result:
                await asyncio.sleep(5.0)
                result = await client.fetch_stock_profiles_batch("hk", batch)
            if result:
                for p in result.get("profiles", []):
                    profiles.append(_profile_from_dict(p, market="hk"))
            else:
                failed_batches += 1
                logger.warning(
                    "[StockProfile] HK batch %d-%d failed after retry",
                    i, min(i + _BATCH_SIZE, len(hk_symbols)),
                )
            # Log progress every 100 symbols
            done = min(i + _BATCH_SIZE, len(hk_symbols))
            if done % 100 < _BATCH_SIZE or done == len(hk_symbols):
                logger.info(
                    "[StockProfile] HK: %d/%d symbols processed, "
                    "%d profiles so far",
                    done, len(hk_symbols), len(profiles),
                )
            # yfinance rate limit courtesy between batches
            if i + _BATCH_SIZE < len(hk_symbols):
                await asyncio.sleep(3.0)

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] HK collection complete: %d profiles in %.0fs "
            "(failed_batches=%d)",
            len(profiles), elapsed, failed_batches,
        )
        return profiles

    # -----------------------------------------------------------------------
    # Concept board mapping (for daily sync) — now uses dedicated endpoint
    # -----------------------------------------------------------------------

    async def collect_cn_concept_mapping(self) -> Dict[str, List[str]]:
        """Collect A-share stock -> concept board mapping only (no individual info).

        Uses the dedicated ``/v1/reference/cn-concept-mapping`` endpoint which
        is much faster than the full profile collection since it only does the
        concept board inversion step (~200s vs hours).

        Returns:
            Dict mapping stock code (6-digit) to list of concept board names.
        """
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Collecting CN concept mapping via data-service")
        t0 = time.monotonic()

        client = await get_data_service_client()
        result = await client.fetch_cn_concept_mapping()

        if not result:
            logger.warning("[StockProfile] CN concept mapping returned no data")
            return {}

        concepts = result.get("concepts", {})
        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] Concept mapping: %d stocks in %.0fs",
            len(concepts), elapsed,
        )
        return concepts

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _get_symbols_by_market(self, market: str) -> List[str]:
        """Get symbol list from StockListService for the given market."""
        try:
            from app.services.stock_list_service import get_stock_list_service

            svc = await get_stock_list_service()
            if not svc or not svc.is_loaded:
                return []
            return [
                s.symbol for s in svc.stocks
                if s.market == market
            ]
        except Exception as e:
            logger.warning(
                "[StockProfile] Failed to get %s symbols: %s", market, e
            )
            return []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: Optional[StockProfileService] = None


def get_stock_profile_service() -> StockProfileService:
    """Get singleton StockProfileService instance."""
    global _service
    if _service is None:
        _service = StockProfileService()
    return _service


async def reset_stock_profile_service() -> None:
    """Reset singleton instance (async variant)."""
    global _service
    _service = None


def reset_stock_profile_service_sync() -> None:
    """Sync reset for Celery singleton cleanup after each task."""
    global _service
    _service = None
