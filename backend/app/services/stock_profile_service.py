"""Stock profile data collection service for building a vectorized knowledge base.

Collects enriched stock profiles (description, industry, concepts, main business)
from the data-service microservice across CN/US/HK markets. Each profile is
converted to an embedding-friendly text string via ``to_embedding_text()``.

Data sources (handled by data-service):
- A-shares (CN): akshare concept boards (inverted mapping) + individual stock info
- US stocks: yfinance Ticker info (industry, sector, description)
- HK stocks: yfinance Ticker info (major stocks only)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


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


class StockProfileService:
    """Collects stock profile data across CN/US/HK markets via data-service.

    Uses ``DataServiceClient.collect_profiles()`` to delegate the actual data
    fetching (akshare, yfinance) to the data-service microservice.
    """

    # -----------------------------------------------------------------------
    # A-shares: concept board inversion + individual info
    # -----------------------------------------------------------------------

    async def collect_cn_profiles(self) -> List[StockProfile]:
        """Collect A-share profiles via data-service.

        The data-service handles akshare concept board inversion and individual
        stock info fetching internally.
        """
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Starting CN profile collection via data-service")
        t0 = time.monotonic()

        client = await get_data_service_client()
        result = await client.collect_profiles("cn")

        if not result:
            logger.warning("[StockProfile] CN collection returned no data")
            return []

        profiles_data = result.get("profiles", [])
        profiles = [
            StockProfile(
                symbol=p.get("symbol", ""),
                name=p.get("name", ""),
                name_zh=p.get("name_zh", ""),
                market=p.get("market", ""),
                description=p.get("description", ""),
                industry=p.get("industry", ""),
                sector=p.get("sector", ""),
                concepts=p.get("concepts", []),
                main_business=p.get("main_business", ""),
            )
            for p in profiles_data
        ]

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] CN collection complete: %d profiles in %.0fs",
            len(profiles), elapsed,
        )
        return profiles

    # -----------------------------------------------------------------------
    # US stocks
    # -----------------------------------------------------------------------

    async def collect_us_profiles(self) -> List[StockProfile]:
        """Collect US stock profiles via data-service."""
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Starting US profile collection via data-service")
        t0 = time.monotonic()

        # Get US symbols from stock list service for the request
        us_symbols = await self._get_symbols_by_market("us")

        client = await get_data_service_client()
        result = await client.collect_profiles("us", symbols=us_symbols[:5000])

        if not result:
            logger.warning("[StockProfile] US collection returned no data")
            return []

        profiles_data = result.get("profiles", [])
        profiles = [
            StockProfile(
                symbol=p.get("symbol", ""),
                name=p.get("name", ""),
                name_zh=p.get("name_zh", ""),
                market=p.get("market", "us"),
                description=p.get("description", ""),
                industry=p.get("industry", ""),
                sector=p.get("sector", ""),
            )
            for p in profiles_data
        ]

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] US collection complete: %d profiles in %.0fs",
            len(profiles), elapsed,
        )
        return profiles

    # -----------------------------------------------------------------------
    # HK stocks
    # -----------------------------------------------------------------------

    async def collect_hk_profiles(self) -> List[StockProfile]:
        """Collect HK stock profiles via data-service."""
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Starting HK profile collection via data-service")
        t0 = time.monotonic()

        # Get HK symbols from stock list service
        hk_symbols = await self._get_symbols_by_market("hk")

        client = await get_data_service_client()
        result = await client.collect_profiles("hk", symbols=hk_symbols[:500])

        if not result:
            logger.warning("[StockProfile] HK collection returned no data")
            return []

        profiles_data = result.get("profiles", [])
        profiles = [
            StockProfile(
                symbol=p.get("symbol", ""),
                name=p.get("name", ""),
                name_zh=p.get("name_zh", ""),
                market=p.get("market", "hk"),
                description=p.get("description", ""),
                industry=p.get("industry", ""),
                sector=p.get("sector", ""),
            )
            for p in profiles_data
        ]

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] HK collection complete: %d profiles in %.0fs",
            len(profiles), elapsed,
        )
        return profiles

    # -----------------------------------------------------------------------
    # Concept board mapping (for daily sync)
    # -----------------------------------------------------------------------

    async def collect_cn_concept_mapping(self) -> Dict[str, List[str]]:
        """Collect A-share stock -> concept board mapping only (no individual info).

        Lighter-weight than full ``collect_cn_profiles()``, used for the
        daily concept sync task to detect changed stocks.

        Returns:
            Dict mapping stock code (6-digit) to list of concept board names.
        """
        from app.services.data_service_client import get_data_service_client

        logger.info("[StockProfile] Collecting CN concept mapping via data-service")

        client = await get_data_service_client()
        # Use collect_profiles with CN market but only extract concept mapping
        result = await client.collect_profiles("cn")

        if not result:
            logger.warning("[StockProfile] CN concept mapping returned no data")
            return {}

        profiles_data = result.get("profiles", [])
        mapping: Dict[str, List[str]] = {}
        for p in profiles_data:
            symbol = p.get("symbol", "")
            code = symbol.split(".")[0] if "." in symbol else symbol
            concepts = p.get("concepts", [])
            if code and concepts:
                mapping[code] = concepts

        logger.info(
            "[StockProfile] Concept mapping: %d stocks with concepts", len(mapping)
        )
        return mapping

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
