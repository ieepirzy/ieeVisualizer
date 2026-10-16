"""visualize() / visualize_vf() build a FigureSpec and draw it through the
Matplotlib adapter (MatplotlibAdapter.draw_into) into their own axes; only
streamlines and the 2D equal aspect are still drawn directly, and so is
anything drawn into a non-rectilinear 2D axes (e.g. polar)."""

import matplotlib.pyplot as plt
import numpy as np
import pytest
import sympy as sp

from ieeVisualizer import Visualizer
from ieeVisualizer.render import RenderWarning
from ieeVisualizer.render.mpl import MatplotlibAdapter
from ieeVisualizer.spec import FigureSpec, spec_hash, to_dict, validate


def rotation(x, y):
    return -y, x * 0.5


def field3(x, y, z):
    return y, -x, 0.5 * z + 1


@pytest.fixture
def drawn(monkeypatch):
    """Records every (spec, ax, kwargs) the adapter is asked to draw into."""
    calls = []
    real = getattr(MatplotlibAdapter, "draw_into", None)

    def spy(self, spec, ax, **kw):
        calls.append((spec, ax, kw))
        return real(self, spec, ax, **kw)

    monkeypatch.setattr(MatplotlibAdapter, "draw_into", spy, raising=False)
    return calls


CASES = [
    ("visualize", dict(fn=lambda x: x**3 - 2 * x, dim=2, domain=(-2, 3)), "function"),
    ("visualize", dict(fn=sp.sin(sp.Symbol("a")) * sp.cos(sp.Symbol("b")), dim=3), "function"),
    ("visualize_vf", dict(fn=rotation, dim=2, n=7), "vector_field"),
    ("visualize_vf", dict(fn=rotation, dim=2, n=7, streamplot=True), "vector_field"),
    ("visualize_vf", dict(fn=field3, dim=3, n=4, domain=(0, 3)), "vector_field"),
]


@pytest.mark.parametrize("method, kw, kind", CASES)
def test_legacy_call_draws_exactly_the_to_spec_spec(drawn, method, kw, kind):
    v = Visualizer(N=12)
    fig = getattr(v, method)(**kw)
    assert len(drawn) == 1, "the legacy call did not draw through the adapter"
    spec, ax, opts = drawn[0]
    assert isinstance(spec, FigureSpec) and validate(spec) == []
    assert ax.figure is fig and fig.axes[0] is ax
    assert opts.get("role_styling") is False
    kw = {k: val for k, val in kw.items() if k != "streamplot"}
    assert spec_hash(spec) == spec_hash(v.to_spec(kind=kind, **kw))
    assert spec.layers[0].role == "reference"


def test_streamlines_are_still_drawn_directly_on_top(drawn):
    Visualizer().visualize_vf(rotation, dim=2, n=10, streamplot=True)
    ((spec, ax, _),) = drawn
    assert "streamplot" not in str(to_dict(spec))  # spec 1.0 cannot say it
    from matplotlib.quiver import Quiver

    kinds = {type(c).__name__ for c in ax.collections if not isinstance(c, Quiver)}
    assert "LineCollection" in kinds
    assert ax.get_aspect() == 1.0


def test_3d_field_spec_colours_arrows_by_magnitude(drawn):
    Visualizer().visualize_vf(field3, dim=3, n=3, domain=(0, 2))
    ((spec, _, _),) = drawn
    assert spec.layers[0].encoding["color"].field == "magnitude"


def test_polar_axes_are_drawn_directly_as_before(drawn):
    fig = plt.figure()
    ax = fig.add_subplot(projection="polar")
    Visualizer(N=20).visualize(np.sin, dim=2, ax=ax)
    (line,) = ax.get_lines()
    np.testing.assert_allclose(line.get_ydata(), np.sin(line.get_xdata()))
    ax2 = plt.figure().add_subplot(projection="polar")
    Visualizer().visualize_vf(rotation, dim=2, n=4, ax=ax2)
    assert ax2.collections
    assert drawn == []  # spec 1.0 polar has its own angle convention


def test_render_warnings_are_reissued_but_null_warning_is_not_doubled(monkeypatch):
    real = MatplotlibAdapter.draw_into

    def noisy(self, spec, ax, **kw):
        built = real(self, spec, ax, **kw)
        built.warnings.append(RenderWarning("some_fidelity_note", "said by the adapter", ""))
        return built

    monkeypatch.setattr(MatplotlibAdapter, "draw_into", noisy)
    with pytest.warns(RuntimeWarning) as record:
        Visualizer(N=21).visualize(np.log, dim=2, domain=(-1, 1))
    messages = [str(w.message) for w in record]
    assert "some_fidelity_note: said by the adapter" in messages
    assert sum("non-finite" in m or "null" in m for m in messages) == 1
