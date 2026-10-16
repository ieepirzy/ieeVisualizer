"""Quiver: a 3D quiver's color channel colours each arrow (and adds a
colourbar); a 2D quiver keeps a null component as a masked arrow; and
draw_into draws a spec into a caller's axes."""

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pytest
from render_examples import one_layer

from ieeVisualizer.render import build_figure
from ieeVisualizer.render.mpl import MatplotlibAdapter
from ieeVisualizer.spec import QuiverMark


def _field3d(color):
    g = np.linspace(0.0, 1.0, 2)
    gx, gy, gz = (a.ravel() for a in np.meshgrid(g, g, g, indexing="xy"))
    cols = {"x": gx, "y": gy, "z": gz, "u": gx, "v": 0 * gy, "w": 0 * gz + 0.1,
            "c": np.hypot(gx, 0.1)}
    enc = {k: k for k in "xyzuvw"}
    if color:
        enc["color"] = "c"
    mark = QuiverMark(encoding=enc, style={"colormap": "plasma"})
    return one_layer(mark, cols, coordinate_system="cartesian3d"), cols


def test_3d_quiver_color_channel_colours_every_stroke_of_each_arrow():
    spec, cols = _field3d(color=True)
    built = build_figure(spec)
    assert not [w for w in built.warnings if w.code == "color_channel_ignored"]
    (q,) = built.layer_artists[0]
    c = cols["c"]
    norm = matplotlib.colors.Normalize(c.min(), c.max())
    expected = matplotlib.colormaps["plasma"](norm(c))
    expected[:, 3] = q.get_alpha() if q.get_alpha() is not None else 1.0
    got = q.get_colors()
    n = len(c)
    for k in range(3):  # shaft, left head stroke, right head stroke
        np.testing.assert_allclose(got[k * n:(k + 1) * n], expected)
    assert len(built.figure.axes) == 2  # main axes + colourbar
    assert built.figure.axes[1].get_ylabel() == "c"
    # without a color channel: one colour, no colourbar (unchanged)
    plain = build_figure(_field3d(color=False)[0])
    assert len(np.unique(plain.layer_artists[0][0].get_colors(), axis=0)) == 1
    assert len(plain.figure.axes) == 1


def test_2d_null_component_is_a_masked_arrow_and_keeps_the_grid_scale():
    gx, gy = (a.ravel() for a in np.meshgrid([0.0, 1.0, 2.0], [0.0, 1.0], indexing="xy"))
    u = [1.0, None, 1.0, 1.0, 1.0, 1.0]
    built = build_figure(one_layer(QuiverMark(encoding={c: c for c in "xyuv"}),
                                   {"x": gx, "y": gy, "u": u, "v": [0.0] * 6}))
    (q,) = built.layer_artists[0]
    assert len(q.X) == 6
    np.testing.assert_array_equal(q.Umask, [False, True, False, False, False, False])
    assert q.scale_units == "xy" and 1.0 / q.scale == pytest.approx(1.0)  # one cell
    assert any(w.code == "null_values_omitted" for w in built.warnings)


def test_draw_into_uses_the_callers_axes_and_checks_the_projection():
    spec, _ = _field3d(color=True)
    fig2d, ax2d = plt.subplots()
    with pytest.raises(ValueError, match="3d"):
        MatplotlibAdapter().draw_into(spec, ax2d)
    fig = plt.figure()
    ax = fig.add_subplot(projection="3d")
    built = MatplotlibAdapter().draw_into(spec, ax)
    assert built.axes is ax and built.figure is fig
    assert built.layer_artists[0][0] in ax.collections


def test_draw_into_caller_styling_is_limited_to_legacy_mark_types():
    from ieeVisualizer.spec import BarMark

    spec = one_layer(BarMark(encoding={"x": "k", "y": "v"}, role="reference"),
                     {"k": ["a", "b"], "v": [1.0, 2.0]})
    _, ax = plt.subplots()
    with pytest.raises(ValueError, match="role_styling=False supports"):
        MatplotlibAdapter().draw_into(spec, ax, role_styling=False)
    MatplotlibAdapter().draw_into(spec, ax)  # role styling on: any mark
