"""Stock profile data collection service for building a vectorized knowledge base.

Collects enriched stock profiles (description, industry, concepts, main business)
from multiple data sources across CN/US/HK markets. Each profile is converted to
an embedding-friendly text string via ``to_embedding_text()``.

Data sources:
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

# Maximum consecutive 429 errors before aborting the current market collection
_MAX_429_STREAK = 10


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception signals HTTP 429 Too Many Requests."""
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return True
    # finnhub-python raises FinnhubAPIException with status_code
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    return False


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
    """Collects stock profile data across CN/US/HK markets.

    Uses existing ``StockListService`` for symbol lists and wraps each
    market's data source with appropriate rate limiting.
    """

    # -----------------------------------------------------------------------
    # A-shares: concept board inversion + individual info
    # -----------------------------------------------------------------------

    async def collect_cn_profiles(self) -> List[StockProfile]:
        """Collect A-share profiles via akshare concept boards + stock info.

        Strategy:
        1. Fetch all concept board names (~400 boards)
        2. For each board, fetch constituent stocks → invert to stock→concepts mapping
        3. For each stock, fetch basic info (description, industry, sector)

        Rate limiting: Semaphore(3) + 1s delay between akshare calls.
        """
        import akshare as ak

        logger.info("[StockProfile] Starting CN profile collection")
        t0 = time.monotonic()

        # Step 1: Fetch concept board list
        try:
            boards_df = await asyncio.to_thread( ak.stock_board_concept_name_em
            )
        except Exception as e:
            logger.error("[StockProfile] Failed to fetch concept boards: %s", e)
            return []

        if boards_df is None or boards_df.empty:
            logger.warning("[StockProfile] Empty concept board list")
            return []

        board_names = boards_df["板块名称"].tolist()
        logger.info("[StockProfile] Found %d concept boards", len(board_names))

        # Step 2: Invert concept boards → stock→concepts mapping
        stock_concepts: Dict[str, Set[str]] = {}  # code → set of concept names
        stock_names: Dict[str, str] = {}  # code → name_zh
        sem = asyncio.Semaphore(3)
        errors = 0

        rate_limit_streak = 0

        async def fetch_board_stocks(board_name: str):
            nonlocal errors, rate_limit_streak
            async with sem:
                for attempt in range(3):
                    try:
                        df = await asyncio.to_thread(
                            lambda: ak.stock_board_concept_cons_em(symbol=board_name)
                        )
                        rate_limit_streak = 0
                        if df is not None and not df.empty:
                            for _, row in df.iterrows():
                                code = str(row.get("代码", "")).strip()
                                name = str(row.get("名称", "")).strip()
                                if code and len(code) == 6:
                                    stock_concepts.setdefault(code, set()).add(board_name)
                                    if name and code not in stock_names:
                                        stock_names[code] = name
                        break
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            rate_limit_streak += 1
                            wait = 30 * (attempt + 1)
                            logger.warning(
                                "[StockProfile] 429 on board '%s' (streak=%d), "
                                "waiting %ds...",
                                board_name, rate_limit_streak, wait,
                            )
                            if rate_limit_streak >= _MAX_429_STREAK:
                                logger.error(
                                    "[StockProfile] Too many 429s, aborting CN boards"
                                )
                                return
                            await asyncio.sleep(wait)
                            continue
                        errors += 1
                        if errors <= 5:
                            logger.warning(
                                "[StockProfile] Error fetching board '%s': %s",
                                board_name, e,
                            )
                        break
                await asyncio.sleep(1.0)

        # Process boards in batches
        batch_size = 20
        for i in range(0, len(board_names), batch_size):
            batch = board_names[i : i + batch_size]
            await asyncio.gather(
                *[fetch_board_stocks(b) for b in batch],
                return_exceptions=True,
            )
            if i % 100 == 0 and i > 0:
                logger.info(
                    "[StockProfile] Processed %d/%d boards, %d stocks so far",
                    i, len(board_names), len(stock_concepts),
                )

        logger.info(
            "[StockProfile] Concept board mapping complete: %d stocks, %d errors",
            len(stock_concepts), errors,
        )

        # Step 3: Fetch individual stock info for each stock
        profiles: List[StockProfile] = []
        info_sem = asyncio.Semaphore(3)
        info_errors = 0

        info_429_streak = 0

        async def fetch_stock_info(code: str, concepts: Set[str]):
            nonlocal info_errors, info_429_streak
            async with info_sem:
                # Determine market suffix
                if code.startswith(("6", "9")):
                    suffix = ".SS"
                    market = "sh"
                elif code.startswith(("0", "2", "3")):
                    suffix = ".SZ"
                    market = "sz"
                else:
                    suffix = ".SS"
                    market = "sh"

                symbol = f"{code}{suffix}"
                name_zh = stock_names.get(code, "")

                profile = StockProfile(
                    symbol=symbol,
                    name="",
                    name_zh=name_zh,
                    market=market,
                    concepts=sorted(concepts),
                )

                for attempt in range(3):
                    try:
                        df = await asyncio.to_thread(
                            lambda c=code: ak.stock_individual_info_em(symbol=c)
                        )
                        info_429_streak = 0
                        if df is not None and not df.empty:
                            info = {}
                            for _, row in df.iterrows():
                                info[row["item"]] = row["value"]
                            profile.description = str(info.get("经营范围", ""))[:500]
                            profile.industry = str(info.get("行业", ""))
                            profile.sector = str(info.get("行业", ""))
                            profile.name = str(info.get("股票简称", ""))
                            if not profile.name_zh:
                                profile.name_zh = profile.name
                            profile.main_business = str(info.get("经营范围", ""))[:300]
                        break
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            info_429_streak += 1
                            wait = 30 * (attempt + 1)
                            logger.warning(
                                "[StockProfile] 429 on stock info %s (streak=%d), "
                                "waiting %ds...",
                                code, info_429_streak, wait,
                            )
                            if info_429_streak >= _MAX_429_STREAK:
                                logger.error(
                                    "[StockProfile] Too many 429s, aborting CN info"
                                )
                                profiles.append(profile)
                                return
                            await asyncio.sleep(wait)
                            continue
                        info_errors += 1
                        if info_errors <= 10:
                            logger.warning(
                                "[StockProfile] Error fetching info for %s: %s",
                                code, e,
                            )
                        break

                profiles.append(profile)
                await asyncio.sleep(1.0)

        # Process stocks in batches
        stock_items = list(stock_concepts.items())
        batch_size = 20
        for i in range(0, len(stock_items), batch_size):
            batch = stock_items[i : i + batch_size]
            await asyncio.gather(
                *[fetch_stock_info(code, concepts) for code, concepts in batch],
                return_exceptions=True,
            )
            if i % 200 == 0 and i > 0:
                logger.info(
                    "[StockProfile] Fetched info for %d/%d CN stocks",
                    i, len(stock_items),
                )

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] CN collection complete: %d profiles in %.0fs "
            "(info_errors=%d)",
            len(profiles), elapsed, info_errors,
        )
        return profiles

    # -----------------------------------------------------------------------
    # US stocks: Finnhub company profiles
    # -----------------------------------------------------------------------

    async def collect_us_profiles(self) -> List[StockProfile]:
        """Collect US stock profiles via yfinance.

        yfinance provides industry, sector, and business description without
        requiring an API key, and allows higher concurrency than Finnhub.
        Rate limiting: Semaphore(10) + 0.3s delay.
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("[StockProfile] yfinance not installed, skipping US")
            return []

        logger.info("[StockProfile] Starting US profile collection (yfinance)")
        t0 = time.monotonic()

        # Get US symbols from stock list service
        us_symbols = await self._get_symbols_by_market("us")
        if not us_symbols:
            logger.warning("[StockProfile] No US symbols found")
            return []

        # Limit to 5000 symbols
        symbols = us_symbols[:5000]
        logger.info("[StockProfile] Collecting profiles for %d US stocks", len(symbols))

        profiles: List[StockProfile] = []
        sem = asyncio.Semaphore(10)
        errors = 0

        us_429_streak = 0

        async def fetch_profile(symbol: str):
            nonlocal errors, us_429_streak
            async with sem:
                for attempt in range(3):
                    try:
                        def _get_info():
                            ticker = yf.Ticker(symbol)
                            return ticker.info

                        info = await asyncio.to_thread(_get_info)
                        us_429_streak = 0
                        if info and isinstance(info, dict):
                            name = info.get("shortName") or info.get("longName", "")
                            if name:
                                profiles.append(StockProfile(
                                    symbol=symbol,
                                    name=name,
                                    market="us",
                                    industry=info.get("industry", ""),
                                    sector=info.get("sector", ""),
                                    description=(
                                        info.get("longBusinessSummary", "")[:500]
                                    ),
                                ))
                        break
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            us_429_streak += 1
                            wait = 30 * (attempt + 1)
                            logger.warning(
                                "[StockProfile] yfinance 429 for %s (streak=%d), "
                                "waiting %ds...",
                                symbol, us_429_streak, wait,
                            )
                            if us_429_streak >= _MAX_429_STREAK:
                                logger.error(
                                    "[StockProfile] Too many yfinance 429s, "
                                    "aborting US collection"
                                )
                                return
                            await asyncio.sleep(wait)
                            continue
                        errors += 1
                        if errors <= 10:
                            logger.warning(
                                "[StockProfile] yfinance error for %s: %s",
                                symbol, e,
                            )
                        break
                await asyncio.sleep(0.3)

        # Process in batches
        batch_size = 20
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            await asyncio.gather(
                *[fetch_profile(s) for s in batch],
                return_exceptions=True,
            )
            if i % 500 == 0 and i > 0:
                logger.info(
                    "[StockProfile] Fetched %d/%d US profiles (%d errors)",
                    len(profiles), i, errors,
                )

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] US collection complete: %d profiles in %.0fs "
            "(errors=%d)",
            len(profiles), elapsed, errors,
        )
        return profiles

    # -----------------------------------------------------------------------
    # HK stocks: yfinance Ticker info (major stocks only)
    # -----------------------------------------------------------------------

    async def collect_hk_profiles(self) -> List[StockProfile]:
        """Collect HK stock profiles via yfinance.

        Only processes major HK stocks (~400) since yfinance is slow.
        Rate limiting: Semaphore(5) + 0.5s delay.
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("[StockProfile] yfinance not installed, skipping HK")
            return []

        logger.info("[StockProfile] Starting HK profile collection")
        t0 = time.monotonic()

        # Get HK symbols — prefer HSI constituents + other major stocks
        hk_symbols = await self._get_symbols_by_market("hk")
        if not hk_symbols:
            logger.warning("[StockProfile] No HK symbols found")
            return []

        # Limit to major stocks
        # yfinance uses 4-digit HK codes (0700.HK), stock list uses 5-digit (00700.HK)
        # Build mapping: yfinance_symbol → canonical_symbol
        raw_symbols = hk_symbols[:500]
        yf_to_canonical = {}
        symbols = []
        for s in raw_symbols:
            code, _, suffix = s.partition(".")
            yf_code = code.lstrip("0").zfill(4)  # 00700 → 0700, 09988 → 9988
            yf_sym = f"{yf_code}.{suffix}" if suffix else yf_code
            yf_to_canonical[yf_sym] = s  # 0700.HK → 00700.HK
            symbols.append(yf_sym)
        logger.info("[StockProfile] Collecting profiles for %d HK stocks", len(symbols))

        profiles: List[StockProfile] = []
        sem = asyncio.Semaphore(5)
        errors = 0

        hk_429_streak = 0

        async def fetch_profile(symbol: str):
            nonlocal errors, hk_429_streak
            async with sem:
                for attempt in range(3):
                    try:
                        def _get_info():
                            ticker = yf.Ticker(symbol)
                            return ticker.info

                        info = await asyncio.to_thread(_get_info)
                        hk_429_streak = 0
                        if info and isinstance(info, dict):
                            name = info.get("shortName") or info.get("longName", "")
                            if name:
                                # Store with canonical symbol (00700.HK not 0700.HK)
                                canonical = yf_to_canonical.get(symbol, symbol)
                                profiles.append(StockProfile(
                                    symbol=canonical,
                                    name=name,
                                    name_zh="",
                                    market="hk",
                                    industry=info.get("industry", ""),
                                    sector=info.get("sector", ""),
                                    description=(
                                        info.get("longBusinessSummary", "")[:500]
                                    ),
                                ))
                        break
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            hk_429_streak += 1
                            wait = 30 * (attempt + 1)
                            logger.warning(
                                "[StockProfile] yfinance 429 for %s (streak=%d), "
                                "waiting %ds...",
                                symbol, hk_429_streak, wait,
                            )
                            if hk_429_streak >= _MAX_429_STREAK:
                                logger.error(
                                    "[StockProfile] Too many yfinance 429s, "
                                    "aborting HK collection"
                                )
                                return
                            await asyncio.sleep(wait)
                            continue
                        errors += 1
                        if errors <= 10:
                            logger.warning(
                                "[StockProfile] yfinance error for %s: %s",
                                symbol, e,
                            )
                        break
                await asyncio.sleep(0.5)

        # Process in batches
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            await asyncio.gather(
                *[fetch_profile(s) for s in batch],
                return_exceptions=True,
            )
            if i % 100 == 0 and i > 0:
                logger.info(
                    "[StockProfile] Fetched %d/%d HK profiles (%d errors)",
                    len(profiles), i, errors,
                )

        elapsed = time.monotonic() - t0
        logger.info(
            "[StockProfile] HK collection complete: %d profiles in %.0fs "
            "(errors=%d)",
            len(profiles), elapsed, errors,
        )
        return profiles

    # -----------------------------------------------------------------------
    # Concept board mapping (for daily sync)
    # -----------------------------------------------------------------------

    async def collect_cn_concept_mapping(self) -> Dict[str, List[str]]:
        """Collect A-share stock → concept board mapping only (no individual info).

        Lighter-weight than full ``collect_cn_profiles()``, used for the
        daily concept sync task to detect changed stocks.

        Returns:
            Dict mapping stock code (6-digit) to list of concept board names.
        """
        import akshare as ak

        logger.info("[StockProfile] Collecting CN concept mapping")

        try:
            boards_df = await asyncio.to_thread( ak.stock_board_concept_name_em
            )
        except Exception as e:
            logger.error("[StockProfile] Failed to fetch concept boards: %s", e)
            return {}

        if boards_df is None or boards_df.empty:
            return {}

        board_names = boards_df["板块名称"].tolist()
        stock_concepts: Dict[str, Set[str]] = {}
        sem = asyncio.Semaphore(3)
        errors = 0

        rate_limit_streak = 0

        async def fetch_board(board_name: str):
            nonlocal errors, rate_limit_streak
            async with sem:
                for attempt in range(3):
                    try:
                        df = await asyncio.to_thread(
                            lambda: ak.stock_board_concept_cons_em(symbol=board_name)
                        )
                        rate_limit_streak = 0
                        if df is not None and not df.empty:
                            for _, row in df.iterrows():
                                code = str(row.get("代码", "")).strip()
                                if code and len(code) == 6:
                                    stock_concepts.setdefault(code, set()).add(board_name)
                        break
                    except Exception as e:
                        if _is_rate_limit_error(e):
                            rate_limit_streak += 1
                            wait = 30 * (attempt + 1)
                            logger.warning(
                                "[StockProfile] 429 on board '%s' (streak=%d), "
                                "waiting %ds...",
                                board_name, rate_limit_streak, wait,
                            )
                            if rate_limit_streak >= _MAX_429_STREAK:
                                logger.error(
                                    "[StockProfile] Too many 429s in concept sync"
                                )
                                return
                            await asyncio.sleep(wait)
                            continue
                        errors += 1
                        if errors <= 5:
                            logger.warning(
                                "[StockProfile] Board fetch error '%s': %s",
                                board_name, e,
                            )
                        break
                await asyncio.sleep(1.0)

        batch_size = 20
        for i in range(0, len(board_names), batch_size):
            batch = board_names[i : i + batch_size]
            await asyncio.gather(
                *[fetch_board(b) for b in batch], return_exceptions=True
            )

        # Convert sets to sorted lists
        return {code: sorted(concepts) for code, concepts in stock_concepts.items()}

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
