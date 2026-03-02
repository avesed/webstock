"""EPS surprise event collection and retrieval.

Fetches historical earnings dates + EPS estimates/actuals from yfinance
and stores them in stock_earnings_events.  The derived feature
last_eps_surprise is forward-filled from the most recent event on or before
each trading day, giving the ML model a persistent signal between
quarterly earnings releases.

Collection:
    - Source: yfinance Ticker.earnings_dates (returns ~5 quarters)
    - Only rows where epsActual is not None (excludes future scheduled dates)
    - UPSERT on (symbol, earnings_date)

Retrieval:
    - SQL returns all events in [start_date, end_date]
    - pandas pivot + ffill produces a daily surprise_pct series per symbol
"""

import asyncio
import logging
from datetime import date
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
    INSERT INTO stock_earnings_events
        (symbol, market, earnings_date, eps_estimate, eps_actual, surprise_pct)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (symbol, earnings_date) DO UPDATE SET
        eps_estimate = COALESCE(EXCLUDED.eps_estimate, stock_earnings_events.eps_estimate),
        eps_actual   = COALESCE(EXCLUDED.eps_actual,   stock_earnings_events.eps_actual),
        surprise_pct = COALESCE(EXCLUDED.surprise_pct, stock_earnings_events.surprise_pct)
"""

_SELECT_SQL = """
    SELECT symbol, earnings_date AS date, surprise_pct AS last_eps_surprise
    FROM stock_earnings_events
    WHERE symbol = ANY($1::text[])
      AND earnings_date BETWEEN $2::date AND $3::date
      AND eps_actual IS NOT NULL
    ORDER BY symbol, earnings_date
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


class EarningsService:
    """Collect EPS surprise events and build forward-filled ML features."""

    async def collect_earnings_events(
        self,
        market: str,
        symbols: list[str],
    ) -> dict[str, Any]:
        """Fetch earnings_dates for each symbol and upsert to stock_earnings_events.

        Uses asyncio.to_thread + Semaphore(10) + 30s timeout per symbol.
        Skips rows without eps_actual (future scheduled earnings).

        Returns:
            dict with total, success, failed, rows_written counts.
        """
        import yfinance as yf

        sem = asyncio.Semaphore(10)
        all_rows: list[tuple] = []
        lock = asyncio.Lock()
        success_count = 0
        failed_count = 0

        async def _fetch_one(symbol: str) -> None:
            nonlocal success_count, failed_count
            async with sem:
                try:
                    rows = await asyncio.wait_for(
                        asyncio.to_thread(self._fetch_symbol_earnings, symbol, market, yf),
                        timeout=30.0,
                    )
                    if rows:
                        async with lock:
                            all_rows.extend(rows)
                            success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.warning("Failed to collect earnings for %s: %s", symbol, e)
                finally:
                    await asyncio.sleep(0.3)

        tasks = [_fetch_one(s) for s in symbols]
        await asyncio.gather(*tasks)

        written = 0
        if all_rows:
            written = await self._write_to_db(all_rows)

        logger.info(
            "Earnings collection: market=%s, symbols=%d, success=%d, "
            "failed=%d, rows_written=%d",
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
    def _fetch_symbol_earnings(
        symbol: str,
        market: str,
        yf: Any,
    ) -> list[tuple]:
        """Synchronous yfinance fetch — runs in thread pool."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.earnings_dates
        except Exception as e:
            logger.debug("earnings_dates failed for %s: %s", symbol, e)
            return []

        if df is None or df.empty:
            return []

        rows: list[tuple] = []
        for idx, row in df.iterrows():
            # Skip future earnings (eps_actual not yet published)
            eps_actual = _safe_float(row.get("Reported EPS") or row.get("EPS Actual"))
            if eps_actual is None:
                continue

            try:
                earnings_dt = pd.Timestamp(idx)
                if pd.isna(earnings_dt):
                    continue
                earnings_date = earnings_dt.date()
            except Exception:
                continue

            eps_estimate = _safe_float(
                row.get("EPS Estimate") or row.get("Estimated EPS")
            )

            # Compute surprise pct: (actual - estimate) / abs(estimate)
            surprise_pct: float | None = None
            if eps_estimate is not None and eps_estimate != 0:
                surprise_pct = (eps_actual - eps_estimate) / abs(eps_estimate)

            rows.append((
                symbol,
                market,
                earnings_date,
                eps_estimate,
                eps_actual,
                surprise_pct,
            ))

        return rows

    async def _write_to_db(self, rows: list[tuple]) -> int:
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.error("DB pool not available for earnings write")
            return 0
        try:
            async with pool.acquire(timeout=10) as conn:
                await conn.executemany(_UPSERT_SQL, rows)
            logger.info("Wrote %d earnings event rows to DB", len(rows))
            return len(rows)
        except Exception as e:
            logger.error("Failed to write earnings events to DB: %s", e)
            return 0

    async def get_earnings_features(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Return forward-filled last_eps_surprise for each (symbol, date).

        SQL fetches all earnings events in the window. pandas then forward-fills
        the surprise_pct across the daily date spine, so each trading day carries
        the most recent realized EPS surprise.

        Returns:
            DataFrame with columns: symbol, date, last_eps_surprise.
            Empty DataFrame if no data available.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.warning("DB pool not available for earnings feature retrieval")
            return pd.DataFrame()

        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as e:
            logger.error("Invalid date format for earnings query: %s", e)
            return pd.DataFrame()

        # Expand lookback 90 days to capture prior-quarter earnings
        # that predate the training window but should still be forward-filled.
        from datetime import timedelta
        lookback_start = start - timedelta(days=90)

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(_SELECT_SQL, symbols, lookback_start, end)
        except Exception as e:
            logger.error("Failed to query stock_earnings_events: %s", e)
            return pd.DataFrame()

        if not rows:
            logger.debug("No earnings data for %d symbols", len(symbols))
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        df["last_eps_surprise"] = df["last_eps_surprise"].astype(float)

        # Forward-fill per symbol across a daily date spine
        full_dates = pd.date_range(start=start_date, end=end_date, freq="D")
        filled_parts: list[pd.DataFrame] = []
        for sym in df["symbol"].unique():
            sym_df = df[df["symbol"] == sym][["date", "last_eps_surprise"]].copy()
            sym_df = sym_df.set_index("date").reindex(full_dates)
            sym_df["last_eps_surprise"] = sym_df["last_eps_surprise"].ffill()
            sym_df["symbol"] = sym
            sym_df = sym_df.reset_index().rename(columns={"index": "date"})
            # Only keep rows within the actual training window
            sym_df = sym_df[sym_df["date"] >= pd.Timestamp(start_date)]
            filled_parts.append(sym_df)

        if not filled_parts:
            return pd.DataFrame()

        result = pd.concat(filled_parts, ignore_index=True)
        result = result.dropna(subset=["last_eps_surprise"])
        logger.info(
            "Earnings features: %d symbols, %d rows",
            result["symbol"].nunique(), len(result),
        )
        return result


# Module singleton
earnings_service = EarningsService()
