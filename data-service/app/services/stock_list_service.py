"""Stock list data fetching service.

Fetches stock symbols from multiple data sources across all markets:
- US: Finnhub ``client.stock_symbols("US")``
- HK: AKShare ``ak.stock_hk_spot()``
- SH: AKShare ``ak.stock_info_sh_name_code()``
- SZ: AKShare ``ak.stock_info_sz_name_code(symbol="A股列表")``
- BJ: AKShare ``ak.stock_info_bj_name_code()``
- Precious metals: Hardcoded from constants.py

Returns raw dicts; does NOT persist to msgpack or manage in-memory indexes.
The backend Celery task calls this endpoint and handles persistence.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Tuple

from app.config import get_settings
from app.core.executor import run_in_executor
from app.providers.constants import PRECIOUS_METALS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pinyin helpers
# ---------------------------------------------------------------------------

def _get_pinyin(name_zh: str) -> Tuple[str, str]:
    """Generate pinyin from Chinese name.

    Returns:
        Tuple of (full_pinyin, initials), e.g. ("PINGGUO", "PG").
    """
    if not name_zh:
        return "", ""

    try:
        from pypinyin import lazy_pinyin, Style

        full = "".join(lazy_pinyin(name_zh))
        initial = "".join(lazy_pinyin(name_zh, style=Style.FIRST_LETTER))
        return full.upper(), initial.upper()
    except Exception as e:
        logger.warning("Failed to generate pinyin for '%s': %s", name_zh, e)
        return "", ""


# ---------------------------------------------------------------------------
# Individual market fetchers (synchronous — run via executor)
# ---------------------------------------------------------------------------

def _fetch_finnhub_us() -> List[Dict[str, Any]]:
    """Fetch US stock symbols from Finnhub API."""
    import finnhub

    from app.core.api_keys import get_api_key
    api_key = get_api_key("finnhub")
    if not api_key:
        logger.warning("Finnhub API key not configured, skipping US stocks")
        return []

    try:
        client = finnhub.Client(api_key=api_key)
        raw = client.stock_symbols("US")
        logger.info("Fetched %d raw US symbols from Finnhub", len(raw))
        return raw
    except Exception as e:
        logger.error("Failed to fetch US symbols from Finnhub: %s", e)
        return []


def _process_finnhub_symbol(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a single Finnhub symbol dict into the standard stock-list format."""
    symbol = data.get("symbol", "")
    name = data.get("description", "")
    exchange = data.get("mic", "") or data.get("exchange", "")

    # Try to extract Chinese characters from name
    name_zh = ""
    if name:
        match = re.search(r"[\u4e00-\u9fff]+", name)
        if match:
            name_zh = match.group()

    pinyin, pinyin_initial = _get_pinyin(name_zh) if name_zh else ("", "")

    return {
        "symbol": symbol,
        "name": name,
        "name_zh": name_zh,
        "exchange": exchange,
        "market": "us",
        "pinyin": pinyin,
        "pinyin_initial": pinyin_initial,
    }


def _fetch_akshare_sh() -> List[Dict[str, Any]]:
    """Fetch Shanghai A-share symbols from AKShare."""
    try:
        import akshare as ak

        df = ak.stock_info_sh_name_code()
        stocks = []
        for _, row in df.iterrows():
            code = str(row.get("证券代码", "")).strip()
            name_zh = str(row.get("证券简称", "")).strip()
            if not code:
                continue
            pinyin, pinyin_initial = _get_pinyin(name_zh)
            stocks.append({
                "symbol": f"{code}.SS",
                "name": name_zh,
                "name_zh": name_zh,
                "exchange": "SSE",
                "market": "sh",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })
        logger.info("Fetched %d Shanghai stocks from AKShare", len(stocks))
        return stocks
    except Exception as e:
        logger.error("Failed to fetch Shanghai stocks from AKShare: %s", e)
        return []


def _fetch_akshare_sz() -> List[Dict[str, Any]]:
    """Fetch Shenzhen A-share symbols from AKShare."""
    try:
        import akshare as ak

        df = ak.stock_info_sz_name_code(symbol="A股列表")
        stocks = []
        for _, row in df.iterrows():
            code = str(row.get("A股代码", "")).strip()
            name_zh = str(row.get("A股简称", "")).strip()
            if not code:
                continue
            pinyin, pinyin_initial = _get_pinyin(name_zh)
            stocks.append({
                "symbol": f"{code}.SZ",
                "name": name_zh,
                "name_zh": name_zh,
                "exchange": "SZSE",
                "market": "sz",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })
        logger.info("Fetched %d Shenzhen stocks from AKShare", len(stocks))
        return stocks
    except Exception as e:
        logger.error("Failed to fetch Shenzhen stocks from AKShare: %s", e)
        return []


def _fetch_akshare_bj() -> List[Dict[str, Any]]:
    """Fetch Beijing Stock Exchange symbols from AKShare."""
    try:
        import akshare as ak

        df = ak.stock_info_bj_name_code()
        stocks = []
        for _, row in df.iterrows():
            code = str(row.get("证券代码", "")).strip()
            name_zh = str(row.get("证券简称", "")).strip()
            if not code:
                continue
            pinyin, pinyin_initial = _get_pinyin(name_zh)
            stocks.append({
                "symbol": f"{code}.BJ",
                "name": name_zh,
                "name_zh": name_zh,
                "exchange": "BSE",
                "market": "bj",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })
        logger.info("Fetched %d Beijing stocks from AKShare", len(stocks))
        return stocks
    except Exception as e:
        logger.error("Failed to fetch Beijing stocks from AKShare: %s", e)
        return []


def _fetch_akshare_hk() -> List[Dict[str, Any]]:
    """Fetch Hong Kong stock symbols from AKShare (Sina source)."""
    try:
        import akshare as ak

        df = ak.stock_hk_spot()
        stocks = []
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            name_zh = str(row.get("中文名称", "")).strip()
            name_en = str(row.get("英文名称", "")).strip()
            if not code:
                continue
            code_padded = code.zfill(5)
            pinyin, pinyin_initial = _get_pinyin(name_zh)
            stocks.append({
                "symbol": f"{code_padded}.HK",
                "name": name_en if name_en else name_zh,
                "name_zh": name_zh,
                "exchange": "HKEX",
                "market": "hk",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })
        logger.info("Fetched %d Hong Kong stocks from AKShare", len(stocks))
        return stocks
    except Exception as e:
        logger.error("Failed to fetch Hong Kong stocks from AKShare: %s", e)
        return []


def _get_precious_metals() -> List[Dict[str, Any]]:
    """Build stock-list entries for precious metals from constants."""
    metals = []
    for symbol, meta in PRECIOUS_METALS.items():
        pinyin, pinyin_initial = _get_pinyin(meta.get("name_zh", ""))
        metals.append({
            "symbol": symbol,
            "name": meta["name"],
            "name_zh": meta.get("name_zh", ""),
            "exchange": meta["exchange"],
            "market": "metal",
            "pinyin": pinyin,
            "pinyin_initial": pinyin_initial,
        })
    logger.info("Got %d precious metals", len(metals))
    return metals


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def build_stock_list() -> List[Dict[str, Any]]:
    """Fetch stock symbols from all markets in parallel and return deduplicated list.

    Returns:
        List of stock dicts, each with keys:
        symbol, name, name_zh, exchange, market, pinyin, pinyin_initial.
    """
    t0 = time.monotonic()
    logger.info("Starting stock list build from all markets")

    # Run all synchronous fetchers in parallel via the thread-pool executor.
    # Each fetcher has its own 90s timeout to allow for slow AKShare calls.
    results = await asyncio.gather(
        run_in_executor(_fetch_finnhub_us, timeout=90.0),
        run_in_executor(_fetch_akshare_hk, timeout=90.0),
        run_in_executor(_fetch_akshare_sh, timeout=90.0),
        run_in_executor(_fetch_akshare_sz, timeout=90.0),
        run_in_executor(_fetch_akshare_bj, timeout=90.0),
        return_exceptions=True,
    )

    # Unpack, treating exceptions as empty lists
    us_raw: List[Dict[str, Any]] = []
    hk_stocks: List[Dict[str, Any]] = []
    sh_stocks: List[Dict[str, Any]] = []
    sz_stocks: List[Dict[str, Any]] = []
    bj_stocks: List[Dict[str, Any]] = []

    labels = ["US", "HK", "SH", "SZ", "BJ"]
    targets = [us_raw, hk_stocks, sh_stocks, sz_stocks, bj_stocks]

    for i, (label, target) in enumerate(zip(labels, targets)):
        result = results[i]
        if isinstance(result, BaseException):
            logger.error("Fetch %s failed: %s", label, result)
        elif isinstance(result, list):
            target.extend(result)

    # Process US stocks (from Finnhub raw format)
    all_stocks: List[Dict[str, Any]] = []
    for raw_sym in us_raw:
        try:
            stock = _process_finnhub_symbol(raw_sym)
            all_stocks.append(stock)
        except Exception as e:
            logger.warning("Failed to process US symbol %s: %s", raw_sym, e)

    # Add AKShare-sourced stocks (already in standard format)
    all_stocks.extend(hk_stocks)
    all_stocks.extend(sh_stocks)
    all_stocks.extend(sz_stocks)
    all_stocks.extend(bj_stocks)

    # Add precious metals
    all_stocks.extend(_get_precious_metals())

    # Deduplicate by symbol (keep first occurrence)
    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for stock in all_stocks:
        sym = stock["symbol"]
        if sym not in seen:
            seen.add(sym)
            unique.append(stock)

    elapsed = time.monotonic() - t0
    by_market: Dict[str, int] = {}
    for s in unique:
        m = s.get("market", "unknown")
        by_market[m] = by_market.get(m, 0) + 1

    logger.info(
        "Stock list build complete: %d unique symbols in %.1fs — %s",
        len(unique),
        elapsed,
        by_market,
    )
    return unique
