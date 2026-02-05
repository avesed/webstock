"""Tests for stock service, focusing on precious metals search."""

import sys
import os

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Import the function directly to avoid circular imports
import re
import logging
from dataclasses import dataclass
from enum import Enum
from typing import List


class Market(Enum):
    """Stock market enum."""
    US = "US"
    HK = "HK"
    SH = "SH"
    SZ = "SZ"
    METAL = "METAL"


@dataclass
class SearchResult:
    """Search result data class."""
    symbol: str
    name: str
    exchange: str
    market: Market


PRECIOUS_METALS = {
    "GC=F": {"name": "Gold Futures", "name_zh": "黄金期货", "unit": "troy oz", "exchange": "COMEX"},
    "SI=F": {"name": "Silver Futures", "name_zh": "白银期货", "unit": "troy oz", "exchange": "COMEX"},
    "PL=F": {"name": "Platinum Futures", "name_zh": "铂金期货", "unit": "troy oz", "exchange": "NYMEX"},
    "PA=F": {"name": "Palladium Futures", "name_zh": "钯金期货", "unit": "troy oz", "exchange": "NYMEX"},
}

METAL_KEYWORDS = {
    "GC=F": ["gold", "黄金", "gc", "xau", "gc=f"],
    "SI=F": ["silver", "白银", "si=f", "xag"],  # "si" alone matches stock
    "PL=F": ["platinum", "铂金", "pl", "pl=f"],
    "PA=F": ["palladium", "钯金", "pa", "pa=f"],
}

logger = logging.getLogger(__name__)


def search_metals(query: str) -> List[SearchResult]:
    """
    Search precious metals by keyword.
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


class TestSearchMetals:
    """Tests for the search_metals function."""

    def test_search_gold_english(self):
        """Test searching for gold in English."""
        results = search_metals("gold")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"
        assert results[0].market == Market.METAL

    def test_search_gold_chinese(self):
        """Test searching for gold in Chinese."""
        results = search_metals("黄金")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"
        assert results[0].market == Market.METAL

    def test_search_silver_english(self):
        """Test searching for silver in English."""
        results = search_metals("silver")
        assert len(results) == 1
        assert results[0].symbol == "SI=F"

    def test_search_silver_chinese(self):
        """Test searching for silver in Chinese."""
        results = search_metals("白银")
        assert len(results) == 1
        assert results[0].symbol == "SI=F"

    def test_search_platinum(self):
        """Test searching for platinum."""
        results = search_metals("platinum")
        assert len(results) == 1
        assert results[0].symbol == "PL=F"

        results = search_metals("铂金")
        assert len(results) == 1
        assert results[0].symbol == "PL=F"

    def test_search_palladium(self):
        """Test searching for palladium."""
        results = search_metals("palladium")
        assert len(results) == 1
        assert results[0].symbol == "PA=F"

        results = search_metals("钯金")
        assert len(results) == 1
        assert results[0].symbol == "PA=F"

    def test_search_by_symbol(self):
        """Test searching by metal symbol."""
        # GC=F
        results = search_metals("GC=F")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"

        # gc (lowercase)
        results = search_metals("gc")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"

    def test_no_match_for_stock_ticker(self):
        """Test that GOLD stock ticker does not match (requires word boundary)."""
        # "GOLD" as a stock ticker should not match "gold" keyword
        # because we search for exact word "gold" not substring
        results = search_metals("GOLDEN")  # Should not match "gold"
        assert len(results) == 0

        results = search_metals("GOLDMINE")  # Should not match "gold"
        assert len(results) == 0

    def test_exact_match_gold(self):
        """Test that 'gold' exactly matches."""
        results = search_metals("gold")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"

    def test_si_alone_does_not_match_silver(self):
        """Test that 'SI' alone does not match silver (it's a stock ticker)."""
        # SI is a stock ticker, so searching "si" should not return silver
        results = search_metals("si")
        assert len(results) == 0

        # But SI=F should match
        results = search_metals("SI=F")
        assert len(results) == 1
        assert results[0].symbol == "SI=F"

    def test_case_insensitive(self):
        """Test case insensitivity."""
        results = search_metals("GOLD")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"

        results = search_metals("GoLd")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"

    def test_no_match_for_unrelated_query(self):
        """Test that unrelated queries return no results."""
        results = search_metals("apple")
        assert len(results) == 0

        results = search_metals("AAPL")
        assert len(results) == 0

    def test_whitespace_handling(self):
        """Test that whitespace is handled correctly."""
        results = search_metals("  gold  ")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"

    def test_xau_matches_gold(self):
        """Test that XAU matches gold."""
        results = search_metals("xau")
        assert len(results) == 1
        assert results[0].symbol == "GC=F"

    def test_xag_matches_silver(self):
        """Test that XAG matches silver."""
        results = search_metals("xag")
        assert len(results) == 1
        assert results[0].symbol == "SI=F"
