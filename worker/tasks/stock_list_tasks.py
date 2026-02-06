"""Stock list update Celery tasks.

This task fetches stock lists from Finnhub API and updates the local stock list
for fast in-memory search functionality.
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Thread pool for parallel API calls
_executor: Optional[ThreadPoolExecutor] = None


def _get_executor() -> ThreadPoolExecutor:
    """Get or create thread pool executor."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4)
    return _executor


def get_pinyin(name_zh: str) -> Tuple[str, str]:
    """
    Generate pinyin from Chinese name.

    Args:
        name_zh: Chinese name string

    Returns:
        Tuple of (full_pinyin, initials)
        e.g., ("PINGGUO", "PG")
    """
    if not name_zh:
        return "", ""

    try:
        from pypinyin import lazy_pinyin, Style

        # Get full pinyin
        full = "".join(lazy_pinyin(name_zh))
        # Get first letter of each character
        initial = "".join(lazy_pinyin(name_zh, style=Style.FIRST_LETTER))

        return full.upper(), initial.upper()
    except Exception as e:
        logger.warning(f"Failed to generate pinyin for '{name_zh}': {e}")
        return "", ""


# Cached Finnhub API key (fetched once per worker process)
_cached_finnhub_api_key: Optional[str] = None
_finnhub_key_fetched: bool = False


def _get_finnhub_api_key() -> Optional[str]:
    """
    Get Finnhub API key from environment or user settings.

    Priority:
    1. Environment variable FINNHUB_API_KEY
    2. First user setting with finnhub_api_key configured

    The key is cached after first successful retrieval to avoid
    repeated database queries and async event loop issues.

    Returns:
        API key string or None if not found
    """
    global _cached_finnhub_api_key, _finnhub_key_fetched

    # Return cached key if already fetched
    if _finnhub_key_fetched:
        return _cached_finnhub_api_key

    # First try environment variable
    api_key = os.environ.get("FINNHUB_API_KEY")
    if api_key:
        _cached_finnhub_api_key = api_key
        _finnhub_key_fetched = True
        return api_key

    # Then try user settings from database using synchronous query
    try:
        from sqlalchemy import create_engine, select, text
        from sqlalchemy.orm import Session
        from app.config import settings
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        # Convert async URL to sync URL
        sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")

        # Remove SSL parameters that psycopg2 doesn't understand
        parsed = urlparse(sync_url)
        query_params = parse_qs(parsed.query)
        # Remove problematic parameters
        for param in ['ssl', 'sslmode']:
            query_params.pop(param, None)
        clean_query = urlencode(query_params, doseq=True)
        sync_url = urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, clean_query, parsed.fragment
        ))

        # Create a sync engine just for this query
        engine = create_engine(sync_url, pool_pre_ping=True)

        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT finnhub_api_key FROM user_settings
                    WHERE finnhub_api_key IS NOT NULL AND finnhub_api_key != ''
                    LIMIT 1
                """)
            )
            row = result.fetchone()
            if row and row[0]:
                _cached_finnhub_api_key = row[0]
                _finnhub_key_fetched = True
                logger.info("Using Finnhub API key from user settings")
                return _cached_finnhub_api_key

        engine.dispose()

    except Exception as e:
        logger.warning(f"Failed to get Finnhub API key from user settings: {e}")

    _finnhub_key_fetched = True  # Mark as fetched even if not found
    return None


def _fetch_finnhub_symbols(exchange: str) -> List[Dict[str, Any]]:
    """
    Fetch stock symbols from Finnhub API.

    Args:
        exchange: Exchange code (US, HK, SS, SZ)

    Returns:
        List of stock symbol data
    """
    import finnhub

    api_key = _get_finnhub_api_key()
    if not api_key:
        logger.warning("FINNHUB_API_KEY not configured (env or user settings), skipping Finnhub fetch")
        return []

    try:
        client = finnhub.Client(api_key=api_key)
        symbols = client.stock_symbols(exchange)
        logger.info(f"Fetched {len(symbols)} symbols from Finnhub for {exchange}")
        return symbols
    except Exception as e:
        logger.error(f"Failed to fetch symbols for {exchange}: {e}")
        return []


def fetch_us_stocks() -> List[Dict[str, Any]]:
    """Fetch US stock symbols from Finnhub."""
    return _fetch_finnhub_symbols("US")


# ============ AKShare Data Sources ============

def fetch_akshare_sh_stocks() -> List[Dict[str, Any]]:
    """
    Fetch Shanghai A-share symbols from AKShare.

    Returns:
        List of stock data dicts with keys: symbol, name, name_zh, exchange, market
    """
    try:
        import akshare as ak

        df = ak.stock_info_sh_name_code()
        stocks = []

        for _, row in df.iterrows():
            code = str(row.get("证券代码", "")).strip()
            name_zh = str(row.get("证券简称", "")).strip()

            if not code:
                continue

            # Generate pinyin
            pinyin, pinyin_initial = get_pinyin(name_zh)

            stocks.append({
                "symbol": f"{code}.SS",
                "name": name_zh,  # Use Chinese name as primary
                "name_zh": name_zh,
                "exchange": "SSE",
                "market": "sh",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })

        logger.info(f"Fetched {len(stocks)} Shanghai stocks from AKShare")
        return stocks

    except Exception as e:
        logger.error(f"Failed to fetch Shanghai stocks from AKShare: {e}")
        return []


def fetch_akshare_sz_stocks() -> List[Dict[str, Any]]:
    """
    Fetch Shenzhen A-share symbols from AKShare.

    Returns:
        List of stock data dicts
    """
    try:
        import akshare as ak

        df = ak.stock_info_sz_name_code(symbol="A股列表")
        stocks = []

        for _, row in df.iterrows():
            code = str(row.get("A股代码", "")).strip()
            name_zh = str(row.get("A股简称", "")).strip()

            if not code:
                continue

            # Generate pinyin
            pinyin, pinyin_initial = get_pinyin(name_zh)

            stocks.append({
                "symbol": f"{code}.SZ",
                "name": name_zh,
                "name_zh": name_zh,
                "exchange": "SZSE",
                "market": "sz",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })

        logger.info(f"Fetched {len(stocks)} Shenzhen stocks from AKShare")
        return stocks

    except Exception as e:
        logger.error(f"Failed to fetch Shenzhen stocks from AKShare: {e}")
        return []


def fetch_akshare_bj_stocks() -> List[Dict[str, Any]]:
    """
    Fetch Beijing Stock Exchange symbols from AKShare.

    Returns:
        List of stock data dicts
    """
    try:
        import akshare as ak

        df = ak.stock_info_bj_name_code()
        stocks = []

        for _, row in df.iterrows():
            code = str(row.get("证券代码", "")).strip()
            name_zh = str(row.get("证券简称", "")).strip()

            if not code:
                continue

            # Generate pinyin
            pinyin, pinyin_initial = get_pinyin(name_zh)

            stocks.append({
                "symbol": f"{code}.BJ",
                "name": name_zh,
                "name_zh": name_zh,
                "exchange": "BSE",
                "market": "bj",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })

        logger.info(f"Fetched {len(stocks)} Beijing stocks from AKShare")
        return stocks

    except Exception as e:
        logger.error(f"Failed to fetch Beijing stocks from AKShare: {e}")
        return []


def fetch_akshare_hk_stocks() -> List[Dict[str, Any]]:
    """
    Fetch Hong Kong stock symbols from AKShare (Sina source).

    Returns:
        List of stock data dicts
    """
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

            # Format HK code: pad to 5 digits and add .HK suffix
            # e.g., "00001" -> "00001.HK"
            code_padded = code.zfill(5)

            # Generate pinyin from Chinese name
            pinyin, pinyin_initial = get_pinyin(name_zh)

            stocks.append({
                "symbol": f"{code_padded}.HK",
                "name": name_en if name_en else name_zh,
                "name_zh": name_zh,
                "exchange": "HKEX",
                "market": "hk",
                "pinyin": pinyin,
                "pinyin_initial": pinyin_initial,
            })

        logger.info(f"Fetched {len(stocks)} Hong Kong stocks from AKShare")
        return stocks

    except Exception as e:
        logger.error(f"Failed to fetch Hong Kong stocks from AKShare: {e}")
        return []


# ============ Finnhub Fallback Functions ============

def fetch_finnhub_hk_stocks() -> List[Dict[str, Any]]:
    """Fetch Hong Kong stock symbols from Finnhub (fallback)."""
    return _fetch_finnhub_symbols("HK")


def fetch_finnhub_sh_stocks() -> List[Dict[str, Any]]:
    """Fetch Shanghai A-share symbols from Finnhub (fallback)."""
    return _fetch_finnhub_symbols("SS")


def fetch_finnhub_sz_stocks() -> List[Dict[str, Any]]:
    """Fetch Shenzhen A-share symbols from Finnhub (fallback)."""
    return _fetch_finnhub_symbols("SZ")


def get_precious_metals() -> List[Dict[str, Any]]:
    """
    Get precious metals data from stock_service.py.

    Returns:
        List of precious metal stock data
    """
    from app.services.stock_service import PRECIOUS_METALS

    metals = []
    for symbol, meta in PRECIOUS_METALS.items():
        # Generate pinyin for Chinese name
        pinyin, pinyin_initial = get_pinyin(meta.get("name_zh", ""))

        metals.append({
            "symbol": symbol,
            "name": meta["name"],
            "name_zh": meta.get("name_zh", ""),
            "exchange": meta["exchange"],
            "market": "metal",
            "pinyin": pinyin,
            "pinyin_initial": pinyin_initial,
        })

    logger.info(f"Got {len(metals)} precious metals")
    return metals


def _process_finnhub_symbol(symbol_data: Dict[str, Any], market: str) -> Dict[str, Any]:
    """
    Process a single Finnhub symbol into LocalStock format.

    Args:
        symbol_data: Raw symbol data from Finnhub
        market: Market identifier (us, hk, sh, sz)

    Returns:
        Processed stock data dict
    """
    symbol = symbol_data.get("symbol", "")
    name = symbol_data.get("description", "")
    exchange = symbol_data.get("mic", "") or symbol_data.get("exchange", "")

    # For HK stocks, add .HK suffix if not present
    if market == "hk" and not symbol.endswith(".HK"):
        symbol = f"{symbol}.HK"

    # For Shanghai stocks, add .SS suffix
    if market == "sh" and not symbol.endswith(".SS"):
        symbol = f"{symbol}.SS"

    # For Shenzhen stocks, add .SZ suffix
    if market == "sz" and not symbol.endswith(".SZ"):
        symbol = f"{symbol}.SZ"

    # Try to extract Chinese name from description (if exists)
    name_zh = ""
    if name:
        # Check if the name contains Chinese characters
        import re
        chinese_match = re.search(r"[\u4e00-\u9fff]+", name)
        if chinese_match:
            name_zh = chinese_match.group()

    # Generate pinyin for Chinese name
    pinyin, pinyin_initial = get_pinyin(name_zh) if name_zh else ("", "")

    return {
        "symbol": symbol,
        "name": name,
        "name_zh": name_zh,
        "exchange": exchange,
        "market": market,
        "pinyin": pinyin,
        "pinyin_initial": pinyin_initial,
    }


def _fetch_all_markets() -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict], List[Dict]]:
    """
    Fetch all markets in parallel using thread pool.

    Data sources:
    - US: Finnhub (requires API key)
    - HK/SH/SZ/BJ: AKShare (free, no API key)

    Returns:
        Tuple of (us_stocks, hk_stocks, sh_stocks, sz_stocks, bj_stocks)
    """
    executor = _get_executor()

    # Submit parallel fetches
    # US from Finnhub
    us_future = executor.submit(fetch_us_stocks)

    # Chinese markets from AKShare (free, no API key needed)
    hk_future = executor.submit(fetch_akshare_hk_stocks)
    sh_future = executor.submit(fetch_akshare_sh_stocks)
    sz_future = executor.submit(fetch_akshare_sz_stocks)
    bj_future = executor.submit(fetch_akshare_bj_stocks)

    # Wait for all to complete
    us_stocks = us_future.result()
    hk_stocks = hk_future.result()
    sh_stocks = sh_future.result()
    sz_stocks = sz_future.result()
    bj_stocks = bj_future.result()

    return us_stocks, hk_stocks, sh_stocks, sz_stocks, bj_stocks


@celery_app.task(bind=True, max_retries=3)
def update_stock_list(self):
    """
    Update the local stock list from multiple data sources.

    Data sources:
    - US: Finnhub API (requires API key from env or user settings)
    - HK/SH/SZ/BJ: AKShare (free, no API key)
    - Precious Metals: Hardcoded from stock_service.py

    This task:
    1. Fetches stock symbols from all markets in parallel
    2. Adds precious metals data
    3. Generates pinyin for Chinese names
    4. Saves to msgpack file
    5. Notifies backend to reload

    Scheduled to run daily at 5:30 AM UTC.
    """
    try:
        logger.info("Starting stock list update task")
        start_time = datetime.utcnow()

        # Fetch from all markets in parallel
        # AKShare data already has pinyin generated
        us_raw, hk_stocks, sh_stocks, sz_stocks, bj_stocks = _fetch_all_markets()

        # Process stocks
        all_stocks: List[Dict[str, Any]] = []

        # Process US stocks (from Finnhub, needs conversion)
        for symbol_data in us_raw:
            try:
                stock = _process_finnhub_symbol(symbol_data, "us")
                all_stocks.append(stock)
            except Exception as e:
                logger.warning(f"Failed to process US stock {symbol_data}: {e}")

        # Add AKShare stocks directly (already processed with pinyin)
        all_stocks.extend(hk_stocks)
        all_stocks.extend(sh_stocks)
        all_stocks.extend(sz_stocks)
        all_stocks.extend(bj_stocks)

        # Add precious metals
        metals = get_precious_metals()
        all_stocks.extend(metals)

        # Deduplicate by symbol (keep first occurrence)
        seen_symbols = set()
        unique_stocks = []
        for stock in all_stocks:
            symbol = stock["symbol"]
            if symbol not in seen_symbols:
                seen_symbols.add(symbol)
                unique_stocks.append(stock)

        # Save to file
        if unique_stocks:
            success = _save_stock_list(unique_stocks)
            if not success:
                raise RuntimeError("Failed to save stock list")

        # Trigger backend reload
        _notify_reload()

        elapsed = (datetime.utcnow() - start_time).total_seconds()
        result = {
            "status": "success",
            "total_stocks": len(unique_stocks),
            "by_market": {
                "us": len([s for s in unique_stocks if s["market"] == "us"]),
                "hk": len([s for s in unique_stocks if s["market"] == "hk"]),
                "sh": len([s for s in unique_stocks if s["market"] == "sh"]),
                "sz": len([s for s in unique_stocks if s["market"] == "sz"]),
                "bj": len([s for s in unique_stocks if s["market"] == "bj"]),
                "metal": len([s for s in unique_stocks if s["market"] == "metal"]),
            },
            "elapsed_seconds": elapsed,
        }

        logger.info(f"Stock list update completed: {result}")
        return result

    except Exception as e:
        logger.exception(f"Stock list update task failed: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


def _save_stock_list(stocks: List[Dict[str, Any]]) -> bool:
    """
    Save stock list to msgpack file.

    Args:
        stocks: List of stock data dicts

    Returns:
        True if saved successfully
    """
    try:
        from app.services.stock_list_service import LocalStock, StockListService
        from pathlib import Path

        # Convert to LocalStock objects
        local_stocks = [LocalStock.from_dict(s) for s in stocks]

        # Get service and save
        # Note: We create a temporary instance just for saving
        service = StockListService()
        return service.save(local_stocks)

    except Exception as e:
        logger.exception(f"Failed to save stock list: {e}")
        return False


def _notify_reload():
    """Notify backend to reload stock list data."""
    try:
        # Use Redis pub/sub to notify backend
        import redis
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        r = redis.Redis(host=redis_host, port=6379, db=0)
        r.publish("stock_list_reload", "reload")
        logger.info("Published stock list reload notification")
    except Exception as e:
        logger.warning(f"Failed to publish reload notification: {e}")


@celery_app.task(bind=True, max_retries=2)
def update_chinese_names(self, symbols: List[str]):
    """
    Update Chinese names for specific stocks.

    This task is for updating Chinese names that may not be available from
    the primary data source.

    Args:
        symbols: List of stock symbols to update
    """
    try:
        logger.info(f"Updating Chinese names for {len(symbols)} stocks")
        # This is a placeholder for future enhancement
        # Could fetch Chinese names from alternative sources like:
        # - East Money API
        # - Sina Finance
        # - Manual CSV file
        return {"status": "success", "updated": 0}
    except Exception as e:
        logger.exception(f"Failed to update Chinese names: {e}")
        raise self.retry(exc=e, countdown=30)


@celery_app.task
def get_stock_list_stats():
    """Get statistics about the current stock list."""
    import asyncio

    async def _get_stats():
        from app.services.stock_list_service import get_stock_list_service
        service = await get_stock_list_service()
        return service.get_stats()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_get_stats())
    finally:
        loop.close()
