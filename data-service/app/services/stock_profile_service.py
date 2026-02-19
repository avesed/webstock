"""Stock profile data collection service for knowledge base construction.

Collects enriched stock profiles from multiple data sources:
- A-shares (CN): akshare concept boards (inverted mapping) + individual stock info
- US stocks: yfinance Ticker info (industry, sector, description)
- HK stocks: yfinance Ticker info (major stocks only)

Returns raw dicts matching the StockProfileData model. Does NOT perform
embedding — the backend handles that step.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

from app.core.executor import run_in_executor

logger = logging.getLogger(__name__)

# Maximum consecutive 429 errors before aborting the current market collection
_MAX_429_STREAK = 10


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception signals HTTP 429 Too Many Requests."""
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    return False


# ---------------------------------------------------------------------------
# CN profiles: concept boards + individual stock info
# ---------------------------------------------------------------------------

async def collect_cn_profiles() -> List[Dict[str, Any]]:
    """Collect A-share profiles via akshare concept boards + stock info.

    Strategy:
    1. Fetch all concept board names (~400 boards)
    2. For each board, fetch constituent stocks -> invert to stock->concepts mapping
    3. For each stock, fetch basic info (description, industry, sector)

    Rate limiting: Semaphore(3) + 1s delay between akshare calls.

    Returns:
        List of profile dicts with keys:
        symbol, market, name, name_zh, sector, industry, concepts, main_business, description.
    """
    import akshare as ak

    logger.info("[StockProfile] Starting CN profile collection")
    t0 = time.monotonic()

    # Step 1: Fetch concept board list
    try:
        boards_df = await run_in_executor(
            ak.stock_board_concept_name_em, timeout=60.0
        )
    except Exception as e:
        logger.error("[StockProfile] Failed to fetch concept boards: %s", e)
        return []

    if boards_df is None or boards_df.empty:
        logger.warning("[StockProfile] Empty concept board list")
        return []

    board_names = boards_df["板块名称"].tolist()
    logger.info("[StockProfile] Found %d concept boards", len(board_names))

    # Step 2: Invert concept boards -> stock->concepts mapping
    stock_concepts: Dict[str, Set[str]] = {}  # code -> set of concept names
    stock_names: Dict[str, str] = {}  # code -> name_zh
    sem = asyncio.Semaphore(3)
    errors = 0
    rate_limit_streak = 0
    abort_event = asyncio.Event()  # signal all tasks to stop on rate-limit flood

    async def fetch_board_stocks(board_name: str) -> None:
        nonlocal errors, rate_limit_streak
        if abort_event.is_set():
            return
        async with sem:
            if abort_event.is_set():
                return
            for attempt in range(3):
                try:
                    df = await run_in_executor(
                        lambda bn=board_name: ak.stock_board_concept_cons_em(symbol=bn),
                        timeout=30.0,
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
                            abort_event.set()
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
    profiles: List[Dict[str, Any]] = []
    info_sem = asyncio.Semaphore(3)
    info_errors = 0
    info_429_streak = 0
    info_abort = asyncio.Event()

    async def fetch_stock_info(code: str, concepts: Set[str]) -> None:
        nonlocal info_errors, info_429_streak
        if info_abort.is_set():
            return
        async with info_sem:
            if info_abort.is_set():
                return
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

            profile: Dict[str, Any] = {
                "symbol": symbol,
                "market": market,
                "name": "",
                "name_zh": name_zh,
                "sector": "",
                "industry": "",
                "concepts": sorted(concepts),
                "main_business": "",
                "description": "",
            }

            for attempt in range(3):
                try:
                    df = await run_in_executor(
                        lambda c=code: ak.stock_individual_info_em(symbol=c),
                        timeout=30.0,
                    )
                    info_429_streak = 0
                    if df is not None and not df.empty:
                        info: Dict[str, str] = {}
                        for _, row in df.iterrows():
                            info[row["item"]] = row["value"]
                        biz_scope = str(info.get("经营范围", ""))[:500]
                        profile["main_business"] = biz_scope
                        profile["description"] = biz_scope
                        profile["industry"] = str(info.get("行业", ""))
                        profile["sector"] = str(info.get("行业", ""))
                        profile["name"] = str(info.get("股票简称", ""))
                        if not profile["name_zh"]:
                            profile["name_zh"] = profile["name"]
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
                            info_abort.set()
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


# ---------------------------------------------------------------------------
# US profiles: yfinance Ticker info
# ---------------------------------------------------------------------------

async def collect_us_profiles(symbols: List[str]) -> List[Dict[str, Any]]:
    """Collect US stock profiles via yfinance.

    Args:
        symbols: List of US stock symbols (e.g. ["AAPL", "MSFT"]).
                 Capped at 5000 internally.

    Returns:
        List of profile dicts with keys:
        symbol, market, name, name_zh, sector, industry, concepts, main_business, description.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[StockProfile] yfinance not installed, skipping US")
        return []

    symbols = symbols[:5000]
    logger.info("[StockProfile] Starting US profile collection for %d symbols", len(symbols))
    t0 = time.monotonic()

    profiles: List[Dict[str, Any]] = []
    sem = asyncio.Semaphore(10)
    errors = 0
    streak_429 = 0
    abort_event = asyncio.Event()

    async def fetch_profile(symbol: str) -> None:
        nonlocal errors, streak_429
        if abort_event.is_set():
            return
        async with sem:
            if abort_event.is_set():
                return
            for attempt in range(3):
                try:
                    def _get_info(s: str = symbol) -> Optional[Dict[str, Any]]:
                        ticker = yf.Ticker(s)
                        return ticker.info

                    info = await run_in_executor(_get_info, timeout=30.0)
                    streak_429 = 0
                    if info and isinstance(info, dict):
                        name = info.get("shortName") or info.get("longName", "")
                        if name:
                            biz_summary = info.get("longBusinessSummary", "")[:500]
                            profiles.append({
                                "symbol": symbol,
                                "market": "us",
                                "name": name,
                                "name_zh": "",
                                "sector": info.get("sector", ""),
                                "industry": info.get("industry", ""),
                                "concepts": [],
                                "main_business": biz_summary,
                                "description": biz_summary,
                            })
                    break
                except Exception as e:
                    if _is_rate_limit_error(e):
                        streak_429 += 1
                        wait = 30 * (attempt + 1)
                        logger.warning(
                            "[StockProfile] yfinance 429 for %s (streak=%d), "
                            "waiting %ds...",
                            symbol, streak_429, wait,
                        )
                        if streak_429 >= _MAX_429_STREAK:
                            logger.error(
                                "[StockProfile] Too many yfinance 429s, "
                                "aborting US collection"
                            )
                            abort_event.set()
                            return
                        await asyncio.sleep(wait)
                        continue
                    errors += 1
                    if errors <= 10:
                        logger.warning(
                            "[StockProfile] yfinance error for %s: %s", symbol, e,
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
        "[StockProfile] US collection complete: %d profiles in %.0fs (errors=%d)",
        len(profiles), elapsed, errors,
    )
    return profiles


# ---------------------------------------------------------------------------
# HK profiles: yfinance Ticker info
# ---------------------------------------------------------------------------

async def collect_hk_profiles(symbols: List[str]) -> List[Dict[str, Any]]:
    """Collect HK stock profiles via yfinance.

    Args:
        symbols: List of HK stock symbols in canonical format (e.g. ["00700.HK"]).
                 Capped at 500 internally.

    Returns:
        List of profile dicts with keys:
        symbol, market, name, name_zh, sector, industry, concepts, main_business, description.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[StockProfile] yfinance not installed, skipping HK")
        return []

    symbols = symbols[:500]
    logger.info("[StockProfile] Starting HK profile collection for %d symbols", len(symbols))
    t0 = time.monotonic()

    # yfinance uses 4-digit HK codes (0700.HK), stock list uses 5-digit (00700.HK)
    # Build mapping: yfinance_symbol -> canonical_symbol
    yf_to_canonical: Dict[str, str] = {}
    yf_symbols: List[str] = []
    for s in symbols:
        code, _, suffix = s.partition(".")
        yf_code = code.lstrip("0").zfill(4)  # 00700 -> 0700, 09988 -> 9988
        yf_sym = f"{yf_code}.{suffix}" if suffix else yf_code
        yf_to_canonical[yf_sym] = s
        yf_symbols.append(yf_sym)

    profiles: List[Dict[str, Any]] = []
    sem = asyncio.Semaphore(5)
    errors = 0
    streak_429 = 0
    abort_event = asyncio.Event()

    async def fetch_profile(yf_symbol: str) -> None:
        nonlocal errors, streak_429
        if abort_event.is_set():
            return
        async with sem:
            if abort_event.is_set():
                return
            for attempt in range(3):
                try:
                    def _get_info(s: str = yf_symbol) -> Optional[Dict[str, Any]]:
                        ticker = yf.Ticker(s)
                        return ticker.info

                    info = await run_in_executor(_get_info, timeout=30.0)
                    streak_429 = 0
                    if info and isinstance(info, dict):
                        name = info.get("shortName") or info.get("longName", "")
                        if name:
                            canonical = yf_to_canonical.get(yf_symbol, yf_symbol)
                            biz_summary = info.get("longBusinessSummary", "")[:500]
                            profiles.append({
                                "symbol": canonical,
                                "market": "hk",
                                "name": name,
                                "name_zh": "",
                                "sector": info.get("sector", ""),
                                "industry": info.get("industry", ""),
                                "concepts": [],
                                "main_business": biz_summary,
                                "description": biz_summary,
                            })
                    break
                except Exception as e:
                    if _is_rate_limit_error(e):
                        streak_429 += 1
                        wait = 30 * (attempt + 1)
                        logger.warning(
                            "[StockProfile] yfinance 429 for %s (streak=%d), "
                            "waiting %ds...",
                            yf_symbol, streak_429, wait,
                        )
                        if streak_429 >= _MAX_429_STREAK:
                            logger.error(
                                "[StockProfile] Too many yfinance 429s, "
                                "aborting HK collection"
                            )
                            abort_event.set()
                            return
                        await asyncio.sleep(wait)
                        continue
                    errors += 1
                    if errors <= 10:
                        logger.warning(
                            "[StockProfile] yfinance error for %s: %s",
                            yf_symbol, e,
                        )
                    break
            await asyncio.sleep(0.5)

    # Process in batches
    batch_size = 10
    for i in range(0, len(yf_symbols), batch_size):
        batch = yf_symbols[i : i + batch_size]
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
        "[StockProfile] HK collection complete: %d profiles in %.0fs (errors=%d)",
        len(profiles), elapsed, errors,
    )
    return profiles
