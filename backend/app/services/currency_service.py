"""
Currency conversion service using Finnhub Forex API.

Provides real-time currency conversion with Redis caching to respect
Finnhub API rate limits. Falls back to USD if conversion rates are unavailable.
"""
import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Dict, Optional

import httpx

from app.config import settings
from app.db.redis import get_redis

logger = logging.getLogger(__name__)


class Currency(str, Enum):
    """Supported currencies for conversion."""

    USD = "USD"  # US Dollar (base currency for precious metals)
    EUR = "EUR"  # Euro
    CNY = "CNY"  # Chinese Yuan
    GBP = "GBP"  # British Pound
    JPY = "JPY"  # Japanese Yen
    HKD = "HKD"  # Hong Kong Dollar
    CHF = "CHF"  # Swiss Franc
    AUD = "AUD"  # Australian Dollar
    CAD = "CAD"  # Canadian Dollar
    SGD = "SGD"  # Singapore Dollar


# Currency display symbols
CURRENCY_SYMBOLS: Dict[Currency, str] = {
    Currency.USD: "$",
    Currency.EUR: "\u20ac",
    Currency.CNY: "\u00a5",
    Currency.GBP: "\u00a3",
    Currency.JPY: "\u00a5",
    Currency.HKD: "HK$",
    Currency.CHF: "CHF",
    Currency.AUD: "A$",
    Currency.CAD: "C$",
    Currency.SGD: "S$",
}

# Currency display names
CURRENCY_NAMES: Dict[Currency, Dict[str, str]] = {
    Currency.USD: {"en": "US Dollar", "zh": "美元"},
    Currency.EUR: {"en": "Euro", "zh": "欧元"},
    Currency.CNY: {"en": "Chinese Yuan", "zh": "人民币"},
    Currency.GBP: {"en": "British Pound", "zh": "英镑"},
    Currency.JPY: {"en": "Japanese Yen", "zh": "日元"},
    Currency.HKD: {"en": "Hong Kong Dollar", "zh": "港币"},
    Currency.CHF: {"en": "Swiss Franc", "zh": "瑞士法郎"},
    Currency.AUD: {"en": "Australian Dollar", "zh": "澳元"},
    Currency.CAD: {"en": "Canadian Dollar", "zh": "加元"},
    Currency.SGD: {"en": "Singapore Dollar", "zh": "新元"},
}

# Redis cache settings
CACHE_KEY = "forex:rates:usd"
CACHE_TTL = 3600  # 1 hour (Finnhub rate limit friendly)

# Fallback rates (used when API is unavailable)
# These are approximate rates and should only be used as last resort
FALLBACK_RATES: Dict[str, Decimal] = {
    "USD": Decimal("1.0"),
    "EUR": Decimal("0.92"),
    "CNY": Decimal("7.25"),
    "GBP": Decimal("0.79"),
    "JPY": Decimal("149.5"),
    "HKD": Decimal("7.82"),
    "CHF": Decimal("0.88"),
    "AUD": Decimal("1.53"),
    "CAD": Decimal("1.36"),
    "SGD": Decimal("1.34"),
}


async def get_exchange_rates(use_fallback: bool = True) -> Dict[str, Decimal]:
    """
    Get exchange rates from USD to other currencies.

    Uses Redis cache to minimize API calls. Falls back to hardcoded rates
    if the API is unavailable.

    Args:
        use_fallback: Whether to use fallback rates on API failure

    Returns:
        Dictionary mapping currency codes to exchange rates from USD

    Example:
        >>> rates = await get_exchange_rates()
        >>> rates["EUR"]
        Decimal('0.92')  # 1 USD = 0.92 EUR
    """
    redis_client = await get_redis()

    # Try cache first
    try:
        cached = await redis_client.get(CACHE_KEY)
        if cached:
            rates = json.loads(cached)
            logger.debug("Using cached exchange rates")
            return {k: Decimal(str(v)) for k, v in rates.items()}
    except Exception as e:
        logger.warning(f"Failed to read exchange rates from cache: {e}")

    # Fetch from Finnhub
    if not settings.FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not configured, using fallback rates")
        return FALLBACK_RATES if use_fallback else {}

    try:
        url = f"https://finnhub.io/api/v1/forex/rates?base=USD&token={settings.FINNHUB_API_KEY}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        rates = data.get("quote", {})

        if not rates:
            logger.warning("Empty rates response from Finnhub")
            return FALLBACK_RATES if use_fallback else {}

        logger.info(f"Fetched exchange rates from Finnhub: {len(rates)} currencies")

        # Cache the rates
        try:
            await redis_client.setex(CACHE_KEY, CACHE_TTL, json.dumps(rates))
            logger.debug(f"Cached exchange rates for {CACHE_TTL} seconds")
        except Exception as e:
            logger.warning(f"Failed to cache exchange rates: {e}")

        return {k: Decimal(str(v)) for k, v in rates.items()}

    except httpx.HTTPStatusError as e:
        logger.error(f"Finnhub API error: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Finnhub request error: {e}")
    except Exception as e:
        logger.error(f"Failed to fetch exchange rates: {e}")

    # Return fallback rates on failure
    if use_fallback:
        logger.warning("Using fallback exchange rates")
        return FALLBACK_RATES
    return {}


async def convert_to_currency(
    usd_amount: Decimal,
    target_currency: Currency,
    precision: int = 2,
) -> Optional[Decimal]:
    """
    Convert USD amount to target currency.

    Args:
        usd_amount: Amount in USD to convert
        target_currency: Target currency for conversion
        precision: Decimal places to round to (default: 2)

    Returns:
        Converted amount in target currency, or None if conversion fails

    Example:
        >>> await convert_to_currency(Decimal("100"), Currency.EUR)
        Decimal('92.00')  # $100 USD = 92.00 EUR
    """
    if target_currency == Currency.USD:
        return usd_amount

    rates = await get_exchange_rates()
    rate = rates.get(target_currency.value)

    if rate is None:
        logger.warning(
            f"Exchange rate for {target_currency.value} not available, "
            f"falling back to USD"
        )
        return None

    converted = usd_amount * rate

    # Round to specified precision
    quantize_str = "0." + "0" * precision
    converted = converted.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)

    logger.debug(
        f"Converted ${usd_amount} USD to {converted} {target_currency.value} "
        f"(rate: {rate})"
    )
    return converted


async def convert_from_currency(
    amount: Decimal,
    source_currency: Currency,
    precision: int = 2,
) -> Optional[Decimal]:
    """
    Convert amount from source currency to USD.

    Args:
        amount: Amount in source currency to convert
        source_currency: Source currency of the amount
        precision: Decimal places to round to (default: 2)

    Returns:
        Converted amount in USD, or None if conversion fails

    Example:
        >>> await convert_from_currency(Decimal("92"), Currency.EUR)
        Decimal('100.00')  # 92 EUR = $100.00 USD
    """
    if source_currency == Currency.USD:
        return amount

    rates = await get_exchange_rates()
    rate = rates.get(source_currency.value)

    if rate is None or rate == Decimal("0"):
        logger.warning(
            f"Exchange rate for {source_currency.value} not available"
        )
        return None

    # Divide to get USD amount
    converted = amount / rate

    # Round to specified precision
    quantize_str = "0." + "0" * precision
    converted = converted.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)

    logger.debug(
        f"Converted {amount} {source_currency.value} to ${converted} USD "
        f"(rate: {rate})"
    )
    return converted


def get_currency_symbol(currency: Currency) -> str:
    """
    Get the display symbol for a currency.

    Args:
        currency: The currency enum value

    Returns:
        Currency symbol (e.g., "$", "EUR", "CNY")
    """
    return CURRENCY_SYMBOLS.get(currency, currency.value)


def get_currency_name(currency: Currency, locale: str = "en") -> str:
    """
    Get the display name for a currency.

    Args:
        currency: The currency enum value
        locale: Language code ("en" or "zh")

    Returns:
        Human-readable currency name
    """
    names = CURRENCY_NAMES.get(currency, {})
    return names.get(locale, names.get("en", currency.value))


def format_currency(
    amount: Decimal,
    currency: Currency,
    include_symbol: bool = True,
) -> str:
    """
    Format an amount with currency symbol.

    Args:
        amount: The amount to format
        currency: The currency for formatting
        include_symbol: Whether to include the currency symbol

    Returns:
        Formatted currency string (e.g., "$1,234.56")
    """
    # Format with thousands separator and 2 decimal places
    formatted = f"{amount:,.2f}"

    if include_symbol:
        symbol = get_currency_symbol(currency)
        return f"{symbol}{formatted}"

    return formatted


async def get_supported_currencies() -> list[Dict[str, str]]:
    """
    Get list of supported currencies with their metadata.

    Returns:
        List of currency info dictionaries
    """
    return [
        {
            "code": c.value,
            "symbol": get_currency_symbol(c),
            "name_en": get_currency_name(c, "en"),
            "name_zh": get_currency_name(c, "zh"),
        }
        for c in Currency
    ]
