"""Market data provider service for institutional holdings, indices, and northbound flow.

NOTE: This module is now a facade that delegates to the providers package.
For new code, consider using the providers directly:

    from app.services.providers import get_provider_router

    router = await get_provider_router()
    # Use router.yfinance for institutional holders, analyst ratings, etc.
    # Use router.akshare for fund holdings, northbound flow, etc.
"""

import asyncio
import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from app.db.redis import get_redis

logger = logging.getLogger(__name__)

# Thread pool for synchronous API calls
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()

EXTERNAL_API_TIMEOUT = 30  # seconds


async def _get_executor() -> ThreadPoolExecutor:
    """Get shared thread pool executor."""
    global _executor
    if _executor is None:
        async with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=4)
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
        return None


# Cache TTL configurations (base_seconds, random_range_seconds)
MARKET_DATA_CACHE_TTL = {
    "institutional_holders": (86400, 3600),  # 24h + rand(1h)
    "sector_industry": (86400, 3600),  # 24h + rand(1h)
    "market_index": (300, 60),  # 5min + rand(1min)
    "fund_holdings": (86400, 3600),  # 24h + rand(1h)
    "northbound_holding": (3600, 600),  # 1h + rand(10min)
    "northbound_flow": (3600, 600),  # 1h + rand(10min)
    "industry_sector_list": (300, 60),  # 5min + rand(1min)
    "stock_industry_cn": (86400, 3600),  # 24h + rand(1h)
    "sector_history": (300, 60),  # 5min + rand(1min)
    "analyst_ratings": (86400, 3600),  # 24h + rand(1h)
    "technical_info": (3600, 600),  # 1h + rand(10min) - SMA, ADTV, beta
}

# Market index symbol mapping
MARKET_INDICES = {
    "sp500": ("^GSPC", "S&P 500"),
    "hang_seng": ("^HSI", "恒生指数"),
    "shanghai": ("000001.SS", "上证综指"),
    "shenzhen": ("399001.SZ", "深证成指"),
}


def _get_ttl(data_type: str) -> int:
    """Get TTL with randomization to prevent cache avalanche."""
    base, rand_range = MARKET_DATA_CACHE_TTL.get(data_type, (3600, 300))
    return base + random.randint(0, rand_range)


class MarketDataProvider:
    """
    Provides market data from yfinance and AKShare.

    Data Sources:
    - yfinance: Institutional holdings, sector/industry, market indices
    - AKShare: A-share fund holdings, northbound flow, stock connect, sector data

    Features:
    - Redis caching with configurable TTL
    - Graceful fallback when data unavailable
    - Clear data freshness indicators
    """

    def __init__(self):
        self._redis = None
        self._cache_prefix = "market_data:"

    async def _get_redis(self):
        """Get Redis client."""
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    def _build_cache_key(self, data_type: str, identifier: str) -> str:
        """Build cache key for market data."""
        return f"{self._cache_prefix}{data_type}:{identifier}"

    async def _get_cached_or_fetch(
        self,
        data_type: str,
        identifier: str,
        fetch_func: Callable,
    ) -> Optional[Dict[str, Any]]:
        """
        Get data from cache or fetch from source.

        Args:
            data_type: Type of data (e.g., 'institutional_holders')
            identifier: Unique identifier (e.g., symbol)
            fetch_func: Async function to fetch data

        Returns:
            Data dict or None if unavailable
        """
        redis = await self._get_redis()
        cache_key = self._build_cache_key(data_type, identifier)

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
                # Store in cache
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

    # ============ Institutional Holdings (yfinance) ============

    async def get_institutional_holders(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get institutional holders for a stock (US/HK).

        Args:
            symbol: Stock symbol (e.g., 'AAPL', '0700.HK')

        Returns:
            Dict with 'holders' list and metadata, or None if unavailable

        Note:
            yfinance returns DataFrame with columns:
            ['Date Reported', 'Holder', 'pctHeld', 'Shares', 'Value', 'pctChange']
        """

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(symbol)
                holders_df = ticker.institutional_holders

                if holders_df is None or holders_df.empty:
                    return None

                holders = []
                for _, row in holders_df.iterrows():
                    holder = {
                        "date_reported": (
                            str(row.get("Date Reported"))[:10]
                            if pd.notna(row.get("Date Reported"))
                            else None
                        ),
                        "holder": row.get("Holder", ""),
                        "pct_held": (
                            float(row.get("pctHeld", 0))
                            if pd.notna(row.get("pctHeld"))
                            else None
                        ),
                        "shares": (
                            int(row.get("Shares", 0))
                            if pd.notna(row.get("Shares"))
                            else None
                        ),
                        "value": (
                            int(row.get("Value", 0))
                            if pd.notna(row.get("Value"))
                            else None
                        ),
                        "pct_change": (
                            float(row.get("pctChange", 0))
                            if pd.notna(row.get("pctChange"))
                            else None
                        ),
                    }
                    holders.append(holder)

                # Calculate total institutional percentage
                total_pct = sum(
                    h["pct_held"] for h in holders if h["pct_held"] is not None
                )

                # Get latest date
                latest_date = None
                if holders and holders[0].get("date_reported"):
                    latest_date = holders[0]["date_reported"]

                return {
                    "symbol": symbol,
                    "holders": holders,
                    "total_institutional_pct": total_pct,
                    "data_as_of": latest_date,
                    "source": "yfinance",
                }

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "institutional_holders",
            symbol,
            fetch,
        )

    # ============ Sector/Industry Info (yfinance) ============

    async def get_sector_industry(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get sector and industry classification for a stock.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with 'sector' and 'industry' fields
        """

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info:
                    return None

                sector = info.get("sector")
                industry = info.get("industry")

                if not sector and not industry:
                    return None

                return {
                    "symbol": symbol,
                    "sector": sector,
                    "industry": industry,
                    "source": "yfinance",
                }

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "sector_industry",
            symbol,
            fetch,
        )

    # ============ Analyst Ratings (yfinance) ============

    async def get_analyst_ratings(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get analyst ratings and price targets for a stock.

        Args:
            symbol: Stock symbol (e.g., 'AAPL', '0700.HK')

        Returns:
            Dict with analyst recommendations and price targets, or None if unavailable

        Note:
            yfinance provides:
            - recommendationKey: 'buy', 'hold', 'sell', 'strong_buy', 'strong_sell'
            - targetMeanPrice: Average analyst price target
            - targetHighPrice, targetLowPrice: Range of targets
            - numberOfAnalystOpinions: Number of analysts
            - recommendationMean: 1.0 (strong buy) to 5.0 (strong sell)
        """

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info:
                    return None

                # Check if analyst data is available
                recommendation = info.get("recommendationKey")
                target_mean = info.get("targetMeanPrice")
                current_price = info.get("currentPrice") or info.get("regularMarketPrice")

                if not recommendation and not target_mean:
                    return None

                # Calculate upside/downside
                upside_pct = None
                if target_mean and current_price and current_price > 0:
                    upside_pct = ((target_mean - current_price) / current_price) * 100

                return {
                    "symbol": symbol,
                    "recommendation": recommendation,  # 'buy', 'hold', 'sell', etc.
                    "recommendation_mean": info.get("recommendationMean"),  # 1.0-5.0 scale
                    "target_mean_price": target_mean,
                    "target_high_price": info.get("targetHighPrice"),
                    "target_low_price": info.get("targetLowPrice"),
                    "target_median_price": info.get("targetMedianPrice"),
                    "number_of_analysts": info.get("numberOfAnalystOpinions"),
                    "current_price": current_price,
                    "upside_pct": round(upside_pct, 2) if upside_pct else None,
                    "source": "yfinance",
                }

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "analyst_ratings",
            symbol,
            fetch,
        )

    async def get_technical_info(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get technical indicator data from yfinance for sentiment analysis.

        This fetches pre-calculated data that yfinance provides, which is more
        accurate than calculating ourselves from price history.

        Args:
            symbol: Stock symbol (e.g., 'AAPL', '0700.HK')

        Returns:
            Dict with technical indicators:
            - fiftyDayAverage (SMA 50)
            - twoHundredDayAverage (SMA 200)
            - averageVolume (3-month ADTV)
            - averageVolume10days (10-day ADTV)
            - beta
            - fiftyTwoWeekHigh/Low
        """

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info:
                    return None

                # Extract technical indicator fields
                return {
                    "symbol": symbol,
                    # Moving averages
                    "fiftyDayAverage": info.get("fiftyDayAverage"),
                    "twoHundredDayAverage": info.get("twoHundredDayAverage"),
                    # Volume
                    "averageVolume": info.get("averageVolume"),  # 3-month
                    "averageVolume10days": info.get("averageVolume10days"),
                    # Risk metrics
                    "beta": info.get("beta"),
                    # Price range
                    "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
                    "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
                    # Current price for reference
                    "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "source": "yfinance",
                }

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "technical_info",
            symbol,
            fetch,
        )

    # ============ Market Index Data (yfinance) ============

    async def get_market_index(
        self,
        index_symbol: str,
        period: str = "5d",
    ) -> Optional[Dict[str, Any]]:
        """
        Get market index data.

        Args:
            index_symbol: Index symbol (^GSPC, ^HSI, 000001.SS, 399001.SZ)
            period: History period (1d, 5d, 1mo, 3mo)

        Returns:
            Dict with OHLCV bars and summary
        """

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(index_symbol)
                df = ticker.history(period=period)

                if df is None or df.empty:
                    return None

                # Get index name
                info = ticker.info
                name = info.get("shortName", index_symbol)

                # Convert to bars
                bars = []
                for idx, row in df.iterrows():
                    bars.append(
                        {
                            "date": idx.isoformat(),
                            "open": round(float(row["Open"]), 2),
                            "high": round(float(row["High"]), 2),
                            "low": round(float(row["Low"]), 2),
                            "close": round(float(row["Close"]), 2),
                            "volume": int(row["Volume"]),
                        }
                    )

                # Calculate change
                latest_close = bars[-1]["close"] if bars else None
                prev_close = bars[-2]["close"] if len(bars) >= 2 else None
                change_pct = None
                if latest_close and prev_close:
                    change_pct = round(
                        (latest_close - prev_close) / prev_close * 100, 2
                    )

                return {
                    "symbol": index_symbol,
                    "name": name,
                    "bars": bars,
                    "latest_close": latest_close,
                    "change_pct": change_pct,
                    "source": "yfinance",
                }

            return await run_in_executor(_fetch_sync)

        cache_key = f"{index_symbol}:{period}"
        return await self._get_cached_or_fetch(
            "market_index",
            cache_key,
            fetch,
        )

    async def get_all_market_indices(
        self,
        period: str = "5d",
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Get all major market indices in parallel.

        Returns:
            Dict mapping index name to data
        """
        tasks = {
            name: self.get_market_index(symbol, period)
            for name, (symbol, _) in MARKET_INDICES.items()
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        return {
            name: result if not isinstance(result, Exception) else None
            for name, result in zip(tasks.keys(), results)
        }

    # ============ A-Share Fund Holdings (AKShare) ============

    async def get_fund_holdings_cn(
        self,
        symbol: str,
        quarter: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get fund holdings for A-share stock.

        Args:
            symbol: A-share code (e.g., '600519.SS' or '600519')
            quarter: Quarter code (e.g., '20243' for 2024 Q3).
                    Default: latest quarter

        Returns:
            Dict with fund holding data for the specific stock

        Note:
            ak.stock_institute_hold() returns ALL stocks.
            Must filter by stock code after fetching.
        """
        # Normalize symbol
        code = symbol.replace(".SS", "").replace(".SZ", "")

        # Default to most recent available quarter
        # Fund holding data is typically released 1-2 months after quarter end
        # Try multiple quarters in case recent data isn't available
        quarters_to_try = []
        if quarter is None:
            now = datetime.now()
            year = now.year
            month = now.month
            # Generate candidate quarters (most recent first, going back 8 quarters)
            for i in range(8):
                # Calculate quarter i periods ago
                total_q = (year * 4 + ((month - 1) // 3)) - i - 1
                q_year = total_q // 4
                q_num = (total_q % 4) + 1
                quarters_to_try.append(f"{q_year}{q_num}")
        else:
            quarters_to_try = [quarter]

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                # Try each quarter until we find data
                for q in quarters_to_try:
                    try:
                        df = ak.stock_institute_hold(symbol=q)

                        if df is None or df.empty:
                            continue

                        # Filter by stock code
                        row = df[df["证券代码"] == code]

                        if row.empty:
                            # Stock not in this quarter's data, try next
                            continue

                        # Extract data
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

                # No data found in any quarter
                return {
                    "symbol": symbol,
                    "quarter": quarters_to_try[0] if quarters_to_try else None,
                    "holdings": None,
                    "source": "akshare",
                    "note": f"股票 {code} 未被基金持仓或无数据",
                }

            return await run_in_executor(_fetch_sync)

        # Use first quarter for cache key
        cache_key = f"{code}:{quarters_to_try[0] if quarters_to_try else 'default'}"
        return await self._get_cached_or_fetch(
            "fund_holdings",
            cache_key,
            fetch,
        )

    # ============ Northbound Individual Stock Holding (AKShare) ============

    async def get_northbound_holding(
        self,
        symbol: str,
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        Get northbound holding for a specific A-share stock.

        Args:
            symbol: A-share code (e.g., '600519.SS')
            days: Number of recent days to return

        Returns:
            Dict with holding history for the specific stock

        Note:
            Uses ak.stock_hsgt_individual_em(symbol) for fast individual query.
            Data stops at 2024-08-16 due to disclosure policy change.
        """
        # Normalize symbol
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

                    # Take recent N days
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

    # ============ Northbound Capital Flow (AKShare) ============

    async def get_northbound_flow(
        self,
        direction: str = "北向资金",
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        Get northbound capital flow history.

        Args:
            direction: '北向资金', '沪股通', or '深股通'
            days: Number of days to return

        Returns:
            Dict with flow data and data freshness notice

        WARNING:
            Data after 2024-08-19 may contain NaN values due to API limitation.
            Always check 'latest_valid_date' in response.
        """

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_hsgt_hist_em(symbol=direction)

                    if df is None or df.empty:
                        return None

                    # Find latest valid data (non-NaN)
                    valid_df = df.dropna(subset=["当日成交净买额"])
                    latest_valid_date = None
                    if not valid_df.empty:
                        latest_valid_date = str(valid_df["日期"].max())[:10]

                    # Take recent N days
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

    # ============ Industry Sector Data (AKShare) ============

    async def get_industry_sector_list(self) -> Optional[Dict[str, Any]]:
        """
        Get list of all industry sectors with real-time data.

        Returns:
            Dict with list of sector summaries
        """

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
        """
        Get industry information for A-share stock.

        Args:
            symbol: A-share code (e.g., '600519.SS')

        Returns:
            Dict with industry name from stock_individual_info_em
        """
        code = symbol.replace(".SS", "").replace(".SZ", "")

        async def fetch():
            import akshare as ak

            def _fetch_sync():
                try:
                    df = ak.stock_individual_info_em(symbol=code)

                    if df is None or df.empty:
                        return None

                    # Convert item-value pairs to dict
                    info = {}
                    for _, row in df.iterrows():
                        info[row["item"]] = row["value"]

                    return {
                        "symbol": symbol,
                        "stock_code": info.get("股票代码"),
                        "stock_name": info.get("股票简称"),
                        "industry": info.get("行业"),  # e.g., "酿酒行业"
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
        """
        Get historical data for an industry sector.

        Args:
            sector_name: Sector name in Chinese (e.g., '酿酒行业')
            period: '日k', '周k', or '月k'
            start_date: Start date YYYYMMDD
            end_date: End date YYYYMMDD

        Returns:
            Dict with OHLCV bars for the sector
        """
        # Default date range: last 6 months
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

    # ============ Hong Kong Stock History (AKShare with yfinance fallback) ============

    async def get_hk_stock_history(
        self,
        symbol: str,
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        Get Hong Kong stock historical data.

        Args:
            symbol: HK stock code (e.g., '00700' or '0700.HK')
            days: Number of recent days

        Returns:
            Dict with OHLCV bars

        Note:
            Primary: AKShare stock_hk_hist (fast, reliable)
            Fallback: yfinance
        """
        # Normalize symbol
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

                    # Take recent N days
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

    # ============ Aggregated Market Context ============

    async def get_market_context(self) -> Dict[str, Any]:
        """
        Get aggregated market context for sentiment analysis.

        Combines:
        - Major market indices
        - Northbound capital flow summary

        Returns:
            Dict with market overview data
        """
        # Fetch all data in parallel
        indices_task = self.get_all_market_indices(period="5d")
        northbound_task = self.get_northbound_flow("北向资金", days=10)

        indices, northbound = await asyncio.gather(
            indices_task,
            northbound_task,
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(indices, Exception):
            logger.error(f"Error fetching indices: {indices}")
            indices = {}
        if isinstance(northbound, Exception):
            logger.error(f"Error fetching northbound: {northbound}")
            northbound = None

        # Build northbound summary
        northbound_summary = None
        if northbound and northbound.get("flows"):
            flows = northbound["flows"]
            valid_flows = [f for f in flows if f.get("net_buy") is not None]
            if valid_flows:
                latest = valid_flows[-1]
                total_5d = sum(
                    f["net_buy"] for f in valid_flows[-5:] if f.get("net_buy")
                )
                northbound_summary = {
                    "latest_date": latest.get("date"),
                    "latest_net_buy": latest.get("net_buy"),
                    "last_5d_net_buy": round(total_5d, 2),
                    "cumulative_net_buy": latest.get("cumulative_net_buy"),
                    "data_cutoff_notice": northbound.get("data_cutoff_notice"),
                }

        return {
            "sp500": indices.get("sp500") if indices else None,
            "hang_seng": indices.get("hang_seng") if indices else None,
            "shanghai_composite": indices.get("shanghai") if indices else None,
            "shenzhen_component": indices.get("shenzhen") if indices else None,
            "northbound_summary": northbound_summary,
            "fetched_at": datetime.utcnow().isoformat(),
            "source": "mixed",
        }


# Singleton instance
_market_data_provider: Optional[MarketDataProvider] = None
_provider_lock = asyncio.Lock()


async def get_market_data_provider() -> MarketDataProvider:
    """Get singleton MarketDataProvider instance."""
    global _market_data_provider
    if _market_data_provider is None:
        async with _provider_lock:
            if _market_data_provider is None:
                _market_data_provider = MarketDataProvider()
    return _market_data_provider
