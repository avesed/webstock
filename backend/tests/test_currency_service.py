"""
Tests for currency service.
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.services.currency_service import (
    Currency,
    convert_to_currency,
    convert_from_currency,
    get_currency_symbol,
    get_currency_name,
    format_currency,
    get_supported_currencies,
    FALLBACK_RATES,
    CURRENCY_SYMBOLS,
    CURRENCY_NAMES,
)


class TestCurrencyConversion:
    """Tests for currency conversion."""

    @pytest.mark.asyncio
    async def test_usd_no_conversion(self):
        result = await convert_to_currency(Decimal("100"), Currency.USD)
        assert result == Decimal("100")

    @pytest.mark.asyncio
    async def test_conversion_with_fallback_rates(self):
        # Test with fallback rates (mock API to return empty)
        with patch('app.services.currency_service.get_exchange_rates', new_callable=AsyncMock) as mock_rates:
            mock_rates.return_value = FALLBACK_RATES
            result = await convert_to_currency(Decimal("100"), Currency.EUR)
            # Should use fallback rate
            expected = Decimal("100") * FALLBACK_RATES.get("EUR", Decimal("1"))
            assert result is not None
            assert abs(result - expected) < Decimal("0.01")

    @pytest.mark.asyncio
    async def test_conversion_from_usd_to_cny(self):
        with patch('app.services.currency_service.get_exchange_rates', new_callable=AsyncMock) as mock_rates:
            mock_rates.return_value = {"CNY": Decimal("7.25")}
            result = await convert_to_currency(Decimal("100"), Currency.CNY)
            assert result is not None
            assert abs(result - Decimal("725")) < Decimal("1")


class TestReverseConversion:
    """Tests for converting from foreign currency to USD."""

    @pytest.mark.asyncio
    async def test_usd_no_conversion(self):
        result = await convert_from_currency(Decimal("100"), Currency.USD)
        assert result == Decimal("100")

    @pytest.mark.asyncio
    async def test_conversion_from_eur_to_usd(self):
        with patch('app.services.currency_service.get_exchange_rates', new_callable=AsyncMock) as mock_rates:
            mock_rates.return_value = {"EUR": Decimal("0.92")}
            result = await convert_from_currency(Decimal("92"), Currency.EUR)
            assert result is not None
            # 92 EUR / 0.92 = 100 USD
            assert abs(result - Decimal("100")) < Decimal("1")


class TestCurrencyHelpers:
    """Tests for currency helper functions."""

    def test_get_currency_symbol(self):
        assert get_currency_symbol(Currency.USD) == "$"
        assert get_currency_symbol(Currency.EUR) == "\u20ac"  # Euro sign
        assert get_currency_symbol(Currency.CNY) == "\u00a5"  # Yen/Yuan sign
        assert get_currency_symbol(Currency.GBP) == "\u00a3"  # Pound sign

    def test_get_currency_symbol_all_currencies(self):
        # All currencies should have a symbol
        for currency in Currency:
            symbol = get_currency_symbol(currency)
            assert symbol is not None
            assert len(symbol) > 0

    def test_get_currency_name_english(self):
        assert get_currency_name(Currency.USD, "en") == "US Dollar"
        assert get_currency_name(Currency.EUR, "en") == "Euro"
        assert get_currency_name(Currency.GBP, "en") == "British Pound"
        assert get_currency_name(Currency.JPY, "en") == "Japanese Yen"

    def test_get_currency_name_chinese(self):
        assert get_currency_name(Currency.USD, "zh") == "美元"
        assert get_currency_name(Currency.CNY, "zh") == "人民币"
        assert get_currency_name(Currency.EUR, "zh") == "欧元"
        assert get_currency_name(Currency.GBP, "zh") == "英镑"

    def test_get_currency_name_default_to_english(self):
        # Unknown locale should default to English
        assert get_currency_name(Currency.USD, "fr") == "US Dollar"


class TestFormatCurrency:
    """Tests for format_currency function."""

    def test_format_with_symbol(self):
        result = format_currency(Decimal("1234.56"), Currency.USD, include_symbol=True)
        assert result == "$1,234.56"

    def test_format_without_symbol(self):
        result = format_currency(Decimal("1234.56"), Currency.USD, include_symbol=False)
        assert result == "1,234.56"

    def test_format_large_number(self):
        result = format_currency(Decimal("1234567.89"), Currency.USD)
        assert result == "$1,234,567.89"

    def test_format_small_number(self):
        result = format_currency(Decimal("0.99"), Currency.USD)
        assert result == "$0.99"


class TestGetSupportedCurrencies:
    """Tests for get_supported_currencies function."""

    @pytest.mark.asyncio
    async def test_returns_all_currencies(self):
        currencies = await get_supported_currencies()
        assert len(currencies) == len(Currency)

    @pytest.mark.asyncio
    async def test_currency_structure(self):
        currencies = await get_supported_currencies()
        for currency in currencies:
            assert "code" in currency
            assert "symbol" in currency
            assert "name_en" in currency
            assert "name_zh" in currency

    @pytest.mark.asyncio
    async def test_usd_is_included(self):
        currencies = await get_supported_currencies()
        usd_currencies = [c for c in currencies if c["code"] == "USD"]
        assert len(usd_currencies) == 1
        usd = usd_currencies[0]
        assert usd["symbol"] == "$"
        assert usd["name_en"] == "US Dollar"
        assert usd["name_zh"] == "美元"


class TestFallbackRates:
    """Tests for fallback exchange rates."""

    def test_fallback_rates_contain_all_currencies(self):
        for currency in Currency:
            assert currency.value in FALLBACK_RATES

    def test_usd_rate_is_one(self):
        assert FALLBACK_RATES["USD"] == Decimal("1.0")

    def test_rates_are_positive(self):
        for code, rate in FALLBACK_RATES.items():
            assert rate > Decimal("0"), f"Rate for {code} should be positive"


class TestCurrencyConstants:
    """Tests for currency constant dictionaries."""

    def test_all_currencies_have_symbols(self):
        for currency in Currency:
            assert currency in CURRENCY_SYMBOLS

    def test_all_currencies_have_names(self):
        for currency in Currency:
            assert currency in CURRENCY_NAMES
            assert "en" in CURRENCY_NAMES[currency]
            assert "zh" in CURRENCY_NAMES[currency]
