"""
Unit conversion service for precious metals.

Provides weight unit conversions between troy ounces (the standard
unit for precious metals pricing) and other common weight units.
"""
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class WeightUnit(str, Enum):
    """Supported weight units for precious metals."""

    TROY_OZ = "troy_oz"  # Standard unit for precious metals
    GRAM = "gram"
    KILOGRAM = "kilogram"
    OUNCE = "ounce"  # Avoirdupois ounce (common/imperial ounce)


# Conversion factors to troy oz
# Based on: 1 troy oz = 31.1035 grams
UNIT_TO_TROY_OZ: Dict[WeightUnit, Decimal] = {
    WeightUnit.TROY_OZ: Decimal("1.0"),
    WeightUnit.GRAM: Decimal("0.0321507"),      # 1 gram = 0.0321507 troy oz
    WeightUnit.KILOGRAM: Decimal("32.1507"),    # 1 kg = 32.1507 troy oz
    WeightUnit.OUNCE: Decimal("0.911458"),      # 1 oz (avdp) = 0.911458 troy oz
}

# For display: how many of each unit equals 1 troy oz
TROY_OZ_TO_UNIT: Dict[WeightUnit, Decimal] = {
    WeightUnit.TROY_OZ: Decimal("1.0"),
    WeightUnit.GRAM: Decimal("31.1035"),        # 1 troy oz = 31.1035 g
    WeightUnit.KILOGRAM: Decimal("0.0311035"),  # 1 troy oz = 0.0311035 kg
    WeightUnit.OUNCE: Decimal("1.09714"),       # 1 troy oz = 1.09714 oz (avdp)
}

# Unit display names (for UI)
UNIT_DISPLAY_NAMES = {
    WeightUnit.TROY_OZ: {"en": "troy oz", "zh": "金衡盎司"},
    WeightUnit.GRAM: {"en": "gram", "zh": "克"},
    WeightUnit.KILOGRAM: {"en": "kg", "zh": "千克"},
    WeightUnit.OUNCE: {"en": "oz", "zh": "常衡盎司"},
}


def convert_weight(
    value: Decimal,
    from_unit: WeightUnit,
    to_unit: WeightUnit,
    precision: int = 4,
) -> Decimal:
    """
    Convert weight between units.

    Args:
        value: The weight value to convert
        from_unit: The source weight unit
        to_unit: The target weight unit
        precision: Decimal places to round to (default: 4)

    Returns:
        Converted weight value as Decimal

    Example:
        >>> convert_weight(Decimal("100"), WeightUnit.GRAM, WeightUnit.TROY_OZ)
        Decimal('3.2151')
    """
    if from_unit == to_unit:
        return value

    # Convert to troy oz first, then to target unit
    troy_oz = value * UNIT_TO_TROY_OZ[from_unit]
    result = troy_oz * TROY_OZ_TO_UNIT[to_unit]

    # Round to specified precision
    quantize_str = "0." + "0" * precision
    result = result.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)

    logger.debug(
        f"Converted {value} {from_unit.value} to {result} {to_unit.value}"
    )
    return result


def convert_price_per_unit(
    price_per_troy_oz: Decimal,
    to_unit: WeightUnit,
    precision: int = 2,
) -> Decimal:
    """
    Convert price per troy oz to price per other unit.

    This is commonly used to display gold/silver prices in different units.
    For example, converting $2000/troy oz to $/gram.

    Args:
        price_per_troy_oz: The price per troy ounce (USD or any currency)
        to_unit: The target weight unit for pricing
        precision: Decimal places to round to (default: 2 for currency)

    Returns:
        Price per target unit as Decimal

    Example:
        >>> convert_price_per_unit(Decimal("2000"), WeightUnit.GRAM)
        Decimal('64.30')  # $2000/troy oz = ~$64.30/gram
    """
    if to_unit == WeightUnit.TROY_OZ:
        return price_per_troy_oz

    # Price per gram = price per troy oz / grams per troy oz
    # If 1 troy oz = 31.1035 grams, then price per gram = price / 31.1035
    result = price_per_troy_oz / TROY_OZ_TO_UNIT[to_unit]

    # Round to specified precision
    quantize_str = "0." + "0" * precision
    result = result.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)

    logger.debug(
        f"Converted price ${price_per_troy_oz}/troy oz to ${result}/{to_unit.value}"
    )
    return result


def get_conversion_factor(from_unit: WeightUnit, to_unit: WeightUnit) -> Decimal:
    """
    Get the conversion factor between two units.

    Args:
        from_unit: The source weight unit
        to_unit: The target weight unit

    Returns:
        Multiplication factor to convert from source to target unit

    Example:
        >>> get_conversion_factor(WeightUnit.GRAM, WeightUnit.TROY_OZ)
        Decimal('0.0321507')  # Multiply grams by this to get troy oz
    """
    if from_unit == to_unit:
        return Decimal("1.0")

    # Convert 1 unit of from_unit to to_unit
    troy_oz = UNIT_TO_TROY_OZ[from_unit]
    factor = troy_oz * TROY_OZ_TO_UNIT[to_unit]

    return factor.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def get_unit_display_name(unit: WeightUnit, locale: str = "en") -> str:
    """
    Get the display name for a weight unit.

    Args:
        unit: The weight unit
        locale: Language code ("en" or "zh")

    Returns:
        Human-readable unit name
    """
    names = UNIT_DISPLAY_NAMES.get(unit, {})
    return names.get(locale, names.get("en", unit.value))
