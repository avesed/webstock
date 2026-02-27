"""Fundamental data collection and retrieval.

Collects PE/PB/ROE and other financial metrics:
- CN: stock_individual_spot_xq (Xueqiu, real-time ~7 fields)
      + stock_financial_analysis_indicator (quarterly ~8 fields)
- US/HK: yfinance Ticker.info -- ~19 fields per stock

Data stored in stock_fundamentals table with daily_snapshot type.
Called by APScheduler for daily collection and by feature_service
for training data retrieval.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Redis progress reporting (writes to DB 0 so backend can read it)
# -----------------------------------------------------------------------

_progress_redis: aioredis.Redis | None = None


async def _get_progress_redis() -> aioredis.Redis:
    """Get Redis connection to DB 0 for progress reporting."""
    global _progress_redis
    if _progress_redis is None:
        settings = get_settings()
        # Force DB 0 regardless of REDIS_URL setting (backend reads from DB 0)
        base_url = settings.REDIS_URL.rsplit("/", 1)[0]
        _progress_redis = aioredis.from_url(f"{base_url}/0", decode_responses=True)
    return _progress_redis


def _progress_key(market: str) -> str:
    return f"kb:fundamentals:{market}:progress"


async def _set_progress(market: str, phase: str, current: int, total: int) -> None:
    """Write collection progress to Redis DB 0."""
    try:
        r = await _get_progress_redis()
        percent = int(current / total * 100) if total > 0 else 0
        data = json.dumps({
            "phase": phase,
            "current": current,
            "total": total,
            "percent": percent,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        await r.set(_progress_key(market), data, ex=3600)  # 1h TTL safety
    except Exception as e:
        logger.debug("Failed to set fundamental progress for %s: %s", market, e)


async def _clear_progress(market: str) -> None:
    """Remove progress key from Redis DB 0."""
    try:
        r = await _get_progress_redis()
        await r.delete(_progress_key(market))
    except Exception as e:
        logger.debug("Failed to clear fundamental progress for %s: %s", market, e)

# -----------------------------------------------------------------------
# Field mappings: external source key -> DB column name
# -----------------------------------------------------------------------

# akshare stock_individual_spot_xq() (Xueqiu) field mapping
# Returns a two-column DataFrame (item, value) with Chinese field names.
# Uses SH/SZ prefix format (e.g. "SH600519").
_CN_FIELD_MAP = {
    "市盈率(TTM)": "pe_ratio",
    "市净率": "pb_ratio",
    "股息率(TTM)": "dividend_yield",  # returned as %, divide by 100
    "每股收益": "eps",
    "每股净资产": "book_value",
    "股息(TTM)": "dividend_rate",
    "资产净值/总市值": "market_cap",
}

# akshare stock_financial_analysis_indicator() quarterly field mapping
# Supplements Xueqiu with profitability/growth metrics from latest quarterly report.
# All percentage fields (%) must be divided by 100 for ratio-form consistency.
_CN_QUARTERLY_FIELD_MAP = {
    "净资产收益率(%)": "roe",
    "总资产利润率(%)": "roa",
    "销售净利率(%)": "profit_margin",
    "营业利润率(%)": "operating_margin",
    "主营业务收入增长率(%)": "revenue_growth_yoy",
    "净利润增长率(%)": "eps_growth",
    "流动比率": "current_ratio",          # NOT a percentage, use as-is
}

_CN_QUARTERLY_PCT_FIELDS = {
    "roe", "roa", "profit_margin", "operating_margin",
    "revenue_growth_yoy", "eps_growth",
}

# yfinance Ticker.info key mapping
_US_HK_FIELD_MAP = {
    "trailingPE": "pe_ratio",
    "priceToBook": "pb_ratio",
    "priceToSalesTrailing12Months": "ps_ratio",
    "returnOnEquity": "roe",
    "returnOnAssets": "roa",
    "profitMargins": "profit_margin",
    "grossMargins": "gross_margin",
    "revenueGrowth": "revenue_growth_yoy",
    "trailingEps": "eps",
    "debtToEquity": "debt_to_equity",  # yfinance returns %, divide by 100
    "currentRatio": "current_ratio",
    "dividendYield": "dividend_yield",
    "marketCap": "market_cap",
    # --- New fields for DB-first financials ---
    "forwardPE": "forward_pe",
    "dividendRate": "dividend_rate",
    "bookValue": "book_value",
    "operatingMargins": "operating_margin",
    "payoutRatio": "payout_ratio",
    "earningsQuarterlyGrowth": "eps_growth",
}

# All DB columns for the INSERT statement (order matters)
_DB_COLUMNS = [
    "symbol", "market", "date", "record_type",
    "pe_ratio", "pb_ratio", "ps_ratio", "roe", "roa",
    "profit_margin", "gross_margin", "revenue", "revenue_growth_yoy",
    "net_income", "eps", "debt_to_equity", "current_ratio",
    "dividend_yield", "market_cap",
    "forward_pe", "dividend_rate", "book_value",
    "operating_margin", "payout_ratio", "eps_growth",
    "data_source",
]

_INSERT_SQL = """
    INSERT INTO stock_fundamentals
        (symbol, market, date, record_type, pe_ratio, pb_ratio, ps_ratio,
         roe, roa, profit_margin, gross_margin, revenue, revenue_growth_yoy,
         net_income, eps, debt_to_equity, current_ratio, dividend_yield,
         market_cap, forward_pe, dividend_rate, book_value,
         operating_margin, payout_ratio, eps_growth, data_source)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26)
    ON CONFLICT (symbol, date, record_type) DO UPDATE SET
        pe_ratio = COALESCE(EXCLUDED.pe_ratio, stock_fundamentals.pe_ratio),
        pb_ratio = COALESCE(EXCLUDED.pb_ratio, stock_fundamentals.pb_ratio),
        ps_ratio = COALESCE(EXCLUDED.ps_ratio, stock_fundamentals.ps_ratio),
        roe = COALESCE(EXCLUDED.roe, stock_fundamentals.roe),
        roa = COALESCE(EXCLUDED.roa, stock_fundamentals.roa),
        profit_margin = COALESCE(EXCLUDED.profit_margin, stock_fundamentals.profit_margin),
        gross_margin = COALESCE(EXCLUDED.gross_margin, stock_fundamentals.gross_margin),
        revenue = COALESCE(EXCLUDED.revenue, stock_fundamentals.revenue),
        revenue_growth_yoy = COALESCE(EXCLUDED.revenue_growth_yoy, stock_fundamentals.revenue_growth_yoy),
        net_income = COALESCE(EXCLUDED.net_income, stock_fundamentals.net_income),
        eps = COALESCE(EXCLUDED.eps, stock_fundamentals.eps),
        debt_to_equity = COALESCE(EXCLUDED.debt_to_equity, stock_fundamentals.debt_to_equity),
        current_ratio = COALESCE(EXCLUDED.current_ratio, stock_fundamentals.current_ratio),
        dividend_yield = COALESCE(EXCLUDED.dividend_yield, stock_fundamentals.dividend_yield),
        market_cap = COALESCE(EXCLUDED.market_cap, stock_fundamentals.market_cap),
        forward_pe = COALESCE(EXCLUDED.forward_pe, stock_fundamentals.forward_pe),
        dividend_rate = COALESCE(EXCLUDED.dividend_rate, stock_fundamentals.dividend_rate),
        book_value = COALESCE(EXCLUDED.book_value, stock_fundamentals.book_value),
        operating_margin = COALESCE(EXCLUDED.operating_margin, stock_fundamentals.operating_margin),
        payout_ratio = COALESCE(EXCLUDED.payout_ratio, stock_fundamentals.payout_ratio),
        eps_growth = COALESCE(EXCLUDED.eps_growth, stock_fundamentals.eps_growth),
        data_source = CASE
            WHEN EXCLUDED.data_source LIKE '%quarterly%' THEN EXCLUDED.data_source
            WHEN stock_fundamentals.data_source LIKE '%quarterly%' THEN stock_fundamentals.data_source
            ELSE EXCLUDED.data_source
        END
"""

_SELECT_SQL = """
    SELECT symbol, date, pe_ratio, pb_ratio, ps_ratio, roe, roa,
           profit_margin, gross_margin, revenue_growth_yoy, eps,
           debt_to_equity, current_ratio, dividend_yield, market_cap,
           forward_pe, dividend_rate, book_value,
           operating_margin, payout_ratio, eps_growth
    FROM stock_fundamentals
    WHERE symbol = ANY($1::text[])
      AND date >= $2::date
      AND date <= $3::date
      AND record_type = 'daily_snapshot'
    ORDER BY symbol, date
"""


def _safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None on failure or NaN."""
    if value is None:
        return None
    try:
        v = float(value)
        if pd.isna(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


class FundamentalService:
    """Collect and retrieve fundamental financial metrics.

    Collection sources:
    - CN (A-shares): stock_individual_spot_xq (Xueqiu, ~7 fields)
                     + stock_financial_analysis_indicator (quarterly, ~8 fields)
    - US/HK: yfinance Ticker.info per symbol (~19 fields)

    Retrieval: asyncpg query with forward-fill for training data.
    """

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    async def collect_market(
        self,
        market: str,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        """Collect fundamental data for a market and store in DB.

        Args:
            market: Market code (cn, us, hk).
            symbols: Optional explicit symbol list. If None, resolved
                     from settings_cache universes or BackendDataClient.

        Returns:
            Summary dict with success/fail/total counts.
        """
        if symbols is None:
            symbols = await self._resolve_symbols(market)

        if not symbols:
            logger.warning("No symbols to collect for market=%s", market)
            return {"market": market, "total": 0, "success": 0, "failed": 0}

        logger.info(
            "Collecting fundamentals: market=%s, symbols=%d",
            market, len(symbols),
        )

        await _set_progress(market, "collecting", 0, len(symbols))
        try:
            if market in ("cn", "sh", "sz"):
                rows = await self._collect_cn_batch(symbols)
            elif market in ("us", "hk"):
                rows = await self._collect_us_hk_batch(symbols, market)
            else:
                logger.warning("Unsupported market for fundamentals: %s", market)
                return {"market": market, "total": len(symbols), "success": 0, "failed": 0}

            await _set_progress(market, "writing", len(rows), len(symbols))

            # Write to DB
            success_count = 0
            if rows:
                success_count = await self._write_to_db(rows)

            failed_count = len(symbols) - success_count
            logger.info(
                "Fundamental collection complete: market=%s, success=%d, failed=%d",
                market, success_count, failed_count,
            )
            return {
                "market": market,
                "total": len(symbols),
                "success": success_count,
                "failed": failed_count,
            }
        finally:
            await _clear_progress(market)

    async def _resolve_symbols(self, market: str) -> list[str]:
        """Resolve symbol list for fundamental data collection.

        Priority:
        1. Universe with explicit symbols
        2. Index-type universe → resolve via data-service constituent API
        3. BackendDataClient.get_symbols() full-market fallback
        """
        try:
            from app.core.settings_cache import settings_cache
            universes = await settings_cache.get_universes(market=market)
            if universes:
                # Try explicit symbols first
                all_symbols: list[str] = []
                for u in universes:
                    if u.symbols:
                        all_symbols.extend(u.symbols)
                if all_symbols:
                    logger.info(
                        "Resolved %d symbols from universe config for market=%s",
                        len(all_symbols), market,
                    )
                    return list(set(all_symbols))

                # Try index-type universe resolution
                for u in universes:
                    if u.universe_type == "index" and u.index_code:
                        try:
                            symbols = await asyncio.wait_for(
                                asyncio.to_thread(
                                    self._get_index_constituents, u.index_code, market,
                                ),
                                timeout=30.0,
                            )
                            if symbols:
                                logger.info(
                                    "Resolved %d symbols from index %s for market=%s",
                                    len(symbols), u.index_code, market,
                                )
                                return symbols
                        except Exception as e:
                            logger.warning(
                                "Index resolution failed for %s: %s", u.index_code, e,
                            )
        except Exception as e:
            logger.warning("Failed to get universes from settings_cache: %s", e)

        # Full-market fallback
        try:
            from app.services.backend_client import get_backend_client
            client = get_backend_client()
            symbols = client.get_symbols(market)
            return symbols
        except Exception as e:
            logger.warning("Failed to get symbols from BackendDataClient: %s", e)
            return []

    @staticmethod
    def _get_index_constituents(index_code: str, market: str) -> list[str]:
        """Synchronous index constituent fetch (for asyncio.to_thread)."""
        from app.services.backend_client import get_backend_client

        client = get_backend_client()
        return client.get_index_constituents(index_code, market)

    async def _collect_cn_batch(self, symbols: list[str]) -> list[tuple]:
        """Collect A-share fundamentals from dual sources in parallel.

        Phase 1: Xueqiu (stock_individual_spot_xq) — ~7 fields (pe, pb, eps,
                 dividend_yield, book_value, dividend_rate, market_cap)
        Phase 2: Quarterly reports (stock_financial_analysis_indicator) — ~8 fields
                 (roe, roa, profit_margin, operating_margin, revenue_growth,
                 eps_growth, current_ratio, gross_margin)

        Both phases run concurrently. Results are merged in memory (Phase 2 only
        fills fields that Phase 1 left as None) before a single DB write.
        """
        today = date.today()
        total = len(symbols)

        # Run both phases in parallel
        phase1_result, phase2_result = await asyncio.gather(
            self._collect_cn_xueqiu(symbols, total),
            self._collect_cn_quarterly_batch(symbols, total),
        )

        # Merge: Phase 2 supplements Phase 1 (only fills None positions)
        all_symbols = set(phase1_result.keys()) | set(phase2_result.keys())
        rows: list[tuple] = []

        for symbol in all_symbols:
            p1 = phase1_result.get(symbol, {})
            p2 = phase2_result.get(symbol, {})

            # Start with Phase 1 data, fill gaps with Phase 2
            merged: dict[str, float | None] = dict(p1)
            for key, val in p2.items():
                if merged.get(key) is None and val is not None:
                    merged[key] = val

            data_source = "xueqiu+quarterly" if (p1 and p2) else (
                "xueqiu" if p1 else "quarterly"
            )
            rows.append(self._build_row(
                symbol=symbol,
                market="cn",
                record_date=today,
                data=merged,
                data_source=data_source,
            ))

        logger.info(
            "CN batch collection: %d/%d succeeded "
            "(phase1=%d, phase2=%d, dual=%d)",
            len(rows), total,
            len(phase1_result), len(phase2_result),
            sum(1 for s in all_symbols
                if s in phase1_result and s in phase2_result),
        )
        return rows

    async def _collect_cn_xueqiu(
        self, symbols: list[str], total: int,
    ) -> dict[str, dict[str, float | None]]:
        """Phase 1: Collect from Xueqiu (stock_individual_spot_xq).

        Returns {symbol: {db_col: value, ...}} for successful fetches.
        """
        import akshare as ak

        sem = asyncio.Semaphore(5)
        results: dict[str, dict[str, float | None]] = {}
        lock = asyncio.Lock()
        completed = 0
        fields_validated = False

        async def _fetch_one(symbol: str) -> None:
            nonlocal completed, fields_validated
            async with sem:
                xq_symbol = self._to_xueqiu_symbol(symbol)
                try:
                    df = await asyncio.wait_for(
                        asyncio.to_thread(
                            ak.stock_individual_spot_xq, symbol=xq_symbol
                        ),
                        timeout=15.0,
                    )
                    if df is None or df.empty:
                        logger.debug("No spot data for CN symbol %s", xq_symbol)
                        return

                    if df.shape[1] < 2:
                        logger.debug("Unexpected df shape for %s: %s", xq_symbol, df.shape)
                        return
                    data_dict = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))

                    # Validate field names on first successful fetch
                    if not fields_validated:
                        expected = set(_CN_FIELD_MAP.keys())
                        actual = set(data_dict.keys())
                        missing = expected - actual
                        if missing:
                            logger.warning(
                                "CN field map keys not in Xueqiu output: %s. "
                                "Available: %s",
                                missing, sorted(actual)[:20],
                            )
                        fields_validated = True

                    row_data: dict[str, float | None] = {}
                    for src_col, db_col in _CN_FIELD_MAP.items():
                        row_data[db_col] = _safe_float(data_dict.get(src_col))

                    # Xueqiu returns 股息率(TTM) as percentage (e.g. 3.5 = 3.5%)
                    dv = row_data.get("dividend_yield")
                    if dv is not None:
                        row_data["dividend_yield"] = dv / 100.0

                    async with lock:
                        results[symbol] = row_data

                except Exception as e:
                    logger.warning(
                        "Failed to collect CN fundamental (Xueqiu) for %s: %s",
                        symbol, e,
                    )
                finally:
                    completed += 1
                    await _set_progress("cn", "collecting_phase1", completed, total)
                    await asyncio.sleep(0.5)

        tasks = [_fetch_one(s) for s in symbols]
        await asyncio.gather(*tasks)

        logger.info("CN Phase 1 (Xueqiu): %d/%d succeeded", len(results), total)
        return results

    async def _collect_cn_quarterly_batch(
        self, symbols: list[str], total: int,
    ) -> dict[str, dict[str, float | None]]:
        """Phase 2: Collect from quarterly financial reports.

        Uses akshare stock_financial_analysis_indicator() to get profitability,
        growth, and balance sheet metrics from the latest quarterly report.

        Upstream (Sina Finance) is aggressive with IP bans, so this method
        uses conservative pacing: Semaphore(2), 1.5s delay, and a consecutive
        failure circuit breaker that aborts early on ban detection.

        Returns {symbol: {db_col: value, ...}} for successful fetches.
        """
        import akshare as ak

        sem = asyncio.Semaphore(2)  # Conservative: Sina bans at ~5 concurrent
        results: dict[str, dict[str, float | None]] = {}
        lock = asyncio.Lock()
        completed = 0
        consecutive_failures = 0
        fields_validated = False
        aborted = False
        current_year = datetime.now().year
        start_year = str(current_year - 1)

        # Circuit breaker: after N consecutive failures, Sina likely banned us
        max_consecutive_failures = 10

        async def _fetch_one(symbol: str) -> None:
            nonlocal completed, fields_validated, consecutive_failures, aborted
            async with sem:
                if aborted:
                    completed += 1
                    return

                bare_code = self._to_bare_code(symbol)
                try:
                    df = await asyncio.wait_for(
                        asyncio.to_thread(
                            ak.stock_financial_analysis_indicator,
                            symbol=bare_code, start_year=start_year,
                        ),
                        timeout=20.0,
                    )
                    if df is None or df.empty:
                        logger.debug(
                            "No quarterly data for CN symbol %s", bare_code,
                        )
                        consecutive_failures += 1
                        return

                    # Latest quarter is the last row
                    latest = df.iloc[-1].to_dict()

                    # Validate field names on first successful fetch
                    if not fields_validated:
                        expected = set(_CN_QUARTERLY_FIELD_MAP.keys())
                        actual = set(latest.keys())
                        missing = expected - actual
                        if missing:
                            logger.warning(
                                "CN quarterly field map keys not in output: %s. "
                                "Available (first 20): %s",
                                missing, sorted(actual)[:20],
                            )
                        fields_validated = True

                    row_data: dict[str, float | None] = {}
                    for src_col, db_col in _CN_QUARTERLY_FIELD_MAP.items():
                        val = _safe_float(latest.get(src_col))
                        if val is not None and db_col in _CN_QUARTERLY_PCT_FIELDS:
                            val = val / 100.0
                        row_data[db_col] = val

                    # Compute gross_margin from cost ratio (销售毛利率 often NaN).
                    # Uses _safe_float intentionally: gross_margin = 1 - cost_ratio/100,
                    # not a simple pct→ratio conversion.
                    cost_ratio = _safe_float(latest.get("主营业务成本率(%)"))
                    if cost_ratio is not None:
                        row_data["gross_margin"] = 1.0 - cost_ratio / 100.0

                    async with lock:
                        results[symbol] = row_data
                    consecutive_failures = 0  # Reset on success

                except Exception as e:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures and not aborted:
                        aborted = True
                        logger.warning(
                            "CN Phase 2 aborted: %d consecutive failures "
                            "(likely Sina IP ban). Collected %d/%d so far.",
                            consecutive_failures, len(results), total,
                        )
                    else:
                        logger.debug(
                            "Failed to collect CN quarterly for %s: %s",
                            symbol, e,
                        )
                finally:
                    completed += 1
                    await _set_progress("cn", "collecting_phase2", completed, total)
                    if not aborted:
                        await asyncio.sleep(1.5)  # Conservative: Sina rate limit

        tasks = [_fetch_one(s) for s in symbols]
        await asyncio.gather(*tasks)

        logger.info(
            "CN Phase 2 (quarterly): %d/%d succeeded%s",
            len(results), total,
            " (aborted: Sina rate limit)" if aborted else "",
        )
        return results

    async def _collect_us_hk_batch(
        self, symbols: list[str], market: str
    ) -> list[tuple]:
        """Collect US/HK fundamentals via yfinance in batches of 50."""
        import yfinance as yf

        today = date.today()
        rows: list[tuple] = []
        batch_size = 50
        completed = 0
        total = len(symbols)

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            for symbol in batch:
                try:
                    yf_symbol = self._normalize_us_symbol(symbol, market)
                    ticker = await asyncio.wait_for(
                        asyncio.to_thread(
                            lambda s=yf_symbol: yf.Ticker(s).info
                        ),
                        timeout=30.0,
                    )
                    if not ticker or not isinstance(ticker, dict):
                        logger.debug("No info data for %s symbol %s", market, symbol)
                        continue

                    row_data: dict[str, float | None] = {}
                    for src_key, db_col in _US_HK_FIELD_MAP.items():
                        row_data[db_col] = _safe_float(ticker.get(src_key))

                    # debtToEquity: yfinance returns percentage, normalize to ratio
                    if row_data.get("debt_to_equity") is not None:
                        row_data["debt_to_equity"] = row_data["debt_to_equity"] / 100.0

                    row = self._build_row(
                        symbol=symbol,
                        market=market,
                        record_date=today,
                        data=row_data,
                        data_source="yfinance",
                    )
                    rows.append(row)

                except Exception as e:
                    logger.warning(
                        "Failed to collect %s fundamental for %s: %s",
                        market.upper(), symbol, e,
                    )
                finally:
                    completed += 1
                    await _set_progress(market, "collecting", completed, total)

            # 批次间延迟, 避免限速
            if i + batch_size < len(symbols):
                logger.debug("Batch %d-%d done, waiting 2s before next batch", i, i + batch_size)
                await asyncio.sleep(2.0)

        logger.info(
            "%s batch collection: %d/%d succeeded",
            market.upper(), len(rows), len(symbols),
        )
        return rows

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def get_fundamentals(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Retrieve fundamental data from DB as a DataFrame.

        Applies forward-fill per symbol: the most recent available value
        fills forward to subsequent dates until a new value appears.

        Args:
            symbols: List of stock symbols.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            DataFrame with columns: symbol, date, pe_ratio, pb_ratio, ...
            Empty DataFrame if no data available.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.warning("DB pool not available for fundamental retrieval")
            return pd.DataFrame()

        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as e:
            logger.error(
                "Invalid date format for fundamental query: "
                "start_date=%r, end_date=%r: %s",
                start_date, end_date, e,
            )
            return pd.DataFrame()

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(_SELECT_SQL, symbols, start, end)
        except Exception as e:
            logger.error("Failed to query stock_fundamentals: %s", e)
            return pd.DataFrame()

        if not rows:
            logger.debug("No fundamental data found for %d symbols", len(symbols))
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])

        # 逐股票前向填充: 最近可用值延续到后续日期
        numeric_cols = [
            c for c in df.columns if c not in ("symbol", "date")
        ]
        filled_parts: list[pd.DataFrame] = []
        for symbol in df["symbol"].unique():
            mask = df["symbol"] == symbol
            symbol_df = df.loc[mask].sort_values("date").copy()
            # 生成完整日期范围并填充
            full_dates = pd.date_range(start=start_date, end=end_date, freq="D")
            symbol_df = symbol_df.set_index("date").reindex(full_dates)
            symbol_df["symbol"] = symbol
            symbol_df[numeric_cols] = symbol_df[numeric_cols].ffill()
            symbol_df = symbol_df.reset_index().rename(columns={"index": "date"})
            filled_parts.append(symbol_df)

        if not filled_parts:
            return pd.DataFrame()

        result = pd.concat(filled_parts, ignore_index=True)
        # 只保留有数据的行 (ffill之后首行之前仍可能是NaN)
        result = result.dropna(subset=numeric_cols, how="all")
        return result

    # ------------------------------------------------------------------
    # DB write
    # ------------------------------------------------------------------

    async def _write_to_db(self, rows: list[tuple]) -> int:
        """Batch insert/upsert fundamental rows via asyncpg executemany.

        Returns the number of rows successfully written.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.error("DB pool not available for fundamental write")
            return 0

        try:
            async with pool.acquire(timeout=10) as conn:
                await conn.executemany(_INSERT_SQL, rows)
            logger.info("Wrote %d fundamental rows to DB", len(rows))
            return len(rows)
        except Exception as e:
            logger.error("Failed to write fundamentals to DB: %s", e)
            return 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_row(
        symbol: str,
        market: str,
        record_date: date,
        data: dict[str, float | None],
        data_source: str,
    ) -> tuple:
        """Build a positional tuple matching _DB_COLUMNS order."""
        return (
            symbol,
            market,
            record_date,
            "daily_snapshot",  # record_type
            data.get("pe_ratio"),
            data.get("pb_ratio"),
            data.get("ps_ratio"),
            data.get("roe"),
            data.get("roa"),
            data.get("profit_margin"),
            data.get("gross_margin"),
            data.get("revenue"),
            data.get("revenue_growth_yoy"),
            data.get("net_income"),
            data.get("eps"),
            data.get("debt_to_equity"),
            data.get("current_ratio"),
            data.get("dividend_yield"),
            data.get("market_cap"),
            data.get("forward_pe"),
            data.get("dividend_rate"),
            data.get("book_value"),
            data.get("operating_margin"),
            data.get("payout_ratio"),
            data.get("eps_growth"),
            data_source,
        )

    @staticmethod
    def _to_xueqiu_symbol(symbol: str) -> str:
        """Convert WebStock symbol to Xueqiu format (SH/SZ prefix).

        Examples:
            600519.SS -> SH600519
            000001.SZ -> SZ000001
            SH600519  -> SH600519  (already correct)
        """
        # Already in Xueqiu format (case-insensitive check)
        upper = symbol.upper()
        if upper.startswith(("SH", "SZ")) and symbol[2:].isdigit():
            return upper
        # Strip exchange suffixes and add prefix
        for suffix, prefix in [(".SS", "SH"), (".ss", "SH"), (".SZ", "SZ"), (".sz", "SZ")]:
            if symbol.endswith(suffix):
                return prefix + symbol[: -len(suffix)]
        # Bare digits: infer exchange from first digit
        if symbol.isdigit():
            if symbol.startswith(("6", "9")):
                return "SH" + symbol
            else:
                return "SZ" + symbol
        return symbol

    @staticmethod
    def _to_bare_code(symbol: str) -> str:
        """Convert WebStock CN symbol to bare 6-digit code.

        Used by stock_financial_analysis_indicator() which expects plain codes.

        Examples:
            600519.SS -> 600519
            SH600519  -> 600519
            000001.SZ -> 000001
        """
        upper = symbol.upper()
        for suffix in (".SS", ".SZ"):
            if upper.endswith(suffix):
                return upper[: -len(suffix)]
        if upper.startswith(("SH", "SZ")) and upper[2:].isdigit():
            return upper[2:]
        return symbol

    @staticmethod
    def _normalize_us_symbol(symbol: str, market: str) -> str:
        """Normalize symbol for yfinance.

        US symbols pass through unchanged. HK symbols need .HK suffix
        if not already present.

        Examples:
            AAPL -> AAPL
            0700.HK -> 0700.HK
            00700 (market=hk) -> 00700.HK
        """
        if market == "hk" and not symbol.endswith(".HK"):
            return f"{symbol}.HK"
        return symbol


# Module singleton
fundamental_service = FundamentalService()
