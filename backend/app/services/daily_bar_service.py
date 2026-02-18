"""Service for collecting and querying daily OHLCV bars in PostgreSQL.

CN market uses akshare direct path (ak.stock_zh_a_hist, dedicated thread pool).
US/HK/Metal use yfinance batch download (50 symbols/call, 5 concurrent batches).
Bars are stored via INSERT ON CONFLICT DO NOTHING upsert with periodic commits.
"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.stock_types import Market, detect_market, normalize_symbol

logger = logging.getLogger(__name__)

# Market -> data_source mapping for the DB column
_MARKET_DATA_SOURCE: dict[str, str] = {
    Market.US.value: "yfinance",
    Market.HK.value: "yfinance",
    Market.METAL.value: "yfinance",
    Market.SH.value: "akshare",
    Market.SZ.value: "akshare",
}

# CN markets use "cn" as the unified market value in the DB
_CN_MARKETS = {Market.SH.value, Market.SZ.value}

INSERT_CHUNK_SIZE = 500
# CN direct path: conservative concurrency to avoid Eastmoney rate limiting
_CN_MAX_CONCURRENT = 12
_CN_FETCH_TIMEOUT = 60  # seconds per symbol
# yfinance batch path for US/HK/Metal markets
_YF_BATCH_SIZE = 50           # symbols per yfinance.download() call
_YF_MAX_CONCURRENT_BATCHES = 5  # simultaneous batch downloads
# Commit every N symbols during upsert to bound transaction size
_UPSERT_COMMIT_INTERVAL = 50

# yfinance.shared._DFS is a global dict not safe for concurrent use across threads.
# Serialize all yf.download() calls with this lock to prevent RuntimeError.
_yf_download_lock = threading.Lock()

# Dedicated thread pool for bulk CN daily bar collection (not shared with real-time path)
_cn_bulk_executor: Optional[ThreadPoolExecutor] = None
_cn_bulk_executor_lock = asyncio.Lock()


async def _get_cn_bulk_executor() -> ThreadPoolExecutor:
    global _cn_bulk_executor
    if _cn_bulk_executor is None:
        async with _cn_bulk_executor_lock:
            if _cn_bulk_executor is None:
                _cn_bulk_executor = ThreadPoolExecutor(max_workers=_CN_MAX_CONCURRENT)
    return _cn_bulk_executor


def _resolve_db_market(market_enum: Market) -> str:
    """Map Market enum to the DB market column value."""
    if market_enum in (Market.SH, Market.SZ):
        return "cn"
    return market_enum.value


def _parse_bar_date(date_str: str) -> Optional[date]:
    """Parse a date from canonical cache bar dict (e.g. '2025-01-15T00:00:00')."""
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


class DailyBarService:
    """Collects daily OHLCV bars from providers and persists to stock_daily_bars."""

    async def collect_market(
        self,
        db: AsyncSession,
        market: str,
        symbols: list[str],
        on_progress=None,
    ) -> dict[str, Any]:
        """Collect daily bars for a list of symbols and upsert into DB.

        Commits periodically (every 50 symbols) to keep transaction size bounded.
        Returns summary dict with symbol_count, new_bars, and errors.

        Args:
            on_progress: Optional async callable(completed, total, with_data, error_count)
                         called every 50 symbols during fetch phase.
        """
        if not symbols:
            return {"symbol_count": 0, "new_bars": 0, "errors": []}

        logger.info(
            "Starting daily bar collection: market=%s, symbols=%d",
            market, len(symbols),
        )

        # 1. Query latest dates per symbol already in DB
        latest_dates = await self._get_latest_dates_for_symbols(db, symbols)

        # 2. Fetch bars from provider and upsert to DB
        # CN: download all first (concurrent akshare), then upsert — CN tasks
        #     run well within the time limit (~1706 symbols * 1 req = minutes).
        # US/HK/Metal: download + upsert per batch so partial progress survives
        #     a task timeout/restart (12K+ US symbols need several hours for
        #     the initial bootstrap; inline commits let the next run resume).
        errors: list[str] = []
        total_inserted = 0

        if market == "cn":
            all_bars: list[tuple[str, str, list[dict]]] = []
            completed = 0
            executor = await _get_cn_bulk_executor()
            semaphore = asyncio.Semaphore(_CN_MAX_CONCURRENT)

            async def fetch_one(symbol: str) -> None:
                nonlocal completed
                async with semaphore:
                    try:
                        bars = await self._fetch_cn_bars_direct(
                            symbol, latest_dates.get(symbol), executor,
                        )
                        if bars:
                            all_bars.append((symbol, "akshare", bars))
                        elif bars is None:
                            logger.warning(
                                "No data returned for %s (market=cn)", symbol,
                            )
                    except Exception as exc:
                        errors.append(f"{symbol}: {exc}")
                        logger.error("Failed to fetch CN bars for %s: %s", symbol, exc)
                    finally:
                        completed += 1
                        if completed % 200 == 0 or completed == len(symbols):
                            logger.info(
                                "Fetch progress: %d/%d completed (%d with data, %d errors)",
                                completed, len(symbols), len(all_bars), len(errors),
                            )
                        if on_progress and (completed % 50 == 0 or completed == len(symbols)):
                            try:
                                await on_progress(completed, len(symbols), len(all_bars), len(errors))
                            except Exception:
                                pass

            tasks = [fetch_one(symbol) for symbol in symbols]
            await asyncio.gather(*tasks)

            # Upsert all CN bars with periodic commits
            symbols_since_commit = 0
            for symbol, data_source, bars in all_bars:
                try:
                    count = await self._batch_upsert(db, symbol, market, data_source, bars)
                    total_inserted += count
                except Exception as exc:
                    errors.append(f"{symbol}: upsert error - {exc}")
                    logger.error("Upsert failed for %s: %s", symbol, exc)
                symbols_since_commit += 1
                if symbols_since_commit >= _UPSERT_COMMIT_INTERVAL:
                    await db.commit()
                    symbols_since_commit = 0
            if symbols_since_commit > 0:
                await db.commit()

        else:
            # US / HK / Metal: download + upsert per batch (inline).
            # Each 50-symbol batch is committed immediately so that partial
            # progress persists if the task is killed by the time limit.
            total_inserted, yf_errors = await self._collect_yf_batches(
                db, market, symbols, latest_dates, on_progress,
            )
            errors.extend(yf_errors)

        logger.info(
            "Daily bar collection complete: market=%s, symbols=%d, new_bars=%d, errors=%d",
            market, len(symbols), total_inserted, len(errors),
        )

        return {
            "symbol_count": len(symbols),
            "new_bars": total_inserted,
            "errors": errors,
        }

    async def get_bars_batch(
        self,
        db: AsyncSession,
        symbols: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, dict[str, list]]:
        """Query bars from DB in columnar format for efficient DataFrame construction.

        Returns dict keyed by symbol, each containing lists of dates, open, high,
        low, close, volume.
        """
        if not symbols:
            return {}

        logger.info(
            "Querying bars: symbols=%d, start=%s, end=%s",
            len(symbols), start_date, end_date,
        )

        query = (
            "SELECT symbol, date, open, high, low, close, volume "
            "FROM stock_daily_bars "
            "WHERE symbol = ANY(:symbols)"
        )
        params: dict[str, Any] = {"symbols": symbols}

        if start_date is not None:
            query += " AND date >= :start_date"
            params["start_date"] = start_date
        if end_date is not None:
            query += " AND date <= :end_date"
            params["end_date"] = end_date

        query += " ORDER BY symbol, date"

        result = await db.execute(text(query), params)
        rows = result.fetchall()

        grouped: dict[str, dict[str, list]] = {}
        for row in rows:
            sym = row[0]
            if sym not in grouped:
                grouped[sym] = {
                    "dates": [],
                    "open": [],
                    "high": [],
                    "low": [],
                    "close": [],
                    "volume": [],
                }
            col = grouped[sym]
            col["dates"].append(str(row[1]))
            col["open"].append(float(row[2]) if isinstance(row[2], Decimal) else row[2])
            col["high"].append(float(row[3]) if isinstance(row[3], Decimal) else row[3])
            col["low"].append(float(row[4]) if isinstance(row[4], Decimal) else row[4])
            col["close"].append(float(row[5]) if isinstance(row[5], Decimal) else row[5])
            col["volume"].append(int(row[6]))

        logger.info(
            "Returned bars for %d/%d symbols, %d total rows",
            len(grouped), len(symbols), len(rows),
        )

        return grouped

    async def get_latest_dates(
        self,
        db: AsyncSession,
        market: str,
    ) -> dict[str, date]:
        """Get the latest data date per symbol for a given market."""
        result = await db.execute(
            text(
                "SELECT symbol, MAX(date) FROM stock_daily_bars "
                "WHERE market = :market GROUP BY symbol"
            ),
            {"market": market},
        )
        return {row[0]: row[1] for row in result.fetchall()}

    async def get_symbols_in_db(
        self,
        db: AsyncSession,
        market: str,
    ) -> list[str]:
        """Get all distinct symbols stored for a given market."""
        result = await db.execute(
            text(
                "SELECT DISTINCT symbol FROM stock_daily_bars "
                "WHERE market = :market"
            ),
            {"market": market},
        )
        return [row[0] for row in result.fetchall()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_latest_dates_for_symbols(
        self,
        db: AsyncSession,
        symbols: list[str],
    ) -> dict[str, date]:
        """Query the latest bar date per symbol.

        Symbols encode their market (e.g. AAPL, 0700.HK, 600000.SS), so no
        market filter is needed -- the unique constraint (symbol, date)
        already provides the correct grouping.
        """
        result = await db.execute(
            text(
                "SELECT symbol, MAX(date) FROM stock_daily_bars "
                "WHERE symbol = ANY(:symbols) "
                "GROUP BY symbol"
            ),
            {"symbols": symbols},
        )
        return {row[0]: row[1] for row in result.fetchall()}

    async def _fetch_cn_bars_direct(
        self,
        symbol: str,
        last_date: Optional[date],
        executor: ThreadPoolExecutor,
    ) -> Optional[list[dict]]:
        """Fetch CN daily bars directly from akshare, bypassing CanonicalCacheService.

        Uses ak.stock_zh_a_hist (Eastmoney) with a dedicated thread pool.
        Returns list of bar dicts with keys: date, open, high, low, close, volume.
        Returns None on fetch failure, empty list if already up to date.
        """
        detected_market = detect_market(symbol)
        code = normalize_symbol(symbol, detected_market)
        today = date.today()

        if last_date is None:
            start_str = "19900101"
        else:
            start = last_date + timedelta(days=1)
            if start >= today:
                return []  # Already up to date
            start_str = start.strftime("%Y%m%d")

        end_str = today.strftime("%Y%m%d")

        def fetch() -> Any:
            import akshare as ak
            return ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_str,
                end_date=end_str,
                adjust="qfq",
            )

        loop = asyncio.get_running_loop()
        try:
            df = await asyncio.wait_for(
                loop.run_in_executor(executor, fetch),
                timeout=_CN_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout fetching CN bars for %s (code=%s)", symbol, code)
            return None

        if df is None or df.empty:
            return []

        bars: list[dict] = []
        for _, row in df.iterrows():
            date_val = row.get("日期")
            if date_val is None:
                continue
            bars.append({
                "date": str(date_val)[:10],
                "open": float(row.get("开盘") or 0),
                "high": float(row.get("最高") or 0),
                "low": float(row.get("最低") or 0),
                "close": float(row.get("收盘") or 0),
                "volume": int(row.get("成交量") or 0),
            })

        # Safety dedup: drop bars on or before last_date
        if last_date is not None:
            last_str = str(last_date)
            bars = [b for b in bars if b["date"] > last_str]

        return bars

    async def _collect_yf_batches(
        self,
        db: AsyncSession,
        market: str,
        symbols: list[str],
        latest_dates: dict[str, date],
        on_progress: Any,
    ) -> tuple[int, list[str]]:
        """Download yfinance batches and upsert each batch immediately to DB.

        Committing after each 50-symbol batch means partial progress survives
        a Celery task timeout: the next run's _get_latest_dates_for_symbols()
        will skip already-inserted symbols via the incremental path.

        Returns (total_inserted, errors).
        """
        from collections import defaultdict

        today = date.today()
        total_inserted = 0
        errors: list[str] = []

        # Group symbols by the start_date they need.
        date_groups: dict[Optional[str], list[tuple[str, Optional[date]]]] = defaultdict(list)
        up_to_date_count = 0

        for sym in symbols:
            last_date = latest_dates.get(sym)
            if last_date is None:
                date_groups[None].append((sym, None))
            else:
                start = last_date + timedelta(days=1)
                if start >= today:
                    up_to_date_count += 1
                    continue
                date_groups[start.strftime("%Y-%m-%d")].append((sym, last_date))

        if up_to_date_count:
            logger.info("Skipped %d already-up-to-date symbols", up_to_date_count)

        # Build flat list of (start_str, batch) chunks
        batches: list[tuple[Optional[str], list[tuple[str, Optional[date]]]]] = []
        for start_key, group in date_groups.items():
            for i in range(0, len(group), _YF_BATCH_SIZE):
                batches.append((start_key, group[i : i + _YF_BATCH_SIZE]))

        total_symbols = len(symbols) - up_to_date_count
        logger.info(
            "yfinance batch download: %d batches, %d date groups, %d symbols to fetch",
            len(batches), len(date_groups), total_symbols,
        )

        semaphore = asyncio.Semaphore(_YF_MAX_CONCURRENT_BATCHES)
        batches_done = 0
        symbols_done = up_to_date_count
        symbols_with_data = 0

        async def run_batch(
            start_str: Optional[str],
            batch: list[tuple[str, Optional[date]]],
        ) -> None:
            nonlocal batches_done, symbols_done, symbols_with_data, total_inserted
            async with semaphore:
                try:
                    batch_bars, batch_errs = await self._fetch_yf_batch(start_str, batch)
                    errors.extend(batch_errs)

                    # Upsert this batch immediately and commit so progress
                    # persists even if the task is killed later.
                    for symbol, data_source, bars in batch_bars:
                        try:
                            count = await self._batch_upsert(
                                db, symbol, market, data_source, bars,
                            )
                            total_inserted += count
                        except Exception as exc:
                            errors.append(f"{symbol}: upsert error - {exc}")
                            logger.error("Upsert failed for %s: %s", symbol, exc)

                    if batch_bars:
                        await db.commit()
                        symbols_with_data += len(batch_bars)

                except Exception as exc:
                    logger.error(
                        "Batch download failed (start=%s, size=%d): %s",
                        start_str, len(batch), exc,
                    )
                    errors.extend(f"{sym}: batch error - {exc}" for sym, _ in batch)
                finally:
                    batches_done += 1
                    symbols_done += len(batch)
                    if batches_done % 20 == 0 or batches_done == len(batches):
                        logger.info(
                            "Batch progress: %d/%d batches done, %d/%d symbols, "
                            "%d inserted, %d errors",
                            batches_done, len(batches), symbols_done, len(symbols),
                            total_inserted, len(errors),
                        )
                    if on_progress and (symbols_done % 500 == 0 or batches_done == len(batches)):
                        try:
                            await on_progress(
                                symbols_done, len(symbols), symbols_with_data, len(errors),
                            )
                        except Exception:
                            pass

        await asyncio.gather(*[run_batch(s, b) for s, b in batches])
        return total_inserted, errors

    async def _fetch_yf_batch(
        self,
        start_str: Optional[str],
        symbols_with_last_dates: list[tuple[str, Optional[date]]],
    ) -> tuple[list[tuple[str, str, list[dict]]], list[str]]:
        """Download a batch of symbols from yfinance in a single call.

        Returns (bars_list, errors) where bars_list items are (symbol, data_source, bars).
        Symbols absent from the response (delisted, invalid) are silently skipped.
        """
        import pandas as pd
        import yfinance as yf

        symbols = [sym for sym, _ in symbols_with_last_dates]
        last_dates_map = {sym: ld for sym, ld in symbols_with_last_dates}

        # Use a safe far-past date for full-history downloads
        actual_start = start_str if start_str is not None else "1970-01-01"

        def download() -> Any:
            # yfinance.shared._DFS is a global, non-thread-safe accumulator.
            # Serialize downloads to prevent concurrent thread corruption.
            with _yf_download_lock:
                return yf.download(
                    symbols,
                    start=actual_start,
                    auto_adjust=True,
                    progress=False,
                )

        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, download)

        if df is None or df.empty:
            logger.warning(
                "Empty batch response: %d symbols, start=%s", len(symbols), actual_start,
            )
            return [], []

        is_multi = isinstance(df.columns, pd.MultiIndex)
        results: list[tuple[str, str, list[dict]]] = []
        errors: list[str] = []

        for sym in symbols:
            try:
                if is_multi:
                    sym_df = df[
                        [("Open", sym), ("High", sym), ("Low", sym), ("Close", sym), ("Volume", sym)]
                    ].copy()
                    sym_df.columns = ["open", "high", "low", "close", "volume"]
                else:
                    # Single-symbol fallback (flat columns)
                    sym_df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
                    sym_df.columns = ["open", "high", "low", "close", "volume"]

                # Drop rows with missing or zero close price
                sym_df = sym_df.dropna(subset=["close"])
                sym_df = sym_df[sym_df["close"] > 0]

                # Dedup: only keep bars strictly after last_date
                last_date = last_dates_map.get(sym)
                if last_date is not None:
                    sym_df = sym_df[sym_df.index.date > last_date]

                bars = [
                    {
                        "date": str(idx.date()),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row["volume"]),
                    }
                    for idx, row in sym_df.iterrows()
                ]

                if bars:
                    results.append((sym, "yfinance", bars))

            except KeyError:
                # Symbol not present in batch response (delisted / invalid)
                logger.debug("Symbol %s absent from batch download response", sym)
            except Exception as exc:
                errors.append(f"{sym}: {exc}")
                logger.warning("Failed to extract bars for %s from batch: %s", sym, exc)

        return results, errors

    async def _batch_upsert(
        self,
        db: AsyncSession,
        symbol: str,
        market: str,
        data_source: str,
        bars: list[dict],
    ) -> int:
        """Insert bars in chunks using ON CONFLICT DO NOTHING. Returns rows inserted."""
        if not bars:
            return 0

        total = 0
        skipped_dates = 0
        for chunk_start in range(0, len(bars), INSERT_CHUNK_SIZE):
            chunk = bars[chunk_start:chunk_start + INSERT_CHUNK_SIZE]
            values_clauses = []
            params: dict[str, Any] = {}

            for i, bar in enumerate(chunk):
                bar_date = _parse_bar_date(bar.get("date", ""))
                if bar_date is None:
                    skipped_dates += 1
                    continue

                prefix = f"p{chunk_start + i}"
                values_clauses.append(
                    f"(:{prefix}_sym, :{prefix}_mkt, :{prefix}_dt, "
                    f":{prefix}_o, :{prefix}_h, :{prefix}_l, :{prefix}_c, "
                    f":{prefix}_v, :{prefix}_ds)"
                )
                params[f"{prefix}_sym"] = symbol
                params[f"{prefix}_mkt"] = market
                params[f"{prefix}_dt"] = bar_date
                params[f"{prefix}_o"] = bar.get("open", 0)
                params[f"{prefix}_h"] = bar.get("high", 0)
                params[f"{prefix}_l"] = bar.get("low", 0)
                params[f"{prefix}_c"] = bar.get("close", 0)
                params[f"{prefix}_v"] = int(bar.get("volume") or 0)
                params[f"{prefix}_ds"] = data_source

            if not values_clauses:
                continue

            sql = (
                "INSERT INTO stock_daily_bars "
                "(symbol, market, date, open, high, low, close, volume, data_source) "
                "VALUES " + ", ".join(values_clauses) + " "
                "ON CONFLICT (symbol, date) DO NOTHING"
            )
            result = await db.execute(text(sql), params)
            total += result.rowcount

        if skipped_dates > 0:
            logger.warning(
                "Skipped %d bars with unparseable dates for %s", skipped_dates, symbol,
            )

        if total > 0:
            logger.info("Inserted %d bars for %s", total, symbol)

        return total
