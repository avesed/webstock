"""Options put/call ratio collection and retrieval.

Fetches the options chain for each US symbol and computes the put/call
open interest ratio for the nearest expiry approximately 30 days out.

A high put/call ratio (>1.0) signals bearish sentiment; a low ratio
(<0.7) signals bullish positioning.  This is only meaningful for US
markets where listed options are widely traded.

Collection:
    - US only (market != 'us' returns early)
    - Semaphore(5) — options chains are large payloads
    - Finds nearest expiry to today+30 days
    - UPSERT on (symbol, flow_date) with flow_date = today

Retrieval:
    - Returns forward-filled put_call_ratio across daily date spine
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
    INSERT INTO stock_options_flow
        (symbol, market, flow_date,
         put_call_ratio, total_call_oi, total_put_oi, nearest_expiry)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (symbol, flow_date) DO UPDATE SET
        put_call_ratio  = COALESCE(EXCLUDED.put_call_ratio,  stock_options_flow.put_call_ratio),
        total_call_oi   = COALESCE(EXCLUDED.total_call_oi,   stock_options_flow.total_call_oi),
        total_put_oi    = COALESCE(EXCLUDED.total_put_oi,    stock_options_flow.total_put_oi),
        nearest_expiry  = COALESCE(EXCLUDED.nearest_expiry,  stock_options_flow.nearest_expiry)
"""

_SELECT_SQL = """
    SELECT symbol, flow_date AS date, put_call_ratio
    FROM stock_options_flow
    WHERE symbol = ANY($1::text[])
      AND flow_date BETWEEN $2::date AND $3::date
    ORDER BY symbol, flow_date
"""


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        if pd.isna(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


class OptionsService:
    """Collect daily options put/call ratios and build ML features."""

    async def collect_options_flow(
        self,
        market: str,
        symbols: list[str],
    ) -> dict[str, Any]:
        """Daily collection of options put/call ratios.

        US only — returns immediately for other markets.
        Uses Semaphore(5) because options chains are large payloads.
        For each symbol, picks the nearest expiry to today + 30 days
        and computes total put OI / call OI.

        Returns:
            dict with total, success, failed, rows_written counts.
        """
        if market != "us":
            logger.info("Options flow collection skipped for market=%s (US only)", market)
            return {"market": market, "total": 0, "success": 0, "failed": 0, "rows": 0}

        import yfinance as yf

        sem = asyncio.Semaphore(5)
        all_rows: list[tuple] = []
        lock = asyncio.Lock()
        success_count = 0
        failed_count = 0
        today = date.today()
        target_expiry = today + timedelta(days=30)

        async def _fetch_one(symbol: str) -> None:
            nonlocal success_count, failed_count
            async with sem:
                try:
                    row = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._fetch_symbol_options, symbol, market, today,
                            target_expiry, yf,
                        ),
                        timeout=45.0,
                    )
                    if row is not None:
                        async with lock:
                            all_rows.append(row)
                            success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.warning("Failed to collect options for %s: %s", symbol, e)
                finally:
                    await asyncio.sleep(0.5)

        tasks = [_fetch_one(s) for s in symbols]
        await asyncio.gather(*tasks)

        written = 0
        if all_rows:
            written = await self._write_to_db(all_rows)

        logger.info(
            "Options flow collection: market=%s, symbols=%d, "
            "success=%d, failed=%d, rows_written=%d",
            market, len(symbols), success_count, failed_count, written,
        )
        return {
            "market": market,
            "total": len(symbols),
            "success": success_count,
            "failed": failed_count,
            "rows": written,
        }

    @staticmethod
    def _fetch_symbol_options(
        symbol: str,
        market: str,
        today: date,
        target_expiry: date,
        yf: Any,
    ) -> tuple | None:
        """Synchronous yfinance fetch — runs in thread pool."""
        try:
            ticker = yf.Ticker(symbol)
            expiries = ticker.options
        except Exception as e:
            logger.debug("ticker.options failed for %s: %s", symbol, e)
            return None

        if not expiries:
            return None

        # Find nearest expiry to today + 30 days
        best_expiry: str | None = None
        best_diff = 10_000
        for exp_str in expiries:
            try:
                exp_date = date.fromisoformat(exp_str)
                diff = abs((exp_date - target_expiry).days)
                if diff < best_diff:
                    best_diff = diff
                    best_expiry = exp_str
            except ValueError:
                continue

        if best_expiry is None:
            return None

        try:
            chain = ticker.option_chain(best_expiry)
        except Exception as e:
            logger.debug("option_chain(%s) failed for %s: %s", best_expiry, symbol, e)
            return None

        calls = chain.calls
        puts = chain.puts
        if calls is None or puts is None:
            return None

        total_call_oi: int | None = None
        total_put_oi: int | None = None
        try:
            call_oi = calls["openInterest"].sum()
            put_oi  = puts["openInterest"].sum()
            if not pd.isna(call_oi) and not pd.isna(put_oi):
                total_call_oi = int(call_oi)
                total_put_oi  = int(put_oi)
        except Exception:
            pass

        put_call_ratio: float | None = None
        if total_call_oi is not None and total_call_oi > 0 and total_put_oi is not None:
            put_call_ratio = total_put_oi / total_call_oi

        if put_call_ratio is None:
            return None

        nearest_expiry = date.fromisoformat(best_expiry)
        return (
            symbol,
            market,
            today,
            put_call_ratio,
            total_call_oi,
            total_put_oi,
            nearest_expiry,
        )

    async def _write_to_db(self, rows: list[tuple]) -> int:
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return 0
        try:
            async with pool.acquire(timeout=10) as conn:
                await conn.executemany(_UPSERT_SQL, rows)
            logger.info("Wrote %d options flow rows to DB", len(rows))
            return len(rows)
        except Exception as e:
            logger.error("Failed to write options flow to DB: %s", e)
            return 0

    async def get_options_features(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Return forward-filled put_call_ratio for each (symbol, date).

        Returns:
            DataFrame with columns: symbol, date, put_call_ratio.
            Empty DataFrame if no data available.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return pd.DataFrame()

        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as e:
            logger.error("Invalid date format for options query: %s", e)
            return pd.DataFrame()

        lookback_start = start - timedelta(days=90)

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(_SELECT_SQL, symbols, lookback_start, end)
        except Exception as e:
            logger.error("Failed to query stock_options_flow: %s", e)
            return pd.DataFrame()

        if not rows:
            logger.debug("No options flow data for %d symbols", len(symbols))
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])

        full_dates = pd.date_range(start=start_date, end=end_date, freq="D")
        filled_parts: list[pd.DataFrame] = []
        for sym in df["symbol"].unique():
            sym_df = df[df["symbol"] == sym][["date", "put_call_ratio"]].copy()
            sym_df = sym_df.set_index("date").reindex(full_dates)
            sym_df["put_call_ratio"] = sym_df["put_call_ratio"].ffill()
            sym_df["symbol"] = sym
            sym_df = sym_df.reset_index().rename(columns={"index": "date"})
            sym_df = sym_df[sym_df["date"] >= pd.Timestamp(start_date)]
            filled_parts.append(sym_df)

        if not filled_parts:
            return pd.DataFrame()

        result = pd.concat(filled_parts, ignore_index=True)
        result = result.dropna(subset=["put_call_ratio"])
        logger.info(
            "Options features: %d symbols, %d rows",
            result["symbol"].nunique(), len(result),
        )
        return result


# Module singleton
options_service = OptionsService()
