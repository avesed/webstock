"""
Tests for symbol validation module.
"""
import pytest
from fastapi import HTTPException

from app.utils.symbol_validation import validate_symbol, is_precious_metal, VALID_METAL_SYMBOLS


class TestValidateSymbol:
    """Tests for validate_symbol function."""

    def test_valid_us_stock(self):
        assert validate_symbol("AAPL") == "AAPL"
        assert validate_symbol("msft") == "MSFT"  # lowercase normalized
        assert validate_symbol("A") == "A"

    def test_valid_hk_stock(self):
        assert validate_symbol("0700.HK") == "0700.HK"
        assert validate_symbol("9988.hk") == "9988.HK"

    def test_valid_a_share(self):
        assert validate_symbol("600519.SS") == "600519.SS"
        assert validate_symbol("000001.SZ") == "000001.SZ"

    def test_valid_metal_futures(self):
        assert validate_symbol("GC=F") == "GC=F"
        assert validate_symbol("SI=F") == "SI=F"
        assert validate_symbol("PL=F") == "PL=F"
        assert validate_symbol("PA=F") == "PA=F"
        assert validate_symbol("gc=f") == "GC=F"  # lowercase normalized

    def test_invalid_symbols(self):
        # Invalid patterns should raise HTTPException
        with pytest.raises(HTTPException):
            validate_symbol("INVALID123")
        with pytest.raises(HTTPException):
            validate_symbol("XX=F")  # Invalid metal
        with pytest.raises(HTTPException):
            validate_symbol("")
        with pytest.raises(HTTPException):
            validate_symbol("TOOLONGSTOCKNAME")

    def test_auto_append_hk_suffix(self):
        # Bare HK codes should get .HK appended
        assert validate_symbol("0700") == "0700.HK"
        assert validate_symbol("9988") == "9988.HK"

    def test_auto_append_a_share_suffix(self):
        # Bare A-share codes should get exchange suffix appended
        assert validate_symbol("600519") == "600519.SS"  # Shanghai
        assert validate_symbol("000001") == "000001.SZ"  # Shenzhen

    def test_normalize_hk_symbol_format(self):
        # Leading zeros in HK symbols should be normalized
        assert validate_symbol("00700.HK") == "0700.HK"


class TestIsPreciousMetal:
    """Tests for is_precious_metal function."""

    def test_valid_metals(self):
        assert is_precious_metal("GC=F") is True
        assert is_precious_metal("SI=F") is True
        assert is_precious_metal("PL=F") is True
        assert is_precious_metal("PA=F") is True

    def test_stocks_not_metals(self):
        assert is_precious_metal("AAPL") is False
        assert is_precious_metal("SI") is False  # Stock, not silver futures
        assert is_precious_metal("GC") is False

    def test_case_insensitive(self):
        assert is_precious_metal("gc=f") is True
        assert is_precious_metal("Gc=F") is True


class TestValidMetalSymbols:
    """Tests for VALID_METAL_SYMBOLS constant."""

    def test_contains_all_metals(self):
        expected = {"GC=F", "SI=F", "PL=F", "PA=F"}
        assert VALID_METAL_SYMBOLS == expected

    def test_count(self):
        assert len(VALID_METAL_SYMBOLS) == 4
