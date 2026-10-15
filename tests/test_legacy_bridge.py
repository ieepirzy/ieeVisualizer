"""Visualizer.to_spec / Visualizer.export: the declarative equivalent of
visualize() / visualize_vf() holds and draws the same data the legacy calls
draw, records how it was sampled, and exports with a provenance manifest."""

import hashlib
import json
import warnings

import numpy as np
import pytest
import sympy as sp
from conftest import surface_vertices
from matplotlib.quiver import Quiver

from ieeVisualizer import Visualizer
from ieeVisualizer.artifacts import MemoryWriter
from ieeVisualizer.render import RenderRequest, build_figure, render
from ieeVisualizer.spec import from_json, spec_hash, to_json, validate


def rotation(x, y):
    return -y, x * 0.5


def field3(x, y, z):
    return y, -x, 0.5 * z + 1


def _col(spec, name):
    return spec.datasets["samples"].columns[name]


def _quiver(fig_or_ax):
    axes = fig_or_ax.axes if hasattr(fig_or_ax, "axes") and isinstance(
        fig_or_ax.axes, list) else [fig_or_ax]
    (q,) = [c for c in axes[0].collections if isinstance(c, Quiver)]
    return q


# --------------------------------------------------------------------------
# equivalence with the legacy drawing
# --------------------------------------------------------------------------


def test_function_2d_spec_draws_the_line_visualize_draws():
    f = lambda x: x**3 - 2 * x  # noqa: E731
    v = Visualizer(N=50)
    spec = v.to_spec(f, dim=2, domain=(-2, 3))
    assert validate(spec) == []
    (legacy,) = v.visualize(f, dim=2, domain=(-2, 3)).axes[0].get_lines()
    (line,) = build_figure(spec).layer_artists[0]
    np.testing.assert_allclose(line.get_xdata(), legacy.get_xdata())
    np.testing.assert_allclose(line.get_ydata(), legacy.get_ydata())
    np.testing.assert_allclose(_col(spec, "x").values, np.linspace(-2, 3, 50))


def test_function_3d_spec_draws_the_surface_visualize_draws():
    a, b = sp.symbols("a b")
    expr = sp.sin(a) * sp.cos(b)
    v = Visualizer(N=12)
    spec = v.to_spec(expr, dim=3, domain=[(-2, 2), (0, 3)])
    assert spec.coordinate_system == "cartesian3d"
    built = build_figure(spec)
    (surf,) = built.layer_artists[0]
    assert type(surf).__name__ == "Poly3DCollection"
    legacy = surface_vertices(v.visualize(expr, dim=3, domain=[(-2, 2), (0, 3)]))
    ours = surface_vertices(built.figure)
    np.testing.assert_allclose(ours, legacy, atol=1e-12)
    np.testing.assert_allclose(ours[:, 2], np.sin(ours[:, 0]) * np.cos(ours[:, 1]), atol=1e-12)


def test_vector_field_2d_spec_draws_the_quiver_visualize_vf_draws():
    v = Visualizer(colormap="magma")
    spec = v.to_spec(rotation, dim=2, kind="vector_field", n=7, domain=[(-1, 1), (0, 2)])
    built = build_figure(spec)
    q = _quiver(built.axes)
    legacy = _quiver(v.visualize_vf(rotation, dim=2, n=7, domain=[(-1, 1), (0, 2)]))
    for attr in ("X", "Y", "U", "V"):
        np.testing.assert_allclose(getattr(q, attr), getattr(legacy, attr))
    # arrow colour is the magnitude, with a labelled colourbar, as in visualize_vf
    np.testing.assert_allclose(q.get_array(), legacy.get_array())
    np.testing.assert_allclose(q.get_array(), np.hypot(*rotation(q.X, q.Y)))
    assert q.get_cmap().name == "magma"
    assert len(built.figure.axes) == 2
    assert built.figure.axes[1].get_ylabel() == "magnitude"


def test_vector_field_3d_spec_has_legacy_arrow_lengths():
    n = 4
    v = Visualizer()
    spec = v.to_spec(field3, dim=3, kind="vector_field", n=n, domain=(0, 3))
    (q,) = build_figure(spec).layer_artists[0]
    (legacy,) = v.visualize_vf(field3, dim=3, n=n, domain=(0, 3)).axes[0].collections
    # Same shafts (tail and tip) as visualize_vf; only the colouring differs.
    segs = np.asarray(q._segments3d)[: n**3]
    legacy_segs = np.asarray(legacy._segments3d)[: n**3]
    np.testing.assert_allclose(segs, legacy_segs, atol=1e-12)
    direction = segs[:, 0] - segs[:, 1]
    assert np.linalg.norm(direction, axis=1).max() == pytest.approx(3 / (n - 1))


def test_random_sampling_uses_the_same_samples_and_records_the_seed():
    v = Visualizer(N=30, linspace=False)
    spec = v.to_spec(np.sin, dim=2, domain=(0, 1), rng=7)
    (legacy,) = v.visualize(np.sin, dim=2, domain=(0, 1), rng=7).axes[0].get_lines()
    np.testing.assert_allclose(_col(spec, "x").values, legacy.get_xdata())
    extra = spec.datasets["samples"].provenance.extra
    assert extra["sampling"] == "uniform_random_sorted"
    assert extra["seed"] == 7


def test_random_sampling_without_per_call_seed_says_it_cannot_be_redrawn():
    spec = Visualizer(N=10, linspace=False, rng=1).to_spec(np.sin, dim=2)
    prov = spec.datasets["samples"].provenance
    assert prov.extra["seed"] is None
    assert "cannot be redrawn" in prov.notes


# --------------------------------------------------------------------------
# provenance, units, nulls
# --------------------------------------------------------------------------


def test_provenance_records_expression_domain_n_and_sampling():
    x, y = sp.symbols("x y")
    spec = Visualizer().to_spec([-y, x], dim=2, kind="vector_field", n=5, domain=(-1, 2))
    prov = spec.datasets["samples"].provenance
    assert prov.source == "ieeVisualizer.Visualizer.to_spec"
    assert prov.extra["input"] == {"type": "sympy", "expression": ["-y", "x"]}
    assert prov.extra["domain"] == [[-1.0, 2.0], [-1.0, 2.0]]
    assert prov.extra["n"] == 5
    assert prov.extra["sampling"] == "linspace"
    assert prov.extra["kind"] == "vector_field"
    assert "computed, not measured" in prov.notes


def test_callable_is_recorded_without_memory_address_so_the_hash_is_stable():
    v = Visualizer(N=20)
    s1 = v.to_spec(lambda x: x**2, dim=2)
    s2 = v.to_spec(lambda x: x**2, dim=2)
    me = test_callable_is_recorded_without_memory_address_so_the_hash_is_stable
    assert s1.datasets["samples"].provenance.extra["input"] == {
        "type": "callable", "callable": f"{__name__}.{me.__qualname__}.<locals>.<lambda>"}
    assert "0x" not in to_json(s1)
    assert spec_hash(s1) == spec_hash(s2)
    assert Visualizer().to_spec(np.cos, dim=2).datasets["samples"].provenance.extra[
        "input"] == {"type": "callable", "callable": "numpy.cos"}


def test_units_appear_in_axis_labels():
    t = sp.Symbol("t")
    spec = Visualizer(N=10).to_spec(sp.exp(-t), dim=2, units={"x": "s", "y": "V"})
    built = build_figure(spec)
    assert built.axes.get_xlabel() == "t (s)"
    assert built.axes.get_ylabel() == "f(t) (V)"
    vf = Visualizer().to_spec(rotation, dim=2, kind="vector_field", n=3,
                              units={"x": "m", "y": "m", "vector": "m/s"})
    built = build_figure(vf)
    assert (built.axes.get_xlabel(), built.axes.get_ylabel()) == ("x (m)", "y (m)")
    assert built.figure.axes[1].get_ylabel() == "magnitude (m/s)"


def test_nonfinite_samples_are_null_and_drawn_as_gaps():
    with pytest.warns(RuntimeWarning, match="non-finite"):
        spec = Visualizer(N=5).to_spec(lambda x: 1 / x, dim=2, domain=(-1, 1))
    assert _col(spec, "f").values[2] is None  # x = 0
    assert spec.datasets["samples"].provenance.extra["nonfinite_points"] == 1
    result = render(spec, RenderRequest(format="svg"))
    assert "null_values_omitted" in [w.code for w in result.warnings]


def test_symbol_names_label_axes_but_columns_stay_fixed():
    u, v = sp.symbols("u v")
    spec = Visualizer().to_spec([v, -u], dim=2, kind="vector_field", n=3)
    assert set(spec.datasets["samples"].columns) == {"x", "y", "u", "v", "magnitude"}
    assert (spec.axes["x"].label, spec.axes["y"].label) == ("u", "v")


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs,match", [
    ({"dim": 2, "kind": "surface"}, "kind must be one of"),
    ({"dim": 4}, "dim must be 2 or 3"),
    ({"dim": 2, "units": {"z": "m"}}, "units= keys must be among x, y"),
    ({"dim": 2, "units": {"x": 5}}, "must be a string"),
    ({"dim": 3, "kind": "vector_field"}, "cannot be called with 3 positional"),
])
def test_bad_input_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Visualizer().to_spec(rotation if kwargs.get("kind") == "vector_field" else np.sin,
                             **kwargs)


def test_all_nonfinite_raises_instead_of_an_empty_spec():
    with pytest.raises(ValueError, match="no finite values"):
        Visualizer().to_spec(lambda x: np.full_like(x, np.nan), dim=2)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def test_export_writes_png_svg_spec_and_manifest(tmp_path):
    x = sp.Symbol("x")
    artifact = Visualizer(N=40).export(sp.sin(x), tmp_path, dim=2, title="sine",
                                       units={"x": "rad"}, name="sine")
    root = tmp_path / artifact.artifact_id
    assert sorted(p.name for p in root.iterdir()) == [
        "figure.png", "figure.svg", "manifest.json", "spec.json", "theme.json"]
    manifest = json.loads((root / "manifest.json").read_text())
    for variant in manifest["variants"]:
        data = (root / variant["file"]["name"]).read_bytes()
        assert variant["file"]["sha256"] == "sha256:" + hashlib.sha256(data).hexdigest()
    prov = manifest["datasets"]["samples"]["provenance"]
    assert prov["extra"]["input"] == {"type": "sympy", "expression": "sin(x)"}
    assert manifest["title"] == "sine"
    # the stored spec re-renders to the exported SVG
    spec = from_json((root / "spec.json").read_text())
    svg = render(spec, RenderRequest(format="svg")).content
    assert svg == (root / "figure.svg").read_bytes()


def test_export_accepts_a_writer_and_spec_options():
    w = MemoryWriter()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # nothing non-finite here
        artifact = Visualizer().export(rotation, w, dim=2, kind="vector_field",
                                       formats=["svg"], n=4)
    assert {name for (_, name) in w.files} == {"figure.svg", "spec.json", "theme.json",
                                               "manifest.json"}
    assert artifact.variant("svg").format == "svg"
