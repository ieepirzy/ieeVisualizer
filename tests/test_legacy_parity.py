"""The adapter-drawn legacy calls draw what the direct Matplotlib calls drew
(the reference below is that direct drawing), except where a change is a
documented improvement (3D magnitude colourbar, isolated-point markers)."""

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pytest
from matplotlib.quiver import Quiver

from ieeVisualizer import Visualizer


def rotation(x, y):
    return -y, x * 0.5


def field3(x, y, z):
    return y, -x, 0.5 * z + 1


def test_overlaid_curves_take_the_axes_own_property_cycle():
    fig, ax = plt.subplots()
    ax.set_prop_cycle(color=["red", "green"], linestyle=["-", "--"])
    Visualizer(N=10).visualize(np.sin, dim=2, ax=ax)
    Visualizer(N=10).visualize(np.cos, dim=2, ax=ax)
    colours = [matplotlib.colors.to_hex(ln.get_color()) for ln in ax.get_lines()]
    assert colours == ["#ff0000", "#008000"]
    assert [ln.get_linestyle() for ln in ax.get_lines()] == ["-", "--"]
    fig, ax = plt.subplots()  # default cycle: distinct colours, as ax.plot gives
    for f in (np.sin, np.cos):
        Visualizer(N=10).visualize(f, dim=2, ax=ax)
    assert len({ln.get_color() for ln in ax.get_lines()}) == 2


def test_legacy_drawing_is_opaque():
    (line,) = Visualizer(N=10).visualize(np.sin, dim=2).axes[0].get_lines()
    assert line.get_alpha() in (None, 1.0)
    assert line.get_linewidth() == plt.rcParams["lines.linewidth"]
    assert line.get_label().startswith("_")  # unlabelled, so ax.legend() skips it, as before
    (surf,) = Visualizer(N=10).visualize(lambda x, y: x * y, dim=3).axes[0].collections
    assert surf.get_alpha() in (None, 1.0)
    q = [c for c in Visualizer().visualize_vf(rotation, dim=2, n=4).axes[0].collections
         if isinstance(c, Quiver)][0]
    assert q.get_alpha() in (None, 1.0)
    (q3,) = Visualizer().visualize_vf(field3, dim=3, n=3).axes[0].collections
    assert q3.get_alpha() in (None, 1.0) and np.all(q3.get_colors()[:, 3] == 1.0)


def test_oversized_input_is_drawn_not_refused():
    # more than the adapter's 1,000,000-point export limit; drawn on main too
    fig = Visualizer(N=1001).visualize(lambda x, y: x * y, dim=3)
    assert len(fig.axes[0].collections) == 1


def test_2d_field_keeps_matplotlib_arrow_scale_and_limits():
    s_fig = Visualizer().visualize_vf(rotation, dim=2, n=9, domain=[(-1, 1), (0, 2)])
    ax = s_fig.axes[0]
    (q,) = [c for c in ax.collections if isinstance(c, Quiver)]
    assert q.scale is None and q.scale_units is None  # matplotlib's own autoscaling
    ref_fig, ref = plt.subplots()  # the direct drawing visualize_vf always did
    gx, gy = np.meshgrid(np.linspace(-1, 1, 9), np.linspace(0, 2, 9))
    u, v = rotation(gx, gy)
    rq = ref.quiver(gx, gy, u, v, np.hypot(u, v))
    ref_fig.colorbar(rq, ax=ref)
    ref.set_aspect("equal", adjustable="box")
    for f in (s_fig, ref_fig):
        f.canvas.draw()
    np.testing.assert_allclose(ax.get_xlim(), ref.get_xlim())
    np.testing.assert_allclose(ax.get_ylim(), ref.get_ylim())
    assert q.scale == pytest.approx(rq.scale)


def test_3d_field_limits_are_not_widened():
    fig = Visualizer().visualize_vf(field3, dim=3, n=3, domain=(0, 2))
    ref = plt.figure().add_subplot(projection="3d")
    g = np.linspace(0, 2, 3)
    pts = [a.ravel() for a in np.meshgrid(g, g, g)]
    vecs = field3(*pts)
    length = 1.0 / np.linalg.norm(np.column_stack(vecs), axis=1).max()  # one cell (2 / 2)
    ref.quiver(*pts, *vecs, length=length, normalize=False)
    ax = fig.axes[0]
    for get in ("get_xlim", "get_ylim", "get_zlim"):
        np.testing.assert_allclose(getattr(ax, get)(), getattr(ref, get)())


def test_3d_colourbar_does_not_cover_the_z_label():
    fig = Visualizer().visualize_vf(field3, dim=3, n=4, domain=(0, 3))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    z_label = fig.axes[0].zaxis.label.get_window_extent(renderer)
    colourbar = fig.axes[1].get_tightbbox(renderer)
    assert z_label.width > 0 and not z_label.overlaps(colourbar)


def test_colormap_is_only_resolved_where_it_is_used():
    Visualizer(colormap="no-such-map").visualize(np.sin, dim=2)  # a curve uses none
    with pytest.raises(ValueError, match="unknown colormap"):
        Visualizer(colormap="no-such-map").visualize(lambda x, y: x * y, dim=3)
    with pytest.raises(ValueError, match="unknown colormap"):
        Visualizer(colormap="no-such-map").visualize_vf(rotation, dim=2)
