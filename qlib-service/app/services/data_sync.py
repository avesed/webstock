"""Data synchronization service for Qlib .bin format.

Provides market data download (yfinance for US/HK/metal, akshare for A-shares)
and conversion to Qlib binary format. Designed to run in ProcessPoolExecutor
via run_qlib_background().

This service reuses the same download logic as scripts/seed_data.py but
exposes it as a callable service with structured return values.
"""
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd

from app.config import get_settings
from app.context import QlibContext
from app.utils.bin_writer import (
    dataframe_to_bin,
    update_calendar,
    update_instruments,
)
from app.utils.symbol_mapping import webstock_to_qlib

logger = logging.getLogger(__name__)

METAL_SYMBOLS = ["GC=F", "SI=F", "PL=F", "PA=F"]

# Rate limiting between batches / sequential downloads
_BATCH_DELAY = 0.5
_CN_DELAY = 0.3
_CN_PROGRESS_INTERVAL = 100


class DataSyncService:
    """Market data synchronization to Qlib .bin format.

    All methods are synchronous -- designed to run in ProcessPoolExecutor
    via run_qlib_background().
    """

    @staticmethod
    def sync_market(
        market: str,
        data_dir: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        update_only: bool = True,
    ) -> Dict[str, Any]:
        """Synchronize market data to Qlib .bin format.

        Args:
            market: Market code (us, hk, cn, metal).
            data_dir: Override base Qlib data directory.
            symbols: Specific symbols to sync. None = full default universe.
            update_only: If True, only fetch dates after the last calendar entry.

        Returns:
            Dict with keys: market, symbol_count, new_symbols, errors, duration_s.
        """
        settings = get_settings()
        data_dir = data_dir or settings.QLIB_DATA_DIR
        os.makedirs(data_dir, exist_ok=True)

        logger.info("Starting data sync for market=%s, update_only=%s", market, update_only)
        start_time = time.monotonic()

        if market == "us":
            result = DataSyncService._sync_us(data_dir, symbols, update_only)
        elif market == "hk":
            result = DataSyncService._sync_hk(data_dir, symbols, update_only)
        elif market in ("cn", "sh", "sz"):
            result = DataSyncService._sync_cn(data_dir, symbols, update_only)
        elif market == "metal":
            result = DataSyncService._sync_metal(data_dir, update_only)
        else:
            raise ValueError(
                f"Unknown market: {market}. Valid: us, hk, cn, sh, sz, metal"
            )

        elapsed = time.monotonic() - start_time
        result["duration_s"] = round(elapsed, 2)
        result["market"] = market

        # Update sync metadata
        DataSyncService._save_metadata(data_dir, market, result)

        logger.info(
            "Data sync complete for market=%s: %d symbols, %d errors in %.1fs",
            market, result["symbol_count"], len(result.get("errors", [])), elapsed,
        )
        return result

    @staticmethod
    def get_sync_status(data_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Read sync status for all markets.

        Returns:
            Dict keyed by market code, each containing last_sync, symbol_count,
            date_range, status, and data_exists fields.
        """
        settings = get_settings()
        data_dir = data_dir or settings.QLIB_DATA_DIR

        # Read metadata file
        meta_path = os.path.join(data_dir, "sync_metadata.json")
        metadata: Dict[str, Any] = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read sync_metadata.json: %s", e)

        markets: Dict[str, Dict[str, Any]] = {}
        for market, subdir in QlibContext.REGION_TO_DATA_DIR.items():
            market_dir = os.path.join(data_dir, subdir)
            data_exists = os.path.isdir(market_dir) and bool(os.listdir(market_dir))

            # Read calendar date range
            date_range = DataSyncService._get_date_range(market_dir)

            # Read instrument count
            instrument_count = DataSyncService._count_instruments(market_dir)

            market_meta = metadata.get(market, {})
            markets[market] = {
                "last_sync": market_meta.get("last_sync"),
                "symbol_count": market_meta.get("symbol_count", instrument_count),
                "date_range": date_range,
                "data_exists": data_exists,
                "status": "idle",
            }

        return markets

    # ------------------------------------------------------------------ #
    # Private sync methods per market
    # ------------------------------------------------------------------ #

    @staticmethod
    def _sync_us(
        data_dir: str,
        symbols: Optional[List[str]],
        update_only: bool,
    ) -> Dict[str, Any]:
        """Sync US market data via yfinance."""
        import yfinance as yf

        market_dir = os.path.join(data_dir, "us_data")
        os.makedirs(market_dir, exist_ok=True)

        # Determine symbol list
        if symbols is None:
            symbols = DataSyncService._get_us_symbols()

        start_date = DataSyncService._resolve_start_date(
            market_dir, update_only, default="2000-01-01"
        )

        logger.info("Syncing %d US symbols from %s", len(symbols), start_date)

        all_dates: Set[str] = set()
        errors: List[str] = []
        success_count = 0

        # Batch download via yfinance
        batch_size = 100
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            logger.info(
                "  US batch %d/%d (%d symbols)",
                i // batch_size + 1,
                (len(symbols) + batch_size - 1) // batch_size,
                len(batch),
            )

            try:
                data = yf.download(
                    batch,
                    start=start_date,
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

                        df.columns = [c.lower() for c in df.columns]
                        if "adj close" in df.columns:
                            df = df.drop(columns=["adj close"])

                        qlib_sym = webstock_to_qlib(sym, "us")
                        dates = [d.strftime("%Y-%m-%d") for d in df.index]
                        all_dates.update(dates)

                        if dataframe_to_bin(df, qlib_sym, market_dir):
                            update_instruments(market_dir, qlib_sym, dates[0], dates[-1])
                            success_count += 1
                    except Exception as e:
                        logger.warning("  Failed to process %s: %s", sym, e)
                        errors.append(f"{sym}: {e}")
            except Exception as e:
                logger.error("  Batch download failed: %s", e)
                errors.append(f"batch_{i}: {e}")

            time.sleep(_BATCH_DELAY)

        if all_dates:
            update_calendar(market_dir, sorted(all_dates))

        return {
            "symbol_count": success_count,
            "new_symbols": success_count,
            "errors": errors,
        }

    @staticmethod
    def _sync_hk(
        data_dir: str,
        symbols: Optional[List[str]],
        update_only: bool,
    ) -> Dict[str, Any]:
        """Sync HK market data via yfinance."""
        import yfinance as yf

        market_dir = os.path.join(data_dir, "hk_data")
        os.makedirs(market_dir, exist_ok=True)

        if symbols is None:
            symbols = [
                "0700.HK", "9988.HK", "0005.HK", "1299.HK", "0941.HK",
                "2318.HK", "0388.HK", "0027.HK", "1398.HK", "3690.HK",
            ]

        start_date = DataSyncService._resolve_start_date(
            market_dir, update_only, default="2000-01-01"
        )

        logger.info("Syncing %d HK symbols from %s", len(symbols), start_date)

        all_dates: Set[str] = set()
        errors: List[str] = []
        success_count = 0

        for sym in symbols:
            try:
                ticker = yf.Ticker(sym)
                df = ticker.history(start=start_date, auto_adjust=True)

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
            except Exception as e:
                logger.warning("  Failed %s: %s", sym, e)
                errors.append(f"{sym}: {e}")

            time.sleep(_BATCH_DELAY)

        if all_dates:
            update_calendar(market_dir, sorted(all_dates))

        return {
            "symbol_count": success_count,
            "new_symbols": success_count,
            "errors": errors,
        }

    @staticmethod
    def _sync_cn(
        data_dir: str,
        symbols: Optional[List[str]],
        update_only: bool,
    ) -> Dict[str, Any]:
        """Sync A-share market data via akshare."""
        import akshare as ak

        market_dir = os.path.join(data_dir, "cn_data")
        os.makedirs(market_dir, exist_ok=True)

        if symbols is None:
            try:
                stock_list = ak.stock_zh_a_spot_em()
                symbols = stock_list["\u4ee3\u7801"].tolist()
            except Exception:
                logger.warning("Could not fetch A-share list, using fallback")
                symbols = ["600000", "000001", "600519", "000858"]

        start_date = DataSyncService._resolve_start_date(
            market_dir, update_only, default="20000101", date_format="%Y%m%d"
        )

        logger.info("Syncing %d A-share symbols from %s", len(symbols), start_date)

        all_dates: Set[str] = set()
        errors: List[str] = []
        success_count = 0

        for idx, sym in enumerate(symbols, 1):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=sym,
                    period="daily",
                    start_date=start_date,
                    adjust="qfq",
                )

                if df is None or df.empty:
                    continue

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

                if sym.startswith(("6", "9")):
                    qlib_sym = f"SH{sym}"
                else:
                    qlib_sym = f"SZ{sym}"

                dates = [d.strftime("%Y-%m-%d") for d in df.index]
                all_dates.update(dates)

                if dataframe_to_bin(df, qlib_sym, market_dir):
                    update_instruments(market_dir, qlib_sym, dates[0], dates[-1])
                    success_count += 1

            except Exception as e:
                logger.warning("  Failed %s: %s", sym, e)
                errors.append(f"{sym}: {e}")

            if idx % _CN_PROGRESS_INTERVAL == 0:
                logger.info(
                    "  CN progress: %d/%d (success: %d)", idx, len(symbols), success_count
                )

            if idx % 10 == 0:
                time.sleep(_CN_DELAY)

        if all_dates:
            update_calendar(market_dir, sorted(all_dates))

        return {
            "symbol_count": success_count,
            "new_symbols": success_count,
            "errors": errors,
        }

    @staticmethod
    def _sync_metal(
        data_dir: str,
        update_only: bool,
    ) -> Dict[str, Any]:
        """Sync precious metals data via yfinance."""
        import yfinance as yf

        market_dir = os.path.join(data_dir, "metal_data")
        os.makedirs(market_dir, exist_ok=True)

        start_date = DataSyncService._resolve_start_date(
            market_dir, update_only, default="2000-01-01"
        )

        logger.info("Syncing %d metal symbols from %s", len(METAL_SYMBOLS), start_date)

        all_dates: Set[str] = set()
        errors: List[str] = []
        success_count = 0

        for sym in METAL_SYMBOLS:
            try:
                ticker = yf.Ticker(sym)
                df = ticker.history(start=start_date, auto_adjust=True)

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
                errors.append(f"{sym}: {e}")

        if all_dates:
            update_calendar(market_dir, sorted(all_dates))

        return {
            "symbol_count": success_count,
            "new_symbols": success_count,
            "errors": errors,
        }

    # ------------------------------------------------------------------ #
    # Helper methods
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_us_symbols() -> List[str]:
        """Fetch S&P 500 symbols, with fallback."""
        try:
            sp500 = pd.read_html(
                "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                storage_options={"timeout": 15},
            )[0]
            return sp500["Symbol"].str.replace(".", "-").tolist()
        except Exception as e:
            logger.warning("Could not fetch S&P 500 list (%s), using fallback", e)
            return [
                "AAPL", "MSFT", "GOOGL", "AMZN", "META",
                "NVDA", "TSLA", "JPM", "V", "WMT",
            ]

    @staticmethod
    def _resolve_start_date(
        market_dir: str,
        update_only: bool,
        default: str = "2000-01-01",
        date_format: str = "%Y-%m-%d",
    ) -> str:
        """Determine start date: last calendar entry if update_only, else default."""
        if not update_only:
            return default

        cal_path = os.path.join(market_dir, "calendars", "day.txt")
        if os.path.exists(cal_path):
            try:
                lines = Path(cal_path).read_text().strip().split("\n")
                if lines:
                    last_date = sorted(lines)[-1].strip()
                    # Return the day after the last calendar entry
                    last_dt = pd.Timestamp(last_date)
                    next_dt = last_dt + pd.Timedelta(days=1)
                    result = next_dt.strftime(date_format)
                    logger.info(
                        "Update mode: starting from %s (last calendar: %s)",
                        result, last_date,
                    )
                    return result
            except Exception as e:
                logger.warning(
                    "Failed to read calendar for update_only, using default: %s", e
                )

        return default

    @staticmethod
    def _get_date_range(market_dir: str) -> Optional[Dict[str, str]]:
        """Read calendar file and return {start, end} date range."""
        cal_path = os.path.join(market_dir, "calendars", "day.txt")
        if not os.path.exists(cal_path):
            return None

        try:
            lines = Path(cal_path).read_text().strip().split("\n")
            lines = [l.strip() for l in lines if l.strip()]
            if lines:
                sorted_dates = sorted(lines)
                return {"start": sorted_dates[0], "end": sorted_dates[-1]}
        except Exception as e:
            logger.warning("Failed to read calendar at %s: %s", cal_path, e)

        return None

    @staticmethod
    def _count_instruments(market_dir: str) -> int:
        """Count instruments in instruments/all.txt."""
        inst_path = os.path.join(market_dir, "instruments", "all.txt")
        if not os.path.exists(inst_path):
            return 0

        try:
            lines = Path(inst_path).read_text().strip().split("\n")
            return len([l for l in lines if l.strip()])
        except Exception:
            return 0

    @staticmethod
    def _save_metadata(
        data_dir: str, market: str, result: Dict[str, Any]
    ) -> None:
        """Update sync_metadata.json with latest sync result."""
        meta_path = os.path.join(data_dir, "sync_metadata.json")
        metadata: Dict[str, Any] = {}

        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # Determine market data dir for date range
        subdir = QlibContext.REGION_TO_DATA_DIR.get(market, f"{market}_data")
        market_dir = os.path.join(data_dir, subdir)
        date_range = DataSyncService._get_date_range(market_dir)

        metadata[market] = {
            "last_sync": datetime.now().isoformat(),
            "symbol_count": result.get("symbol_count", 0),
            "error_count": len(result.get("errors", [])),
            "duration_s": result.get("duration_s", 0),
            "date_range": date_range,
        }

        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)
