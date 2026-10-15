import math

import numpy as np
import pytest

from ieeVisualizer.spec import DataSnapshot, QuiverMark, validate
from ieeVisualizer.templates import TemplateInputError, get

VF = get("vector_field_2d")


def errors(issues):
    return [(i.path, i.code) for i in issues if i.severity == "error"]


def test_example_builds_and_validates_clean():
    spec = VF.build_payload(VF.example())
    assert validate(spec) == []
    (layer,) = spec.layers
    assert isinstance(layer, QuiverMark)
    assert {ch: e.field for ch, e in layer.encoding.items()} == {
        "x": "x", "y": "y", "u": "u", "v": "v"}
    assert layer.role == "reference"  # an evaluated formula is not a measurement
    assert spec.datasets["field"].num_rows == 11 * 11
    meta = spec.metadata["template"]
    assert (meta["name"], meta["version"]) == ("vector_field_2d", "1.0.0")
    assert meta["expressions"] == {"u": "-y", "v": "x"}
    prov = spec.datasets["field"].provenance
    assert prov.extra["expressions"] == {"u": "-y", "v": "x"}
    assert prov.extra["grid"] == [11, 11]
    assert "computed, not measured" in prov.notes


def test_values_equal_the_expression_at_every_grid_point():
    spec = VF.build(u="x*y - sin(x)", v="exp(-y**2) + k*x", constants={"k": 0.5},
                    x_range=[-1, 2], y_range=[0, 3], grid=[4, 3])
    cols = spec.datasets["field"].columns
    xs, ys, us, vs = (cols[c].values for c in "xyuv")
    assert len(xs) == 12
    # grid order: y outer, x inner
    assert xs[:4] == pytest.approx([-1.0, 0.0, 1.0, 2.0])
    assert ys[:4] == [0.0] * 4 and ys[-1] == 3.0
    assert sorted(set(ys)) == pytest.approx([0.0, 1.5, 3.0])
    for x, y, u, v in zip(xs, ys, us, vs):
        assert u == pytest.approx(x * y - math.sin(x), abs=1e-12)
        assert v == pytest.approx(math.exp(-y * y) + 0.5 * x, abs=1e-12)
    assert spec.metadata["template"]["params"]["constants"] == {"k": 0.5}


def test_constant_component_is_broadcast():
    spec = VF.build(u="1", v="0", grid=3)
    cols = spec.datasets["field"].columns
    assert cols["u"].values == [1.0] * 9 and cols["v"].values == [0.0] * 9


def test_nonfinite_samples_become_null_with_a_warning():
    spec = VF.build(u="1/x", v="y", x_range=[-1, 1], grid=3)
    u = spec.datasets["field"].columns["u"].values
    assert u == [-1.0, None, 1.0] * 3
    (w,) = spec.metadata["template"]["warnings"]
    assert (w["path"], w["code"]) == ("/params/u", "nonfinite_samples")
    assert validate(spec) == []


def test_field_that_is_nowhere_finite_is_refused():
    issues = VF.check(u="log(-1 - x**2)", v="y")
    assert errors(issues) == [("/params/u", "all_null_column")]


def test_units_and_labels_in_expression_mode():
    spec = VF.build(u="-y", v="x", grid=3, x_unit="m", y_unit="m", vector_unit="m/s",
                    x_label="East", normalize=True, scale=2.0, role="observed")
    cols = spec.datasets["field"].columns
    assert (cols["x"].unit, cols["u"].unit, cols["v"].unit) == ("m", "m/s", "m/s")
    assert (spec.axes["x"].label, spec.axes["x"].unit, spec.axes["y"].label) == ("East", "m", "y")
    layer = spec.layers[0]
    assert (layer.normalize, layer.scale, layer.role) == (True, 2.0, "observed")
    assert "warnings" not in spec.metadata["template"]  # unitless math does not warn


def test_sampled_mode_uses_columns_and_units():
    data = DataSnapshot.from_columns(
        {"px": [0.0, 1.0], "py": [0.0, 0.0], "vx": [1.0, None], "vy": [0.0, 2.0]},
        units={"px": "km", "py": "km", "vx": "m/s", "vy": "m/s"})
    spec = VF.build(data, x_column="px", y_column="py", u_column="vx", v_column="vy",
                    title="Wind")
    assert validate(spec) == []
    layer = spec.layers[0]
    assert layer.role == "observed"
    assert layer.encoding["u"].field == "vx"
    assert spec.datasets["field"].columns["vx"].values == [1.0, None]  # null stays null
    assert (spec.axes["x"].unit, spec.axes["y"].unit) == ("km", "km")
    assert spec.metadata["template"]["input_hash"] == data.content_hash()
    assert "expressions" not in spec.metadata["template"]


def test_sampled_mode_unit_rules():
    data = DataSnapshot.from_columns({"x": [0.0], "y": [0.0], "u": [1.0], "v": [1.0]},
                                     units={"x": "m", "u": "m/s", "v": "km/h"})
    assert errors(VF.check(data)) == [("/data/columns/v", "mixed_units")]
    data.columns["v"].unit = "m/s"
    assert errors(VF.check(data, x_unit="km")) == [("/params/x_unit", "unit_override_conflict")]
    issues = VF.check(data, y_unit="m")  # fills the missing y unit
    assert errors(issues) == []
    spec = VF.build(data, y_unit="m")
    assert spec.datasets["field"].columns["y"].unit == "m"
    issues = VF.check(data)
    assert [(i.path, i.code, i.severity) for i in issues] == [
        ("/data/columns/y", "missing_unit", "warning")]


def test_mode_conflicts_and_missing_input():
    data = {"x": [0.0], "y": [0.0], "u": [1.0], "v": [1.0]}
    assert errors(VF.check(data, u="x", v="y")) == [("/params/u", "conflicting_parameters")]
    assert errors(VF.check()) == [("/data", "missing_data")]
    assert errors(VF.check(u="x")) == [("/params/v", "missing_parameter")]
    assert errors(VF.check({"x": [0.0, None], "y": [0.0, 1.0], "u": [1.0, 1.0],
                            "v": [1.0, 1.0]})) == [("/data/columns/x", "null_in_key_column")]


@pytest.mark.parametrize("expr, code, hint", [
    ("x^2", "invalid_expression", "Use ** for powers"),
    ("__import__('os').system('true')", "unknown_symbol", "not allowed"),
    ("x.real", "invalid_expression", "Attribute"),
    ("xx + y", "unknown_symbol", "Did you mean 'x'?"),
    ("sinn(x)", "unknown_symbol", "Did you mean 'sin'?"),
    ("9**9**9", "invalid_expression", "constant power"),
    ("sin", "invalid_expression", "must be called"),
    ("atan2(x)", "invalid_expression", "SymPy cannot interpret"),
    ("'x'", "invalid_expression", "only numbers"),
    ("x +", "invalid_expression", "cannot parse"),
])
def test_unsafe_or_bad_expressions_are_structured_errors(expr, code, hint):
    issues = VF.check(u=expr, v="y")
    assert errors(issues) == [("/params/u", code)]
    assert hint in issues[0].message
    with pytest.raises(TemplateInputError):
        VF.build(u=expr, v="y")


def test_parameter_errors():
    assert errors(VF.check(u="x", v="y", grid=1)) == [("/params/grid", "invalid_parameter")]
    assert errors(VF.check(u="x", v="y", x_range=[1, 0])) == [
        ("/params/x_range", "invalid_parameter")]
    assert errors(VF.check(u="x", v="y", role="forecast")) == [
        ("/params/role", "invalid_parameter")]
    assert errors(VF.check(u="x", v="y", constants={"x": 1})) == [
        ("/params/constants", "invalid_parameter")]
    issues = VF.check(u="x", v="y", gird=5)
    assert errors(issues) == [("/params/gird", "unknown_parameter")]
    assert "Did you mean 'grid'?" in issues[0].message


def test_matches_numpy_quiver_grid_convention():
    spec = VF.build(u="x + 10*y", v="0", x_range=[0, 1], y_range=[0, 1], grid=[2, 3])
    gx, gy = np.meshgrid(np.linspace(0, 1, 2), np.linspace(0, 1, 3))
    assert spec.datasets["field"].columns["u"].values == pytest.approx(
        (gx + 10 * gy).ravel().tolist())
