"""
Tests for unit converter service.
"""
import pytest
from decimal import Decimal

from app.services.unit_converter import (
    WeightUnit,
    convert_weight,
    convert_price_per_unit,
    get_conversion_factor,
    get_unit_display_name,
    TROY_OZ_TO_UNIT,
    UNIT_TO_TROY_OZ,
)


class TestConvertWeight:
    """Tests for weight conversion."""

    def test_same_unit_no_conversion(self):
        result = convert_weight(Decimal("10"), WeightUnit.TROY_OZ, WeightUnit.TROY_OZ)
        assert result == Decimal("10")

    def test_troy_oz_to_gram(self):
        # 1 troy oz = 31.1035 grams
        result = convert_weight(Decimal("1"), WeightUnit.TROY_OZ, WeightUnit.GRAM)
        assert abs(result - Decimal("31.1035")) < Decimal("0.001")

    def test_gram_to_troy_oz(self):
        # 31.1035 grams = 1 troy oz
        result = convert_weight(Decimal("31.1035"), WeightUnit.GRAM, WeightUnit.TROY_OZ)
        assert abs(result - Decimal("1")) < Decimal("0.001")

    def test_troy_oz_to_kilogram(self):
        # 1 troy oz = 0.0311035 kg
        result = convert_weight(Decimal("1"), WeightUnit.TROY_OZ, WeightUnit.KILOGRAM)
        assert abs(result - Decimal("0.0311")) < Decimal("0.001")

    def test_troy_oz_to_ounce(self):
        # 1 troy oz = 1.09714 avoirdupois oz
        result = convert_weight(Decimal("1"), WeightUnit.TROY_OZ, WeightUnit.OUNCE)
        assert abs(result - Decimal("1.0971")) < Decimal("0.001")

    def test_gram_to_kilogram(self):
        # 1000 grams = 1 kg
        result = convert_weight(Decimal("1000"), WeightUnit.GRAM, WeightUnit.KILOGRAM)
        assert abs(result - Decimal("1")) < Decimal("0.01")

    def test_precision_parameter(self):
        # Test that precision parameter affects the result
        result_2 = convert_weight(
            Decimal("1"), WeightUnit.TROY_OZ, WeightUnit.GRAM, precision=2
        )
        result_6 = convert_weight(
            Decimal("1"), WeightUnit.TROY_OZ, WeightUnit.GRAM, precision=6
        )
        # Both should be close to 31.1035 but with different precision
        assert abs(result_2 - Decimal("31.10")) < Decimal("0.01")
        assert abs(result_6 - Decimal("31.1035")) < Decimal("0.0001")


class TestConvertPricePerUnit:
    """Tests for price conversion."""

    def test_price_per_troy_oz_unchanged(self):
        result = convert_price_per_unit(Decimal("2000"), WeightUnit.TROY_OZ)
        assert result == Decimal("2000")

    def test_price_per_gram(self):
        # $2000/troy oz = $64.30/gram (approx)
        result = convert_price_per_unit(Decimal("2000"), WeightUnit.GRAM)
        assert abs(result - Decimal("64.30")) < Decimal("1")

    def test_price_per_kilogram(self):
        # $2000/troy oz = $64,301/kg (approx)
        result = convert_price_per_unit(Decimal("2000"), WeightUnit.KILOGRAM)
        assert result > Decimal("60000")

    def test_price_per_ounce(self):
        # $2000/troy oz = ~$1822.90/avoirdupois oz
        result = convert_price_per_unit(Decimal("2000"), WeightUnit.OUNCE)
        assert abs(result - Decimal("1822.90")) < Decimal("10")

    def test_precision_parameter(self):
        # Test price conversion with different precision
        result_0 = convert_price_per_unit(Decimal("2000"), WeightUnit.GRAM, precision=0)
        result_4 = convert_price_per_unit(Decimal("2000"), WeightUnit.GRAM, precision=4)
        # Integer precision
        assert result_0 == Decimal("64")
        # Higher precision
        assert abs(result_4 - Decimal("64.3013")) < Decimal("0.001")


class TestGetConversionFactor:
    """Tests for get_conversion_factor function."""

    def test_same_unit_factor_is_one(self):
        assert get_conversion_factor(WeightUnit.TROY_OZ, WeightUnit.TROY_OZ) == Decimal("1.0")
        assert get_conversion_factor(WeightUnit.GRAM, WeightUnit.GRAM) == Decimal("1.0")

    def test_troy_oz_to_gram_factor(self):
        factor = get_conversion_factor(WeightUnit.TROY_OZ, WeightUnit.GRAM)
        # 1 troy oz * factor = grams
        assert abs(factor - Decimal("31.1035")) < Decimal("0.001")

    def test_gram_to_troy_oz_factor(self):
        factor = get_conversion_factor(WeightUnit.GRAM, WeightUnit.TROY_OZ)
        # 1 gram * factor = troy oz
        assert abs(factor - Decimal("0.0321507")) < Decimal("0.0001")


class TestGetUnitDisplayName:
    """Tests for get_unit_display_name function."""

    def test_english_names(self):
        assert get_unit_display_name(WeightUnit.TROY_OZ, "en") == "troy oz"
        assert get_unit_display_name(WeightUnit.GRAM, "en") == "gram"
        assert get_unit_display_name(WeightUnit.KILOGRAM, "en") == "kg"
        assert get_unit_display_name(WeightUnit.OUNCE, "en") == "oz"

    def test_chinese_names(self):
        assert get_unit_display_name(WeightUnit.TROY_OZ, "zh") == "金衡盎司"
        assert get_unit_display_name(WeightUnit.GRAM, "zh") == "克"
        assert get_unit_display_name(WeightUnit.KILOGRAM, "zh") == "千克"
        assert get_unit_display_name(WeightUnit.OUNCE, "zh") == "常衡盎司"

    def test_default_to_english(self):
        # Unknown locale should default to English
        assert get_unit_display_name(WeightUnit.TROY_OZ, "fr") == "troy oz"


class TestConversionFactorConstants:
    """Tests for conversion factor constants."""

    def test_unit_to_troy_oz_keys(self):
        expected_keys = {WeightUnit.TROY_OZ, WeightUnit.GRAM, WeightUnit.KILOGRAM, WeightUnit.OUNCE}
        assert set(UNIT_TO_TROY_OZ.keys()) == expected_keys

    def test_troy_oz_to_unit_keys(self):
        expected_keys = {WeightUnit.TROY_OZ, WeightUnit.GRAM, WeightUnit.KILOGRAM, WeightUnit.OUNCE}
        assert set(TROY_OZ_TO_UNIT.keys()) == expected_keys

    def test_troy_oz_is_base_unit(self):
        assert UNIT_TO_TROY_OZ[WeightUnit.TROY_OZ] == Decimal("1.0")
        assert TROY_OZ_TO_UNIT[WeightUnit.TROY_OZ] == Decimal("1.0")
