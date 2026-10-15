import warnings

import matplotlib.pyplot as plt
import numpy as np
import pytest
import sympy as sp
from conftest import surface_vertices

from ieeVisualizer import Visualizer


def line_data(fig):
    (ax,) = fig.axes
    (line,) = ax.get_lines()
    return np.asarray(line.get_xdata(), float), np.ma.asarray(line.get_ydata())


def test_returns_figure():
    fig = Visualizer().visualize(lambda x: x, dim=2)
    assert isinstance(fig, plt.Figure)


def test_line_data_equals_function_on_linspace_domain():
    fig = Visualizer(N=50).visualize(lambda x: x**3 - 2 * x, dim=2, domain=(-2, 3))
    x, y = line_data(fig)
    np.testing.assert_allclose(x, np.linspace(-2, 3, 50))
    np.testing.assert_allclose(y, x**3 - 2 * x)


def test_sympy_and_callable_give_identical_data():
    t = sp.symbols("t")
    v = Visualizer(N=80)
    _, y_sym = line_data(v.visualize(sp.sin(t) * sp.exp(-t / 3), dim=2))
    _, y_fn = line_data(v.visualize(lambda t: np.sin(t) * np.exp(-t / 3), dim=2))
    np.testing.assert_allclose(y_sym, y_fn)


def test_numpy_ufunc_is_accepted():
    x, y = line_data(Visualizer(N=10).visualize(np.cos, dim=2))
    np.testing.assert_allclose(y, np.cos(x))


def test_constant_function_is_broadcast():
    _, y = line_data(Visualizer(N=7).visualize(lambda x: 2.5, dim=2))
    np.testing.assert_allclose(y, np.full(7, 2.5))


def test_random_domain_is_seeded_sorted_and_in_bounds():
    v = Visualizer(N=40, linspace=False)
    x1, y1 = line_data(v.visualize(np.sin, dim=2, domain=(0, 1), rng=3))
    x2, _ = line_data(v.visualize(np.sin, dim=2, domain=(0, 1), rng=3))
    np.testing.assert_array_equal(x1, x2)
    assert np.all(np.diff(x1) >= 0)
    assert x1.min() >= 0 and x1.max() <= 1
    assert not np.allclose(x1, np.linspace(0, 1, 40))
    np.testing.assert_allclose(y1, np.sin(x1))


def test_per_call_linspace_overrides_constructor():
    v = Visualizer(N=40, linspace=False, rng=0)
    x, _ = line_data(v.visualize(np.sin, dim=2, linspace=True))
    np.testing.assert_allclose(x, np.linspace(-5, 5, 40))


def test_surface_vertices_lie_on_function_for_callable_and_sympy():
    f = lambda x, y: np.sin(x) * np.cos(y)  # noqa: E731
    a, b = sp.symbols("a b")
    for fn in (f, sp.sin(a) * sp.cos(b)):
        fig = Visualizer(N=30).visualize(fn, dim=3, domain=[(-2, 2), (0, 3)])
        pts = surface_vertices(fig)
        assert len(pts) > 0
        np.testing.assert_allclose(pts[:, 2], f(pts[:, 0], pts[:, 1]), atol=1e-12)
        assert pts[:, 0].min() == pytest.approx(-2) and pts[:, 0].max() == pytest.approx(2)
        assert pts[:, 1].min() == pytest.approx(0) and pts[:, 1].max() == pytest.approx(3)


def test_explicit_symbols_choose_input_order():
    x, y = sp.symbols("x y")
    # inferred order is (x, y): z = x; with symbols=(y, x) the first input is y
    fig = Visualizer(N=10).visualize(x, dim=3, symbols=(y, x), domain=[(0, 1), (5, 6)])
    pts = surface_vertices(fig)
    np.testing.assert_allclose(pts[:, 2], pts[:, 1])  # z equals the second axis


def test_surface_uses_colormap():
    fig = Visualizer(N=10, colormap="plasma").visualize(lambda x, y: x + y, dim=3)
    (coll,) = fig.axes[0].collections
    assert coll.get_cmap().name == "plasma"


@pytest.mark.parametrize("dim", [0, 1, 4, "2", True])
def test_unsupported_dim_raises(dim):
    with pytest.raises(ValueError, match="dim"):
        Visualizer().visualize(lambda x: x, dim=dim)


@pytest.mark.parametrize(
    "fn, dim",
    [(lambda x, y: x, 2), (lambda x: x, 3), (np.add, 2)],
)
def test_wrong_arity_raises(fn, dim):
    with pytest.raises(ValueError, match="argument"):
        Visualizer().visualize(fn, dim=dim)


def test_sympy_symbol_count_mismatch_raises():
    x, y = sp.symbols("x y")
    with pytest.raises(ValueError, match="symbols="):
        Visualizer().visualize(x * y, dim=2)


def test_all_nonfinite_raises_instead_of_empty_plot():
    with pytest.raises(ValueError, match="no finite values"):
        Visualizer().visualize(lambda x: np.full_like(x, np.nan), dim=2)
    assert plt.get_fignums() == []  # nothing was drawn


def test_partial_nonfinite_is_masked_with_warning():
    with pytest.warns(RuntimeWarning, match="non-finite"):
        fig = Visualizer(N=21).visualize(np.log, dim=2, domain=(-1, 1))
    x, y = line_data(fig)
    assert np.ma.is_masked(y)
    np.testing.assert_array_equal(np.ma.getmaskarray(y), ~(x > 0))
    np.testing.assert_allclose(y.compressed(), np.log(x[x > 0]))


def test_fully_finite_output_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Visualizer(N=21).visualize(np.exp, dim=2)


def test_complex_output_raises():
    with pytest.raises(ValueError, match="complex"):
        Visualizer().visualize(lambda x: x + 1j, dim=2)


@pytest.mark.parametrize("domain", [(1, 1), (2, 1), (0, np.inf), (1, 2, 3)])
def test_bad_domain_raises(domain):
    with pytest.raises(ValueError, match="domain"):
        Visualizer().visualize(lambda x: x, dim=2, domain=domain)


def test_show_false_does_not_call_plt_show(monkeypatch):
    calls = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: calls.append(1))
    Visualizer().visualize(lambda x: x, dim=2)
    assert calls == []
    Visualizer().visualize(lambda x: x, dim=2, show=True)
    assert calls == [1]


def test_draws_into_given_axes_and_rejects_wrong_projection():
    fig, ax = plt.subplots()
    assert Visualizer().visualize(np.sin, dim=2, ax=ax) is fig
    assert len(ax.get_lines()) == 1
    with pytest.raises(ValueError, match="3D axes"):
        Visualizer().visualize(lambda x, y: x, dim=3, ax=ax)


def test_style_is_applied():
    fig = Visualizer(style="dark_background").visualize(np.sin, dim=2)
    assert fig.get_facecolor()[:3] == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("symbols", ["x", "x,", ["x"]])
def test_symbols_given_as_string_is_parsed_like_sympy_symbols(symbols):
    x = sp.symbols("x")
    xs, y = line_data(Visualizer(N=9).visualize(x**2, dim=2, symbols=symbols))
    np.testing.assert_allclose(y, xs**2)


def test_symbols_string_names_inputs_in_order_for_surface():
    x, y = sp.symbols("x y")
    fig = Visualizer(N=10).visualize(x, dim=3, symbols="y x", domain=[(0, 1), (5, 6)])
    pts = surface_vertices(fig)
    np.testing.assert_allclose(pts[:, 2], pts[:, 1])  # first input is y, so z = second axis
    ax = fig.axes[0]
    assert (ax.get_xlabel(), ax.get_ylabel()) == ("y", "x")


def test_symbols_string_with_wrong_count_raises():
    x = sp.symbols("x")
    with pytest.raises(ValueError, match="symbol"):
        Visualizer().visualize(x**2, dim=2, symbols="x y")
