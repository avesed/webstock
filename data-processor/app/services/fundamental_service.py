"""Fundamental data collection and retrieval.

Collects PE/PB/ROE and other financial metrics:
- CN: akshare stock_a_indicator_lg() -- latest snapshot per stock
- US/HK: yfinance Ticker.info -- point-in-time data

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
    except Exception:
        pass

# -----------------------------------------------------------------------
# Field mappings: external source key -> DB column name
# -----------------------------------------------------------------------

# akshare stock_individual_spot_xq() (Xueqiu) field mapping
# Output is a DataFrame with Chinese column names
_CN_FIELD_MAP = {
    "市盈率(TTM)": "pe_ratio",
    "市净率": "pb_ratio",
    "股息率(TTM)": "dividend_yield",  # already in %
    "每股收益": "eps",
    "资产净值/总市值": "market_cap",
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
        pe_ratio = EXCLUDED.pe_ratio,
        pb_ratio = EXCLUDED.pb_ratio,
        ps_ratio = EXCLUDED.ps_ratio,
        roe = EXCLUDED.roe,
        roa = EXCLUDED.roa,
        profit_margin = EXCLUDED.profit_margin,
        gross_margin = EXCLUDED.gross_margin,
        revenue = EXCLUDED.revenue,
        revenue_growth_yoy = EXCLUDED.revenue_growth_yoy,
        net_income = EXCLUDED.net_income,
        eps = EXCLUDED.eps,
        debt_to_equity = EXCLUDED.debt_to_equity,
        current_ratio = EXCLUDED.current_ratio,
        dividend_yield = EXCLUDED.dividend_yield,
        market_cap = EXCLUDED.market_cap,
        forward_pe = EXCLUDED.forward_pe,
        dividend_rate = EXCLUDED.dividend_rate,
        book_value = EXCLUDED.book_value,
        operating_margin = EXCLUDED.operating_margin,
        payout_ratio = EXCLUDED.payout_ratio,
        eps_growth = EXCLUDED.eps_growth,
        data_source = EXCLUDED.data_source
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
    - CN (A-shares): akshare stock_a_indicator_lg() per symbol
    - US/HK: yfinance Ticker.info per symbol

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
        """Collect A-share fundamentals via akshare (Xueqiu source).

        Uses stock_individual_spot_xq per symbol with Semaphore(5) and
        0.5s delay between calls to avoid rate limits.
        """
        import akshare as ak

        sem = asyncio.Semaphore(5)
        today = date.today()
        rows: list[tuple] = []
        lock = asyncio.Lock()
        completed = 0
        total = len(symbols)

        async def _fetch_one(symbol: str) -> None:
            nonlocal completed
            async with sem:
                xq_symbol = self._to_xueqiu_symbol(symbol)
                try:
                    # akshare is synchronous, run in thread (15s timeout)
                    df = await asyncio.wait_for(
                        asyncio.to_thread(
                            ak.stock_individual_spot_xq, symbol=xq_symbol
                        ),
                        timeout=15.0,
                    )
                    if df is None or df.empty:
                        logger.debug("No spot data for CN symbol %s", xq_symbol)
                        return

                    # DataFrame has two columns: item name and value
                    # Convert to dict for lookup
                    if df.shape[1] >= 2:
                        data_dict = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
                    else:
                        logger.debug("Unexpected df shape for %s: %s", xq_symbol, df.shape)
                        return

                    row_data: dict[str, float | None] = {}
                    for src_col, db_col in _CN_FIELD_MAP.items():
                        row_data[db_col] = _safe_float(data_dict.get(src_col))

                    # dividend_yield: Xueqiu returns %, convert to ratio (0.xx)
                    if row_data.get("dividend_yield") is not None:
                        row_data["dividend_yield"] = row_data["dividend_yield"] / 100.0

                    row = self._build_row(
                        symbol=symbol,
                        market="cn",
                        record_date=today,
                        data=row_data,
                        data_source="xueqiu",
                    )
                    async with lock:
                        rows.append(row)

                except Exception as e:
                    logger.warning("Failed to collect CN fundamental for %s: %s", symbol, e)
                finally:
                    completed += 1
                    await _set_progress("cn", "collecting", completed, total)
                    await asyncio.sleep(0.5)

        tasks = [_fetch_one(s) for s in symbols]
        await asyncio.gather(*tasks)

        logger.info("CN batch collection: %d/%d succeeded", len(rows), len(symbols))
        return rows

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
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(
                    _SELECT_SQL, symbols, start_date, end_date
                )
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
        # Already in Xueqiu format
        if symbol.startswith(("SH", "SZ")) and symbol[2:].isdigit():
            return symbol
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
