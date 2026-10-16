"""visualize() / visualize_vf() build a FigureSpec and draw it through the
Matplotlib adapter (MatplotlibAdapter.draw_into) into their own axes; only
streamlines and the 2D equal aspect are still drawn directly."""

import matplotlib.pyplot as plt
import numpy as np
import pytest
import sympy as sp

from ieeVisualizer import Visualizer
from ieeVisualizer.render.mpl import MatplotlibAdapter
from ieeVisualizer.spec import FigureSpec, to_dict, validate


def rotation(x, y):
    return -y, x * 0.5


def field3(x, y, z):
    return y, -x, 0.5 * z + 1


@pytest.fixture
def drawn(monkeypatch):
    """Records every (spec, ax) the adapter is asked to draw into."""
    calls = []
    real = getattr(MatplotlibAdapter, "draw_into", None)

    def spy(self, spec, ax, **kw):
        calls.append((spec, ax))
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
def test_legacy_call_draws_a_valid_spec_holding_to_spec_samples(drawn, method, kw, kind):
    v = Visualizer(N=12)
    fig = getattr(v, method)(**kw)
    assert len(drawn) == 1, "the legacy call did not draw through the adapter"
    spec, ax = drawn[0]
    assert isinstance(spec, FigureSpec) and validate(spec) == []
    assert ax.figure is fig and fig.axes[0] is ax
    # the same samples, labels and axes as the declarative to_spec() route
    kw = {k: val for k, val in kw.items() if k != "streamplot"}
    ref = to_dict(v.to_spec(kind=kind, **kw))
    got = to_dict(spec)
    assert got["datasets"] == ref["datasets"]
    assert got["axes"] == ref["axes"]
    assert got["coordinate_system"] == ref["coordinate_system"]
    (layer,), (ref_layer,) = got["layers"], ref["layers"]
    assert layer["role"] == ref_layer["role"] == "reference"
    assert {k: val for k, val in layer.items() if k != "style"} == {
        k: val for k, val in ref_layer.items() if k != "style"}


def test_legacy_draw_keeps_full_strength_look_and_given_axes(drawn):
    fig, ax = plt.subplots()
    with plt.style.context("ggplot"):
        first = plt.rcParams["axes.prop_cycle"].by_key()["color"][0]
        Visualizer(N=10, style="ggplot").visualize(np.sin, dim=2, ax=ax)
    (spec, used), = drawn
    assert used is ax
    (line,) = ax.get_lines()
    assert line.get_alpha() in (None, 1.0)
    assert plt.matplotlib.colors.to_hex(line.get_color()) == first.lower()


def test_streamlines_are_still_drawn_directly_on_top(drawn):
    Visualizer().visualize_vf(rotation, dim=2, n=10, streamplot=True)
    (spec, ax), = drawn
    assert "streamplot" not in str(to_dict(spec))  # spec 1.0 cannot say it
    from matplotlib.quiver import Quiver

    kinds = {type(c).__name__ for c in ax.collections if not isinstance(c, Quiver)}
    assert "LineCollection" in kinds
    assert ax.get_aspect() == 1.0


def test_3d_field_spec_colours_arrows_by_magnitude(drawn):
    Visualizer().visualize_vf(field3, dim=3, n=3, domain=(0, 2))
    (spec, _), = drawn
    assert spec.layers[0].encoding["color"].field == "magnitude"
