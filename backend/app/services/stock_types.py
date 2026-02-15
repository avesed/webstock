"""Stock data types, constants, and utility functions.

Extracted from stock_service.py for clean separation of concerns.
Contains enums, dataclasses, constants, and utility functions
used across the stock data provider ecosystem.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
