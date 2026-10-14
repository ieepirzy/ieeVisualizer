import matplotlib.pyplot as plt
import numpy as np
import pytest
import sympy as sp
from matplotlib.quiver import Quiver

from ieeVisualizer import Visualizer


def rotation(x, y):
    return -y, x * 0.5


def the_quiver(fig):
    ax = fig.axes[0]
    (q,) = [c for c in ax.collections if isinstance(c, Quiver)]
    return q


def test_quiver_uv_match_field():
    fig = Visualizer().visualize_vf(rotation, dim=2, n=7, domain=[(-1, 1), (0, 2)])
    q = the_quiver(fig)
    X, Y = q.X, q.Y
    assert len(X) == 49
    u, v = rotation(X, Y)
    np.testing.assert_allclose(q.U, u)
    np.testing.assert_allclose(q.V, v)
    np.testing.assert_allclose(np.unique(X), np.linspace(-1, 1, 7))
    np.testing.assert_allclose(np.unique(Y), np.linspace(0, 2, 7))


def test_quiver_colour_is_magnitude_and_has_colorbar():
    fig = Visualizer(colormap="magma").visualize_vf(rotation, dim=2, n=5)
    q = the_quiver(fig)
    u, v = rotation(q.X, q.Y)
    np.testing.assert_allclose(q.get_array(), np.hypot(u, v))
    assert q.get_cmap().name == "magma"
    assert len(fig.axes) == 2  # main axes + colorbar


def test_sympy_field_matches_callable():
    x, y = sp.symbols("x y")
    q_sym = the_quiver(Visualizer().visualize_vf([-y, x / 2], dim=2, n=6))
    q_fn = the_quiver(Visualizer().visualize_vf(rotation, dim=2, n=6))
    np.testing.assert_allclose(q_sym.U, q_fn.U)
    np.testing.assert_allclose(q_sym.V, q_fn.V)


def test_sympy_constant_component_is_broadcast():
    x, y = sp.symbols("x y")
    q = the_quiver(Visualizer().visualize_vf(sp.Matrix([1, x]), dim=2, n=4, symbols=(x, y)))
    np.testing.assert_allclose(q.U, np.ones(16))
    np.testing.assert_allclose(q.V, q.X)


@pytest.mark.parametrize(
    "const", [(1.0, 2.0), [1.0, 2.0], np.array([1.0, 2.0]), np.array([1, 2], dtype=np.int64)]
)
def test_constant_field_components_accepted_as_tuple_list_or_1d_array(const):
    q = the_quiver(Visualizer().visualize_vf(lambda x, y: const, dim=2, n=3))
    np.testing.assert_allclose(q.U, np.ones(9))
    np.testing.assert_allclose(q.V, np.full(9, 2.0))


def test_constant_3d_field_as_1d_array():
    fig = Visualizer().visualize_vf(lambda x, y, z: np.array([0.0, 0.0, 1.0]), dim=3, n=2)
    assert fig.axes[0].collections


def test_1d_array_of_wrong_length_is_rejected():
    with pytest.raises(ValueError, match="2 components"):
        Visualizer().visualize_vf(lambda x, y: np.array([1.0, 2.0, 3.0]), dim=2, n=3)


def test_default_vf_grid_is_coarse():
    assert len(the_quiver(Visualizer().visualize_vf(rotation, dim=2)).X) == 20 * 20
    assert len(the_quiver(Visualizer(N=5).visualize_vf(rotation, dim=2)).X) == 5 * 5


def test_streamplot_adds_streamlines():
    fig = Visualizer().visualize_vf(rotation, dim=2, n=10, streamplot=True)
    ax = fig.axes[0]
    non_quiver = [c for c in ax.collections if not isinstance(c, Quiver)]
    assert non_quiver, "expected a streamline LineCollection"


def test_streamplot_rejects_random_grid_and_3d():
    with pytest.raises(ValueError, match="linspace"):
        Visualizer(linspace=False).visualize_vf(rotation, dim=2, streamplot=True)
    with pytest.raises(ValueError, match="dim=2"):
        Visualizer().visualize_vf(lambda x, y, z: (x, y, z), dim=3, streamplot=True)


def test_3d_quiver_segments_follow_field():
    def field(x, y, z):
        return y, -x, 0.5 * z + 1

    n = 4
    fig = Visualizer().visualize_vf(field, dim=3, n=n, domain=(0, 3))
    ax = fig.axes[0]
    assert ax.name == "3d"
    (coll,) = ax.collections
    segs = np.asarray(coll._segments3d)
    n_arrows = n**3
    assert len(segs) == 3 * n_arrows  # shaft + two head strokes per arrow
    shafts = segs[:n_arrows]
    tail = shafts[:, 1]  # pivot="tail": shaft drawn from tip to tail
    direction = shafts[:, 0] - tail
    expected = np.column_stack(field(*tail.T))
    # same direction, one common positive scale for every arrow
    scale = np.linalg.norm(direction, axis=1) / np.linalg.norm(expected, axis=1)
    np.testing.assert_allclose(scale, scale[0])
    np.testing.assert_allclose(direction, expected * scale[0], atol=1e-12)
    # longest arrow spans one grid cell
    assert np.linalg.norm(direction, axis=1).max() == pytest.approx(3 / (n - 1))


def test_3d_drops_nonfinite_points_with_warning():
    def field(x, y, z):
        return np.where(x > 0, np.nan, 1.0), y, z

    with pytest.warns(RuntimeWarning, match="non-finite"):
        fig = Visualizer().visualize_vf(field, dim=3, n=3, domain=(-1, 1))
    (coll,) = fig.axes[0].collections
    assert len(coll._segments3d) == 3 * 18  # 9 of 27 points have x > 0


def test_2d_partial_nonfinite_masks_arrows():
    def field(x, y):
        return 1 / x, y

    with pytest.warns(RuntimeWarning, match="3 of 9"):
        fig = Visualizer().visualize_vf(field, dim=2, n=3, domain=(-1, 1))
    q = the_quiver(fig)
    # Quiver fills masked U/V and records the dropped arrows in Umask.
    np.testing.assert_array_equal(q.Umask, q.X == 0)
    assert q.Umask.sum() == 3  # the x == 0 column


def test_all_nonfinite_field_raises():
    with pytest.raises(ValueError, match="no finite values"):
        Visualizer().visualize_vf(lambda x, y: (x * np.inf, y * np.nan), dim=2)


@pytest.mark.parametrize(
    "fn, dim, match",
    [
        (lambda x, y: (x, y, x), 2, "components"),
        (lambda x, y: x, 2, "components"),
        (lambda x: (x, x), 2, "argument"),
        (lambda x, y: (x, y), 3, "argument"),
        (lambda x, y: (x, y), 1, "dim"),
        (sp.Symbol("x"), 2, "Matrix or list"),
        ([sp.Symbol("x")], 2, "component"),
    ],
)
def test_vf_errors(fn, dim, match):
    with pytest.raises(ValueError, match=match):
        Visualizer().visualize_vf(fn, dim=dim)


def test_vf_show_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: calls.append(1))
    Visualizer().visualize_vf(rotation, dim=2)
    assert calls == []
    Visualizer().visualize_vf(rotation, dim=2, show=True)
    assert calls == [1]


def test_3d_arrow_colour_encodes_magnitude_for_shaft_and_head():
    def field(x, y, z):
        return x, 0 * y, 0 * z + 0.1

    n = 3
    v = Visualizer(colormap="viridis")
    fig = v.visualize_vf(field, dim=3, n=n, domain=(0, 2))
    (coll,) = fig.axes[0].collections
    segs = np.asarray(coll._segments3d)
    colors = coll.get_colors()
    n_arrows = n**3
    tails = segs[:n_arrows, 1]
    mags = np.linalg.norm(np.column_stack(field(*tails.T)), axis=1)
    norm = plt.Normalize(mags.min(), mags.max())
    expected = plt.get_cmap("viridis")(norm(mags))
    np.testing.assert_allclose(colors[:n_arrows], expected)
    # both head strokes of each arrow carry the shaft's colour
    np.testing.assert_allclose(colors[n_arrows : 2 * n_arrows], expected)
    np.testing.assert_allclose(colors[2 * n_arrows :], expected)
