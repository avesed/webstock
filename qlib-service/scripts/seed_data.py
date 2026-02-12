"""Initial EOD data download and conversion to Qlib .bin format.

Usage:
    python -m scripts.seed_data --market us     # US stocks via yfinance
    python -m scripts.seed_data --market hk     # HK stocks via yfinance
    python -m scripts.seed_data --market cn     # A-shares via akshare
    python -m scripts.seed_data --market metal  # Precious metals via yfinance
    python -m scripts.seed_data --market all    # All markets

For US/HK, this script uses yfinance for batch downloading.

For A-shares, it uses akshare which has no rate limit concerns but requires
sequential download per symbol.

Checkpoint support: progress is saved every CHECKPOINT_INTERVAL symbols.
Use --resume to continue from the last checkpoint.
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.utils.bin_writer import (
    dataframe_to_bin,
    update_calendar,
    update_instruments,
)
from app.utils.symbol_mapping import webstock_to_qlib

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = os.environ.get("QLIB_DATA_DIR", "/app/data/qlib")

METAL_SYMBOLS = ["GC=F", "SI=F", "PL=F", "PA=F"]

CHECKPOINT_INTERVAL = 100  # Save checkpoint every N symbols


def _load_checkpoint(market_dir: str, market: str) -> Set[str]:
    """Load checkpoint: set of already-processed symbols."""
    ckpt_path = os.path.join(market_dir, f".checkpoint_{market}.json")
    if os.path.exists(ckpt_path):
        with open(ckpt_path) as f:
            data = json.load(f)
            done = set(data.get("completed_symbols", []))
            logger.info("Checkpoint loaded: %d symbols already processed for %s", len(done), market)
            return done
    return set()


def _save_checkpoint(market_dir: str, market: str, completed: Set[str]) -> None:
    """Save checkpoint with completed symbols."""
    ckpt_path = os.path.join(market_dir, f".checkpoint_{market}.json")
    with open(ckpt_path, "w") as f:
        json.dump({
            "completed_symbols": sorted(completed),
            "timestamp": datetime.now().isoformat(),
        }, f)
    logger.debug("Checkpoint saved: %d symbols for %s", len(completed), market)


def _clear_checkpoint(market_dir: str, market: str) -> None:
    """Remove checkpoint file after successful completion."""
    ckpt_path = os.path.join(market_dir, f".checkpoint_{market}.json")
    if os.path.exists(ckpt_path):
        os.remove(ckpt_path)
        logger.info("Checkpoint cleared for %s", market)


def download_us_data(
    data_dir: str,
    max_symbols: Optional[int] = None,
    start_date: Optional[str] = None,
    resume: bool = False,
) -> int:
    """Download US stock data via yfinance.

    Returns number of symbols successfully processed.
    """
    import yfinance as yf

    market_dir = os.path.join(data_dir, "us_data")
    os.makedirs(market_dir, exist_ok=True)

    # Get S&P 500 + NASDAQ 100 symbols as initial universe
    logger.info("Fetching US symbol list...")
    try:
        sp500 = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            storage_options={"timeout": 15},
        )[0]
        symbols = sp500["Symbol"].str.replace(".", "-").tolist()
    except Exception as e:
        logger.warning("Could not fetch S&P 500 list (%s), using fallback", e)
        symbols = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "META",
            "NVDA", "TSLA", "JPM", "V", "WMT",
        ]

    if max_symbols:
        symbols = symbols[:max_symbols]

    # Load checkpoint
    completed: Set[str] = set()
    if resume:
        completed = _load_checkpoint(market_dir, "us")

    remaining = [s for s in symbols if s not in completed]
    logger.info(
        "Downloading %d US symbols (%d already done, %d remaining)...",
        len(symbols), len(completed), len(remaining),
    )

    start = start_date or "2000-01-01"
    all_dates: set = set()
    success_count = len(completed)

    # Batch download with yfinance (efficient)
    batch_size = 100
    for i in range(0, len(remaining), batch_size):
        batch = remaining[i : i + batch_size]
        logger.info(
            "  Batch %d/%d (%d symbols)...",
            i // batch_size + 1,
            (len(remaining) + batch_size - 1) // batch_size,
            len(batch),
        )

        try:
            data = yf.download(
                batch,
                start=start,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
            )

            for sym in batch:
                try:
                    if len(batch) == 1:
                        df = data
                    else:
                        df = data[sym].dropna(how="all")

                    if df.empty:
                        continue

                    # Rename columns to lowercase
                    df.columns = [c.lower() for c in df.columns]
                    if "adj close" in df.columns:
                        df = df.drop(columns=["adj close"])

                    # Convert symbol
                    qlib_sym = webstock_to_qlib(sym, "us")

                    # Collect dates
                    dates = [d.strftime("%Y-%m-%d") for d in df.index]
                    all_dates.update(dates)

                    # Write .bin
                    if dataframe_to_bin(df, qlib_sym, market_dir):
                        update_instruments(market_dir, qlib_sym, dates[0], dates[-1])
                        success_count += 1
                        completed.add(sym)
                except Exception as e:
                    logger.warning("  Failed to process %s: %s", sym, e)
        except Exception as e:
            logger.error("  Batch download failed: %s", e)

        # Checkpoint after each batch
        if completed:
            _save_checkpoint(market_dir, "us", completed)

        # Small delay between batches
        time.sleep(0.5)

    # Write calendar
    if all_dates:
        update_calendar(market_dir, sorted(all_dates))

    _clear_checkpoint(market_dir, "us")
    logger.info("US: %d/%d symbols processed", success_count, len(symbols))
    return success_count


def download_cn_data(
    data_dir: str,
    max_symbols: Optional[int] = None,
    start_date: Optional[str] = None,
    resume: bool = False,
) -> int:
    """Download A-share data via akshare.

    Returns number of symbols successfully processed.
    """
    import akshare as ak

    market_dir = os.path.join(data_dir, "cn_data")
    os.makedirs(market_dir, exist_ok=True)

    logger.info("Fetching A-share symbol list...")
    try:
        stock_list = ak.stock_zh_a_spot_em()
        symbols = stock_list["\u4ee3\u7801"].tolist()
    except Exception:
        logger.warning("Could not fetch A-share list, using fallback")
        symbols = ["600000", "000001", "600519", "000858"]

    if max_symbols:
        symbols = symbols[:max_symbols]

    # Load checkpoint
    completed: Set[str] = set()
    if resume:
        completed = _load_checkpoint(market_dir, "cn")

    remaining = [s for s in symbols if s not in completed]
    logger.info(
        "Downloading %d A-share symbols (%d already done, %d remaining)...",
        len(symbols), len(completed), len(remaining),
    )

    start = start_date or "20000101"
    all_dates: set = set()
    success_count = len(completed)

    for idx, sym in enumerate(remaining, 1):
        try:
            df = ak.stock_zh_a_hist(
                symbol=sym,
                period="daily",
                start_date=start,
                adjust="qfq",  # Forward-adjusted
            )

            if df is None or df.empty:
                continue

            # Standardize columns
            col_map = {
                "\u65e5\u671f": "date",
                "\u5f00\u76d8": "open",
                "\u6700\u9ad8": "high",
                "\u6700\u4f4e": "low",
                "\u6536\u76d8": "close",
                "\u6210\u4ea4\u91cf": "volume",
            }
            df = df.rename(columns=col_map)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            df = df[["open", "high", "low", "close", "volume"]]

            # Determine exchange prefix
            if sym.startswith(("6", "9")):
                qlib_sym = f"SH{sym}"
            else:
                qlib_sym = f"SZ{sym}"

            dates = [d.strftime("%Y-%m-%d") for d in df.index]
            all_dates.update(dates)

            if dataframe_to_bin(df, qlib_sym, market_dir):
                update_instruments(market_dir, qlib_sym, dates[0], dates[-1])
                success_count += 1
                completed.add(sym)

        except Exception as e:
            logger.warning("  Failed %s: %s", sym, e)

        # Progress, checkpoint, and rate limit
        if idx % CHECKPOINT_INTERVAL == 0:
            logger.info(
                "  Progress: %d/%d (success: %d)", idx, len(remaining), success_count
            )
            _save_checkpoint(market_dir, "cn", completed)

        if idx % 10 == 0:
            time.sleep(0.3)

    if all_dates:
        update_calendar(market_dir, sorted(all_dates))

    _clear_checkpoint(market_dir, "cn")
    logger.info("CN: %d/%d symbols processed", success_count, len(symbols))
    return success_count


def download_hk_data(
    data_dir: str,
    max_symbols: Optional[int] = None,
    start_date: Optional[str] = None,
    resume: bool = False,
) -> int:
    """Download HK stock data via yfinance."""
    import yfinance as yf

    market_dir = os.path.join(data_dir, "hk_data")
    os.makedirs(market_dir, exist_ok=True)

    # Common HK stocks (Hang Seng Index constituents)
    logger.info("Fetching HK symbol list...")
    hk_symbols = [
        "0700.HK", "9988.HK", "0005.HK", "1299.HK", "0941.HK",
        "2318.HK", "0388.HK", "0027.HK", "1398.HK", "3690.HK",
    ]

    if max_symbols:
        hk_symbols = hk_symbols[:max_symbols]

    # Load checkpoint
    completed: Set[str] = set()
    if resume:
        completed = _load_checkpoint(market_dir, "hk")

    remaining = [s for s in hk_symbols if s not in completed]
    logger.info(
        "Downloading %d HK symbols (%d already done, %d remaining)...",
        len(hk_symbols), len(completed), len(remaining),
    )

    start = start_date or "2000-01-01"
    all_dates: set = set()
    success_count = len(completed)

    for sym in remaining:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(start=start, auto_adjust=True)

            if df.empty:
                continue

            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]]

            qlib_sym = webstock_to_qlib(sym, "hk")
            dates = [d.strftime("%Y-%m-%d") for d in df.index]
            all_dates.update(dates)

            if dataframe_to_bin(df, qlib_sym, market_dir):
                update_instruments(market_dir, qlib_sym, dates[0], dates[-1])
                success_count += 1
                completed.add(sym)
        except Exception as e:
            logger.warning("  Failed %s: %s", sym, e)

        time.sleep(0.5)

    if all_dates:
        update_calendar(market_dir, sorted(all_dates))

    _clear_checkpoint(market_dir, "hk")
    logger.info("HK: %d/%d symbols processed", success_count, len(hk_symbols))
    return success_count


def download_metal_data(
    data_dir: str,
    start_date: Optional[str] = None,
) -> int:
    """Download precious metals data via yfinance."""
    import yfinance as yf

    market_dir = os.path.join(data_dir, "metal_data")
    os.makedirs(market_dir, exist_ok=True)

    start = start_date or "2000-01-01"
    all_dates: set = set()
    success_count = 0

    logger.info("Downloading %d metal symbols...", len(METAL_SYMBOLS))

    for sym in METAL_SYMBOLS:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(start=start, auto_adjust=True)

            if df.empty:
                continue

            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]]

            qlib_sym = webstock_to_qlib(sym, "metal")
            dates = [d.strftime("%Y-%m-%d") for d in df.index]
            all_dates.update(dates)

            if dataframe_to_bin(df, qlib_sym, market_dir):
                update_instruments(market_dir, qlib_sym, dates[0], dates[-1])
                success_count += 1
        except Exception as e:
            logger.warning("  Failed %s: %s", sym, e)

    if all_dates:
        update_calendar(market_dir, sorted(all_dates))

    logger.info("Metal: %d/%d symbols processed", success_count, len(METAL_SYMBOLS))
    return success_count


def save_sync_metadata(data_dir: str, results: Dict[str, int]) -> None:
    """Save sync metadata with timestamps."""
    meta_path = os.path.join(data_dir, "sync_metadata.json")
    metadata = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            metadata = json.load(f)

    for market, count in results.items():
        metadata[market] = {
            "last_sync": datetime.now().isoformat(),
            "symbol_count": count,
        }

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Download EOD data and convert to Qlib .bin format"
    )
    parser.add_argument(
        "--market",
        choices=["us", "hk", "cn", "metal", "all"],
        default="all",
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--max-symbols", type=int, default=None, help="Limit symbols (for testing)"
    )
    parser.add_argument("--start-date", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last checkpoint (skip already-processed symbols)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    os.makedirs(args.data_dir, exist_ok=True)
    results = {}

    markets = (
        [args.market] if args.market != "all" else ["us", "hk", "cn", "metal"]
    )

    for market in markets:
        logger.info("=" * 60)
        logger.info("Starting %s market download...", market.upper())
        logger.info("=" * 60)

        if market == "us":
            results["us"] = download_us_data(
                args.data_dir, args.max_symbols, args.start_date, args.resume
            )
        elif market == "hk":
            results["hk"] = download_hk_data(
                args.data_dir, args.max_symbols, args.start_date, args.resume
            )
        elif market == "cn":
            start = (
                args.start_date.replace("-", "") if args.start_date else None
            )
            results["cn"] = download_cn_data(
                args.data_dir, args.max_symbols, start, args.resume
            )
        elif market == "metal":
            results["metal"] = download_metal_data(
                args.data_dir, args.start_date
            )

    save_sync_metadata(args.data_dir, results)

    logger.info("=" * 60)
    logger.info("Download complete! Results:")
    for market, count in results.items():
        logger.info("  %s: %d symbols", market.upper(), count)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
