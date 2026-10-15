"""Placement of an ``"auto"`` legend (see ``render/mpl/legend_layout.py``).

``loc="best"`` always picks a spot inside the axes, even when every spot
hides data. An ``"auto"`` legend with more than five entries, or one that
``"best"`` could only place over data, goes outside the axes instead; the
layout makes room so nothing is clipped. Explicit positions are honoured.
"""

import numpy as np
import pytest

from ieeVisualizer.render import RenderRequest, build_figure, render
from ieeVisualizer.spec import (
    DataSnapshot,
    FigureSpec,
    Legend,
    LineMark,
    ReferenceLine,
    ScatterMark,
)

WEEKS = [f"2026-09-{d:02d}" for d in (1, 8, 15, 22, 29)] + ["2026-10-06", "2026-10-13"]


def _regions_with_bounds_and_forecast(position="auto", caption=None):
    """Two regions, each with lower/upper bounds, an observed and a forecast
    part, and a target line: nine legend entries, like the timeseries
    template's example with a series column and bounds."""
    n = len(WEEKS)
    t = WEEKS * 2
    region = ["North"] * n + ["South"] * n
    base = [1180.0, 1200.0, 1290.0, 1350.0, 1390.0, 1410.0, 1450.0]
    rev = base + [v * 0.7 for v in base]
    cols = {"t": t, "region": region, "rev": rev,
            "lo": [v * 0.93 for v in rev], "hi": [v * 1.07 for v in rev]}
    obs = DataSnapshot.from_columns({k: [v for v, w in zip(c, t) if w <= "2026-09-29"]
                                     for k, c in cols.items()}, dtypes={"t": "datetime"})
    fc = DataSnapshot.from_columns({k: [v for v, w in zip(c, t) if w >= "2026-09-29"]
                                    for k, c in cols.items()}, dtypes={"t": "datetime"})
    layers = []
    for data, role, suffix in (("obs", "observed", ""), ("fc", "forecast", " (forecast)")):
        layers.append(LineMark(data=data, role=role, name="revenue" + suffix,
                               encoding={"x": "t", "y": "rev", "series": "region"}))
        for bound in ("lo", "hi"):
            layers.append(LineMark(data=data, role=role, name="revenue range (lo to hi)" + suffix,
                                   encoding={"x": "t", "y": bound, "series": "region"},
                                   style={"linestyle": "dotted", "linewidth": 1}))
    return FigureSpec(title="Weekly revenue", datasets={"obs": obs, "fc": fc}, layers=layers,
                      annotations=[ReferenceLine(axis="y", value=1300.0, label="Target")],
                      legend=Legend(position=position), caption=caption)


def _drawn(spec, request=None):
    built = build_figure(spec, request)
    built.figure.draw_without_rendering()
    return built, built.axes.get_legend()


def _data_extents(built):
    ax = built.axes
    return [a.get_window_extent() for a in (*ax.lines, *ax.collections, *ax.patches)
            if a.get_visible()]


def _entries(legend):
    return [t.get_text() for t in legend.get_texts()]


def _inside(inner, outer):
    return (inner.x0 >= outer.x0 and inner.y0 >= outer.y0
            and inner.x1 <= outer.x1 and inner.y1 <= outer.y1)


def test_many_entries_go_outside_the_axes_clear_of_data_and_inside_the_figure():
    built, legend = _drawn(_regions_with_bounds_and_forecast())
    assert len(_entries(legend)) > 5
    box = legend.get_window_extent()
    hits = [e for e in _data_extents(built) if e.overlaps(box)]
    assert hits == []
    assert not box.overlaps(built.axes.get_window_extent())
    assert _inside(box, built.figure.bbox)
    # the axis labels are not covered either
    for label in (built.axes.xaxis.label, built.axes.yaxis.label):
        assert not label.get_window_extent().overlaps(box)


def test_wide_figure_puts_the_outside_legend_below_in_columns():
    built, legend = _drawn(_regions_with_bounds_and_forecast(caption="Forecast is a model."),
                           RenderRequest(width=11, height=4.4))
    box, frame = legend.get_window_extent(), built.axes.get_window_extent()
    assert box.y1 < built.axes.xaxis.label.get_window_extent().y0 < frame.y0
    assert len({round(t.get_window_extent().x0) for t in legend.get_texts()}) > 1  # columns
    assert _inside(box, built.figure.bbox)
    assert not built.caption.get_window_extent().overlaps(box)


def _series(n, points=40):
    x = np.linspace(0.0, 10.0, points)
    cols = {"x": list(np.tile(x, n)),
            "y": [float(v) for k in range(n) for v in np.sin(x + k) + 0.3 * k],
            "g": [chr(ord("a") + k) for k in range(n) for _ in x]}
    return DataSnapshot.from_columns(cols)


def test_short_labels_go_to_the_right():
    spec = FigureSpec(datasets={"d": _series(6)},
                      layers=[LineMark(data="d", encoding={"x": "x", "y": "y", "series": "g"})])
    built, legend = _drawn(spec)
    box, frame = legend.get_window_extent(), built.axes.get_window_extent()
    assert box.x0 > frame.x1
    assert _inside(box, built.figure.bbox)


def test_few_entries_over_data_everywhere_go_outside():
    rng = np.random.default_rng(0)
    pts = rng.uniform(0.0, 1.0, size=(2, 2000))
    d = DataSnapshot.from_columns({"x": list(pts[0]), "y": list(pts[1]),
                                   "g": ["p", "q"] * 1000})
    spec = FigureSpec(datasets={"d": d},
                      layers=[ScatterMark(data="d", encoding={"x": "x", "y": "y", "series": "g"})])
    built, legend = _drawn(spec)
    assert len(_entries(legend)) == 2
    assert not legend.get_window_extent().overlaps(built.axes.get_window_extent())


def test_few_entries_with_room_stay_at_best_inside_the_axes():
    spec = FigureSpec(datasets={"d": _series(2)},
                      layers=[LineMark(data="d", encoding={"x": "x", "y": "y", "series": "g"})])
    built, legend = _drawn(spec)
    assert _inside(legend.get_window_extent(), built.axes.get_window_extent())


@pytest.mark.parametrize("position", ["upper-left", "lower-right"])
def test_explicit_position_is_honoured_even_with_many_entries(position):
    built, legend = _drawn(_regions_with_bounds_and_forecast(position))
    box, frame = legend.get_window_extent(), built.axes.get_window_extent()
    assert _inside(box, frame)
    pad = 0.1 * frame.width  # the corner asked for, not another one
    if position == "upper-left":
        assert box.x0 - frame.x0 < pad and frame.y1 - box.y1 < pad
    else:
        assert frame.x1 - box.x1 < pad and box.y0 - frame.y0 < pad


@pytest.mark.parametrize("fmt", ["svg", "png"])
def test_outside_placement_is_deterministic(fmt):
    spec = _regions_with_bounds_and_forecast()
    req = RenderRequest(format=fmt)
    assert render(spec, req).content == render(spec, req).content
