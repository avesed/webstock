"""AKShare data provider for A-shares, HK stocks, and institutional data."""

import asyncio
import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

import pandas as pd

from app.db.redis import get_redis
from app.services.providers.base import DataProvider
from app.services.stock_service import (
    DataSource,
    HistoryInterval,
    HistoryPeriod,
    Market,
    OHLCVBar,
    SearchResult,
    StockFinancials,
    StockHistory,
    StockInfo,
    StockQuote,
    normalize_symbol,
)

logger = logging.getLogger(__name__)

# Thread pool for synchronous akshare calls
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()

EXTERNAL_API_TIMEOUT = 30  # seconds

# Cache TTL configurations (base_seconds, random_range_seconds)
CACHE_TTL = {
    "fund_holdings": (86400, 3600),  # 24h + rand(1h)
    "northbound_holding": (3600, 600),  # 1h + rand(10min)
    "northbound_flow": (3600, 600),  # 1h + rand(10min)
    "industry_sector_list": (300, 60),  # 5min + rand(1min)
    "stock_industry_cn": (86400, 3600),  # 24h + rand(1h)
    "sector_history": (300, 60),  # 5min + rand(1min)
    "hk_history": (300, 60),  # 5min + rand(1min)
}


async def _get_executor() -> ThreadPoolExecutor:
    """Get thread pool executor, initialize if needed."""
    global _executor
    if _executor is None:
        async with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=10)
    return _executor


async def run_in_executor(func: Callable, *args, **kwargs) -> Any:
    """Run synchronous function in thread pool with timeout."""
    loop = asyncio.get_running_loop()
    executor = await _get_executor()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                executor,
                lambda: func(*args, **kwargs),
            ),
            timeout=EXTERNAL_API_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"Executor timeout after {EXTERNAL_API_TIMEOUT}s for function: {func.__name__}"
        )
        raise


def _get_ttl(data_type: str) -> int:
    """Get TTL with randomization to prevent cache avalanche."""
    base, rand_range = CACHE_TTL.get(data_type, (3600, 300))
    return base + random.randint(0, rand_range)


class AKShareProvider(DataProvider):
    """
    AKShare data provider for A-shares and HK stocks.

    Primary provider for:
    - A-shares (Shanghai, Shenzhen)
    - HK stocks

    Also provides institutional data:
    - Fund holdings (A-shares)
    - Northbound capital flow
    - Industry sector data
    """

    def __init__(self):
        self._redis = None
        self._cache_prefix = "akshare:"

    @property
    def source(self) -> DataSource:
        return DataSource.AKSHARE

    @property
    def supported_markets(self) -> Set[Market]:
        return {Market.SH, Market.SZ, Market.HK}

    async def _get_redis(self):
        """Get Redis client."""
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    async def _get_cached_or_fetch(
        self,
        data_type: str,
        identifier: str,
        fetch_func: Callable,
    ) -> Optional[Dict[str, Any]]:
        """Get data from cache or fetch from source."""
        redis = await self._get_redis()
        cache_key = f"{self._cache_prefix}{data_type}:{identifier}"

        # Try cache first
        try:
            cached_data = await redis.get(cache_key)
            if cached_data:
                logger.debug(f"Cache hit: {cache_key}")
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"Cache read error: {e}")

        # Fetch from source
        try:
            data = await fetch_func()
            if data:
                try:
                    ttl = _get_ttl(data_type)
                    await redis.setex(
                        cache_key,
                        ttl,
                        json.dumps(data, default=str),
                    )
                    logger.debug(f"Cached: {cache_key} (TTL: {ttl}s)")
                except Exception as e:
                    logger.warning(f"Cache write error: {e}")
            return data
        except Exception as e:
            logger.error(f"Fetch error for {data_type}/{identifier}: {e}")
            return None

    # === Core Methods ===

    async def get_quote(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockQuote]:
        """Get real-time quote from akshare."""
        if market == Market.HK:
            return await self._get_quote_hk(symbol)
        elif market in (Market.SH, Market.SZ):
            return await self._get_quote_cn(symbol, market)
        return None

    async def _get_quote_cn(
        self, symbol: str, market: Market
    ) -> Optional[StockQuote]:
        """Get real-time quote for A-shares."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, market)

            def fetch():
                df = ak.stock_zh_a_spot_em()
                row = df[df["代码"] == code]
                if row.empty:
                    return None
                return row.iloc[0].to_dict()

            data = await run_in_executor(fetch)
            if not data:
                return None

            price = float(data.get("最新价", 0))
            change = float(data.get("涨跌额", 0))
            change_pct = float(data.get("涨跌幅", 0))

            return StockQuote(
                symbol=symbol,
                name=data.get("名称"),
                price=price,
                change=round(change, 4),
                change_percent=round(change_pct, 2),
                volume=int(data.get("成交量", 0)),
                market_cap=float(data.get("总市值", 0)) if data.get("总市值") else None,
                day_high=float(data.get("最高", 0)) if data.get("最高") else None,
                day_low=float(data.get("最低", 0)) if data.get("最低") else None,
                open=float(data.get("今开", 0)) if data.get("今开") else None,
                previous_close=float(data.get("昨收", 0)) if data.get("昨收") else None,
                timestamp=datetime.utcnow(),
                market=market,
                source=DataSource.AKSHARE,
            )
        except Exception as e:
            logger.error(f"AKShare CN quote error for {symbol}: {e}")
            return None

    async def _get_quote_hk(self, symbol: str) -> Optional[StockQuote]:
        """Get real-time quote for HK stocks."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, Market.HK)

            def fetch():
                df = ak.stock_hk_spot_em()
                row = df[df["代码"] == code]
                if row.empty:
                    return None
                return row.iloc[0].to_dict()

            data = await run_in_executor(fetch)
            if not data:
                return None

            price = float(data.get("最新价", 0))
            change = float(data.get("涨跌额", 0))
            change_pct = float(data.get("涨跌幅", 0))

            return StockQuote(
                symbol=symbol,
                name=data.get("名称"),
                price=price,
                change=round(change, 4),
                change_percent=round(change_pct, 2),
                volume=int(data.get("成交量", 0)),
                market_cap=float(data.get("总市值", 0)) if data.get("总市值") else None,
                day_high=float(data.get("最高", 0)) if data.get("最高") else None,
                day_low=float(data.get("最低", 0)) if data.get("最低") else None,
                open=float(data.get("今开", 0)) if data.get("今开") else None,
                previous_close=float(data.get("昨收", 0)) if data.get("昨收") else None,
                timestamp=datetime.utcnow(),
                market=Market.HK,
                source=DataSource.AKSHARE,
            )
        except Exception as e:
            logger.error(f"AKShare HK quote error for {symbol}: {e}")
            return None

    async def get_history(
        self,
        symbol: str,
        market: Market,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """Get historical data from akshare."""
        if market == Market.HK:
            return await self._get_history_hk(symbol, period, interval)
        elif market in (Market.SH, Market.SZ):
            return await self._get_history_cn(symbol, market, period, interval)
        return None

    async def _get_history_cn(
        self,
        symbol: str,
        market: Market,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """Get historical data for A-shares."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, market)

            # Intraday intervals: use ak.stock_zh_a_minute()
            intraday_period = {
                HistoryInterval.ONE_MINUTE: "1",
                HistoryInterval.FIVE_MINUTES: "5",
                HistoryInterval.FIFTEEN_MINUTES: "15",
                HistoryInterval.HOURLY: "60",
            }.get(interval)

            if intraday_period is not None:
                sina_symbol = f"{'sh' if market == Market.SH else 'sz'}{code}"

                def fetch_minute():
                    return ak.stock_zh_a_minute(
                        symbol=sina_symbol,
                        period=intraday_period,
                        adjust="qfq",
                    )

                df = await run_in_executor(fetch_minute)
                if df is None or df.empty:
                    return None

                df = df.dropna(subset=["open", "high", "low", "close"])

                bars = []
                for _, row in df.iterrows():
                    date_val = row["day"]
                    if isinstance(date_val, str):
                        date_val = datetime.strptime(date_val, "%Y-%m-%d %H:%M:%S")
                    bars.append(
                        OHLCVBar(
                            date=date_val,
                            open=round(float(row["open"]), 4),
                            high=round(float(row["high"]), 4),
                            low=round(float(row["low"]), 4),
                            close=round(float(row["close"]), 4),
                            volume=int(row["volume"]),
                        )
                    )

                if bars:
                    period_days_map = {
                        HistoryPeriod.ONE_DAY: 1,
                        HistoryPeriod.FIVE_DAYS: 5,
                    }
                    max_days = period_days_map.get(period, 5)
                    cutoff = datetime.now() - timedelta(days=max_days + 1)
                    bars = [b for b in bars if b.date >= cutoff]

                return StockHistory(
                    symbol=symbol,
                    interval=interval,
                    bars=bars,
                    market=market,
                    source=DataSource.AKSHARE,
                ) if bars else None

            # Daily/weekly/monthly: use ak.stock_zh_a_hist()
            ak_period = {
                HistoryInterval.DAILY: "daily",
                HistoryInterval.WEEKLY: "weekly",
                HistoryInterval.MONTHLY: "monthly",
            }.get(interval, "daily")

            end_date = datetime.now()
            period_days = {
                HistoryPeriod.ONE_DAY: 1,
                HistoryPeriod.FIVE_DAYS: 5,
                HistoryPeriod.ONE_MONTH: 30,
                HistoryPeriod.THREE_MONTHS: 90,
                HistoryPeriod.SIX_MONTHS: 180,
                HistoryPeriod.ONE_YEAR: 365,
                HistoryPeriod.TWO_YEARS: 730,
                HistoryPeriod.FIVE_YEARS: 1825,
                HistoryPeriod.MAX: 3650,
            }
            start_date = end_date - timedelta(days=period_days.get(period, 365))

            def fetch():
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period=ak_period,
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust="qfq",
                )
                return df

            df = await run_in_executor(fetch)
            if df is None or df.empty:
                return None

            bars = []
            for _, row in df.iterrows():
                date_val = row["日期"]
                if isinstance(date_val, str):
                    date_val = datetime.strptime(date_val, "%Y-%m-%d")
                bars.append(
                    OHLCVBar(
                        date=date_val,
                        open=round(float(row["开盘"]), 4),
                        high=round(float(row["最高"]), 4),
                        low=round(float(row["最低"]), 4),
                        close=round(float(row["收盘"]), 4),
                        volume=int(row["成交量"]),
                    )
                )

            return StockHistory(
                symbol=symbol,
                interval=interval,
                bars=bars,
                market=market,
                source=DataSource.AKSHARE,
            )
        except Exception as e:
            logger.error(f"AKShare CN history error for {symbol}: {e}")
            return None

    async def _get_history_hk(
        self,
        symbol: str,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """Get historical data for HK stocks."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, Market.HK)

            def fetch():
                df = ak.stock_hk_hist(
                    symbol=code,
                    period="daily",
                    adjust="qfq",
                )
                return df

            df = await run_in_executor(fetch)
            if df is None or df.empty:
                return None

            period_days = {
                HistoryPeriod.ONE_MONTH: 30,
                HistoryPeriod.THREE_MONTHS: 90,
                HistoryPeriod.SIX_MONTHS: 180,
                HistoryPeriod.ONE_YEAR: 365,
                HistoryPeriod.TWO_YEARS: 730,
                HistoryPeriod.FIVE_YEARS: 1825,
                HistoryPeriod.MAX: 9999,
            }
            cutoff = datetime.now() - timedelta(days=period_days.get(period, 365))

            bars = []
            for _, row in df.iterrows():
                date_val = row["日期"]
                if isinstance(date_val, str):
                    date_val = datetime.strptime(date_val, "%Y-%m-%d")
                if date_val < cutoff:
                    continue

                bars.append(
                    OHLCVBar(
                        date=date_val,
                        open=round(float(row["开盘"]), 4),
                        high=round(float(row["最高"]), 4),
                        low=round(float(row["最低"]), 4),
                        close=round(float(row["收盘"]), 4),
                        volume=int(row["成交量"]),
                    )
                )

            # Resample for weekly/monthly if needed
            if interval != HistoryInterval.DAILY and bars:
                bars = self._resample_bars(bars, interval)

            return StockHistory(
                symbol=symbol,
                interval=interval,
                bars=bars,
                market=Market.HK,
                source=DataSource.AKSHARE,
            )
        except Exception as e:
            logger.error(f"AKShare HK history error for {symbol}: {e}")
            return None

    def _resample_bars(
        self, bars: List[OHLCVBar], interval: HistoryInterval
    ) -> List[OHLCVBar]:
        """Resample daily bars to weekly or monthly."""
        if not bars:
            return bars

        data = {
            "date": [b.date for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
        df = pd.DataFrame(data)
        df.set_index("date", inplace=True)

        freq = "W" if interval == HistoryInterval.WEEKLY else "ME"
        resampled = df.resample(freq).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        ).dropna()

        result = []
        for idx, row in resampled.iterrows():
            result.append(
                OHLCVBar(
                    date=idx.to_pydatetime(),
                    open=round(row["open"], 4),
                    high=round(row["high"], 4),
                    low=round(row["low"], 4),
                    close=round(row["close"], 4),
                    volume=int(row["volume"]),
                )
            )
        return result

    async def search(
        self,
        query: str,
        markets: Optional[Set[Market]] = None,
    ) -> List[SearchResult]:
        """Search stocks using akshare."""
        results = []

        if markets is None:
            markets = {Market.SH, Market.SZ, Market.HK}

        if Market.SH in markets or Market.SZ in markets:
            cn_results = await self._search_cn(query)
            results.extend(cn_results)

        if Market.HK in markets:
            hk_results = await self._search_hk(query)
            results.extend(hk_results)

        return results

    async def _search_cn(self, query: str) -> List[SearchResult]:
        """Search A-share stocks."""
        try:
            import akshare as ak

            def fetch():
                df = ak.stock_zh_a_spot_em()
                mask = df["名称"].str.contains(query, na=False) | df["代码"].str.contains(query, na=False)
                return df[mask].head(20).to_dict("records")

            results = await run_in_executor(fetch)

            return [
                SearchResult(
                    symbol=f"{r['代码']}.{'SS' if r['代码'].startswith('6') else 'SZ'}",
                    name=r["名称"],
                    exchange="SSE" if r["代码"].startswith("6") else "SZSE",
                    market=Market.SH if r["代码"].startswith("6") else Market.SZ,
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"AKShare CN search error for {query}: {e}")
            return []

    async def _search_hk(self, query: str) -> List[SearchResult]:
        """Search HK stocks."""
        try:
            import akshare as ak

            def fetch():
                df = ak.stock_hk_spot_em()
                mask = df["名称"].str.contains(query, na=False) | df["代码"].str.contains(query, na=False)
                return df[mask].head(20).to_dict("records")

            results = await run_in_executor(fetch)

            return [
                SearchResult(
                    symbol=f"{r['代码']}.HK",
                    name=r["名称"],
                    exchange="HKEX",
                    market=Market.HK,
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"AKShare HK search error for {query}: {e}")
            return []

    # === Optional Methods ===

    async def get_info(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockInfo]:
        """Get company info for A-shares."""
        if market not in (Market.SH, Market.SZ):
            return None

        try:
            import akshare as ak

            code = normalize_symbol(symbol, market)

            def fetch():
                df = ak.stock_individual_info_em(symbol=code)
                if df is None or df.empty:
                    return None
                info = {}
                for _, row in df.iterrows():
                    info[row["item"]] = row["value"]
                return info

            info = await run_in_executor(fetch)
            if not info:
                return None

            return StockInfo(
                symbol=symbol,
                name=info.get("股票简称", ""),
                description=info.get("经营范围"),
                sector=info.get("行业"),
                industry=info.get("行业"),
                website=info.get("公司网址"),
                employees=int(info.get("员工人数", 0)) if info.get("员工人数") else None,
                market_cap=float(info.get("总市值", 0)) if info.get("总市值") else None,
                currency="CNY",
                exchange="SSE" if market == Market.SH else "SZSE",
                market=market,
                source=DataSource.AKSHARE,
            )
        except Exception as e:
            logger.error(f"AKShare CN info error for {symbol}: {e}")
            return None

    async def get_financials(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockFinancials]:
        """Get financial data for A-shares."""
        if market not in (Market.SH, Market.SZ):
            return None

        try:
            import akshare as ak

            code = normalize_symbol(symbol, market)

            def fetch():
                df = ak.stock_a_indicator_lg(symbol=code)
                if df is None or df.empty:
                    return None
                return df.iloc[-1].to_dict()

            data = await run_in_executor(fetch)
            if not data:
                return None

            return StockFinancials(
                symbol=symbol,
                pe_ratio=float(data.get("pe", 0)) if data.get("pe") else None,
                forward_pe=None,
                eps=float(data.get("eps", 0)) if data.get("eps") else None,
                dividend_yield=float(data.get("dv_ratio", 0)) if data.get("dv_ratio") else None,
                dividend_rate=None,
                book_value=float(data.get("bps", 0)) if data.get("bps") else None,
                price_to_book=float(data.get("pb", 0)) if data.get("pb") else None,
                revenue=float(data.get("total_revenue", 0)) if data.get("total_revenue") else None,
                revenue_growth=None,
                net_income=float(data.get("net_profit", 0)) if data.get("net_profit") else None,
                profit_margin=None,
                gross_margin=float(data.get("gross_profit_margin", 0)) if data.get("gross_profit_margin") else None,
                operating_margin=None,
                roe=float(data.get("roe", 0)) if data.get("roe") else None,
                roa=None,
                debt_to_equity=None,
                current_ratio=None,
                eps_growth=None,
                payout_ratio=None,
                market=market,
                source=DataSource.AKSHARE,
            )
        except Exception as e:
            logger.error(f"AKShare CN financials error for {symbol}: {e}")
            return None

    # === Extended Methods (Institutional Data) ===

    async def get_fund_holdings_cn(
        self,
        symbol: str,
        quarter: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get fund holdings for A-share stock."""
        code = symbol.replace(".SS", "").replace(".SZ", "")

        quarters_to_try = []
        if quarter is None:
            now = datetime.now()
            year = now.year
            month = now.month
            for i in range(8):
                total_q = (year * 4 + ((month - 1) // 3)) - i - 1
                q_year = total_q // 4
                q_num = (total_q % 4) + 1
                quarters_to_try.append(f"{q_year}{q_num}")
        else:
            quarters_to_try = [quarter]

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                for q in quarters_to_try:
                    try:
                        df = ak.stock_institute_hold(symbol=q)
                        if df is None or df.empty:
                            continue

                        row = df[df["证券代码"] == code]
                        if row.empty:
                            continue

                        r = row.iloc[0]
                        holdings = {
                            "stock_code": r.get("证券代码"),
                            "stock_name": r.get("证券简称"),
                            "institution_count": int(r.get("机构数", 0)),
                            "institution_count_change": (
                                int(r.get("机构数变化", 0))
                                if pd.notna(r.get("机构数变化"))
                                else None
                            ),
                            "holding_pct": (
                                float(r.get("持股比例", 0))
                                if pd.notna(r.get("持股比例"))
                                else None
                            ),
                            "holding_pct_change": (
                                float(r.get("持股比例增幅", 0))
                                if pd.notna(r.get("持股比例增幅"))
                                else None
                            ),
                            "float_pct": (
                                float(r.get("占流通股比例", 0))
                                if pd.notna(r.get("占流通股比例"))
                                else None
                            ),
                            "float_pct_change": (
                                float(r.get("占流通股比例增幅", 0))
                                if pd.notna(r.get("占流通股比例增幅"))
                                else None
                            ),
                        }

                        return {
                            "symbol": symbol,
                            "quarter": q,
                            "holdings": holdings,
                            "source": "akshare",
                        }
                    except Exception as e:
                        logger.warning(f"AKShare fund holdings error for {q}: {e}")
                        continue

                return {
                    "symbol": symbol,
                    "quarter": quarters_to_try[0] if quarters_to_try else None,
                    "holdings": None,
                    "source": "akshare",
                    "note": f"股票 {code} 未被基金持仓或无数据",
                }

            return await run_in_executor(_fetch_sync)

        cache_key = f"{code}:{quarters_to_try[0] if quarters_to_try else 'default'}"
        return await self._get_cached_or_fetch(
            "fund_holdings",
            cache_key,
            fetch,
        )

    async def get_northbound_holding(
        self,
        symbol: str,
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound holding for a specific A-share stock."""
        code = symbol.replace(".SS", "").replace(".SZ", "")

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_hsgt_individual_em(symbol=code)

                    if df is None or df.empty:
                        return {
                            "symbol": symbol,
                            "holdings": [],
                            "latest_holding": None,
                            "data_cutoff_notice": "无北向持仓数据",
                            "source": "akshare",
                        }

                    df = df.tail(days)

                    holdings = []
                    for _, row in df.iterrows():
                        holding = {
                            "holding_date": str(row.get("持股日期", ""))[:10],
                            "close_price": (
                                float(row.get("当日收盘价", 0))
                                if pd.notna(row.get("当日收盘价"))
                                else None
                            ),
                            "change_pct": (
                                float(row.get("当日涨跌幅", 0))
                                if pd.notna(row.get("当日涨跌幅"))
                                else None
                            ),
                            "holding_shares": int(row.get("持股数量", 0)),
                            "holding_value": float(row.get("持股市值", 0)),
                            "holding_pct": (
                                float(row.get("持股数量占A股百分比", 0))
                                if pd.notna(row.get("持股数量占A股百分比"))
                                else None
                            ),
                            "change_shares": (
                                float(row.get("今日增持股数", 0))
                                if pd.notna(row.get("今日增持股数"))
                                else None
                            ),
                            "change_value": (
                                float(row.get("今日增持资金", 0))
                                if pd.notna(row.get("今日增持资金"))
                                else None
                            ),
                            "value_change": (
                                float(row.get("今日持股市值变化", 0))
                                if pd.notna(row.get("今日持股市值变化"))
                                else None
                            ),
                        }
                        holdings.append(holding)

                    latest = holdings[-1] if holdings else None

                    return {
                        "symbol": symbol,
                        "holdings": holdings,
                        "latest_holding": latest,
                        "data_cutoff_notice": "数据可能在 2024-08-16 后不更新，请以交易所公告为准",
                        "source": "akshare",
                    }
                except Exception as e:
                    logger.error(f"AKShare northbound holding error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "northbound_holding",
            code,
            fetch,
        )

    async def get_northbound_flow(
        self,
        direction: str = "北向资金",
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound capital flow history."""

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_hsgt_hist_em(symbol=direction)

                    if df is None or df.empty:
                        return None

                    valid_df = df.dropna(subset=["当日成交净买额"])
                    latest_valid_date = None
                    if not valid_df.empty:
                        latest_valid_date = str(valid_df["日期"].max())[:10]

                    df = df.tail(days)

                    flows = []
                    for _, row in df.iterrows():
                        flow = {
                            "date": str(row.get("日期", ""))[:10],
                            "net_buy": (
                                float(row.get("当日成交净买额", 0))
                                if pd.notna(row.get("当日成交净买额"))
                                else None
                            ),
                            "buy_amount": (
                                float(row.get("买入成交额", 0))
                                if pd.notna(row.get("买入成交额"))
                                else None
                            ),
                            "sell_amount": (
                                float(row.get("卖出成交额", 0))
                                if pd.notna(row.get("卖出成交额"))
                                else None
                            ),
                            "cumulative_net_buy": (
                                float(row.get("历史累计净买额", 0))
                                if pd.notna(row.get("历史累计净买额"))
                                else None
                            ),
                            "inflow": (
                                float(row.get("当日资金流入", 0))
                                if pd.notna(row.get("当日资金流入"))
                                else None
                            ),
                            "remaining_quota": (
                                float(row.get("当日余额", 0))
                                if pd.notna(row.get("当日余额"))
                                else None
                            ),
                            "holding_value": (
                                float(row.get("持股市值", 0))
                                if pd.notna(row.get("持股市值"))
                                else None
                            ),
                        }
                        flows.append(flow)

                    return {
                        "direction": direction,
                        "flows": flows,
                        "latest_valid_date": latest_valid_date,
                        "data_cutoff_notice": "数据可能在 2024-08-19 后不完整，请以交易所公告为准",
                        "source": "akshare",
                    }
                except Exception as e:
                    logger.error(f"AKShare northbound flow error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        cache_key = f"{direction}:{days}"
        return await self._get_cached_or_fetch(
            "northbound_flow",
            cache_key,
            fetch,
        )

    async def get_industry_sector_list(self) -> Optional[Dict[str, Any]]:
        """Get list of all industry sectors with real-time data."""

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_board_industry_name_em()

                    if df is None or df.empty:
                        return None

                    sectors = []
                    for _, row in df.iterrows():
                        sector = {
                            "rank": int(row.get("排名", 0)),
                            "sector_name": row.get("板块名称"),
                            "sector_code": row.get("板块代码"),
                            "latest_price": (
                                float(row.get("最新价", 0))
                                if pd.notna(row.get("最新价"))
                                else None
                            ),
                            "change": (
                                float(row.get("涨跌额", 0))
                                if pd.notna(row.get("涨跌额"))
                                else None
                            ),
                            "change_pct": (
                                float(row.get("涨跌幅", 0))
                                if pd.notna(row.get("涨跌幅"))
                                else None
                            ),
                            "total_market_cap": (
                                float(row.get("总市值", 0))
                                if pd.notna(row.get("总市值"))
                                else None
                            ),
                            "turnover_rate": (
                                float(row.get("换手率", 0))
                                if pd.notna(row.get("换手率"))
                                else None
                            ),
                            "up_count": (
                                int(row.get("上涨家数", 0))
                                if pd.notna(row.get("上涨家数"))
                                else None
                            ),
                            "down_count": (
                                int(row.get("下跌家数", 0))
                                if pd.notna(row.get("下跌家数"))
                                else None
                            ),
                            "leading_stock": row.get("领涨股票"),
                            "leading_stock_change": (
                                float(row.get("领涨股票-涨跌幅", 0))
                                if pd.notna(row.get("领涨股票-涨跌幅"))
                                else None
                            ),
                        }
                        sectors.append(sector)

                    return {
                        "sectors": sectors,
                        "update_time": datetime.now().isoformat(),
                        "source": "akshare",
                    }
                except Exception as e:
                    logger.error(f"AKShare sector list error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "industry_sector_list",
            "all",
            fetch,
        )

    async def get_stock_industry_cn(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get industry information for A-share stock."""
        code = symbol.replace(".SS", "").replace(".SZ", "")

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_individual_info_em(symbol=code)

                    if df is None or df.empty:
                        return None

                    info = {}
                    for _, row in df.iterrows():
                        info[row["item"]] = row["value"]

                    return {
                        "symbol": symbol,
                        "stock_code": info.get("股票代码"),
                        "stock_name": info.get("股票简称"),
                        "industry": info.get("行业"),
                        "total_market_cap": info.get("总市值"),
                        "float_market_cap": info.get("流通市值"),
                        "source": "akshare",
                    }
                except Exception as e:
                    logger.error(f"AKShare stock info error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "stock_industry_cn",
            code,
            fetch,
        )

    async def get_sector_history(
        self,
        sector_name: str,
        period: str = "日k",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get historical data for an industry sector."""
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_board_industry_hist_em(
                        symbol=sector_name,
                        period=period,
                        start_date=start_date,
                        end_date=end_date,
                        adjust="",
                    )

                    if df is None or df.empty:
                        return None

                    bars = []
                    for _, row in df.iterrows():
                        bar = {
                            "date": str(row.get("日期", ""))[:10],
                            "open": float(row.get("开盘", 0)),
                            "close": float(row.get("收盘", 0)),
                            "high": float(row.get("最高", 0)),
                            "low": float(row.get("最低", 0)),
                            "change_pct": (
                                float(row.get("涨跌幅", 0))
                                if pd.notna(row.get("涨跌幅"))
                                else None
                            ),
                            "change": (
                                float(row.get("涨跌额", 0))
                                if pd.notna(row.get("涨跌额"))
                                else None
                            ),
                            "volume": (
                                int(row.get("成交量", 0))
                                if pd.notna(row.get("成交量"))
                                else None
                            ),
                            "amount": (
                                float(row.get("成交额", 0))
                                if pd.notna(row.get("成交额"))
                                else None
                            ),
                            "amplitude": (
                                float(row.get("振幅", 0))
                                if pd.notna(row.get("振幅"))
                                else None
                            ),
                            "turnover_rate": (
                                float(row.get("换手率", 0))
                                if pd.notna(row.get("换手率"))
                                else None
                            ),
                        }
                        bars.append(bar)

                    return {
                        "sector_name": sector_name,
                        "period": period,
                        "bars": bars,
                        "source": "akshare",
                    }
                except Exception as e:
                    logger.error(f"AKShare sector history error: {e}")
                    return None

            return await run_in_executor(_fetch_sync)

        cache_key = f"{sector_name}:{period}:{start_date}:{end_date}"
        return await self._get_cached_or_fetch(
            "sector_history",
            cache_key,
            fetch,
        )

    async def get_hk_stock_history(
        self,
        symbol: str,
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get Hong Kong stock historical data with yfinance fallback."""
        code = symbol.replace(".HK", "").lstrip("0").zfill(5)

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_hk_hist(
                        symbol=code,
                        period="daily",
                        adjust="qfq",
                    )

                    if df is None or df.empty:
                        return None

                    df = df.tail(days)

                    bars = []
                    for _, row in df.iterrows():
                        bars.append(
                            {
                                "date": str(row.get("日期", ""))[:10],
                                "open": float(row.get("开盘", 0)),
                                "high": float(row.get("最高", 0)),
                                "low": float(row.get("最低", 0)),
                                "close": float(row.get("收盘", 0)),
                                "volume": int(row.get("成交量", 0)),
                                "amount": float(row.get("成交额", 0)),
                                "change_pct": (
                                    float(row.get("涨跌幅", 0))
                                    if pd.notna(row.get("涨跌幅"))
                                    else None
                                ),
                            }
                        )

                    return {
                        "symbol": symbol,
                        "bars": bars,
                        "source": "akshare",
                    }
                except Exception as e:
                    logger.warning(f"AKShare HK history error: {e}, trying yfinance")
                    return None

            result = await run_in_executor(_fetch_sync)

            # Fallback to yfinance
            if result is None:
                import yfinance as yf

                def _fetch_yf():
                    ticker = yf.Ticker(f"{code}.HK")
                    df = ticker.history(period=f"{days}d")

                    if df is None or df.empty:
                        return None

                    bars = []
                    for idx, row in df.iterrows():
                        bars.append(
                            {
                                "date": idx.strftime("%Y-%m-%d"),
                                "open": round(float(row["Open"]), 2),
                                "high": round(float(row["High"]), 2),
                                "low": round(float(row["Low"]), 2),
                                "close": round(float(row["Close"]), 2),
                                "volume": int(row["Volume"]),
                            }
                        )

                    return {
                        "symbol": symbol,
                        "bars": bars,
                        "source": "yfinance",
                    }

                result = await run_in_executor(_fetch_yf)

            return result

        return await self._get_cached_or_fetch(
            "hk_history",
            f"{code}:{days}",
            fetch,
        )
