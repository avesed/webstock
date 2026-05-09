"""Stock profile data collection service for building a vectorized knowledge base.

Collects enriched stock profiles (description, industry, concepts, main business)
from the StockPulse external data platform across CN/US/HK markets. Each
profile is converted to an embedding-friendly text string via
``to_embedding_text()``.

Uses **granular batch endpoints** in StockPulse (max 50 symbols per HTTP call)
to avoid HTTP timeout issues with large markets:
- US/HK: batched yfinance collection (N calls of 50 symbols each)

Data sources (handled by StockPulse):
- US stocks: yfinance Ticker info (industry, sector, description)
- HK stocks: yfinance Ticker info (major stocks only)

NOTE: CN profile collection used to bootstrap from a concept-board mapping
fetched via ``fetch_cn_concept_mapping`` on the data-service. That control
endpoint was removed during the StockPulse migration — the StockPulse admin
UI now owns concept-board collection and the resulting profiles are exposed
through ``fetch_stock_profiles_batch`` directly. ``collect_cn_profiles`` is
therefore a no-op and ``collect_cn_concept_mapping`` has been removed.
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
        """Collect A-share profiles.

        TODO: CN profile collection not yet implemented. Needs a
        listable inventory endpoint from StockPulse or a CN-aware
        ``_get_symbols_by_market("cn")`` source.
        """
        logger.warning(
            "[StockProfile] collect_cn_profiles is currently a no-op: "
            "control plane for CN concept mapping moved to StockPulse "
            "admin UI; falling back to empty profile set."
        )
        return []

    # -----------------------------------------------------------------------
    # US stocks: batched yfinance collection
    # -----------------------------------------------------------------------

    async def collect_us_profiles(self) -> List[StockProfile]:
        """Collect US stock profiles via StockPulse (granular batching)."""
        from app.services.stockpulse_client import get_stockpulse_client

        logger.info("[StockProfile] Starting US profile collection (granular)")
        t0 = time.monotonic()

        us_symbols = await self._get_symbols_by_market("us")
        if not us_symbols:
            logger.warning("[StockProfile] No US symbols found")
            return []
        us_symbols = us_symbols[:5000]

        client = await get_stockpulse_client()
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
        """Collect HK stock profiles via StockPulse (granular batching)."""
        from app.services.stockpulse_client import get_stockpulse_client

        logger.info("[StockProfile] Starting HK profile collection (granular)")
        t0 = time.monotonic()

        hk_symbols = await self._get_symbols_by_market("hk")
        if not hk_symbols:
            logger.warning("[StockProfile] No HK symbols found")
            return []
        hk_symbols = hk_symbols[:500]

        client = await get_stockpulse_client()
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
    # Concept board mapping
    #
    # TODO: removed in StockPulse migration. The previous
    # ``collect_cn_concept_mapping`` method called the data-service control
    # endpoint ``/v1/reference/cn-concept-mapping`` to fetch a full
    # akshare-driven inversion of A-share concept boards. That control plane
    # has been moved into the StockPulse admin UI; backend-side scheduled
    # syncs no longer have a way to bootstrap the mapping. The concept-board
    # daily-sync Celery task (`worker.tasks.stock_profile_tasks.sync_concept_boards`)
    # should be reviewed and either removed or re-pointed at a future
    # StockPulse public endpoint that exposes pre-collected concept data.
    # -----------------------------------------------------------------------

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
