"""Fundamental data collection and retrieval.

Collects PE/PB/ROE and other financial metrics:
- CN: stock_individual_spot_xq (Xueqiu, real-time ~7 fields)
      + stock_financial_analysis_indicator (quarterly ~8 fields, multi-quarter)
- US/HK: yfinance Ticker.info -- ~19 fields per stock
- US/HK backfill: yfinance quarterly_income_stmt + quarterly_balance_sheet
                   for historical quarterly data (2+ years)

Data stored in stock_fundamentals table with daily_snapshot type.
Called by APScheduler for daily collection and by feature_service
for training data retrieval.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import redis.asyncio as aioredis

from app.config import get_settings
from app.services.market_config import get_market_config

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
    # Short interest (available in daily .info)
    "shortPercentOfFloat": "short_pct_float",
    "shortRatio": "short_ratio",
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
    # Category 1: FCF/valuation ratios (quarterly backfill) — $27-$33
    "fcf_margin", "fcf_yield", "capex_ratio", "buyback_yield",
    "ev_ebitda", "rd_ratio", "net_cash_ratio",
    # Short interest (daily US/HK collection from .info) — $34-$35
    "short_pct_float", "short_ratio",
    "data_source",
]

_INSERT_SQL = """
    INSERT INTO stock_fundamentals
        (symbol, market, date, record_type, pe_ratio, pb_ratio, ps_ratio,
         roe, roa, profit_margin, gross_margin, revenue, revenue_growth_yoy,
         net_income, eps, debt_to_equity, current_ratio, dividend_yield,
         market_cap, forward_pe, dividend_rate, book_value,
         operating_margin, payout_ratio, eps_growth,
         fcf_margin, fcf_yield, capex_ratio, buyback_yield,
         ev_ebitda, rd_ratio, net_cash_ratio,
         short_pct_float, short_ratio, data_source)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35)
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
        fcf_margin = COALESCE(EXCLUDED.fcf_margin, stock_fundamentals.fcf_margin),
        fcf_yield = COALESCE(EXCLUDED.fcf_yield, stock_fundamentals.fcf_yield),
        capex_ratio = COALESCE(EXCLUDED.capex_ratio, stock_fundamentals.capex_ratio),
        buyback_yield = COALESCE(EXCLUDED.buyback_yield, stock_fundamentals.buyback_yield),
        ev_ebitda = COALESCE(EXCLUDED.ev_ebitda, stock_fundamentals.ev_ebitda),
        rd_ratio = COALESCE(EXCLUDED.rd_ratio, stock_fundamentals.rd_ratio),
        net_cash_ratio = COALESCE(EXCLUDED.net_cash_ratio, stock_fundamentals.net_cash_ratio),
        short_pct_float = COALESCE(EXCLUDED.short_pct_float, stock_fundamentals.short_pct_float),
        short_ratio = COALESCE(EXCLUDED.short_ratio, stock_fundamentals.short_ratio),
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
           operating_margin, payout_ratio, eps_growth,
           fcf_margin, fcf_yield, capex_ratio, buyback_yield,
           ev_ebitda, rd_ratio, net_cash_ratio,
           short_pct_float, short_ratio
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


def _parse_quarter_date(date_str: Any) -> date | None:
    """Parse quarter date from akshare output.

    Format typically: '2024-12-31' or '2024年12月31日'.
    Returns None on failure.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    # Try standard format first
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        pass
    # Try Chinese format
    try:
        clean = s.replace("年", "-").replace("月", "-").replace("日", "")
        return datetime.strptime(clean.strip(), "%Y-%m-%d").date()
    except ValueError:
        pass
    # Try pandas Timestamp (akshare sometimes returns Timestamp objects)
    try:
        ts = pd.Timestamp(date_str)
        if not pd.isna(ts):
            return ts.date()
    except Exception:
        pass
    return None


def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    """Safe division returning None on zero/None/near-zero denominator."""
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


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
                 Applies to TODAY's date only.
        Phase 2: Quarterly reports (stock_financial_analysis_indicator) — ~8 fields
                 (roe, roa, profit_margin, operating_margin, revenue_growth,
                 eps_growth, current_ratio, gross_margin)
                 Returns multiple quarter-end dates per symbol.

        Both phases run concurrently. For today's row, Phase 2 latest quarter
        supplements Phase 1 (only fills None positions). Historical quarter rows
        are written as separate daily_snapshot records.
        """
        today = date.today()
        total = len(symbols)

        # Run both phases in parallel
        phase1_result, phase2_result = await asyncio.gather(
            self._collect_cn_xueqiu(symbols, total),
            self._collect_cn_quarterly_batch(symbols, total),
        )

        # Merge: Phase 1 (today) + Phase 2 latest quarter → today's row
        # Phase 2 historical quarters → separate rows per quarter date
        all_symbols = set(phase1_result.keys()) | set(phase2_result.keys())
        rows: list[tuple] = []
        historical_count = 0

        for symbol in all_symbols:
            p1 = phase1_result.get(symbol, {})
            p2_quarters = phase2_result.get(symbol, [])

            # Get the latest quarter data for merging with today's row
            p2_latest: dict[str, float | None] = {}
            if p2_quarters:
                # Sort by date, pick the latest
                p2_quarters_sorted = sorted(p2_quarters, key=lambda x: x[0])
                p2_latest = p2_quarters_sorted[-1][1]

            # Build today's row: Phase 1 + Phase 2 latest quarter merged
            merged: dict[str, float | None] = dict(p1)
            for key, val in p2_latest.items():
                if merged.get(key) is None and val is not None:
                    merged[key] = val

            data_source = "xueqiu+quarterly" if (p1 and p2_latest) else (
                "xueqiu" if p1 else "quarterly"
            )
            rows.append(self._build_row(
                symbol=symbol,
                market="cn",
                record_date=today,
                data=merged,
                data_source=data_source,
            ))

            # Write historical quarterly rows (excluding the latest which
            # is already merged into today's row)
            if len(p2_quarters) > 1:
                p2_quarters_sorted = sorted(p2_quarters, key=lambda x: x[0])
                for quarter_date, quarter_data in p2_quarters_sorted[:-1]:
                    # Skip if the quarter date is today (already covered)
                    if quarter_date == today:
                        continue
                    rows.append(self._build_row(
                        symbol=symbol,
                        market="cn",
                        record_date=quarter_date,
                        data=quarter_data,
                        data_source="quarterly_historical",
                    ))
                    historical_count += 1

        logger.info(
            "CN batch collection: %d symbols, %d rows "
            "(phase1=%d, phase2=%d, dual=%d, historical_quarters=%d)",
            len(all_symbols), len(rows),
            len(phase1_result), len(phase2_result),
            sum(1 for s in all_symbols
                if s in phase1_result and s in phase2_result),
            historical_count,
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
    ) -> dict[str, list[tuple[date, dict[str, float | None]]]]:
        """Phase 2: Collect from quarterly financial reports.

        Uses akshare stock_financial_analysis_indicator() to get profitability,
        growth, and balance sheet metrics from ALL available quarterly reports
        (typically 8-12 quarters with start_year = current_year - 2).

        Upstream (Sina Finance) is aggressive with IP bans, so this method
        uses conservative pacing: Semaphore(2), 1.5s delay, and a consecutive
        failure circuit breaker that aborts early on ban detection.

        Returns {symbol: [(quarter_date, {db_col: value, ...}), ...]}
        for successful fetches. Each symbol may have multiple quarter records.
        """
        import akshare as ak

        sem = asyncio.Semaphore(2)  # Conservative: Sina bans at ~5 concurrent
        results: dict[str, list[tuple[date, dict[str, float | None]]]] = {}
        lock = asyncio.Lock()
        completed = 0
        consecutive_failures = 0
        fields_validated = False
        aborted = False
        current_year = datetime.now().year
        start_year = str(current_year - 2)  # ~8-12 quarters for training data

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

                    # Validate field names on first successful fetch
                    if not fields_validated:
                        sample = df.iloc[-1].to_dict()
                        expected = set(_CN_QUARTERLY_FIELD_MAP.keys())
                        actual = set(sample.keys())
                        missing = expected - actual
                        if missing:
                            logger.warning(
                                "CN quarterly field map keys not in output: %s. "
                                "Available (first 20): %s",
                                missing, sorted(actual)[:20],
                            )
                        fields_validated = True

                    # Extract ALL quarterly rows, not just the latest
                    quarterly_rows: list[tuple[date, dict[str, float | None]]] = []
                    for _, row in df.iterrows():
                        row_dict = row.to_dict()
                        raw_date = row_dict.get("日期", "")
                        report_date = _parse_quarter_date(raw_date)
                        if report_date is None:
                            logger.debug("Skipping row with unparseable date: %r", raw_date)
                            continue

                        row_data: dict[str, float | None] = {}
                        for src_col, db_col in _CN_QUARTERLY_FIELD_MAP.items():
                            val = _safe_float(row_dict.get(src_col))
                            if val is not None and db_col in _CN_QUARTERLY_PCT_FIELDS:
                                val = val / 100.0
                            row_data[db_col] = val

                        # Compute gross_margin from cost ratio (销售毛利率 often NaN).
                        cost_ratio = _safe_float(row_dict.get("主营业务成本率(%)"))
                        if cost_ratio is not None:
                            row_data["gross_margin"] = 1.0 - cost_ratio / 100.0

                        quarterly_rows.append((report_date, row_data))

                    if quarterly_rows:
                        async with lock:
                            results[symbol] = quarterly_rows
                        consecutive_failures = 0  # Reset on success
                    else:
                        consecutive_failures += 1

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

        total_quarters = sum(len(v) for v in results.values())
        logger.info(
            "CN Phase 2 (quarterly): %d/%d symbols succeeded, "
            "%d total quarter records%s",
            len(results), total, total_quarters,
            " (aborted: Sina rate limit)" if aborted else "",
        )
        return results

    async def _collect_us_hk_batch(
        self, symbols: list[str], market: str
    ) -> list[tuple]:
        """Collect US/HK fundamentals via yfinance in batches of 50.

        Also piggybacks sector/industry extraction from the same yfinance
        .info call and upserts stale entries into stock_sectors.
        """
        import yfinance as yf

        today = date.today()
        rows: list[tuple] = []
        sector_rows: list[tuple[str, str, str | None, str | None]] = []
        batch_size = 50
        completed = 0
        total = len(symbols)

        # Pre-fetch symbols with fresh sector data to skip re-writing
        fresh_symbols = await self._get_fresh_sector_symbols(market)

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

                    # Piggyback sector extraction (skip if already fresh)
                    if symbol not in fresh_symbols:
                        sector = ticker.get("sector")
                        industry = ticker.get("industry")
                        if sector or industry:
                            sector_rows.append((symbol, market, sector, industry))

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

        # Write piggybacked sector data
        if sector_rows:
            wrote = await self._write_sectors(sector_rows)
            logger.info(
                "%s sector piggyback: %d/%d upserted",
                market.upper(), wrote, len(sector_rows),
            )

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
        market: str = "us",
    ) -> pd.DataFrame:
        """Retrieve fundamental data from DB as a DataFrame.

        Applies forward-fill per symbol: the most recent available value
        fills forward to subsequent dates, up to MarketConfig.ffill_limit days.
        This prevents stale quarterly data from silently contaminating training:
        CN (90-day limit): quarterly reporting cycle; US/HK (45-day limit): more frequent.

        Args:
            symbols: List of stock symbols.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            market: Market code — used to look up ffill_limit from MarketConfig.

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

        # 逐股票前向填充: 最近可用值延续到后续日期，上限由 MarketConfig 控制
        # ffill(limit=N) caps how many calendar days stale data can propagate.
        # Dates beyond the limit remain NaN → caught by sparse feature filter.
        # This prevents outdated quarterly data from silently contaminating training.
        ffill_limit = get_market_config(market).ffill_limit
        logger.info(
            "Fundamental ffill: market=%s, limit=%d calendar days, "
            "symbols=%d, range=%s~%s",
            market, ffill_limit, len(symbols), start_date, end_date,
        )
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
            symbol_df[numeric_cols] = symbol_df[numeric_cols].ffill(limit=ffill_limit)
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
            # Category 1: FCF/valuation ($27-$33)
            data.get("fcf_margin"),
            data.get("fcf_yield"),
            data.get("capex_ratio"),
            data.get("buyback_yield"),
            data.get("ev_ebitda"),
            data.get("rd_ratio"),
            data.get("net_cash_ratio"),
            # Short interest ($34-$35)
            data.get("short_pct_float"),
            data.get("short_ratio"),
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

    # ------------------------------------------------------------------
    # US/HK Quarterly Fundamental Backfill
    # ------------------------------------------------------------------

    async def _get_daily_closes(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[tuple[date, float]]]:
        """Query daily close prices from stock_daily_bars via asyncpg.

        Returns {symbol: [(date, close), ...]} sorted by date.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.warning("DB pool not available for daily close lookup")
            return {}

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(
                    "SELECT symbol, date, close FROM stock_daily_bars "
                    "WHERE symbol = ANY($1::text[]) AND date >= $2 AND date <= $3 "
                    "ORDER BY symbol, date",
                    symbols, start_date, end_date,
                )
        except Exception as e:
            logger.error("Failed to query stock_daily_bars for close prices: %s", e)
            return {}

        result: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for row in rows:
            close_val = _safe_float(row["close"])
            if close_val is not None and close_val > 0:
                result[row["symbol"]].append((row["date"], close_val))
        return dict(result)

    @staticmethod
    def _find_closest_close(
        closes: list[tuple[date, float]],
        target_date: date,
        max_offset_days: int = 10,
    ) -> float | None:
        """Find the closest daily close price to a target date.

        Searches within +/- max_offset_days window. Returns the close
        price of the bar with the smallest absolute date difference.
        """
        if not closes:
            return None
        best_close: float | None = None
        best_diff = max_offset_days + 1
        for bar_date, close_val in closes:
            diff = abs((bar_date - target_date).days)
            if diff < best_diff:
                best_diff = diff
                best_close = close_val
        if best_diff > max_offset_days:
            return None
        return best_close

    @staticmethod
    def _find_closest_shares(
        shares_series: "pd.Series",
        target_date: date,
        max_offset_days: int = 30,
    ) -> float | None:
        """Find closest shares outstanding value from get_shares_full() series.

        shares_series has DatetimeIndex. Finds the entry with smallest
        absolute date difference within max_offset_days.
        """
        if shares_series is None or shares_series.empty:
            return None
        # Strip timezone from index to avoid tz-naive/aware comparison errors
        # (yfinance get_shares_full() returns tz-aware DatetimeIndex)
        series_idx = shares_series.index
        if hasattr(series_idx, "tz") and series_idx.tz is not None:
            series_idx = series_idx.tz_localize(None)
        target_ts = pd.Timestamp(target_date)
        # Use searchsorted for efficiency on sorted index
        pos = series_idx.searchsorted(target_ts)
        candidates = []
        if pos > 0:
            candidates.append(pos - 1)
        if pos < len(shares_series):
            candidates.append(pos)
        best_val: float | None = None
        best_diff = max_offset_days + 1
        for ci in candidates:
            diff = abs((series_idx[ci] - target_ts).days)
            if diff < best_diff:
                best_diff = diff
                val = _safe_float(shares_series.iloc[ci])
                if val is not None and val > 0:
                    best_val = val
                    best_diff = diff
        return best_val

    @staticmethod
    def _compute_ttm(
        quarters_sorted: list[dict[str, Any]],
        current_idx: int,
        field_name: str,
    ) -> float | None:
        """Sum last 4 quarters of a field for TTM (Trailing Twelve Months).

        Returns None if fewer than 4 quarters available or any value is None.
        """
        if current_idx < 3:
            return None
        vals = [
            quarters_sorted[j].get(field_name)
            for j in range(current_idx - 3, current_idx + 1)
        ]
        if all(v is not None for v in vals):
            return sum(vals)  # type: ignore[arg-type]
        return None

    async def backfill_us_hk_quarterly(
        self,
        market: str,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        """Backfill historical quarterly fundamentals for US/HK from yfinance.

        Fetches quarterly_income_stmt and quarterly_balance_sheet from yfinance,
        computes derived metrics (PE, PB, ROE, etc.) using the closest daily
        close price from stock_daily_bars, and writes to stock_fundamentals.

        Args:
            market: 'us' or 'hk'.
            symbols: Optional explicit symbol list. If None, resolved from
                     prediction_universes default universe.

        Returns:
            Summary dict with success/fail/total counts.
        """
        import yfinance as yf

        market = market.lower()
        if market not in ("us", "hk"):
            logger.warning("backfill_us_hk_quarterly: unsupported market=%s", market)
            return {"market": market, "total": 0, "success": 0, "failed": 0, "rows": 0}

        if symbols is None:
            symbols = await self._resolve_symbols(market)

        if not symbols:
            logger.warning("No symbols to backfill for market=%s", market)
            return {"market": market, "total": 0, "success": 0, "failed": 0, "rows": 0}

        logger.info(
            "Starting US/HK quarterly backfill: market=%s, symbols=%d",
            market, len(symbols),
        )

        backfill_key = f"fundamentals:backfill:{market}:progress"
        total = len(symbols)
        await _set_backfill_progress(backfill_key, "starting", 0, total)

        sem = asyncio.Semaphore(5)
        batch_size = 20
        all_rows: list[tuple] = []
        success_count = 0
        failed_count = 0

        for batch_idx in range(0, len(symbols), batch_size):
            batch = symbols[batch_idx: batch_idx + batch_size]

            # Collect all quarter dates from this batch to query closes in bulk
            # Each entry: (symbol, quarters, shares_full, dividends)
            batch_quarter_data: list[tuple[str, list[dict[str, Any]], Any, Any]] = []

            async def _fetch_one_quarterly(symbol: str) -> tuple[str, list[dict[str, Any]], Any, Any] | None:
                async with sem:
                    try:
                        yf_symbol = self._normalize_us_symbol(symbol, market)
                        quarters, shares_full, dividends = await asyncio.wait_for(
                            asyncio.to_thread(
                                self._fetch_yf_quarterly_statements, yf_symbol,
                            ),
                            timeout=30.0,
                        )
                        if quarters:
                            return (symbol, quarters, shares_full, dividends)
                        return None
                    except Exception as e:
                        logger.warning(
                            "Failed to fetch quarterly statements for %s: %s",
                            symbol, e,
                        )
                        return None
                    finally:
                        await asyncio.sleep(0.5)

            # Fetch quarterly data for all symbols in this batch
            fetch_tasks = [_fetch_one_quarterly(s) for s in batch]
            fetch_results = await asyncio.gather(*fetch_tasks)

            for res in fetch_results:
                if res is not None:
                    batch_quarter_data.append(res)

            if not batch_quarter_data:
                failed_count += len(batch)
                completed = min(batch_idx + batch_size, total)
                await _set_backfill_progress(backfill_key, "collecting", completed, total)
                if batch_idx + batch_size < total:
                    await asyncio.sleep(3.0)
                continue

            # Determine date range for daily close lookup
            all_quarter_dates: list[date] = []
            all_batch_symbols: list[str] = []
            for sym, quarters, _sf, _dv in batch_quarter_data:
                all_batch_symbols.append(sym)
                for q in quarters:
                    qd = q.get("quarter_date")
                    if qd:
                        all_quarter_dates.append(qd)

            if all_quarter_dates:
                min_date = min(all_quarter_dates) - timedelta(days=15)
                max_date = max(all_quarter_dates) + timedelta(days=15)
                daily_closes = await self._get_daily_closes(
                    all_batch_symbols, min_date, max_date,
                )
            else:
                daily_closes = {}

            # Compute derived metrics and build rows
            for symbol, quarters, shares_full, dividends in batch_quarter_data:
                sym_closes = daily_closes.get(symbol, [])
                symbol_rows = self._compute_quarterly_rows(
                    symbol, market, quarters, sym_closes,
                    shares_series=shares_full,
                    dividends_series=dividends,
                )
                if symbol_rows:
                    all_rows.extend(symbol_rows)
                    success_count += 1
                else:
                    failed_count += 1

            completed = min(batch_idx + batch_size, total)
            await _set_backfill_progress(backfill_key, "collecting", completed, total)

            # Batch delay
            if batch_idx + batch_size < total:
                logger.debug(
                    "Backfill batch %d-%d done (%d rows so far), waiting 3s",
                    batch_idx, batch_idx + len(batch), len(all_rows),
                )
                await asyncio.sleep(3.0)

        # Write all rows to DB
        written = 0
        if all_rows:
            await _set_backfill_progress(backfill_key, "writing", len(all_rows), len(all_rows))
            written = await self._write_to_db(all_rows)

        await _clear_backfill_progress(backfill_key)

        logger.info(
            "US/HK quarterly backfill complete: market=%s, "
            "symbols=%d, success=%d, failed=%d, rows_written=%d",
            market, total, success_count, failed_count, written,
        )
        return {
            "market": market,
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "rows": written,
        }

    @staticmethod
    def _fetch_yf_quarterly_statements(yf_symbol: str) -> tuple[
        list[dict[str, Any]],
        "pd.Series | None",
        "pd.Series | None",
    ]:
        """Fetch quarterly statements, shares history, and dividends from yfinance.

        Synchronous — intended to be called via asyncio.to_thread().
        Returns:
            - List of dicts (one per quarter) with raw financial values
            - Historical shares outstanding Series (from get_shares_full)
            - Per-share dividend history Series (from ticker.dividends)
        """
        import yfinance as yf

        ticker = yf.Ticker(yf_symbol)

        # Fetch statements; yfinance returns DataFrames with dates as columns
        try:
            income_df = ticker.quarterly_income_stmt
        except Exception as e:
            logger.debug("quarterly_income_stmt failed for %s: %s", yf_symbol, e)
            income_df = None

        try:
            balance_df = ticker.quarterly_balance_sheet
        except Exception as e:
            logger.debug("quarterly_balance_sheet failed for %s: %s", yf_symbol, e)
            balance_df = None

        # Cash flow — for dividend_paid (total amount, negative)
        try:
            cashflow_df = ticker.quarterly_cashflow
        except Exception as e:
            logger.debug("quarterly_cashflow failed for %s: %s", yf_symbol, e)
            cashflow_df = None

        # Historical shares outstanding — accurate for multi-class stocks
        # (BRK-B, GOOG, META) where balance sheet Ordinary Shares Number is wrong
        shares_full: pd.Series | None = None
        try:
            sf = ticker.get_shares_full(start="2022-01-01")
            if sf is not None and not sf.empty:
                shares_full = sf
        except Exception as e:
            logger.debug("get_shares_full failed for %s: %s", yf_symbol, e)

        # Per-share dividend history (adjusted for splits)
        dividends: pd.Series | None = None
        try:
            divs = ticker.dividends
            if divs is not None and not divs.empty:
                dividends = divs
        except Exception as e:
            logger.debug("dividends failed for %s: %s", yf_symbol, e)

        if (income_df is None or income_df.empty) and (balance_df is None or balance_df.empty):
            return [], shares_full, dividends

        # Collect all available quarter dates from both statements
        quarter_dates: set[Any] = set()
        if income_df is not None and not income_df.empty:
            quarter_dates.update(income_df.columns)
        if balance_df is not None and not balance_df.empty:
            quarter_dates.update(balance_df.columns)

        results: list[dict[str, Any]] = []
        for qdate in sorted(quarter_dates):
            parsed_date = _parse_quarter_date(qdate)
            if parsed_date is None:
                continue

            record: dict[str, Any] = {"quarter_date": parsed_date}

            # Extract income statement fields
            if income_df is not None and not income_df.empty and qdate in income_df.columns:
                col = income_df[qdate]
                record["net_income"] = _safe_float(col.get("Net Income"))
                record["total_revenue"] = _safe_float(col.get("Total Revenue"))
                record["basic_eps"] = _safe_float(
                    col.get("Basic EPS") or col.get("Diluted EPS")
                )
                record["gross_profit"] = _safe_float(col.get("Gross Profit"))
                record["operating_income"] = _safe_float(col.get("Operating Income"))
                record["ebitda"] = _safe_float(col.get("EBITDA"))
                # Category 1: R&D expense
                record["rd_expense"] = _safe_float(col.get("Research And Development"))

            # Extract balance sheet fields
            if balance_df is not None and not balance_df.empty and qdate in balance_df.columns:
                col = balance_df[qdate]
                record["stockholders_equity"] = _safe_float(
                    col.get("Stockholders Equity") or col.get("Total Equity Gross Minority Interest")
                )
                record["total_debt"] = _safe_float(
                    col.get("Total Debt") or col.get("Net Debt")
                )
                record["current_assets"] = _safe_float(col.get("Current Assets"))
                record["current_liabilities"] = _safe_float(col.get("Current Liabilities"))
                record["total_assets"] = _safe_float(col.get("Total Assets"))
                record["ordinary_shares"] = _safe_float(col.get("Ordinary Shares Number"))

            # Extract cash flow fields
            if cashflow_df is not None and not cashflow_df.empty and qdate in cashflow_df.columns:
                col = cashflow_df[qdate]
                record["dividend_paid"] = _safe_float(
                    col.get("Common Stock Dividend Paid") or col.get("Cash Dividends Paid")
                )
                # Category 1: FCF and related fields
                # yfinance provides a direct "Free Cash Flow" row in quarterly_cashflow
                record["free_cash_flow"] = _safe_float(col.get("Free Cash Flow"))
                record["capital_expenditure"] = _safe_float(col.get("Capital Expenditure"))
                record["repurchase"] = _safe_float(
                    col.get("Repurchase Of Capital Stock") or col.get("Common Stock Repurchase")
                )
                record["cash_end"] = _safe_float(col.get("End Cash Position"))

            results.append(record)

        return results, shares_full, dividends

    def _compute_quarterly_rows(
        self,
        symbol: str,
        market: str,
        quarters: list[dict[str, Any]],
        sym_closes: list[tuple[date, float]],
        shares_series: "pd.Series | None" = None,
        dividends_series: "pd.Series | None" = None,
    ) -> list[tuple]:
        """Compute derived fundamental metrics for each quarter and build DB rows.

        Uses the closest daily close price for valuation ratios (P/E, P/B, P/S).
        Uses get_shares_full() for accurate shares outstanding (handles
        multi-class stocks like BRK-B, GOOG, META correctly).
        Uses ticker.dividends for per-share dividend history.
        Uses quarterly_cashflow for total dividend paid (payout ratio).
        """
        rows: list[tuple] = []

        # Sort quarters by date for TTM calculations
        quarters_sorted = sorted(quarters, key=lambda q: q.get("quarter_date", date.min))

        for i, quarter in enumerate(quarters_sorted):
            quarter_date = quarter.get("quarter_date")
            if not quarter_date:
                continue

            close_price = self._find_closest_close(sym_closes, quarter_date)

            net_income = quarter.get("net_income")
            total_revenue = quarter.get("total_revenue")
            basic_eps = quarter.get("basic_eps")
            gross_profit = quarter.get("gross_profit")
            operating_income = quarter.get("operating_income")
            equity = quarter.get("stockholders_equity")
            total_debt = quarter.get("total_debt")
            current_assets = quarter.get("current_assets")
            current_liabilities = quarter.get("current_liabilities")
            total_assets = quarter.get("total_assets")

            # --- Shares outstanding (3-tier fallback) ---
            # 1. get_shares_full(): accurate for all stocks incl. multi-class
            # 2. Ordinary Shares Number: from balance sheet (wrong for BRK-B/GOOG)
            # 3. net_income / basic_eps: last resort approximation
            shares: float | None = None
            if shares_series is not None:
                shares = self._find_closest_shares(shares_series, quarter_date)
            if shares is None:
                shares = quarter.get("ordinary_shares")
            if shares is None and basic_eps and basic_eps != 0 and net_income and net_income != 0:
                approx = net_income / basic_eps
                if approx > 0:
                    shares = approx

            # --- TTM calculations ---
            ttm_eps = self._compute_ttm(quarters_sorted, i, "basic_eps")
            ttm_net_income = self._compute_ttm(quarters_sorted, i, "net_income")
            ttm_revenue = self._compute_ttm(quarters_sorted, i, "total_revenue")

            # Compute derived metrics with safe division
            data: dict[str, float | None] = {}

            # P/E ratio: close / TTM_EPS
            if close_price is not None:
                eps_for_pe = ttm_eps
                if eps_for_pe is None and basic_eps is not None and basic_eps != 0:
                    eps_for_pe = basic_eps * 4  # Annualize single quarter
                data["pe_ratio"] = _safe_divide(close_price, eps_for_pe)

            # P/B ratio: close / (equity / shares) — using real shares
            book_value_per_share = _safe_divide(equity, shares)
            if close_price is not None and book_value_per_share is not None:
                data["pb_ratio"] = _safe_divide(close_price, book_value_per_share)

            # P/S ratio: close / (TTM_revenue / shares)
            if close_price is not None and shares is not None:
                rev_for_ps = ttm_revenue
                if rev_for_ps is None and total_revenue is not None:
                    rev_for_ps = total_revenue * 4  # Annualize fallback
                revenue_per_share = _safe_divide(rev_for_ps, shares)
                data["ps_ratio"] = _safe_divide(close_price, revenue_per_share)

            # Market cap: close × shares
            if close_price is not None and shares is not None:
                data["market_cap"] = close_price * shares

            # ROE, ROA — use TTM net income to match yfinance .info
            roe_income = ttm_net_income if ttm_net_income is not None else (
                net_income * 4 if net_income is not None else None
            )
            data["roe"] = _safe_divide(roe_income, equity)
            data["roa"] = _safe_divide(roe_income, total_assets)

            # Margin ratios
            data["profit_margin"] = _safe_divide(net_income, total_revenue)
            data["operating_margin"] = _safe_divide(operating_income, total_revenue)
            data["gross_margin"] = _safe_divide(gross_profit, total_revenue)

            # Balance sheet ratios
            data["current_ratio"] = _safe_divide(current_assets, current_liabilities)
            data["debt_to_equity"] = _safe_divide(total_debt, equity)

            # EPS: use TTM (matching yfinance .info)
            if ttm_eps is not None:
                data["eps"] = ttm_eps
            elif basic_eps is not None:
                data["eps"] = basic_eps * 4  # Annualize single quarter as fallback
            data["net_income"] = net_income
            data["revenue"] = total_revenue
            if book_value_per_share is not None:
                data["book_value"] = book_value_per_share

            # Revenue growth YoY: compare same quarter last year (i-4)
            if i >= 4:
                prev_rev = quarters_sorted[i - 4].get("total_revenue")
                if prev_rev and prev_rev != 0 and total_revenue is not None:
                    data["revenue_growth_yoy"] = (total_revenue - prev_rev) / abs(prev_rev)

            # EPS growth YoY: compare same quarter last year (i-4)
            if i >= 4:
                prev_eps = quarters_sorted[i - 4].get("basic_eps")
                if prev_eps and prev_eps != 0 and basic_eps is not None:
                    data["eps_growth"] = (basic_eps - prev_eps) / abs(prev_eps)

            # Dividend yield / rate from ticker.dividends (per-share, split-adjusted)
            if dividends_series is not None and not dividends_series.empty:
                end_dt = pd.Timestamp(quarter_date)
                start_dt = end_dt - pd.Timedelta(days=365)
                # Strip timezone from dividends index (yfinance returns tz-aware)
                div_idx = dividends_series.index
                if hasattr(div_idx, "tz") and div_idx.tz is not None:
                    div_idx = div_idx.tz_localize(None)
                mask = (div_idx >= start_dt) & (div_idx <= end_dt)
                ttm_div_per_share = float(dividends_series.loc[mask].sum())
                if ttm_div_per_share > 0:
                    data["dividend_rate"] = ttm_div_per_share
                    if close_price is not None and close_price > 0:
                        data["dividend_yield"] = ttm_div_per_share / close_price

            # Payout ratio: TTM dividends paid / TTM net income
            if i >= 3 and ttm_net_income is not None and ttm_net_income > 0:
                div_vals = [
                    quarters_sorted[j].get("dividend_paid")
                    for j in range(i - 3, i + 1)
                ]
                if all(v is not None for v in div_vals):
                    ttm_div_paid = abs(sum(div_vals))  # type: ignore[arg-type]
                    if ttm_div_paid > 0:
                        data["payout_ratio"] = ttm_div_paid / ttm_net_income

            # --- Category 1: FCF / valuation ratios ---
            ttm_ebitda = self._compute_ttm(quarters_sorted, i, "ebitda")

            # FCF: yfinance "Free Cash Flow" is already a quarterly figure;
            # sum 4 quarters for TTM.
            ttm_fcf = self._compute_ttm(quarters_sorted, i, "free_cash_flow")

            # fcf_margin: TTM FCF / TTM revenue
            data["fcf_margin"] = _safe_divide(ttm_fcf, ttm_revenue)

            # fcf_yield: TTM FCF / market cap
            data["fcf_yield"] = _safe_divide(ttm_fcf, data.get("market_cap"))

            # capex_ratio: |CapEx| / TTM revenue  (CapEx is negative in yfinance)
            capex = quarter.get("capital_expenditure")
            if capex is not None:
                data["capex_ratio"] = _safe_divide(abs(capex), ttm_revenue)

            # buyback_yield: |Repurchase| / market cap (Repurchase is negative)
            repurchase = quarter.get("repurchase")
            if repurchase is not None:
                data["buyback_yield"] = _safe_divide(
                    abs(repurchase), data.get("market_cap"),
                )

            # ev_ebitda: (market_cap + total_debt - cash) / TTM EBITDA
            cash_end = quarter.get("cash_end")
            mkt_cap = data.get("market_cap")
            if (
                mkt_cap is not None
                and total_debt is not None
                and cash_end is not None
                and ttm_ebitda is not None
                and ttm_ebitda != 0
            ):
                ev = mkt_cap + total_debt - cash_end
                data["ev_ebitda"] = _safe_divide(ev, ttm_ebitda)

            # rd_ratio: R&D expense / TTM revenue (R&D is a quarterly figure)
            rd_expense = quarter.get("rd_expense")
            data["rd_ratio"] = _safe_divide(rd_expense, ttm_revenue)

            # net_cash_ratio: (cash - total_debt) / market_cap
            if mkt_cap is not None and total_debt is not None and cash_end is not None:
                data["net_cash_ratio"] = _safe_divide(cash_end - total_debt, mkt_cap)

            # Skip if we have essentially no data
            non_none = sum(1 for v in data.values() if v is not None)
            if non_none < 2:
                continue

            rows.append(self._build_row(
                symbol=symbol,
                market=market,
                record_date=quarter_date,
                data=data,
                data_source="yfinance_quarterly",
            ))

        return rows

    # ------------------------------------------------------------------
    # Sector / industry classification
    # ------------------------------------------------------------------

    _SECTOR_FRESHNESS_DAYS = 7  # Re-fetch if older than this

    async def _get_fresh_sector_symbols(self, market: str) -> set[str]:
        """Return symbols whose sector data is still fresh (< 7 days old)."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return set()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self._SECTOR_FRESHNESS_DAYS)
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(
                    "SELECT symbol FROM stock_sectors "
                    "WHERE market = $1 AND updated_at >= $2",
                    market, cutoff,
                )
            return {r["symbol"] for r in rows}
        except Exception as e:
            logger.debug("Failed to query fresh sector symbols: %s", e)
            return set()

    async def _write_sectors(
        self, rows: list[tuple[str, str, str | None, str | None]],
    ) -> int:
        """Upsert sector/industry rows into stock_sectors.

        Args:
            rows: List of (symbol, market, sector, industry) tuples.

        Returns:
            Number of rows written.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool or not rows:
            return 0
        try:
            async with pool.acquire(timeout=10) as conn:
                await conn.executemany(
                    "INSERT INTO stock_sectors (symbol, market, sector, industry, updated_at) "
                    "VALUES ($1, $2, $3, $4, NOW()) "
                    "ON CONFLICT (symbol, market) DO UPDATE SET "
                    "  sector = COALESCE(EXCLUDED.sector, stock_sectors.sector), "
                    "  industry = COALESCE(EXCLUDED.industry, stock_sectors.industry), "
                    "  updated_at = NOW()",
                    rows,
                )
            return len(rows)
        except Exception as e:
            logger.error("Failed to write sectors: %s", e)
            return 0

    async def collect_sector_data(
        self, market: str, symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        """Collect sector/industry classification for a market.

        US/HK: piggybacked from yfinance during daily fundamental collection.
        CN: fetched from data-service ``/v1/analysis/sector/{symbol}`` (akshare).

        This method handles CN explicitly. US/HK sectors are collected
        automatically during ``_collect_us_hk_batch`` piggyback.
        Only stale symbols (>7 days) are re-fetched.
        """
        if symbols is None:
            symbols = await self._resolve_symbols(market)
        if not symbols:
            return {"market": market, "total": 0, "success": 0, "skipped": 0}

        # Filter to stale-only
        fresh = await self._get_fresh_sector_symbols(market)
        stale_symbols = [s for s in symbols if s not in fresh]
        skipped = len(symbols) - len(stale_symbols)

        if not stale_symbols:
            logger.info("All %d %s sectors are fresh, skipping collection", len(symbols), market)
            return {"market": market, "total": len(symbols), "success": 0, "skipped": skipped}

        logger.info(
            "Collecting sectors: market=%s, stale=%d, fresh=%d",
            market, len(stale_symbols), skipped,
        )

        if market in ("cn", "sh", "sz"):
            sector_rows = await self._collect_cn_sectors(stale_symbols, market)
        elif market in ("us", "hk"):
            # US/HK sectors are piggybacked in _collect_us_hk_batch.
            # This path handles explicit sector-only collection (e.g. initial seed).
            sector_rows = await self._collect_us_hk_sectors(stale_symbols, market)
        else:
            logger.warning("Unsupported market for sector collection: %s", market)
            return {"market": market, "total": len(symbols), "success": 0, "skipped": skipped}

        wrote = 0
        if sector_rows:
            wrote = await self._write_sectors(sector_rows)

        logger.info(
            "Sector collection complete: market=%s, wrote=%d, skipped=%d",
            market, wrote, skipped,
        )
        return {"market": market, "total": len(symbols), "success": wrote, "skipped": skipped}

    async def _collect_cn_sectors(
        self, symbols: list[str], market: str,
    ) -> list[tuple[str, str, str | None, str | None]]:
        """Fetch CN sector data from data-service API.

        Calls GET /v1/analysis/sector/{symbol}?market=sh|sz per symbol
        with concurrency limit to avoid overwhelming data-service.
        """
        import httpx

        settings = get_settings()
        base_url = settings.DATA_SERVICE_URL.rstrip("/")
        token = settings.INTERNAL_API_TOKEN
        rows: list[tuple[str, str, str | None, str | None]] = []
        semaphore = asyncio.Semaphore(5)

        async def _fetch_one(client: httpx.AsyncClient, symbol: str) -> None:
            async with semaphore:
                try:
                    # Determine sh/sz market hint for data-service
                    upper = symbol.upper()
                    if upper.endswith(".SS") or upper.startswith("SH"):
                        ds_market = "sh"
                    else:
                        ds_market = "sz"

                    bare = self._to_bare_code(symbol)
                    resp = await client.get(
                        f"{base_url}/v1/analysis/sector/{bare}",
                        params={"market": ds_market},
                        headers={"X-Internal-Token": token} if token else {},
                        timeout=15.0,
                    )
                    if resp.status_code != 200:
                        return
                    data = resp.json()
                    if not data.get("success") or not data.get("data"):
                        return
                    d = data["data"]
                    industry = d.get("industry")
                    if industry:
                        # CN akshare only returns industry, no GICS sector
                        rows.append((symbol, "cn", industry, industry))
                except Exception as e:
                    logger.debug("Failed to fetch CN sector for %s: %s", symbol, e)

        async with httpx.AsyncClient() as client:
            tasks = [_fetch_one(client, s) for s in symbols]
            await asyncio.gather(*tasks)

        return rows

    async def _collect_us_hk_sectors(
        self, symbols: list[str], market: str,
    ) -> list[tuple[str, str, str | None, str | None]]:
        """Fetch US/HK sector data via yfinance (standalone, non-piggyback).

        Used for initial seeding or explicit sector-only collection.
        """
        import yfinance as yf

        rows: list[tuple[str, str, str | None, str | None]] = []
        for symbol in symbols:
            try:
                yf_symbol = self._normalize_us_symbol(symbol, market)
                info = await asyncio.wait_for(
                    asyncio.to_thread(lambda s=yf_symbol: yf.Ticker(s).info),
                    timeout=15.0,
                )
                if not info or not isinstance(info, dict):
                    continue
                sector = info.get("sector")
                industry = info.get("industry")
                if sector or industry:
                    rows.append((symbol, market, sector, industry))
            except Exception as e:
                logger.debug("Failed to fetch %s sector for %s: %s", market, symbol, e)
        return rows

    async def get_sector_map(
        self, market: str, symbols: list[str] | None = None,
    ) -> dict[str, str]:
        """Return {symbol: sector} mapping from stock_sectors table.

        Used by prediction_service for sector-neutral label construction
        and by feature_service for sector-adjusted ranking.
        """
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            return {}

        try:
            async with pool.acquire(timeout=10) as conn:
                if symbols:
                    rows = await conn.fetch(
                        "SELECT symbol, sector FROM stock_sectors "
                        "WHERE market = $1 AND symbol = ANY($2::text[]) "
                        "AND sector IS NOT NULL",
                        market, symbols,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT symbol, sector FROM stock_sectors "
                        "WHERE market = $1 AND sector IS NOT NULL",
                        market,
                    )
            return {r["symbol"]: r["sector"] for r in rows}
        except Exception as e:
            logger.error("Failed to query sector map for %s: %s", market, e)
            return {}


# -----------------------------------------------------------------------
# Backfill progress helpers (separate from daily collection progress)
# -----------------------------------------------------------------------


async def _set_backfill_progress(key: str, phase: str, current: int, total: int) -> None:
    """Write backfill progress to Redis DB 0."""
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
        await r.set(key, data, ex=7200)  # 2h TTL (backfill takes longer)
    except Exception as e:
        logger.debug("Failed to set backfill progress: %s", e)


async def _clear_backfill_progress(key: str) -> None:
    """Remove backfill progress key from Redis DB 0."""
    try:
        r = await _get_progress_redis()
        await r.delete(key)
    except Exception as e:
        logger.debug("Failed to clear backfill progress: %s", e)


# Module singleton
fundamental_service = FundamentalService()
