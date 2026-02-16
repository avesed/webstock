"""Service for collecting and querying daily OHLCV bars in PostgreSQL.

Provides incremental collection from CanonicalCacheService and efficient
batch queries returning columnar format for DataFrame construction.
"""

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.canonical_cache_service import get_canonical_cache_service
from app.services.stock_types import Market, detect_market

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
MAX_CONCURRENT_FETCHES = 10
# Commit every N symbols during upsert to bound transaction size
_UPSERT_COMMIT_INTERVAL = 50


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
    ) -> dict[str, Any]:
        """Collect daily bars for a list of symbols and upsert into DB.

        Commits periodically (every 50 symbols) to keep transaction size bounded.
        Returns summary dict with symbol_count, new_bars, and errors.
        """
        if not symbols:
            return {"symbol_count": 0, "new_bars": 0, "errors": []}

        logger.info(
            "Starting daily bar collection: market=%s, symbols=%d",
            market, len(symbols),
        )

        # 1. Query latest dates per symbol already in DB
        latest_dates = await self._get_latest_dates_for_symbols(db, symbols)

        # 2. Fetch bars concurrently with semaphore
        cache_svc = await get_canonical_cache_service()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        errors: list[str] = []
        all_bars: list[tuple[str, str, list[dict]]] = []  # (symbol, data_source, bars)
        completed = 0

        async def fetch_one(symbol: str) -> None:
            nonlocal completed
            async with semaphore:
                try:
                    bars = await self._fetch_symbol_bars(
                        cache_svc, symbol, latest_dates.get(symbol),
                    )
                    if bars:
                        detected_market = detect_market(symbol)
                        data_source = _MARKET_DATA_SOURCE.get(
                            detected_market.value, "yfinance"
                        )
                        all_bars.append((symbol, data_source, bars))
                    elif bars is not None:
                        # Empty list -- up to date
                        pass
                    else:
                        logger.warning(
                            "No data returned for %s (market=%s)", symbol, market,
                        )
                except Exception as exc:
                    msg = f"{symbol}: {exc}"
                    errors.append(msg)
                    logger.error("Failed to fetch bars for %s: %s", symbol, exc)
                finally:
                    completed += 1
                    if completed % 200 == 0 or completed == len(symbols):
                        logger.info(
                            "Fetch progress: %d/%d completed (%d with data, %d errors)",
                            completed, len(symbols), len(all_bars), len(errors),
                        )

        tasks = []
        for i, symbol in enumerate(symbols):
            tasks.append(fetch_one(symbol))
            if (i + 1) % 100 == 0:
                logger.info("Scheduled fetch for %d/%d symbols", i + 1, len(symbols))

        await asyncio.gather(*tasks)

        # 3. Batch upsert into DB with periodic commits
        total_inserted = 0
        symbols_since_commit = 0
        for symbol, data_source, bars in all_bars:
            try:
                count = await self._batch_upsert(db, symbol, market, data_source, bars)
                total_inserted += count
            except Exception as exc:
                msg = f"{symbol}: upsert error - {exc}"
                errors.append(msg)
                logger.error("Upsert failed for %s: %s", symbol, exc)

            symbols_since_commit += 1
            if symbols_since_commit >= _UPSERT_COMMIT_INTERVAL:
                try:
                    await db.commit()
                except Exception as exc:
                    logger.error(
                        "Failed to commit daily bars batch: market=%s, "
                        "symbols_in_batch=%d: %s",
                        market, symbols_since_commit, exc,
                    )
                    raise
                symbols_since_commit = 0

        # Final commit for remaining symbols
        if symbols_since_commit > 0:
            try:
                await db.commit()
            except Exception as exc:
                logger.error(
                    "Failed to commit final daily bars: market=%s, "
                    "pending_symbols=%d, total_inserted=%d: %s",
                    market, symbols_since_commit, total_inserted, exc,
                )
                raise

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

    async def _fetch_symbol_bars(
        self,
        cache_svc: Any,
        symbol: str,
        last_date: date | None,
    ) -> Optional[list[dict]]:
        """Fetch daily bars from CanonicalCacheService for one symbol.

        If last_date is None, fetches full history. Otherwise fetches
        incrementally from last_date + 1 day.
        """
        detected_market = detect_market(symbol)
        today = date.today()

        if last_date is None:
            # Full history fetch
            logger.debug("Fetching full history for %s", symbol)
            bars = await cache_svc.get_history(
                symbol=symbol,
                interval="1d",
                period_days=99999,
                market=detected_market,
            )
        else:
            start = last_date + timedelta(days=1)
            if start > today:
                return []  # Already up to date
            delta_days = (today - start).days + 1
            logger.debug(
                "Fetching bars for %s: last_date=%s, period_days=%d",
                symbol, last_date, delta_days,
            )
            bars = await cache_svc.get_history(
                symbol=symbol,
                interval="1d",
                period_days=delta_days,
                market=detected_market,
                start=str(start),
                end=str(today),
            )

        if not bars:
            return bars  # None or empty list

        # Filter out bars with dates on or before last_date (safety dedup)
        if last_date is not None:
            filtered = []
            for bar in bars:
                bar_date = _parse_bar_date(bar.get("date", ""))
                if bar_date is not None and bar_date > last_date:
                    filtered.append(bar)
            if len(bars) != len(filtered):
                logger.debug(
                    "Filtered %d -> %d bars for %s (dedup before %s)",
                    len(bars), len(filtered), symbol, last_date,
                )
            return filtered

        return bars

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
                params[f"{prefix}_v"] = int(bar.get("volume", 0))
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
