"""Drawing intervals: what is on screen is exactly the bounds in the data,
gaps where a bound is missing, and a statement of what the interval is."""

import dataclasses

import numpy as np
import pytest
from matplotlib.colors import to_rgb

from ieeVisualizer.render import Capabilities, RenderRequest, render, requirements_for
from ieeVisualizer.render.mpl.adapter import build_figure
from ieeVisualizer.spec import from_dict

T = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
Y = [1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 1.0]
LO = [0.5, 1.2, 2.1, 3.3, 2.0, 1.5, 0.2]
HI = [1.5, 2.9, 3.6, 4.4, 3.8, 2.6, 1.9]


def spec(layers, lo=LO, hi=HI, **figure):
    cols = {"t": {"unit": "d", "values": T}, "y": {"unit": "kg", "values": Y},
            "lo": {"unit": "kg", "values": list(lo)}, "hi": {"unit": "kg", "values": list(hi)}}
    return from_dict(dict({"schema_version": "1.0",
                           "datasets": {"d": {"type": "snapshot", "columns": cols}},
                           "layers": [dict({"data": "d"}, **la) for la in layers]}, **figure))


BAND = {"type": "band", "encoding": {"x": "t", "y_lower": "lo", "y_upper": "hi"},
        "interval_kind": "prediction", "interval_level": 0.95}


def on(mark, **kw):
    return dict({"type": mark, "encoding": {"x": "t", "y": "y", "y_lower": "lo", "y_upper": "hi"},
                 "interval_kind": "confidence", "interval_level": 0.9}, **kw)


def legend_texts(built):
    legend = built.axes.get_legend()
    return [t.get_text() for t in legend.get_texts()] if legend is not None else []


def polygons(artists):
    """Vertex arrays of every filled polygon drawn (fill_between paths)."""
    out = []
    for a in artists:
        if a.__class__.__name__ in ("PolyCollection", "FillBetweenPolyCollection"):
            out += [p.vertices for p in a.get_paths() if len(p.vertices)]
    return out


def segments(artists):
    """(x, y0, y1) of every vertical segment drawn (vlines)."""
    out = []
    for a in artists:
        if a.__class__.__name__ == "LineCollection":
            for s in a.get_segments():
                (x0, y0), (x1, y1) = s
                assert x0 == x1
                out.append((x0, y0, y1))
    return sorted(out)


@pytest.mark.parametrize("layers, text", [
    ([BAND], "95\u00a0% prediction interval"),
    ([on("line")], "90\u00a0% confidence interval"),
    ([on("scatter")], "90\u00a0% confidence interval"),
    ([on("bar")], "90\u00a0% confidence interval"),
    ([on("errorbar")], "90\u00a0% confidence interval"),
    ([dict(BAND, interval_kind="min-max", interval_level=None)], "min-max range"),
    ([dict(on("errorbar"), interval_kind="standard-error", interval_level=None,
           interval_multiplier=1)], "±1 standard error"),
])
def test_legend_states_kind_and_level(layers, text):
    built = build_figure(spec(layers))
    assert any(text in label for label in legend_texts(built)), legend_texts(built)


def test_a_lone_band_still_gets_its_legend_and_a_forecast_says_so():
    built = build_figure(spec([dict(BAND, role="forecast")]))
    assert legend_texts(built) == ["95\u00a0% prediction interval (forecast)"]


def test_hidden_legend_moves_the_statement_to_the_caption():
    built = build_figure(spec([BAND], caption="Model v3.", legend={"show": False}))
    assert built.axes.get_legend() is None
    assert built.caption.get_text() == "Model v3.\nIntervals: 95\u00a0% prediction interval."


def test_decimal_separator_follows_the_theme_locale():
    built = build_figure(spec([dict(BAND, interval_level=0.975)], theme="report-light"))
    assert "97,5\u00a0% prediction interval" in legend_texts(built)


def test_band_polygon_is_exactly_the_data():
    built = build_figure(spec([BAND]))
    (poly,) = polygons(built.layer_artists[0])
    want = {(t, v) for t, v in zip(T, LO)} | {(t, v) for t, v in zip(T, HI)}
    assert {(float(x), float(y)) for x, y in poly} == want  # no point added, moved or smoothed


def test_null_bounds_are_gaps_never_zero():
    lo = list(LO)
    lo[3] = None  # one missing lower bound splits the band
    hi = list(HI)
    hi[6] = None  # and the last row has no interval at all
    built = build_figure(spec([BAND], lo=lo, hi=hi))
    polys = polygons(built.layer_artists[0])
    assert len(polys) == 2
    xs = {float(x) for p in polys for x, _ in p}
    assert xs == {0.0, 1.0, 2.0, 4.0, 5.0}  # rows 3 and 6 are not in any polygon
    ys = {float(y) for p in polys for _, y in p}
    assert 0.0 not in ys
    assert {(float(x), float(y)) for p in polys for x, y in p} == (
        {(T[i], LO[i]) for i in (0, 1, 2, 4, 5)} | {(T[i], HI[i]) for i in (0, 1, 2, 4, 5)})
    codes = [w.code for w in built.warnings]
    assert codes.count("interval_bounds_missing") == 1
    (w,) = [w for w in built.warnings if w.code == "interval_bounds_missing"]
    assert w.message.startswith("2 row(s)")


def test_a_lone_row_between_gaps_stays_visible():
    lo = [None, None, 2.1, None, None, None, None]
    built = build_figure(spec([BAND], lo=lo))
    assert polygons(built.layer_artists[0]) == [] or all(
        len({float(x) for x, _ in p}) == 1 for p in polygons(built.layer_artists[0]))
    assert segments(built.layer_artists[0]) == [(2.0, 2.1, 3.6)]


def test_line_interval_is_a_band_in_the_line_colour():
    built = build_figure(spec([on("line", name="mean")]))
    line = built.layer_artists[0][0]
    (poly,) = polygons(built.layer_artists[0])
    band = [a for a in built.layer_artists[0] if polygons([a])][0]
    assert np.allclose(band.get_facecolor()[0][:3], to_rgb(line.get_color()))
    assert {(float(x), float(y)) for x, y in poly} == (
        set(zip(T, LO)) | set(zip(T, HI)))
    assert "mean: 90\u00a0% confidence interval" in legend_texts(built)


@pytest.mark.parametrize("mark", ["errorbar", "scatter", "bar"])
def test_error_bars_run_exactly_from_lower_to_upper(mark):
    lo = list(LO)
    lo[2] = None
    built = build_figure(spec([on(mark)], lo=lo))
    segs = segments(built.layer_artists[0])
    want = [(T[i], LO[i], HI[i]) for i in range(len(T)) if i != 2]
    assert segs == want
    assert "interval_bounds_missing" in [w.code for w in built.warnings]


def test_errorbar_point_is_drawn_without_its_interval_where_a_bound_is_missing():
    hi = list(HI)
    hi[4] = None
    built = build_figure(spec([on("errorbar")], hi=hi))
    points = [a for a in built.layer_artists[0]
              if a.__class__.__name__ == "Line2D" and a.get_marker() == "o"]
    (pts,) = points
    assert list(pts.get_xdata()) == T and list(pts.get_ydata()) == Y
    assert 4.0 not in [x for x, _, _ in segments(built.layer_artists[0])]


def test_forecast_band_is_hatched_and_observed_is_not():
    built = build_figure(spec([BAND, dict(BAND, role="forecast")]))
    observed = [a for a in built.layer_artists[0] if polygons([a])][0]
    forecast = [a for a in built.layer_artists[1] if polygons([a])][0]
    assert not observed.get_hatch() and forecast.get_hatch()
    assert forecast.get_alpha() < observed.get_alpha()


def test_band_opacity_comes_from_the_theme(monkeypatch):
    from ieeVisualizer import themes
    from ieeVisualizer.render.mpl import style

    base = themes.get("default")
    t = dataclasses.replace(base, roles=dataclasses.replace(base.roles, interval_fill_alpha=0.1))
    monkeypatch.setattr(style, "resolve_for_render", lambda name: (t, None))
    built = build_figure(spec([BAND]))
    (band,) = [a for a in built.layer_artists[0] if polygons([a])]
    assert band.get_alpha() == pytest.approx(0.1)
    monkeypatch.undo()
    default = build_figure(spec([BAND]))
    (band,) = [a for a in default.layer_artists[0] if polygons([a])]
    assert band.get_alpha() == pytest.approx(0.25)


def test_an_adapter_without_interval_support_is_not_chosen():
    s = spec([on("line")])
    req = requirements_for(s, RenderRequest(format="svg"))
    caps = Capabilities(coordinate_systems=frozenset({"cartesian2d"}),
                        mark_types=frozenset({"line"}), output_formats=frozenset({"svg"}),
                        vector_export=True)
    assert any("interval" in m for m in req.unmet(caps))
    assert not req.unmet(dataclasses.replace(caps, intervals=True))


def test_interval_figures_render_deterministically():
    s = spec([BAND, on("line"), on("errorbar", role="forecast")])
    a = render(s, RenderRequest(format="svg"))
    b = render(s, RenderRequest(format="svg"))
    assert a.content == b.content


def _gallery():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "examples" / "gallery.py"
    mod_spec = importlib.util.spec_from_file_location("_gallery_intervals", path)
    gallery = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(gallery)
    return gallery.all_specs()


GALLERY = _gallery()
#: Every gallery figure that draws an interval (the themed variants included).
INTERVAL_KEYS = ["mark-band", "mark-errorbar", "interval-line", "interval-scatter",
                 "interval-bar", "template-timeseries", "template-timeseries--report-light",
                 "template-timeseries--dashboard-dark", "template-estimate_comparison",
                 "template-estimate_comparison--report-light",
                 "template-estimate_comparison--dashboard-dark"]


@pytest.mark.parametrize("key", INTERVAL_KEYS)
def test_interval_figures_pass_the_phone_email_gate(key):
    from ieeVisualizer.render import check_legible, get_profile

    spec = GALLERY[key]
    assert any(m.type in ("band", "errorbar") or "y_lower" in m.encoding for m in spec.layers)
    result = render(spec, get_profile("email-phone").figure_request("png"))
    check_legible(result)  # raises LegibilityError on any legibility_* warning
    assert not [w.code for w in result.warnings if w.code.startswith("legibility")]
