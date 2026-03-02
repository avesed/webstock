"""Analyst snapshot and insider activity collection and retrieval.

Fetches daily snapshots of analyst recommendations, price targets,
EPS revisions, growth estimates, and insider transactions from yfinance,
then upserts into stock_analyst_snapshots and stock_insider_activity.

Features derived for ML training:
    analyst_buy_ratio   — % analysts with Buy/Strong-Buy rating
    analyst_net_score   — (strongBuy+buy - sell-strongSell) / total
    eps_revision_score  — (up7d - down7d) / max(up7d+down7d, 1)
    target_premium      — analyst mean target / current close - 1
    growth_est_next_y   — next-year consensus EPS growth estimate
    net_shares_pct      — insider net share change as % of outstanding
    insider_ownership_pct — % of shares held by insiders

All features are forward-filled across the daily date spine since
snapshots are collected once per day.
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_ANALYST_UPSERT_SQL = """
    INSERT INTO stock_analyst_snapshots
        (symbol, market, snapshot_date,
         target_price_mean, target_price_high, target_price_low,
         analyst_buy, analyst_hold, analyst_sell,
         analyst_strong_buy, analyst_strong_sell,
         eps_revision_up_7d, eps_revision_down_7d,
         eps_revision_up_30d, eps_revision_down_30d,
         growth_est_current_q, growth_est_next_y)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
    ON CONFLICT (symbol, snapshot_date) DO UPDATE SET
        target_price_mean   = COALESCE(EXCLUDED.target_price_mean,   stock_analyst_snapshots.target_price_mean),
        target_price_high   = COALESCE(EXCLUDED.target_price_high,   stock_analyst_snapshots.target_price_high),
        target_price_low    = COALESCE(EXCLUDED.target_price_low,    stock_analyst_snapshots.target_price_low),
        analyst_buy         = COALESCE(EXCLUDED.analyst_buy,         stock_analyst_snapshots.analyst_buy),
        analyst_hold        = COALESCE(EXCLUDED.analyst_hold,        stock_analyst_snapshots.analyst_hold),
        analyst_sell        = COALESCE(EXCLUDED.analyst_sell,        stock_analyst_snapshots.analyst_sell),
        analyst_strong_buy  = COALESCE(EXCLUDED.analyst_strong_buy,  stock_analyst_snapshots.analyst_strong_buy),
        analyst_strong_sell = COALESCE(EXCLUDED.analyst_strong_sell, stock_analyst_snapshots.analyst_strong_sell),
        eps_revision_up_7d  = COALESCE(EXCLUDED.eps_revision_up_7d,  stock_analyst_snapshots.eps_revision_up_7d),
        eps_revision_down_7d  = COALESCE(EXCLUDED.eps_revision_down_7d,  stock_analyst_snapshots.eps_revision_down_7d),
        eps_revision_up_30d = COALESCE(EXCLUDED.eps_revision_up_30d, stock_analyst_snapshots.eps_revision_up_30d),
        eps_revision_down_30d = COALESCE(EXCLUDED.eps_revision_down_30d, stock_analyst_snapshots.eps_revision_down_30d),
        growth_est_current_q = COALESCE(EXCLUDED.growth_est_current_q, stock_analyst_snapshots.growth_est_current_q),
        growth_est_next_y   = COALESCE(EXCLUDED.growth_est_next_y,   stock_analyst_snapshots.growth_est_next_y)
"""

_INSIDER_UPSERT_SQL = """
    INSERT INTO stock_insider_activity
        (symbol, market, activity_date,
         net_shares_pct, buy_transactions, sell_transactions,
         insider_ownership_pct)
    VALUES ($1,$2,$3,$4,$5,$6,$7)
    ON CONFLICT (symbol, activity_date) DO UPDATE SET
        net_shares_pct       = COALESCE(EXCLUDED.net_shares_pct,       stock_insider_activity.net_shares_pct),
        buy_transactions     = COALESCE(EXCLUDED.buy_transactions,     stock_insider_activity.buy_transactions),
        sell_transactions    = COALESCE(EXCLUDED.sell_transactions,    stock_insider_activity.sell_transactions),
        insider_ownership_pct = COALESCE(EXCLUDED.insider_ownership_pct, stock_insider_activity.insider_ownership_pct)
"""

_ANALYST_SELECT_SQL = """
    SELECT symbol, snapshot_date AS date,
           target_price_mean, analyst_buy, analyst_hold, analyst_sell,
           analyst_strong_buy, analyst_strong_sell,
           eps_revision_up_7d, eps_revision_down_7d,
           growth_est_next_y
    FROM stock_analyst_snapshots
    WHERE symbol = ANY($1::text[])
      AND snapshot_date BETWEEN $2::date AND $3::date
    ORDER BY symbol, snapshot_date
"""

_INSIDER_SELECT_SQL = """
    SELECT symbol, activity_date AS date,
           net_shares_pct, insider_ownership_pct
    FROM stock_insider_activity
    WHERE symbol = ANY($1::text[])
      AND activity_date BETWEEN $2::date AND $3::date
    ORDER BY symbol, activity_date
"""

# For target_premium we need to join with stock_daily_bars
_TARGET_PREMIUM_SQL = """
    SELECT a.symbol, a.snapshot_date AS date,
           a.target_price_mean,
           b.close
    FROM stock_analyst_snapshots a
    JOIN stock_daily_bars b
        ON b.symbol = a.symbol AND b.date = a.snapshot_date
    WHERE a.symbol = ANY($1::text[])
      AND a.snapshot_date BETWEEN $2::date AND $3::date
      AND a.target_price_mean IS NOT NULL
      AND b.close IS NOT NULL AND b.close > 0
    ORDER BY a.symbol, a.snapshot_date
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


def _safe_int(value: Any) -> int | None:
    v = _safe_float(value)
    return int(round(v)) if v is not None else None


class AnalystService:
    """Collect analyst snapshots + insider activity; build ML features."""

    async def collect_analyst_snapshots(
        self,
        market: str,
        symbols: list[str],
    ) -> dict[str, Any]:
        """Daily collection of analyst data and insider transactions.

        Per symbol (asyncio.to_thread, Semaphore(10), 30s timeout):
        - ticker.analyst_price_targets → target_price_mean/high/low
        - ticker.recommendations_summary → analyst_buy/hold/sell counts
        - ticker.eps_revisions → eps_revision_up/down_7d/30d
        - ticker.growth_estimates → growth_est_current_q/next_y
        - ticker.insider_purchases → net_shares_pct, ownership_pct

        Upserts snapshot_date = today for both tables.
        """
        import yfinance as yf

        sem = asyncio.Semaphore(10)
        analyst_rows: list[tuple] = []
        insider_rows: list[tuple] = []
        lock = asyncio.Lock()
        success_count = 0
        failed_count = 0
        today = date.today()

        async def _fetch_one(symbol: str) -> None:
            nonlocal success_count, failed_count
            async with sem:
                try:
                    a_row, i_row = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._fetch_symbol_analyst, symbol, market, today, yf,
                        ),
                        timeout=30.0,
                    )
                    async with lock:
                        if a_row is not None:
                            analyst_rows.append(a_row)
                        if i_row is not None:
                            insider_rows.append(i_row)
                        if a_row is not None or i_row is not None:
                            success_count += 1
                        else:
                            failed_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.warning("Failed to collect analyst data for %s: %s", symbol, e)
                finally:
                    await asyncio.sleep(0.3)

        tasks = [_fetch_one(s) for s in symbols]
        await asyncio.gather(*tasks)

        analyst_written = 0
        insider_written = 0
        if analyst_rows:
            analyst_written = await self._write_analyst(analyst_rows)
        if insider_rows:
            insider_written = await self._write_insider(insider_rows)

        logger.info(
            "Analyst collection: market=%s, symbols=%d, success=%d, failed=%d, "
            "analyst_rows=%d, insider_rows=%d",
            market, len(symbols), success_count, failed_count,
            analyst_written, insider_written,
        )
        return {
            "market": market,
            "total": len(symbols),
            "success": success_count,
            "failed": failed_count,
            "analyst_rows": analyst_written,
            "insider_rows": insider_written,
        }

    @staticmethod
    def _fetch_symbol_analyst(
        symbol: str,
        market: str,
        today: date,
        yf: Any,
    ) -> tuple[tuple | None, tuple | None]:
        """Synchronous yfinance fetch — runs in thread pool.

        Returns (analyst_row, insider_row), either may be None.
        """
        ticker = yf.Ticker(symbol)

        # ── Analyst price targets ──────────────────────────────────────────
        target_mean = target_high = target_low = None
        try:
            pt = ticker.analyst_price_targets
            if pt is not None and not (isinstance(pt, pd.DataFrame) and pt.empty):
                if isinstance(pt, dict):
                    target_mean = _safe_float(pt.get("mean") or pt.get("targetMeanPrice"))
                    target_high = _safe_float(pt.get("high") or pt.get("targetHighPrice"))
                    target_low  = _safe_float(pt.get("low") or pt.get("targetLowPrice"))
                elif isinstance(pt, pd.DataFrame) and "mean" in pt.columns:
                    row = pt.iloc[0]
                    target_mean = _safe_float(row.get("mean"))
                    target_high = _safe_float(row.get("high"))
                    target_low  = _safe_float(row.get("low"))
        except Exception as e:
            logger.debug("analyst_price_targets failed for %s: %s", symbol, e)

        # ── Recommendations summary ───────────────────────────────────────
        buy = hold = sell = strong_buy = strong_sell = None
        try:
            rec = ticker.recommendations_summary
            if rec is not None and isinstance(rec, pd.DataFrame) and not rec.empty:
                # Most recent period (row 0)
                row = rec.iloc[0]
                strong_buy  = _safe_int(row.get("strongBuy"))
                buy         = _safe_int(row.get("buy"))
                hold        = _safe_int(row.get("hold"))
                sell        = _safe_int(row.get("sell"))
                strong_sell = _safe_int(row.get("strongSell"))
        except Exception as e:
            logger.debug("recommendations_summary failed for %s: %s", symbol, e)

        # ── EPS revisions ─────────────────────────────────────────────────
        rev_up_7d = rev_down_7d = rev_up_30d = rev_down_30d = None
        try:
            rev = ticker.eps_revisions
            if rev is not None and isinstance(rev, pd.DataFrame) and not rev.empty:
                # Row index is period; '0q' = current quarter
                if "0q" in rev.index:
                    r = rev.loc["0q"]
                    rev_up_7d   = _safe_int(r.get("upLast7days"))
                    rev_down_7d = _safe_int(r.get("downLast7days"))
                if "1q" in rev.index:
                    r = rev.loc["1q"]
                    rev_up_30d   = _safe_int(r.get("upLast7days"))  # 30d proxy via next quarter
                    rev_down_30d = _safe_int(r.get("downLast7days"))
                # Prefer explicit 30d columns if available
                if "0q" in rev.index:
                    r = rev.loc["0q"]
                    if "upLast30days" in r.index:
                        rev_up_30d   = _safe_int(r.get("upLast30days"))
                        rev_down_30d = _safe_int(r.get("downLast30days"))
        except Exception as e:
            logger.debug("eps_revisions failed for %s: %s", symbol, e)

        # ── Growth estimates ──────────────────────────────────────────────
        growth_current_q = growth_next_y = None
        try:
            ge = ticker.growth_estimates
            if ge is not None and isinstance(ge, pd.DataFrame) and not ge.empty:
                if symbol in ge.columns:
                    col = ge[symbol]
                    if "0q" in col.index:
                        growth_current_q = _safe_float(col["0q"])
                    if "+1y" in col.index:
                        growth_next_y = _safe_float(col["+1y"])
                elif len(ge.columns) == 1:
                    col = ge.iloc[:, 0]
                    if "0q" in col.index:
                        growth_current_q = _safe_float(col["0q"])
                    if "+1y" in col.index:
                        growth_next_y = _safe_float(col["+1y"])
        except Exception as e:
            logger.debug("growth_estimates failed for %s: %s", symbol, e)

        # Build analyst row only if we have at least some data
        analyst_row: tuple | None = None
        has_analyst_data = any(v is not None for v in [
            target_mean, buy, hold, sell, rev_up_7d, growth_next_y,
        ])
        if has_analyst_data:
            analyst_row = (
                symbol, market, today,
                target_mean, target_high, target_low,
                buy, hold, sell, strong_buy, strong_sell,
                rev_up_7d, rev_down_7d, rev_up_30d, rev_down_30d,
                growth_current_q, growth_next_y,
            )

        # ── Insider purchases ─────────────────────────────────────────────
        # yfinance returns a summary DataFrame with transaction type as the index
        # (rows: "Purchases", "Sales", "Net Activity (Purchases)", etc.)
        # not as a column. Handle both summary and transaction-list formats.
        insider_row: tuple | None = None
        try:
            ip = ticker.insider_purchases
            if ip is not None and isinstance(ip, pd.DataFrame) and not ip.empty:
                purchase_shares = 0.0
                sale_shares = 0.0

                # Format 1: summary DataFrame with type in index
                idx_lower = {str(i).lower(): i for i in ip.index}
                shares_col = None
                for col in ("Shares", "shares", "Value", "value"):
                    if col in ip.columns:
                        shares_col = col
                        break

                if shares_col is not None:
                    for key in ("purchases", "purchase"):
                        if key in idx_lower:
                            purchase_shares = _safe_float(ip.loc[idx_lower[key], shares_col]) or 0.0
                            break
                    for key in ("sales", "sale", "sells", "sell"):
                        if key in idx_lower:
                            sale_shares = _safe_float(ip.loc[idx_lower[key], shares_col]) or 0.0
                            break
                elif "Transaction" in ip.columns:
                    # Format 2: transaction-list DataFrame with type in column
                    for _, irow in ip.iterrows():
                        shares = _safe_float(irow.get("Shares")) or 0.0
                        txn_type = str(irow.get("Transaction", "")).lower()
                        if "buy" in txn_type or "purchase" in txn_type:
                            purchase_shares += shares
                        elif "sell" in txn_type or "sale" in txn_type:
                            sale_shares += shares

                net_shares = purchase_shares - sale_shares
                buy_txns = 1 if purchase_shares > 0 else 0
                sell_txns = 1 if sale_shares > 0 else 0

                # insider_ownership from ticker.info
                ownership_pct: float | None = None
                try:
                    info = ticker.info
                    ownership_pct = _safe_float(info.get("heldPercentInsiders"))
                except Exception:
                    pass

                if buy_txns > 0 or sell_txns > 0 or ownership_pct is not None:
                    insider_row = (
                        symbol, market, today,
                        net_shares / 1_000_000,  # in millions for scale
                        buy_txns,
                        sell_txns,
                        ownership_pct,
                    )
        except Exception as e:
            logger.debug("insider_purchases failed for %s: %s", symbol, e)

        return analyst_row, insider_row

    async def _write_analyst(self, rows: list[tuple]) -> int:
        from app.core.settings_cache import settings_cache
        pool = settings_cache.pool
        if not pool:
            return 0
        try:
            async with pool.acquire(timeout=10) as conn:
                await conn.executemany(_ANALYST_UPSERT_SQL, rows)
            return len(rows)
        except Exception as e:
            logger.error("Failed to write analyst snapshots: %s", e)
            return 0

    async def _write_insider(self, rows: list[tuple]) -> int:
        from app.core.settings_cache import settings_cache
        pool = settings_cache.pool
        if not pool:
            return 0
        try:
            async with pool.acquire(timeout=10) as conn:
                await conn.executemany(_INSIDER_UPSERT_SQL, rows)
            return len(rows)
        except Exception as e:
            logger.error("Failed to write insider activity: %s", e)
            return 0

    async def get_analyst_features(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Return forward-filled analyst-derived features for ML training.

        Computed columns:
        - analyst_buy_ratio: (strongBuy + buy) / total_analysts
        - analyst_net_score: (strongBuy + buy - sell - strongSell) / total
        - eps_revision_score: (up7d - down7d) / max(up7d + down7d, 1)
        - target_premium: target_mean / close - 1  (joined with stock_daily_bars)
        - growth_est_next_y: direct from DB

        Returns:
            DataFrame with columns: symbol, date, + feature columns.
            Empty DataFrame if no data.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return pd.DataFrame()

        try:
            start = __import__("datetime").date.fromisoformat(start_date)
            end = __import__("datetime").date.fromisoformat(end_date)
        except ValueError as e:
            logger.error("Invalid date format for analyst feature query: %s", e)
            return pd.DataFrame()

        lookback_start = start - __import__("datetime").timedelta(days=90)

        try:
            async with pool.acquire(timeout=10) as conn:
                analyst_rows = await conn.fetch(_ANALYST_SELECT_SQL, symbols, lookback_start, end)
                premium_rows = await conn.fetch(_TARGET_PREMIUM_SQL, symbols, lookback_start, end)
        except Exception as e:
            logger.error("Failed to query analyst snapshots: %s", e)
            return pd.DataFrame()

        if not analyst_rows:
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in analyst_rows])
        df["date"] = pd.to_datetime(df["date"])

        # Compute derived ratios
        total = (
            df["analyst_buy"].fillna(0)
            + df["analyst_hold"].fillna(0)
            + df["analyst_sell"].fillna(0)
            + df["analyst_strong_buy"].fillna(0)
            + df["analyst_strong_sell"].fillna(0)
        )
        df["analyst_buy_ratio"] = (
            df["analyst_strong_buy"].fillna(0) + df["analyst_buy"].fillna(0)
        ) / total.clip(lower=1)
        df["analyst_net_score"] = (
            df["analyst_strong_buy"].fillna(0)
            + df["analyst_buy"].fillna(0)
            - df["analyst_sell"].fillna(0)
            - df["analyst_strong_sell"].fillna(0)
        ) / total.clip(lower=1)
        up = df["eps_revision_up_7d"].fillna(0)
        down = df["eps_revision_down_7d"].fillna(0)
        df["eps_revision_score"] = (up - down) / (up + down).clip(lower=1)

        feature_cols = [
            "analyst_buy_ratio", "analyst_net_score",
            "eps_revision_score", "growth_est_next_y",
        ]

        # Merge target_premium
        if premium_rows:
            prem_df = pd.DataFrame([dict(r) for r in premium_rows])
            prem_df["date"] = pd.to_datetime(prem_df["date"])
            prem_df["target_premium"] = (
                prem_df["target_price_mean"] / prem_df["close"] - 1.0
            )
            df = df.merge(
                prem_df[["symbol", "date", "target_premium"]],
                on=["symbol", "date"],
                how="left",
            )
            feature_cols.append("target_premium")

        # Forward-fill per symbol
        full_dates = pd.date_range(start=start_date, end=end_date, freq="D")
        filled_parts: list[pd.DataFrame] = []
        for sym in df["symbol"].unique():
            sym_df = df[df["symbol"] == sym][["date"] + feature_cols].copy()
            sym_df = sym_df.set_index("date").reindex(full_dates)
            sym_df[feature_cols] = sym_df[feature_cols].ffill()
            sym_df["symbol"] = sym
            sym_df = sym_df.reset_index().rename(columns={"index": "date"})
            sym_df = sym_df[sym_df["date"] >= pd.Timestamp(start_date)]
            filled_parts.append(sym_df)

        if not filled_parts:
            return pd.DataFrame()

        result = pd.concat(filled_parts, ignore_index=True)
        result = result.dropna(subset=feature_cols, how="all")
        logger.info(
            "Analyst features: %d symbols, %d rows, cols=%s",
            result["symbol"].nunique(), len(result), feature_cols,
        )
        return result

    async def get_insider_features(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Return forward-filled insider activity features.

        Columns: symbol, date, net_shares_pct, insider_ownership_pct.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return pd.DataFrame()

        try:
            start = __import__("datetime").date.fromisoformat(start_date)
        except ValueError as e:
            logger.error("Invalid date for insider query: %s", e)
            return pd.DataFrame()

        lookback_start = start - timedelta(days=90)

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(
                    _INSIDER_SELECT_SQL,
                    symbols,
                    lookback_start,
                    __import__("datetime").date.fromisoformat(end_date),
                )
        except Exception as e:
            logger.error("Failed to query stock_insider_activity: %s", e)
            return pd.DataFrame()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])

        feature_cols = ["net_shares_pct", "insider_ownership_pct"]
        full_dates = pd.date_range(start=start_date, end=end_date, freq="D")
        filled_parts: list[pd.DataFrame] = []
        for sym in df["symbol"].unique():
            sym_df = df[df["symbol"] == sym][["date"] + feature_cols].copy()
            sym_df = sym_df.set_index("date").reindex(full_dates)
            sym_df[feature_cols] = sym_df[feature_cols].ffill()
            sym_df["symbol"] = sym
            sym_df = sym_df.reset_index().rename(columns={"index": "date"})
            sym_df = sym_df[sym_df["date"] >= pd.Timestamp(start_date)]
            filled_parts.append(sym_df)

        if not filled_parts:
            return pd.DataFrame()

        result = pd.concat(filled_parts, ignore_index=True)
        result = result.dropna(subset=feature_cols, how="all")
        return result


# Module singleton
analyst_service = AnalystService()
