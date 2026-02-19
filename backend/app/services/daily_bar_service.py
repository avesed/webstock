"""Service for collecting and querying daily OHLCV bars in PostgreSQL.

All markets (CN, US, HK, Metal) fetch bars via the data-service HTTP
microservice.  Bars are stored via INSERT ON CONFLICT DO NOTHING upsert
with periodic commits so that partial progress survives task restarts.
"""

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

INSERT_CHUNK_SIZE = 500
# CN batch reduced from 50 to 36: ceil(36/12)*60s = 180s worst case,
# well within the 300s data-service timeout (50 → ceil(50/12)*60 = 300s = zero margin).
_CN_BATCH_SIZE = 36            # symbols per fetch-upsert cycle (bounds memory)
# yfinance batch path for US/HK/Metal markets
_YF_BATCH_SIZE = 50           # symbols per data-service batch call
_YF_MAX_CONCURRENT_BATCHES = 5  # simultaneous batch requests
# Commit every N symbols during upsert to bound transaction size
_UPSERT_COMMIT_INTERVAL = 50


def _parse_bar_date(date_str: str) -> Optional[date]:
    """Parse a date from canonical cache bar dict (e.g. '2025-01-15T00:00:00')."""
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


class DailyBarService:
    """Collects daily OHLCV bars from data-service and persists to stock_daily_bars."""

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

        # 2. Fetch bars from data-service and upsert to DB
        errors: list[str] = []
        total_inserted = 0

        if market == "cn":
            from app.services.data_service_client import get_data_service_client
            client = await get_data_service_client()
            symbols_done = 0
            symbols_with_data = 0
            today = date.today()

            for batch_start in range(0, len(symbols), _CN_BATCH_SIZE):
                batch = symbols[batch_start : batch_start + _CN_BATCH_SIZE]

                # Build request: skip symbols already up to date
                batch_request = []
                skipped = 0
                for sym in batch:
                    last_date = latest_dates.get(sym)
                    if last_date is not None and last_date + timedelta(days=1) >= today:
                        skipped += 1
                        continue
                    start_date = (
                        (last_date + timedelta(days=1)).isoformat()
                        if last_date is not None else None
                    )
                    batch_request.append({"symbol": sym, "start_date": start_date})

                if batch_request:
                    resp = await client.fetch_daily_bars_batch(batch_request, market)
                    if resp and resp.get("results"):
                        for symbol, data in resp["results"].items():
                            try:
                                bars = data.get("bars") if isinstance(data, dict) else []
                                source = data.get("source", "akshare") if isinstance(data, dict) else "akshare"
                                count = await self._batch_upsert(
                                    db, symbol, market, source, bars,
                                )
                                total_inserted += count
                            except Exception as exc:
                                errors.append(f"{symbol}: upsert - {exc}")
                        symbols_with_data += len(resp["results"])
                    if resp and resp.get("errors"):
                        for sym, msg in resp["errors"].items():
                            errors.append(f"{sym}: {msg}")
                    if resp is None:
                        errors.append(f"batch {batch_start // _CN_BATCH_SIZE}: data-service request failed")

                await db.commit()

                symbols_done += len(batch)
                logger.info(
                    "CN batch: %d/%d done (%d with data, %d skipped, %d errors)",
                    symbols_done, len(symbols), symbols_with_data, skipped, len(errors),
                )
                if on_progress:
                    try:
                        await on_progress(
                            symbols_done, len(symbols), symbols_with_data, len(errors),
                        )
                    except Exception:
                        pass

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

    async def delete_market_bars(
        self,
        db: AsyncSession,
        market: str,
    ) -> int:
        """Delete all daily bars for a given market. Returns the number of rows deleted."""
        logger.warning("Deleting all daily bars for market=%s", market)
        result = await db.execute(
            text("DELETE FROM stock_daily_bars WHERE market = :market"),
            {"market": market},
        )
        await db.commit()
        deleted = result.rowcount
        logger.info("Deleted %d daily bars for market=%s", deleted, market)
        return deleted

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

    async def _collect_yf_batches(
        self,
        db: AsyncSession,
        market: str,
        symbols: list[str],
        latest_dates: dict[str, date],
        on_progress: Any,
    ) -> tuple[int, list[str]]:
        """Fetch daily bars via data-service and upsert to DB.

        Two-phase approach:
        1. HTTP fetch phase — parallel with asyncio.gather + semaphore
        2. DB upsert phase — sequential to avoid concurrent AsyncSession access
        """
        from collections import defaultdict
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
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
            "data-service batch download: %d batches, %d date groups, %d symbols to fetch",
            len(batches), len(date_groups), total_symbols,
        )

        # ── Phase 1: Parallel HTTP fetch ──────────────────────────────
        semaphore = asyncio.Semaphore(_YF_MAX_CONCURRENT_BATCHES)
        # Each element: (batch_info, response_or_none)
        fetch_results: list[tuple[Optional[str], list[tuple[str, Optional[date]]], Optional[dict]]] = []

        async def fetch_batch(
            start_str: Optional[str],
            batch: list[tuple[str, Optional[date]]],
        ) -> tuple[Optional[str], list[tuple[str, Optional[date]]], Optional[dict]]:
            async with semaphore:
                try:
                    batch_request = [
                        {"symbol": sym, "start_date": start_str}
                        for sym, _ in batch
                    ]
                    resp = await client.fetch_daily_bars_batch(batch_request, market)
                    return (start_str, batch, resp)
                except Exception as exc:
                    logger.error(
                        "Batch fetch failed (start=%s, size=%d): %s",
                        start_str, len(batch), exc,
                    )
                    errors.extend(f"{sym}: batch error - {exc}" for sym, _ in batch)
                    return (start_str, batch, None)

        fetch_results = await asyncio.gather(
            *[fetch_batch(s, b) for s, b in batches]
        )

        # ── Phase 2: Sequential DB upsert ─────────────────────────────
        symbols_done = up_to_date_count
        symbols_with_data = 0

        for batch_idx, (start_str, batch, resp) in enumerate(fetch_results):
            if resp and resp.get("results"):
                for symbol, data in resp["results"].items():
                    try:
                        bars = data.get("bars") if isinstance(data, dict) else []
                        source = data.get("source", "yfinance") if isinstance(data, dict) else "yfinance"
                        count = await self._batch_upsert(
                            db, symbol, market, source, bars,
                        )
                        total_inserted += count
                    except Exception as exc:
                        errors.append(f"{symbol}: upsert error - {exc}")
                        logger.error("Upsert failed for %s: %s", symbol, exc)

                await db.commit()
                symbols_with_data += len(resp["results"])

            if resp and resp.get("errors"):
                for sym, msg in resp["errors"].items():
                    errors.append(f"{sym}: {msg}")

            if resp is None:
                errors.append(f"batch: data-service request failed (start={start_str}, size={len(batch)})")

            symbols_done += len(batch)
            if (batch_idx + 1) % 20 == 0 or (batch_idx + 1) == len(fetch_results):
                logger.info(
                    "Upsert progress: %d/%d batches done, %d/%d symbols, "
                    "%d inserted, %d errors",
                    batch_idx + 1, len(fetch_results), symbols_done, len(symbols),
                    total_inserted, len(errors),
                )
            if on_progress and (symbols_done % 500 == 0 or (batch_idx + 1) == len(fetch_results)):
                try:
                    await on_progress(
                        symbols_done, len(symbols), symbols_with_data, len(errors),
                    )
                except Exception:
                    pass

        return total_inserted, errors

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
