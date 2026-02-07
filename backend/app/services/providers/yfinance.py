"""YFinance data provider for US stocks, HK, and precious metals."""

import asyncio
import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
    PRECIOUS_METALS,
    SearchResult,
    StockFinancials,
    StockHistory,
    StockInfo,
    StockQuote,
)

logger = logging.getLogger(__name__)

# Thread pool for synchronous yfinance calls
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()

EXTERNAL_API_TIMEOUT = 30  # seconds

# Cache TTL configurations (base_seconds, random_range_seconds)
CACHE_TTL = {
    "institutional_holders": (86400, 3600),  # 24h + rand(1h)
    "market_index": (300, 60),  # 5min + rand(1min)
    "analyst_ratings": (86400, 3600),  # 24h + rand(1h)
    "technical_info": (3600, 600),  # 1h + rand(10min)
}

# Market index symbol mapping
MARKET_INDICES = {
    "sp500": ("^GSPC", "S&P 500"),
    "hang_seng": ("^HSI", "恒生指数"),
    "shanghai": ("000001.SS", "上证综指"),
    "shenzhen": ("399001.SZ", "深证成指"),
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


class YFinanceProvider(DataProvider):
    """
    YFinance data provider for US stocks, HK stocks, and precious metals.

    Primary provider for:
    - US stocks (NYSE, NASDAQ)
    - Precious metals (COMEX/NYMEX futures)

    Fallback provider for:
    - HK stocks (when AKShare fails)
    - A-shares (when AKShare and Tushare fail)
    """

    def __init__(self):
        self._redis = None
        self._cache_prefix = "yfinance:"

    @property
    def source(self) -> DataSource:
        return DataSource.YFINANCE

    @property
    def supported_markets(self) -> Set[Market]:
        return {Market.US, Market.HK, Market.METAL, Market.SH, Market.SZ}

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
        """Get real-time quote from yfinance."""
        try:
            import yfinance as yf

            def fetch():
                ticker = yf.Ticker(symbol)
                info = ticker.info
                if not info or info.get("regularMarketPrice") is None:
                    return None
                return info

            info = await run_in_executor(fetch)
            if not info:
                return None

            price = info.get("regularMarketPrice", 0)
            prev_close = info.get("previousClose", price)
            change = price - prev_close if prev_close else 0
            change_pct = (change / prev_close * 100) if prev_close else 0

            return StockQuote(
                symbol=symbol,
                name=info.get("shortName") or info.get("longName"),
                price=price,
                change=round(change, 4),
                change_percent=round(change_pct, 2),
                volume=info.get("regularMarketVolume", 0),
                market_cap=info.get("marketCap"),
                day_high=info.get("dayHigh"),
                day_low=info.get("dayLow"),
                open=info.get("open"),
                previous_close=prev_close,
                timestamp=datetime.utcnow(),
                market=market,
                source=DataSource.YFINANCE,
            )
        except Exception as e:
            logger.error(f"YFinance quote error for {symbol}: {e}")
            return None

    async def get_history(
        self,
        symbol: str,
        market: Market,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """Get historical data from yfinance."""
        try:
            import yfinance as yf

            def fetch():
                ticker = yf.Ticker(symbol)
                df = ticker.history(period=period.value, interval=interval.value)
                return df

            df = await run_in_executor(fetch)
            if df is None or df.empty:
                return None

            # Drop rows with NaN OHLC values (common in 1m data)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])

            bars = []
            for idx, row in df.iterrows():
                bars.append(
                    OHLCVBar(
                        date=idx.to_pydatetime(),
                        open=round(row["Open"], 4),
                        high=round(row["High"], 4),
                        low=round(row["Low"], 4),
                        close=round(row["Close"], 4),
                        volume=int(row["Volume"]),
                    )
                )

            return StockHistory(
                symbol=symbol,
                interval=interval,
                bars=bars,
                market=market,
                source=DataSource.YFINANCE,
            )
        except Exception as e:
            logger.error(f"YFinance history error for {symbol}: {e}")
            return None

    async def search(
        self,
        query: str,
        markets: Optional[Set[Market]] = None,
    ) -> List[SearchResult]:
        """Search stocks using yfinance (limited - direct ticker lookup only)."""
        try:
            import yfinance as yf

            def fetch():
                # yfinance doesn't have a proper search API
                # Try to get ticker info directly
                ticker = yf.Ticker(query.upper())
                info = ticker.info
                if info and info.get("shortName"):
                    return [
                        {
                            "symbol": query.upper(),
                            "name": info.get("shortName", ""),
                            "exchange": info.get("exchange", ""),
                        }
                    ]
                return []

            results = await run_in_executor(fetch)
            return [
                SearchResult(
                    symbol=r["symbol"],
                    name=r["name"],
                    exchange=r["exchange"],
                    market=Market.US,
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"YFinance search error for {query}: {e}")
            return []

    # === Optional Methods ===

    async def get_info(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockInfo]:
        """Get company/asset info from yfinance."""
        # Handle precious metals specially
        if market == Market.METAL and symbol in PRECIOUS_METALS:
            metal_info = PRECIOUS_METALS[symbol]
            return StockInfo(
                symbol=symbol,
                name=metal_info["name"],
                description=f"{metal_info['name_zh']} ({metal_info['name']})",
                sector="Commodities",
                industry="Precious Metals",
                website=None,
                employees=None,
                market_cap=None,
                currency=metal_info["currency"],
                exchange=metal_info["exchange"],
                market=market,
                source=DataSource.YFINANCE,
            )

        try:
            import yfinance as yf

            def fetch():
                ticker = yf.Ticker(symbol)
                return ticker.info

            info = await run_in_executor(fetch)
            if not info or not info.get("shortName"):
                return None

            return StockInfo(
                symbol=symbol,
                name=info.get("shortName") or info.get("longName", ""),
                description=info.get("longBusinessSummary"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                website=info.get("website"),
                employees=info.get("fullTimeEmployees"),
                market_cap=info.get("marketCap"),
                currency=info.get("currency", "USD"),
                exchange=info.get("exchange", ""),
                market=market,
                source=DataSource.YFINANCE,
            )
        except Exception as e:
            logger.error(f"YFinance info error for {symbol}: {e}")
            return None

    async def get_financials(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockFinancials]:
        """Get financial data from yfinance."""
        # Precious metals don't have financials
        if market == Market.METAL:
            return None

        try:
            import yfinance as yf

            def fetch():
                ticker = yf.Ticker(symbol)
                return ticker.info

            info = await run_in_executor(fetch)
            if not info:
                return None

            # Normalize dividend yield
            dividend_yield = info.get("dividendYield")
            if dividend_yield is not None:
                dividend_yield = dividend_yield / 100  # Convert 0.37 -> 0.0037
            elif info.get("payoutRatio") == 0:
                dividend_yield = 0.0

            dividend_rate = info.get("dividendRate")
            if dividend_rate is None and info.get("payoutRatio") == 0:
                dividend_rate = 0.0

            return StockFinancials(
                symbol=symbol,
                pe_ratio=info.get("trailingPE"),
                forward_pe=info.get("forwardPE"),
                eps=info.get("trailingEps"),
                dividend_yield=dividend_yield,
                dividend_rate=dividend_rate,
                book_value=info.get("bookValue"),
                price_to_book=info.get("priceToBook"),
                revenue=info.get("totalRevenue"),
                revenue_growth=info.get("revenueGrowth"),
                net_income=info.get("netIncomeToCommon"),
                profit_margin=info.get("profitMargins"),
                gross_margin=info.get("grossMargins"),
                operating_margin=info.get("operatingMargins"),
                roe=info.get("returnOnEquity"),
                roa=info.get("returnOnAssets"),
                debt_to_equity=info.get("debtToEquity"),
                current_ratio=info.get("currentRatio"),
                eps_growth=info.get("earningsQuarterlyGrowth"),
                payout_ratio=info.get("payoutRatio"),
                market=market,
                source=DataSource.YFINANCE,
            )
        except Exception as e:
            logger.error(f"YFinance financials error for {symbol}: {e}")
            return None

    async def get_analyst_ratings(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get analyst ratings and price targets."""

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info:
                    return None

                recommendation = info.get("recommendationKey")
                target_mean = info.get("targetMeanPrice")
                current_price = info.get("currentPrice") or info.get("regularMarketPrice")

                if not recommendation and not target_mean:
                    return None

                upside_pct = None
                if target_mean and current_price and current_price > 0:
                    upside_pct = ((target_mean - current_price) / current_price) * 100

                return {
                    "symbol": symbol,
                    "recommendation": recommendation,
                    "recommendation_mean": info.get("recommendationMean"),
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
        """Get pre-calculated technical data from yfinance."""

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info:
                    return None

                return {
                    "symbol": symbol,
                    "fiftyDayAverage": info.get("fiftyDayAverage"),
                    "twoHundredDayAverage": info.get("twoHundredDayAverage"),
                    "averageVolume": info.get("averageVolume"),
                    "averageVolume10days": info.get("averageVolume10days"),
                    "beta": info.get("beta"),
                    "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
                    "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
                    "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "source": "yfinance",
                }

            return await run_in_executor(_fetch_sync)

        return await self._get_cached_or_fetch(
            "technical_info",
            symbol,
            fetch,
        )

    # === Extended Methods (Institutional Data) ===

    async def get_institutional_holders(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get institutional holders for a stock (US/HK)."""

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

                total_pct = sum(
                    h["pct_held"] for h in holders if h["pct_held"] is not None
                )

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

    # === Market Index Methods ===

    async def get_market_index(
        self,
        index_symbol: str,
        period: str = "5d",
    ) -> Optional[Dict[str, Any]]:
        """Get market index data."""

        async def fetch():
            import yfinance as yf

            def _fetch_sync():
                ticker = yf.Ticker(index_symbol)
                df = ticker.history(period=period)

                if df is None or df.empty:
                    return None

                info = ticker.info
                name = info.get("shortName", index_symbol)

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
        """Get all major market indices in parallel."""
        tasks = {
            name: self.get_market_index(symbol, period)
            for name, (symbol, _) in MARKET_INDICES.items()
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        return {
            name: result if not isinstance(result, Exception) else None
            for name, result in zip(tasks.keys(), results)
        }

    async def get_sector_industry(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get sector and industry classification for a stock."""

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

        # No caching for this simple call as it's part of other cached operations
        return await fetch()
