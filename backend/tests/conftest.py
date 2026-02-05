"""
Pytest configuration and fixtures for WebStock backend tests.
"""
import pytest
from decimal import Decimal


@pytest.fixture
def sample_metal_symbols():
    """Sample precious metal symbols for testing."""
    return ["GC=F", "SI=F", "PL=F", "PA=F"]


@pytest.fixture
def sample_stock_symbols():
    """Sample stock symbols for testing."""
    return ["AAPL", "MSFT", "0700.HK", "600519.SS"]


@pytest.fixture
def sample_exchange_rates():
    """Sample exchange rates for testing."""
    return {
        "EUR": Decimal("0.92"),
        "CNY": Decimal("7.24"),
        "GBP": Decimal("0.79"),
        "JPY": Decimal("149.50"),
        "HKD": Decimal("7.82"),
    }
