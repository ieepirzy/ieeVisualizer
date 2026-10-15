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


def test_display_rounding_is_half_up_on_the_written_value():
    # binary half-even / float-repr rounding would give 18,2 %, 0,12 € and 2,67
    assert format_percent(18.25, FI_FI) == f"18,3{S}%"
    assert format_currency(0.125, FI_FI) == f"0,13{S}€"
    assert format_number(2.675, FI_FI, 2) == "2,68"
    assert format_number(0.125, FI_FI) == "0,13"  # max_decimals path
    assert format_number(-18.25, FI_FI, 1) == "−18,3"  # away from zero
    assert format_number(-0.004, FI_FI, 2) == "0,00"  # no "−0,00"
    assert format_number(1e16, FI_FI, 0) == S.join(["10", "000", "000", "000", "000", "000"])


def test_a_non_zero_percent_never_displays_as_zero():
    assert format_percent(0.016, FI_FI) == f"<{S}0,1{S}%"
    assert format_percent(-0.04, FI_FI) == f">{S}−0,1{S}%"
    assert format_percent(0.4, FI_FI, decimals=0) == f"<{S}1{S}%"
    assert format_percent(0.016) == f"<{S}0.1%"  # neutral locale
    assert format_percent(1e-300, FI_FI) == f"<{S}0,1{S}%"
    # a real zero is still zero, and values that round up are shown normally
    assert format_percent(0, FI_FI) == f"0,0{S}%"
    assert format_percent(-0.0, FI_FI) == f"0,0{S}%"
    assert format_percent(0.05, FI_FI) == f"0,1{S}%"
    assert format_quantity(0.016, "%", FI_FI) == f"<{S}0,1{S}%"
