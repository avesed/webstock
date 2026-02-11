"""Multi-source stock data service with fallback support."""

import asyncio
import hashlib
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

import pandas as pd

from app.services.data_aggregator import DataAggregator, DataType, get_data_aggregator

logger = logging.getLogger(__name__)

# Thread pool for synchronous library calls (yfinance, akshare)
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()

# Timeout for external API calls (in seconds)
EXTERNAL_API_TIMEOUT = 30


async def _get_executor() -> ThreadPoolExecutor:
    """Get thread pool executor, initialize if needed."""
    global _executor
    if _executor is None:
        async with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=10)
    return _executor


async def shutdown_executor() -> None:
    """Shutdown the thread pool executor."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True)
        _executor = None
        logger.info("ThreadPoolExecutor shutdown complete")


class Market(str, Enum):
    """Stock market identifiers."""

    US = "us"  # NYSE, NASDAQ
    HK = "hk"  # Hong Kong
    SH = "sh"  # Shanghai A-shares
    SZ = "sz"  # Shenzhen A-shares
    METAL = "metal"  # Precious metals futures (COMEX/NYMEX)


class DataSource(str, Enum):
    """Data source providers."""

    YFINANCE = "yfinance"
    AKSHARE = "akshare"
    TUSHARE = "tushare"
    TIINGO = "tiingo"


class HistoryInterval(str, Enum):
    """Historical data intervals."""

    ONE_MINUTE = "1m"
    TWO_MINUTES = "2m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    HOURLY = "1h"
    DAILY = "1d"
    WEEKLY = "1wk"
    MONTHLY = "1mo"


class HistoryPeriod(str, Enum):
    """Historical data periods."""

    ONE_DAY = "1d"
    FIVE_DAYS = "5d"
    ONE_MONTH = "1mo"
    THREE_MONTHS = "3mo"
    SIX_MONTHS = "6mo"
    ONE_YEAR = "1y"
    TWO_YEARS = "2y"
    FIVE_YEARS = "5y"
    MAX = "max"


@dataclass
class StockQuote:
    """Real-time stock quote data."""

    symbol: str
    name: Optional[str]
    price: float
    change: float
    change_percent: float
    volume: int
    market_cap: Optional[float]
    day_high: Optional[float]
    day_low: Optional[float]
    open: Optional[float]
    previous_close: Optional[float]
    timestamp: datetime
    market: Market
    source: DataSource

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "change": self.change,
            "change_percent": self.change_percent,
            "volume": self.volume,
            "market_cap": self.market_cap,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "open": self.open,
            "previous_close": self.previous_close,
            "timestamp": self.timestamp.isoformat(),
            "market": self.market.value,
            "source": self.source.value,
        }


@dataclass
class StockInfo:
    """Company information."""

    symbol: str
    name: str
    description: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    website: Optional[str]
    employees: Optional[int]
    market_cap: Optional[float]
    currency: str
    exchange: str
    market: Market
    source: DataSource

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "description": self.description,
            "sector": self.sector,
            "industry": self.industry,
            "website": self.website,
            "employees": self.employees,
            "market_cap": self.market_cap,
            "currency": self.currency,
            "exchange": self.exchange,
            "market": self.market.value,
            "source": self.source.value,
        }


@dataclass
class StockFinancials:
    """Financial metrics."""

    symbol: str
    pe_ratio: Optional[float]
    forward_pe: Optional[float]
    eps: Optional[float]
    dividend_yield: Optional[float]
    dividend_rate: Optional[float]
    book_value: Optional[float]
    price_to_book: Optional[float]
    revenue: Optional[float]
    revenue_growth: Optional[float]
    net_income: Optional[float]
    profit_margin: Optional[float]
    gross_margin: Optional[float]
    operating_margin: Optional[float]
    roe: Optional[float]
    roa: Optional[float]
    debt_to_equity: Optional[float]
    current_ratio: Optional[float]
    eps_growth: Optional[float]
    payout_ratio: Optional[float]
    market: Market
    source: DataSource

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "pe_ratio": self.pe_ratio,
            "forward_pe": self.forward_pe,
            "eps": self.eps,
            "dividend_yield": self.dividend_yield,
            "dividend_rate": self.dividend_rate,
            "book_value": self.book_value,
            "price_to_book": self.price_to_book,
            "revenue": self.revenue,
            "revenue_growth": self.revenue_growth,
            "net_income": self.net_income,
            "profit_margin": self.profit_margin,
            "gross_margin": self.gross_margin,
            "operating_margin": self.operating_margin,
            "roe": self.roe,
            "roa": self.roa,
            "debt_to_equity": self.debt_to_equity,
            "current_ratio": self.current_ratio,
            "eps_growth": self.eps_growth,
            "payout_ratio": self.payout_ratio,
            "market": self.market.value,
            "source": self.source.value,
        }


@dataclass
class OHLCVBar:
    """Single OHLCV bar."""

    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class StockHistory:
    """Historical OHLCV data."""

    symbol: str
    interval: HistoryInterval
    bars: List[OHLCVBar]
    market: Market
    source: DataSource

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval.value,
            "bars": [bar.to_dict() for bar in self.bars],
            "market": self.market.value,
            "source": self.source.value,
        }


@dataclass
class SearchResult:
    """Stock search result."""

    symbol: str
    name: str
    exchange: str
    market: Market

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "exchange": self.exchange,
            "market": self.market.value,
        }


# Precious metals metadata
# Futures contracts traded on COMEX (CME Group) and NYMEX
PRECIOUS_METALS = {
    "GC=F": {
        "name": "Gold Futures",
        "name_zh": "黄金期货",
        "unit": "troy oz",
        "exchange": "COMEX",
        "currency": "USD",
    },
    "SI=F": {
        "name": "Silver Futures",
        "name_zh": "白银期货",
        "unit": "troy oz",
        "exchange": "COMEX",
        "currency": "USD",
    },
    "PL=F": {
        "name": "Platinum Futures",
        "name_zh": "铂金期货",
        "unit": "troy oz",
        "exchange": "NYMEX",
        "currency": "USD",
    },
    "PA=F": {
        "name": "Palladium Futures",
        "name_zh": "钯金期货",
        "unit": "troy oz",
        "exchange": "NYMEX",
        "currency": "USD",
    },
}

# Metal search keywords mapping
METAL_KEYWORDS = {
    "GC=F": ["gold", "黄金", "gc", "xau", "gc=f"],
    "SI=F": ["silver", "白银", "si=f", "xag"],  # "si" alone matches stock
    "PL=F": ["platinum", "铂金", "pl", "pl=f"],
    "PA=F": ["palladium", "钯金", "pa", "pa=f"],
}


def is_precious_metal(symbol: str) -> bool:
    """Check if symbol is a precious metal future."""
    return symbol.upper() in PRECIOUS_METALS


def search_metals(query: str) -> List[SearchResult]:
    """
    Search precious metals by keyword.

    Supports keywords in English and Chinese:
    - gold/黄金/gc/xau -> GC=F (Gold Futures)
    - silver/白银/si=f/xag -> SI=F (Silver Futures)
    - platinum/铂金/pl -> PL=F (Platinum Futures)
    - palladium/钯金/pa -> PA=F (Palladium Futures)

    Args:
        query: Search query string

    Returns:
        List of matching precious metal SearchResult objects
    """
    query_lower = query.lower().strip()
    results = []

    for symbol, keywords in METAL_KEYWORDS.items():
        for kw in keywords:
            # For Chinese keywords, use exact match or contains
            if any('\u4e00' <= c <= '\u9fff' for c in kw):
                # Chinese character - check if keyword is in query
                if kw in query_lower:
                    meta = PRECIOUS_METALS[symbol]
                    results.append(SearchResult(
                        symbol=symbol,
                        name=meta["name"],
                        exchange=meta["exchange"],
                        market=Market.METAL,
                    ))
                    logger.debug(f"Metal search matched (Chinese): {symbol} for query '{query}'")
                    break
            else:
                # English/symbol - use word boundary or exact match
                # Match: "gold", "gc", "gc=f" but not "golden" or "goldmine"
                pattern = rf'\b{re.escape(kw)}\b' if len(kw) > 2 else rf'^{re.escape(kw)}$'
                if re.search(pattern, query_lower):
                    meta = PRECIOUS_METALS[symbol]
                    results.append(SearchResult(
                        symbol=symbol,
                        name=meta["name"],
                        exchange=meta["exchange"],
                        market=Market.METAL,
                    ))
                    logger.debug(f"Metal search matched (English): {symbol} for query '{query}'")
                    break

    if results:
        logger.info(f"Metal search found {len(results)} results for query '{query}'")

    return results


def detect_market(symbol: str) -> Market:
    """
    Detect market from symbol format.

    Formats:
    - Precious metals: GC=F, SI=F, PL=F, PA=F (checked FIRST to avoid conflicts)
    - US: AAPL, MSFT (no suffix)
    - HK: 0700.HK, 9988.HK
    - Shanghai: 600519.SS, 600036.SS
    - Shenzhen: 000001.SZ, 000858.SZ
    """
    symbol = symbol.upper()

    # Check precious metals FIRST (SI=F would otherwise match US pattern)
    if symbol in PRECIOUS_METALS:
        logger.debug(f"Detected market METAL for symbol: {symbol}")
        return Market.METAL

    if symbol.endswith(".HK"):
        return Market.HK
    elif symbol.endswith(".SS"):
        return Market.SH
    elif symbol.endswith(".SZ"):
        return Market.SZ
    else:
        return Market.US


def normalize_symbol(symbol: str, market: Market) -> str:
    """Normalize symbol format for different markets."""
    symbol = symbol.upper().strip()

    if market == Market.HK:
        # Remove .HK suffix, pad to 5 digits for akshare
        code = symbol.replace(".HK", "")
        return code.zfill(5)
    elif market in (Market.SH, Market.SZ):
        # Remove suffix for akshare
        return symbol.replace(".SS", "").replace(".SZ", "")
    else:
        return symbol


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


class YFinanceProvider:
    """YFinance data provider for US stocks and fallback.

    DEPRECATED: This class is deprecated and will be removed in a future version.
    Use the new providers package instead:

        from app.services.providers import YFinanceProvider
    """

    @staticmethod
    async def get_quote(symbol: str, market: Market) -> Optional[StockQuote]:
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

    @staticmethod
    async def get_history(
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

    @staticmethod
    async def get_info(symbol: str, market: Market) -> Optional[StockInfo]:
        """Get company info from yfinance."""
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

    @staticmethod
    async def get_financials(
        symbol: str, market: Market
    ) -> Optional[StockFinancials]:
        """Get financial data from yfinance."""
        try:
            import yfinance as yf

            def fetch():
                ticker = yf.Ticker(symbol)
                return ticker.info

            info = await run_in_executor(fetch)
            if not info:
                return None

            # If dividendYield is None but payoutRatio is 0, the stock pays no dividends
            # yfinance returns dividendYield as percentage (0.37 = 0.37%)
            # Normalize to decimal format (0.0037) for consistency with other metrics
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

    @staticmethod
    async def search(query: str) -> List[SearchResult]:
        """Search stocks using yfinance."""
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


class AKShareProvider:
    """AKShare data provider for A-shares and HK stocks.

    DEPRECATED: This class is deprecated and will be removed in a future version.
    Use the new providers package instead:

        from app.services.providers import AKShareProvider
    """

    @staticmethod
    async def get_quote_cn(symbol: str, market: Market) -> Optional[StockQuote]:
        """Get real-time quote for A-shares from akshare."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, market)

            def fetch():
                # Get real-time quote for A-shares
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

    @staticmethod
    async def get_quote_hk(symbol: str) -> Optional[StockQuote]:
        """Get real-time quote for HK stocks from akshare (Xueqiu source).

        DEPRECATED: Use providers.akshare.AKShareProvider instead.
        """
        try:
            import akshare as ak

            code = normalize_symbol(symbol, Market.HK)

            def fetch():
                df = ak.stock_individual_spot_xq(symbol=code)
                if df is None or df.empty:
                    return None
                return dict(zip(df["item"], df["value"]))

            data = await run_in_executor(fetch)
            if not data:
                return None

            price = float(data.get("现价", 0))
            change = float(data.get("涨跌", 0))
            change_pct = float(data.get("涨幅", 0))

            return StockQuote(
                symbol=symbol,
                name=data.get("名称"),
                price=price,
                change=round(change, 4),
                change_percent=round(change_pct, 2),
                volume=int(data.get("成交量", 0)),
                market_cap=float(data.get("资产净值/总市值", 0)) if data.get("资产净值/总市值") else None,
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

    @staticmethod
    async def get_history_hk(
        symbol: str,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """Get historical data for HK stocks from akshare."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, Market.HK)

            def fetch():
                df = ak.stock_hk_hist(
                    symbol=code,
                    period="daily",  # akshare HK only supports daily
                    adjust="qfq",
                )
                return df

            df = await run_in_executor(fetch)
            if df is None or df.empty:
                return None

            # Filter by period
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
                bars = AKShareProvider._resample_bars(bars, interval)

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

    @staticmethod
    def _resample_bars(
        bars: List[OHLCVBar], interval: HistoryInterval
    ) -> List[OHLCVBar]:
        """Resample daily bars to weekly or monthly."""
        if not bars:
            return bars

        # Create DataFrame for resampling
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

        # Resample
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

    @staticmethod
    async def get_info_cn(symbol: str, market: Market) -> Optional[StockInfo]:
        """Get company info for A-shares from akshare."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, market)

            def fetch():
                # Get basic company info
                df = ak.stock_individual_info_em(symbol=code)
                if df is None or df.empty:
                    return None
                # Convert to dict
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

    @staticmethod
    async def get_financials_cn(
        symbol: str, market: Market
    ) -> Optional[StockFinancials]:
        """Get financial data for A-shares from akshare."""
        try:
            import akshare as ak

            code = normalize_symbol(symbol, market)

            def fetch():
                # Get financial indicators
                df = ak.stock_a_indicator_lg(symbol=code)
                if df is None or df.empty:
                    return None
                # Get latest row
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

    @staticmethod
    async def search_cn(query: str) -> List[SearchResult]:
        """Search A-share stocks."""
        try:
            import akshare as ak

            def fetch():
                # Get all A-share stock list
                df = ak.stock_zh_a_spot_em()
                # Filter by name or code
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

    @staticmethod
    async def search_hk(query: str) -> List[SearchResult]:
        """Search HK stocks."""
        try:
            import akshare as ak

            def fetch():
                # Get HK stock list
                df = ak.stock_hk_spot_em()
                # Filter by name or code
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


class TushareProvider:
    """Tushare data provider for A-shares (fallback).

    DEPRECATED: This class is deprecated and will be removed in a future version.
    Use the new providers package instead:

        from app.services.providers import TushareProvider
    """

    _token: Optional[str] = None

    @classmethod
    def is_available(cls) -> bool:
        """Check if Tushare API key is available."""
        if cls._token is None:
            cls._token = os.environ.get("TUSHARE_TOKEN", "")
        return bool(cls._token)

    @classmethod
    async def get_quote(cls, symbol: str, market: Market) -> Optional[StockQuote]:
        """Get quote from Tushare - skip if no API key."""
        if not cls.is_available():
            logger.debug("Tushare API key not configured, skipping")
            return None

        try:
            import tushare as ts

            ts.set_token(cls._token)
            pro = ts.pro_api()

            code = normalize_symbol(symbol, market)
            ts_code = f"{code}.{'SH' if market == Market.SH else 'SZ'}"

            def fetch():
                df = pro.daily(ts_code=ts_code, start_date=(datetime.now() - timedelta(days=5)).strftime("%Y%m%d"))
                if df is None or df.empty:
                    return None
                return df.iloc[0].to_dict()

            data = await run_in_executor(fetch)
            if not data:
                return None

            price = float(data.get("close", 0))
            prev_close = float(data.get("pre_close", price))
            change = price - prev_close
            change_pct = float(data.get("pct_chg", 0))

            return StockQuote(
                symbol=symbol,
                name=None,  # Tushare daily doesn't include name
                price=price,
                change=round(change, 4),
                change_percent=round(change_pct, 2),
                volume=int(data.get("vol", 0) * 100),  # Tushare uses lots
                market_cap=None,
                day_high=float(data.get("high", 0)) if data.get("high") else None,
                day_low=float(data.get("low", 0)) if data.get("low") else None,
                open=float(data.get("open", 0)) if data.get("open") else None,
                previous_close=prev_close,
                timestamp=datetime.utcnow(),
                market=market,
                source=DataSource.TUSHARE,
            )
        except Exception as e:
            logger.error(f"Tushare quote error for {symbol}: {e}")
            return None


class StockService:
    """
    Multi-source stock data service.

    Uses ProviderRouter for automatic provider selection and fallback.

    Data source strategy:
    - US stocks: yfinance (primary)
    - HK stocks: AKShare (primary), yfinance (fallback)
    - A-shares: AKShare (primary), Tushare (fallback), yfinance (fallback)
    - Precious metals: yfinance only
    """

    def __init__(self, aggregator: Optional[DataAggregator] = None):
        self._aggregator = aggregator
        self._router = None

    async def _get_aggregator(self) -> DataAggregator:
        """Get data aggregator, initialize if needed."""
        if self._aggregator is None:
            self._aggregator = await get_data_aggregator()
        return self._aggregator

    async def _get_router(self):
        """Get provider router, initialize if needed."""
        if self._router is None:
            from app.services.providers import get_provider_router
            self._router = await get_provider_router()
        return self._router

    async def get_quote(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get real-time quote for a stock.

        Args:
            symbol: Stock symbol (AAPL, 0700.HK, 600519.SS, etc.)
            force_refresh: Force fetch from source, skip cache

        Returns:
            Quote data as dict or None if unavailable
        """
        market = detect_market(symbol)
        aggregator = await self._get_aggregator()
        router = await self._get_router()

        async def fetch_quote() -> Optional[Dict[str, Any]]:
            quote = await router.get_quote(symbol, market)
            return quote.to_dict() if quote else None

        return await aggregator.get_data(
            symbol=symbol,
            data_type=DataType.QUOTE,
            fetch_func=fetch_quote,
            force_refresh=force_refresh,
        )

    async def get_history(
        self,
        symbol: str,
        period: HistoryPeriod = HistoryPeriod.ONE_YEAR,
        interval: HistoryInterval = HistoryInterval.DAILY,
        force_refresh: bool = False,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get historical OHLCV data.

        Args:
            symbol: Stock symbol
            period: Time period (1mo, 3mo, 6mo, 1y, 2y, 5y, max) - ignored when start/end provided
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)
            force_refresh: Force fetch from source
            start: Optional start date/datetime (e.g. '2025-03-01' or '2025-03-01T09:30:00')
            end: Optional end date/datetime (e.g. '2025-03-15' or '2025-03-15T15:00:00')

        Returns:
            Historical data as dict or None if unavailable
        """
        market = detect_market(symbol)
        aggregator = await self._get_aggregator()
        router = await self._get_router()

        # Build params hash for cache key uniqueness (include start/end if provided)
        if start and end:
            hash_input = f"{start}:{end}:{interval.value}"
        else:
            hash_input = f"{period.value}:{interval.value}"
        params_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]

        async def fetch_history() -> Optional[Dict[str, Any]]:
            history = await router.get_history(
                symbol, period, interval, market, start=start, end=end,
            )
            return history.to_dict() if history else None

        return await aggregator.get_data(
            symbol=symbol,
            data_type=DataType.HISTORY,
            fetch_func=fetch_history,
            params_hash=params_hash,
            force_refresh=force_refresh,
        )

    async def get_info(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get company/commodity information.

        Args:
            symbol: Stock or commodity symbol
            force_refresh: Force fetch from source

        Returns:
            Info as dict or None if unavailable
        """
        market = detect_market(symbol)
        aggregator = await self._get_aggregator()
        router = await self._get_router()

        async def fetch_info() -> Optional[Dict[str, Any]]:
            # Handle precious metals with static metadata
            if market == Market.METAL:
                metal_info = PRECIOUS_METALS.get(symbol.upper())
                if metal_info:
                    logger.info(f"Returning static info for precious metal: {symbol}")
                    return {
                        "symbol": symbol.upper(),
                        "name": metal_info["name"],
                        "name_zh": metal_info["name_zh"],
                        "description": f"{metal_info['name']} ({metal_info['name_zh']}) futures contract traded on {metal_info['exchange']}. Unit: {metal_info['unit']}.",
                        "sector": "Commodities",
                        "industry": "Precious Metals",
                        "website": None,
                        "employees": None,
                        "market_cap": None,
                        "currency": metal_info["currency"],
                        "exchange": metal_info["exchange"],
                        "market": market.value,
                        "source": "static",
                        "unit": metal_info["unit"],
                    }
                return None

            info = await router.get_info(symbol, market)
            return info.to_dict() if info else None

        return await aggregator.get_data(
            symbol=symbol,
            data_type=DataType.INFO,
            fetch_func=fetch_info,
            force_refresh=force_refresh,
        )

    async def get_financials(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get financial metrics.

        Args:
            symbol: Stock symbol
            force_refresh: Force fetch from source

        Returns:
            Financial data as dict or None if unavailable.
            Returns None for precious metals (no fundamental data).
        """
        market = detect_market(symbol)

        # Precious metals don't have traditional financial metrics
        if market == Market.METAL:
            logger.debug(f"Skipping financials for precious metal: {symbol}")
            return None

        aggregator = await self._get_aggregator()
        router = await self._get_router()

        async def fetch_financials() -> Optional[Dict[str, Any]]:
            financials = await router.get_financials(symbol, market)
            return financials.to_dict() if financials else None

        return await aggregator.get_data(
            symbol=symbol,
            data_type=DataType.FINANCIAL,
            fetch_func=fetch_financials,
            force_refresh=force_refresh,
        )

    async def search(
        self,
        query: str,
        markets: Optional[List[Market]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for stocks and precious metals across markets.

        Args:
            query: Search query (symbol or name)
            markets: Markets to search (default: all including METAL)

        Returns:
            List of search results
        """
        if not query or len(query) < 1:
            return []

        if markets is None:
            markets = [Market.US, Market.HK, Market.SH, Market.SZ, Market.METAL]

        aggregator = await self._get_aggregator()
        router = await self._get_router()

        # Use cache for search results
        cache_key = hashlib.md5(f"{query}:{','.join(m.value for m in markets)}".encode()).hexdigest()[:12]

        async def fetch_search() -> List[Dict[str, Any]]:
            results = await router.search(query, markets)
            # Filter by requested markets
            filtered = [r for r in results if r.market in markets]
            return [r.to_dict() for r in filtered[:50]]

        return await aggregator.get_data(
            symbol=cache_key,
            data_type=DataType.SEARCH,
            fetch_func=fetch_search,
        ) or []

    async def get_batch_quotes(
        self,
        symbols: List[str],
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Get quotes for multiple symbols efficiently.

        Args:
            symbols: List of stock symbols

        Returns:
            Dict mapping symbol to quote data
        """
        aggregator = await self._get_aggregator()

        async def fetch_single_quote(symbol: str) -> Optional[Dict[str, Any]]:
            return await self.get_quote(symbol)

        return await aggregator.get_batch_data(
            symbols=symbols,
            data_type=DataType.QUOTE,
            fetch_func=fetch_single_quote,
        )


# Singleton instance
_stock_service: Optional[StockService] = None
_stock_service_lock = asyncio.Lock()


async def get_stock_service() -> StockService:
    """Get singleton stock service instance."""
    global _stock_service
    if _stock_service is None:
        async with _stock_service_lock:
            if _stock_service is None:  # double-check after acquiring lock
                _stock_service = StockService()
    return _stock_service


async def cleanup_stock_service() -> None:
    """Cleanup stock service resources."""
    global _stock_service
    if _stock_service is not None:
        _stock_service = None
    # Shutdown the thread pool executor
    await shutdown_executor()
