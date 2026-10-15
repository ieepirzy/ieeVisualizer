import pytest

from ieeVisualizer.themes import (
    FI_FI,
    NEUTRAL,
    format_currency,
    format_number,
    format_percent,
    format_quantity,
)
from ieeVisualizer.themes.locale import localize_decimal

S = " "  # no-break space: the fi-FI group separator


@pytest.mark.parametrize("value, decimals, expected", [
    (1234.5, None, f"1{S}234,5"),
    (1234, None, f"1{S}234"),
    (999, None, "999"),
    (1234567.891, None, f"1{S}234{S}567,89"),
    (-1234.5, None, f"−1{S}234,5"),
    (-0.001, 2, "0,00"),
    (0.5, 1, "0,5"),
    (12.0, 2, "12,00"),
])
def test_fi_numbers(value, decimals, expected):
    assert format_number(value, FI_FI, decimals) == expected


def test_currency_percent_and_units():
    assert format_currency(1234.5, FI_FI) == f"1{S}234,50{S}€"
    assert format_percent(42.105, FI_FI) == f"42,1{S}%"
    assert format_quantity(1234.5, "EUR", FI_FI) == f"1{S}234,50{S}€"
    assert format_quantity(12.5, "%", FI_FI) == f"12,5{S}%"
    assert format_quantity(1234.5, "sessions", FI_FI) == f"1{S}234,5"  # unit on the axis
    assert format_quantity(1234.5, "EUR", NEUTRAL) == f"1,234.50{S}EUR"
    assert format_number(1234.5) == "1,234.5"


def test_nulls_are_never_formatted_as_zero():
    for f in (format_number, format_percent, format_currency):
        assert f(None, FI_FI) is None
    assert format_number(float("nan"), FI_FI) is None
    assert format_quantity(None, "EUR", FI_FI) is None


def test_localize_leaves_non_decimals_alone():
    assert localize_decimal("1e6", FI_FI) == "1e6"
    assert localize_decimal("−2500.0", FI_FI) == f"−2{S}500,0"
    assert localize_decimal("", FI_FI) == ""
