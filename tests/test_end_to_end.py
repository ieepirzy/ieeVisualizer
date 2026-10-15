"""End-to-end test of the first slice (docs/ROADMAP.md):

    An installable Python library renders a function and vector field
    correctly; a declarative spec creates a reproducible figure; a template
    fills data; the result exports as PNG and SVG with provenance; tests cover
    errors, axes/units and repeatability.

Each test below is one clause of that sentence, exercised through the public
entry points only (Visualizer, ieeVisualizer.spec, templates, render,
artifacts).
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import sympy as sp
from matplotlib.quiver import Quiver

from ieeVisualizer import Visualizer, templates
from ieeVisualizer.artifacts import LocalDirectoryWriter, export
from ieeVisualizer.render import RenderRequest, build_figure, render
from ieeVisualizer.spec import (
    DataSnapshot,
    SpecParseError,
    SpecValidationError,
    apply_patch,
    from_json,
    to_json,
)
from ieeVisualizer.templates import TemplateInputError

ROOT = Path(__file__).resolve().parent.parent

SPEC_JSON = {
    "schema_version": "1.0",
    "title": "Cooling curve",
    "datasets": {
        "obs": {
            "type": "snapshot",
            "columns": {
                "t": {"dtype": "float", "unit": "min", "values": [0, 5, 10, 15, 20, 25]},
                "T": {"dtype": "float", "unit": "°C",
                      "values": [90.0, 71.5, 58.2, None, 41.0, 35.9]},
            },
            "provenance": {"source": "e2e-test", "notes": "synthetic"},
        }
    },
    "layers": [
        {"type": "line", "data": "obs", "encoding": {"x": "t", "y": "T"}, "name": "measured"},
        {"type": "function", "expression": "20 + 70*exp(-k*x)", "domain": {"x": [0, 25]},
         "samples": 60, "parameters": {"k": 0.06}, "role": "reference", "name": "model"},
    ],
    "axes": {"x": {"label": "Time"}, "y": {"label": "Temperature"}},
}


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


# -- a function and a vector field render correctly ----------------------------


def test_legacy_function_renders_correctly_directly_and_through_the_spec():
    x = sp.Symbol("x")
    expr = sp.exp(-x / 2) * sp.cos(3 * x)
    v = Visualizer(N=120)
    (legacy,) = v.visualize(expr, dim=2, domain=(0, 6)).axes[0].get_lines()
    xs = np.asarray(legacy.get_xdata(), float)
    np.testing.assert_allclose(legacy.get_ydata(), np.exp(-xs / 2) * np.cos(3 * xs))

    (line,) = build_figure(v.to_spec(expr, dim=2, domain=(0, 6))).layer_artists[0]
    np.testing.assert_allclose(line.get_xdata(), xs)
    np.testing.assert_allclose(line.get_ydata(), legacy.get_ydata())


def test_legacy_vector_field_renders_correctly_directly_and_through_the_spec():
    def field(x, y):
        return -y, x

    v = Visualizer()
    fig = v.visualize_vf(field, dim=2, n=9, domain=(-1, 1))
    (legacy,) = [c for c in fig.axes[0].collections if isinstance(c, Quiver)]
    u, w = field(legacy.X, legacy.Y)
    np.testing.assert_allclose(legacy.U, u)
    np.testing.assert_allclose(legacy.V, w)

    built = build_figure(v.to_spec(field, dim=2, kind="vector_field", n=9, domain=(-1, 1)))
    (q,) = built.layer_artists[0]
    for attr in ("X", "Y", "U", "V"):
        np.testing.assert_allclose(getattr(q, attr), getattr(legacy, attr))


# -- a declarative spec creates a reproducible figure --------------------------


_RENDER_SCRIPT = """
import sys
from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.spec import from_json
spec = from_json(open(sys.argv[1], encoding="utf-8").read())
sys.stdout.buffer.write(render(spec, RenderRequest(format="svg")).content)
"""


def _render_in_fresh_process(spec_path: Path) -> bytes:
    env = dict(os.environ, PYTHONPATH=str(ROOT), MPLBACKEND="Agg")
    out = subprocess.run([sys.executable, "-c", _RENDER_SCRIPT, str(spec_path)],
                         capture_output=True, env=env, cwd=str(spec_path.parent), check=False)
    assert out.returncode == 0, out.stderr.decode()
    return out.stdout


def test_spec_json_renders_byte_identical_svg_in_two_fresh_processes(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(SPEC_JSON), encoding="utf-8")
    first = _render_in_fresh_process(path)
    second = _render_in_fresh_process(path)
    assert first.startswith(b"<?xml") and len(first) > 1000
    assert first == second
    in_process = render(from_json(path.read_text(encoding="utf-8")),
                        RenderRequest(format="svg")).content
    assert in_process == first


def test_spec_draws_its_data_with_a_gap_for_the_null():
    built = build_figure(from_json(json.dumps(SPEC_JSON)))
    (line,) = built.layer_artists[0]
    y = np.ma.asarray(line.get_ydata())
    assert np.ma.getmaskarray(y).tolist() == [False, False, False, True, False, False]
    assert "null_values_omitted" in [w.code for w in built.warnings]


# -- a template fills data ----------------------------------------------------


def test_template_fills_supplied_data_into_a_renderable_spec():
    data = DataSnapshot.from_columns(
        {"day": ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"],
         "load": [3.2, 3.9, None, 4.4]}, dtypes={"day": "datetime"})
    spec = templates.build("timeseries", data, {
        "time_column": "day", "value_columns": ["load"], "y_unit": "kWh",
        "title": "Daily load"})
    ((_, snap),) = spec.datasets.items()
    assert snap.columns["load"].values == [3.2, 3.9, None, 4.4]
    assert snap.columns["load"].unit == "kWh"
    assert spec.metadata["template"]["name"] == "timeseries"
    built = build_figure(spec)
    assert built.axes.get_ylabel() == "load (kWh)"
    assert built.axes.get_title() == "Daily load"


# -- exports as PNG and SVG with provenance; repeatable from a frozen snapshot --


def test_export_png_svg_with_provenance_and_rerender_from_the_frozen_spec(tmp_path):
    spec = from_json(json.dumps(SPEC_JSON))
    artifact = export(spec, ["png", "svg"], LocalDirectoryWriter(tmp_path), name="cooling")
    root = tmp_path / artifact.artifact_id
    files = sorted(p.name for p in root.iterdir())
    assert files == ["figure.png", "figure.svg", "manifest.json", "spec.json"]

    manifest = json.loads((root / "manifest.json").read_text())
    assert (root / "figure.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    for v in manifest["variants"]:
        assert _sha((root / v["file"]["name"]).read_bytes()) == v["file"]["sha256"]
    obs = manifest["datasets"]["obs"]
    assert obs["provenance"]["source"] == "e2e-test"
    assert obs["content_hash"] == spec.datasets["obs"].content_hash()
    assert obs["columns"]["T"]["unit"] == "°C"
    assert set(manifest["library_versions"]) >= {"ieeVisualizer", "matplotlib", "numpy"}

    # Repeatability: the frozen spec.json alone reproduces the exported SVG.
    frozen = from_json((root / "spec.json").read_text(encoding="utf-8"))
    svg = render(frozen, RenderRequest(format="svg")).content
    assert _sha(svg) == artifact.variant("svg").sha256
    # ...and a second export of it gets the same id and the same file hashes.
    again = export(frozen, ["png", "svg"], LocalDirectoryWriter(tmp_path / "again"),
                   name="cooling")
    assert again.artifact_id == artifact.artifact_id
    assert [v.file.sha256 for v in again.variants] == [v.file.sha256 for v in artifact.variants]


def test_legacy_export_is_repeatable_from_its_frozen_snapshot(tmp_path):
    def field(x, y):
        return np.sin(y), np.cos(x)

    artifact = Visualizer().export(field, tmp_path, dim=2, kind="vector_field", n=8,
                                   units={"x": "m", "y": "m", "vector": "m/s"})
    root = tmp_path / artifact.artifact_id
    frozen = from_json((root / "spec.json").read_text(encoding="utf-8"))
    # The callable is gone; the stored samples are enough to re-render.
    assert frozen.datasets["samples"].provenance.extra["input"]["type"] == "callable"
    svg = render(frozen, RenderRequest(format="svg")).content
    assert svg == (root / "figure.svg").read_bytes()


# -- axes and units ----------------------------------------------------------


def test_axis_labels_carry_units_from_columns_and_axes():
    built = build_figure(from_json(json.dumps(SPEC_JSON)))
    assert built.axes.get_xlabel() == "Time (min)"
    assert built.axes.get_ylabel() == "Temperature (°C)"
    t = sp.Symbol("t")
    legacy = build_figure(Visualizer(N=20).to_spec(sp.sin(t), dim=2,
                                                   units={"x": "s", "y": "V"}))
    assert (legacy.axes.get_xlabel(), legacy.axes.get_ylabel()) == ("t (s)", "f(t) (V)")


# -- errors --------------------------------------------------------------------


def test_bad_json_and_unknown_schema_version_are_parse_errors():
    with pytest.raises(SpecParseError):
        from_json("{not json")
    with pytest.raises(SpecParseError) as e:
        from_json(json.dumps(dict(SPEC_JSON, schema_version="9.9")))
    assert e.value.issues[0].path == "/schema_version"


def test_spec_with_a_wrong_column_is_refused_with_a_patchable_path():
    bad = json.loads(json.dumps(SPEC_JSON))
    bad["layers"][0]["encoding"]["y"] = "temp"
    spec = from_json(json.dumps(bad))
    with pytest.raises(SpecValidationError) as e:
        render(spec, RenderRequest(format="svg"))
    (issue,) = [i for i in e.value.issues if i.severity == "error"]
    assert issue.path == "/layers/0/encoding/y/field"
    assert issue.code == "unknown_column"
    fixed = apply_patch(spec, [{"op": "replace", "path": issue.path, "value": "T"}])
    assert render(fixed, RenderRequest(format="svg")).content == render(
        from_json(json.dumps(SPEC_JSON)), RenderRequest(format="svg")).content


def test_template_and_legacy_errors_name_the_problem():
    with pytest.raises(TemplateInputError) as e:
        templates.build("timeseries", {"day": ["2026-10-01"], "load": [1.0]},
                        {"time_column": "day", "value_columns": ["laod"]})
    assert any(i.code == "missing_column" for i in e.value.issues)
    with pytest.raises(ValueError, match="dim must be 2 or 3"):
        Visualizer().to_spec(np.sin, dim=1)
    with pytest.raises(ValueError, match="no finite values"):
        Visualizer().visualize(lambda x: np.log(-1 - x**2), dim=2)


def test_empty_series_is_an_error_not_an_empty_figure():
    bad = json.loads(json.dumps(SPEC_JSON))
    bad["datasets"]["obs"]["columns"]["T"]["values"] = [None] * 6
    with pytest.raises(SpecValidationError) as e:
        render(from_json(json.dumps(bad)), RenderRequest(format="png"))
    assert [(i.code, i.path) for i in e.value.issues if i.severity == "error"] == [
        ("all_null_column", "/layers/0/encoding/y")]


def test_canonical_json_round_trip_is_stable():
    spec = from_json(json.dumps(SPEC_JSON))
    assert to_json(from_json(to_json(spec))) == to_json(spec)
