"""Layout fixes found by looking at the gallery PNGs: quiver arrows must not
run out of the plot, and a "no data" bar note must be visible on a log axis."""

import numpy as np
import pytest
from render_examples import one_layer

from ieeVisualizer.render import build_figure
from ieeVisualizer.spec import Axis, BarMark, QuiverMark


def _saddle(n=9):
    gx, gy = np.meshgrid(np.linspace(-2, 2, n), np.linspace(-2, 2, n), indexing="xy")
    return {"x": gx.ravel(), "y": gy.ravel(), "u": gx.ravel(), "v": -gy.ravel()}


def test_default_quiver_arrows_fit_in_the_view_and_longest_spans_one_cell():
    cols = _saddle()
    built = build_figure(one_layer(QuiverMark(encoding={c: c for c in "xyuv"}), cols))
    (q,) = built.layer_artists[0]
    assert q.scale_units == "xy" and q.angles == "xy"
    length = 1.0 / q.scale  # data units per unit of |(u, v)|
    cell = 4.0 / 8
    assert length * np.hypot(cols["u"], cols["v"]).max() == pytest.approx(cell)
    built.figure.canvas.draw()
    (x0, x1), (y0, y1) = built.axes.get_xlim(), built.axes.get_ylim()
    tips_x, tips_y = cols["x"] + length * cols["u"], cols["y"] + length * cols["v"]
    assert x0 <= tips_x.min() and tips_x.max() <= x1
    assert y0 <= tips_y.min() and tips_y.max() <= y1


def test_explicit_quiver_scale_is_kept():
    built = build_figure(one_layer(QuiverMark(encoding={c: c for c in "xyuv"}, scale=0.1),
                                   _saddle()))
    (q,) = built.layer_artists[0]
    assert q.scale == pytest.approx(10.0)


def _quiver(cols, **kw):
    built = build_figure(one_layer(QuiverMark(encoding={c: c for c in "xyuv"}), cols, **kw))
    built.figure.canvas.draw()
    (q,) = built.layer_artists[0]
    return built, q


def test_log_axis_quiver_uses_matplotlib_arrow_scaling():
    # The one-cell rule measures arrows in linear data units: on a log x axis
    # equal vectors would be drawn at very different lengths, and tips left of
    # x = 0 cannot be shown. Arrows fall back to matplotlib's screen-relative
    # scaling ("uv" angles, no scale_units), where equal vectors look equal.
    gx, gy = np.meshgrid([1.0, 10.0, 100.0, 1000.0], [1.0, 2.0, 3.0])
    cols = {"x": gx.ravel(), "y": gy.ravel(), "u": np.full(12, -100.0), "v": np.zeros(12)}
    built, q = _quiver(cols, axes={"x": Axis(label="x", scale="log")})
    assert q.scale_units is None and q.angles == "uv"
    x0, x1 = built.axes.get_xlim()
    assert 0 < x0 <= 1.0 and x1 >= 1000.0  # view follows the positions, not tips < 0


def test_scattered_quiver_positions_use_matplotlib_arrow_scaling():
    # 100 scattered points are not a grid: "extent / (distinct - 1)" would make
    # every arrow a speck, so the one-cell rule must not apply.
    rng = np.random.default_rng(0)
    x, y = rng.uniform(0, 10, 100), rng.uniform(0, 10, 100)
    built, q = _quiver({"x": x, "y": y, "u": np.ones(100), "v": np.zeros(100)})
    assert q.scale_units is None and q.angles == "uv"
    x0, x1 = built.axes.get_xlim()
    assert x0 <= x.min() and x.max() <= x1


def test_incomplete_grid_falls_back_and_uneven_complete_grid_keeps_one_cell_rule():
    cols = _saddle(3)
    cut = {k: v[:-1] for k, v in cols.items()}  # one (x, y) cell missing
    _, q = _quiver(cut)
    assert q.scale_units is None
    gx, gy = np.meshgrid([0.0, 1.0, 4.0], [0.0, 2.0])  # rectilinear, uneven spacing
    _, q = _quiver({"x": gx.ravel(), "y": gy.ravel(), "u": np.ones(6), "v": np.zeros(6)})
    assert q.scale_units == "xy" and 1.0 / q.scale == pytest.approx(2.0)  # min(4/2, 2/1)


@pytest.mark.parametrize("horizontal", [False, True])
def test_no_data_note_is_inside_the_plot_on_a_log_axis(horizontal):
    cat, val = ("y", "x") if horizontal else ("x", "y")
    spec = one_layer(
        BarMark(encoding={cat: "m", val: "k"},
                orientation="horizontal" if horizontal else "vertical"),
        {"m": ["a", "b", "c"], "k": [50.0, None, 0.2]},
        axes={val: Axis(label="k", scale="log")})
    built = build_figure(spec)
    (note,) = [a for a in built.layer_artists[0][1:] if hasattr(a, "get_text")]
    assert note.get_text() == "no data"
    built.figure.canvas.draw()
    bbox = note.get_window_extent()
    ax_box = built.axes.get_window_extent()
    assert np.all(np.isfinite(bbox.get_points()))
    assert ax_box.x0 <= bbox.x0 and bbox.x1 <= ax_box.x1 + 1
    assert ax_box.y0 <= bbox.y0 and bbox.y1 <= ax_box.y1 + 1


# ------------------------------------------------- an axis title is a claim


def _ylabel(spec):
    return build_figure(spec).axes.get_ylabel()


def test_an_axis_holding_several_layers_is_never_titled_by_one_of_them():
    """'revenue' over revenue + cost; with 11 layers 'a*sin(x) + 0'."""
    from ieeVisualizer.spec import DataSnapshot, FigureSpec, FunctionMark, Legend, LineMark

    def fn(expr, **kw):
        return FunctionMark(expression=expr, variables=["x"], domain={"x": [0.0, 6.0]},
                            samples=20, **kw)

    two = FigureSpec(layers=[fn("sin(x)", name="revenue"), fn("cos(x)", name="cost")],
                     legend=Legend(show=False))
    assert _ylabel(two) == ""
    many = FigureSpec(layers=[fn(f"sin(x) + {k}") for k in range(11)])
    assert _ylabel(many) == ""
    ds = DataSnapshot.from_columns({"week": [1, 2], "bookings": [3.0, 4.0], "quotes": [5.0, 6.0]},
                                   units={"bookings": "count", "quotes": "count"})
    lines = FigureSpec(datasets={"d": ds}, layers=[
        LineMark(data="d", encoding={"x": "week", "y": "bookings"}, name="bookings"),
        LineMark(data="d", encoding={"x": "week", "y": "quotes"}, name="quotes")])
    assert _ylabel(lines) == "(count)" and build_figure(lines).axes.get_xlabel() == "week"
    # an explicit label still applies; one layer is still titled by its name
    two.axes = {"y": Axis(label="EUR")}
    assert _ylabel(two) == "EUR"
    assert _ylabel(FigureSpec(layers=[fn("sin(x)", name="revenue")])) == "revenue"
    same = FigureSpec(datasets={"d": ds}, layers=[
        LineMark(data="d", encoding={"x": "week", "y": "bookings"}),
        LineMark(data="d", encoding={"x": "week", "y": "bookings"}, role="forecast")])
    assert _ylabel(same) == "bookings (count)"  # several layers, one series
