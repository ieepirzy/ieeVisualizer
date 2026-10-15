"""The built-in themes: derived colours recomputed from the cited tokens, and
the pre-theme presets unchanged.

Token values are quoted from the files the themes cite
(movingfirm-admin/frontend/src/styles/tokens.css and
movingfirm-admin/tests/fixtures/interface/home.css); that repository is not
needed to run these tests.
"""

import pytest

from ieeVisualizer import themes
from ieeVisualizer.render.mpl.style import theme_rc
from ieeVisualizer.themes.color import (
    blend,
    contrast_ratio,
    darken_to_contrast,
    parse_color,
    to_hex,
    with_lightness,
)

ADMIN = {"bg": "#08090a", "text": "#d9d6cf", "text2": "#95928a", "text3": "#5f5d58",
         "green": "#6fd3a0", "orange": "#ffb547", "red": "#ff6e5e", "indigo": "#9db8ff",
         "cyan": "#d7a6ff", "border2": "#ece8e0"}  # border2 = rgba(236,232,224,0.1)
SITE = {"paper": "#ffffff", "ink": "#0a0a0a", "mint-deep": "oklch(0.50 0.15 162)"}


def test_admin_dark_is_derived_from_admin_tokens():
    t = themes.get("muutto365-admin-dark")
    assert (t.background, t.foreground, t.muted) == (ADMIN["bg"], ADMIN["text"], ADMIN["text2"])
    assert t.grid == to_hex(blend(ADMIN["border2"], ADMIN["bg"], 0.1))
    assert t.palette == (ADMIN["indigo"], ADMIN["green"], with_lightness(ADMIN["cyan"], 0.62))
    assert t.signal == ADMIN["orange"] and ADMIN["orange"] not in t.palette
    assert ADMIN["red"] not in t.palette
    assert t.semantic == {"positive": ADMIN["green"], "negative": ADMIN["red"],
                          "attention": ADMIN["orange"]}
    assert t.diverging[1] == ADMIN["text3"]
    assert "tokens.css" in t.provenance["source"]


def test_report_light_reuses_site_and_admin_hues_only_where_they_pass():
    t = themes.get("muutto365-report-light")
    white = SITE["paper"]
    assert (t.background, t.foreground) == (SITE["paper"], SITE["ink"])
    assert t.muted == to_hex(blend(SITE["ink"], white, 0.7))
    assert t.grid == to_hex(blend(SITE["ink"], white, 0.12))
    assert t.palette == (to_hex(parse_color(SITE["mint-deep"])),
                         with_lightness(ADMIN["indigo"], 0.40),
                         darken_to_contrast(ADMIN["cyan"], white, 4.5))
    assert t.signal == darken_to_contrast(ADMIN["orange"], white, 3.0)
    assert t.semantic["negative"] == darken_to_contrast(ADMIN["red"], white, 4.5)
    assert t.semantic["positive"] == darken_to_contrast(ADMIN["green"], white, 4.5)
    # none of the admin's own (dark-UI) hues passes 3:1 on white as they are
    assert all(contrast_ratio(ADMIN[k], white) < 3 for k in
               ("green", "orange", "red", "indigo", "cyan"))
    assert set(t.pending) == {"palette", "signal", "semantic", "typography.family"}
    assert "no brand guideline" in t.provenance["source"]


@pytest.mark.parametrize("name", ["muutto365-admin-dark", "muutto365-report-light"])
def test_muutto_themes_pass_every_check(name):
    t = themes.get(name)
    assert [i for i in themes.check_theme(t) if i.severity != "info"] == []
    rep = themes.report(t)
    assert rep["text_contrast"] >= 4.5 and rep["muted_contrast"] >= 4.5
    assert min(rep["palette_contrast"]) >= 3.0
    assert rep["min_pair_delta_e2000_deuteranopia"] >= 8
    assert t.locale.tag == "fi-FI" and set(t.targets) == {"email", "slide"}


@pytest.mark.parametrize("name", ["muutto365-admin-dark", "muutto365-report-light"])
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


def test_report_light_provenance_quotes_the_measured_dim_ink_contrast():
    import re

    note = themes.get("muutto365-report-light").provenance["muted"]
    quoted = float(re.search(r"ink at 55 %, is ([0-9.]+):1", note).group(1))
    dim = blend(SITE["ink"], SITE["paper"], 0.55)
    # 4.40:1 composited exactly, 4.42:1 once quantized to #787878: 4.4 either way
    assert quoted == round(contrast_ratio(dim, SITE["paper"]), 1) == 4.4
    assert quoted == round(contrast_ratio(to_hex(dim), SITE["paper"]), 1)
