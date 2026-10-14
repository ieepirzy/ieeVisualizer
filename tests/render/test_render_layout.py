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
