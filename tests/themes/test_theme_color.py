"""Colour maths against published reference values."""

import pytest

from ieeVisualizer.themes.color import (
    CVD_MATRICES,
    blend,
    contrast_ratio,
    darken_to_contrast,
    delta_e76,
    delta_e2000,
    parse_color,
    relative_luminance,
    simulate_cvd,
    to_hex,
    to_lab,
)


def test_wcag_contrast_reference_pairs():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)  # order-free
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    # #767676 is the well-known lightest grey that passes 4.5:1 on white
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)
    assert contrast_ratio("#777777", "#ffffff") < 4.5
    assert relative_luminance("#ffffff") == pytest.approx(1.0)
    assert relative_luminance("#000") == 0.0


@pytest.mark.parametrize("lab1, lab2, expected", [
    # Sharma, Wu & Dalal (2005), test data pairs 1, 7, 17 and 25
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
])
def test_ciede2000_matches_sharma_reference_data(lab1, lab2, expected):
    from ieeVisualizer.themes.color import _de2000

    assert _de2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)
    assert _de2000(lab2, lab1) == pytest.approx(expected, abs=1e-4)


def test_lab_and_delta_e76():
    assert to_lab("#ffffff") == pytest.approx((100.0, 0.0, 0.0), abs=0.01)
    assert to_lab("#000000") == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
    assert delta_e76("#000000", "#ffffff") == pytest.approx(100.0, abs=0.01)
    assert delta_e2000("#123456", "#123456") == 0.0


def test_cvd_simulation_keeps_greys_and_collapses_red_green():
    for kind, m in CVD_MATRICES.items():
        assert [sum(row) for row in m] == pytest.approx([1.0, 1.0, 1.0], abs=1e-4), kind
        for grey in ("#000000", "#808080", "#ffffff"):
            assert to_hex(simulate_cvd(grey, kind)) == grey
    red, green = "#d62728", "#2ca02c"  # matplotlib tab10 red / green
    normal = delta_e2000(red, green)
    deut = delta_e2000(simulate_cvd(red, "deuteranopia"), simulate_cvd(green, "deuteranopia"))
    assert normal > 60 and deut < normal / 5
    # blue/orange is the classic CVD-safe pair: it survives
    b, o = "#1f77b4", "#ff7f0e"
    assert delta_e2000(simulate_cvd(b, "deuteranopia"), simulate_cvd(o, "deuteranopia")) > 40


def test_parse_hex_and_oklch():
    assert parse_color("#fff") == (1.0, 1.0, 1.0)
    assert to_hex(parse_color("#6FD3A0")) == "#6fd3a0"
    assert to_hex(parse_color("oklch(1 0 0)")) == "#ffffff"
    assert to_hex(parse_color("oklch(0% 0 0)")) == "#000000"
    for bad in ("red", "#12345", "rgb(1,2,3)", None, 3):
        with pytest.raises(ValueError):
            parse_color(bad)


def test_blend_and_darken_to_contrast():
    assert to_hex(blend("#000000", "#ffffff", 0.5)) == "#808080"
    out = darken_to_contrast("#9db8ff", "#ffffff", 4.5)
    assert 4.5 <= contrast_ratio(out, "#ffffff") < 4.6  # the lightest that passes
    assert darken_to_contrast("#000000", "#ffffff", 4.5) == "#000000"  # already passes
