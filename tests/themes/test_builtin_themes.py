"""The built-in themes: the properties the neutral themes promise (contrast,
distinctness, provenance that quotes what it measured), and the pre-theme
presets unchanged. The tests assert properties, not hex values, so a palette
can be redesigned without rewriting them.
"""

import re

import pytest

from ieeVisualizer import themes
from ieeVisualizer.render.mpl.style import theme_rc
from ieeVisualizer.themes.color import blend, contrast_ratio, delta_e2000, to_hex

NEUTRAL = ["dashboard-dark", "report-light"]


@pytest.mark.parametrize("name", NEUTRAL)
def test_neutral_themes_reserve_the_signal_and_keep_the_grid_quiet(name):
    t = themes.get(name)
    assert len(t.palette) >= 4
    assert t.signal not in t.palette
    # the reserved emphasis colour must not read as one more series
    assert min(delta_e2000(t.signal, c) for c in t.palette) >= 15
    assert t.semantic["attention"] == t.signal
    assert t.semantic["positive"] != t.semantic["negative"]
    # visible, but never competing with data or text
    assert 1.1 <= contrast_ratio(t.grid, t.background) < 1.6
    assert t.roles.reference_color == t.muted
    assert t.pending == ()


@pytest.mark.parametrize("name", NEUTRAL)
def test_provenance_covers_every_colour_slot_and_quotes_measured_contrast(name):
    t = themes.get(name)
    for slot in ("background", "foreground", "muted", "grid", "palette", "signal", "semantic",
                 "sequential", "diverging"):
        assert t.provenance.get(slot), slot
    assert len(t.provenance["palette"]) == len(t.palette)
    for slot in ("foreground", "muted", "grid", "signal"):
        quoted = re.findall(r"([0-9]+(?:\.[0-9]+)?):1", t.provenance[slot])
        for q in quoted:
            assert float(q) == round(contrast_ratio(getattr(t, slot), t.background), 1), slot
    for i, note in enumerate(t.provenance["palette"]):
        assert t.palette[i] in note


@pytest.mark.parametrize("name", NEUTRAL)
def test_neutral_themes_pass_every_check(name):
    t = themes.get(name)
    assert [i for i in themes.check_theme(t) if i.severity != "info"] == []
    rep = themes.report(t)
    assert rep["text_contrast"] >= 4.5 and rep["muted_contrast"] >= 4.5
    assert min(rep["palette_contrast"]) >= 3.0
    assert rep["min_pair_delta_e2000_deuteranopia"] >= 8
    assert t.locale.tag == "fi-FI" and set(t.targets) == {"email", "slide"}


@pytest.mark.parametrize("name", NEUTRAL)
def test_forecast_lines_keep_graphics_contrast(name):
    # measured on the line actually drawn, not on the theme's declaration
    from matplotlib import colors as mcolors

    from ieeVisualizer.render import build_figure
    from ieeVisualizer.spec import DataSnapshot, FigureSpec, LineMark

    t = themes.get(name)
    data = DataSnapshot.from_columns({"x": [0.0, 1.0], **{f"y{k}": [1.0, 2.0] for k in
                                                          range(len(t.palette))}})
    spec = FigureSpec(datasets={"d": data}, theme=name, layers=[
        LineMark(data="d", encoding={"x": "x", "y": f"y{k}"}, name=f"y{k}", role="forecast")
        for k in range(len(t.palette))])
    built = build_figure(spec)
    for k in range(len(t.palette)):
        line = built.layer_artists[k][0]
        seen = to_hex(blend(mcolors.to_hex(line.get_color()), t.background, line.get_alpha()))
        assert contrast_ratio(seen, t.background) >= 3.0, (line.get_color(), line.get_alpha())


def test_presets_map_to_the_former_rcparams_exactly():
    # the matplotlib adapter's THEMES dict before themes were data
    assert theme_rc(themes.get("default")) == {}
    assert theme_rc(themes.get("minimal")) == {
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.alpha": 0.3, "grid.linewidth": 0.6}
    assert theme_rc(themes.get("presentation")) == {
        "font.size": 13, "axes.titlesize": 16, "axes.labelsize": 14, "lines.linewidth": 2.2,
        "axes.spines.top": False, "axes.spines.right": False}
