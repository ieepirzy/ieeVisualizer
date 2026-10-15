"""Regression tests for the second round of FigureSpec core review findings.

The contract under test: a spec for which ``validate`` reports no errors
survives ``to_json``/``from_dict(to_dict(...))``, and ``validate`` returns
issues for malformed Python-built specs instead of raising.
"""

import numpy as np
import pytest

from ieeVisualizer.spec import (
    Axis,
    Column,
    DatasetRef,
    DataSnapshot,
    FigureSpec,
    LineMark,
    PatchError,
    ReferenceBand,
    ReferenceLine,
    TextAnnotation,
    apply_patch,
    from_dict,
    from_json,
    spec_hash,
    to_dict,
    to_json,
    validate,
)


def base(**axis):
    ds = DataSnapshot.from_columns({"t": ["2026-01-01", "2026-01-02"], "y": [1.0, 2.0]},
                                   dtypes={"t": "datetime"})
    return FigureSpec(datasets={"d": ds},
                      layers=[LineMark(data="d", encoding={"x": "t", "y": "y"})],
                      axes={"x": Axis(scale="time", **axis)})


def issues(spec):
    return [(i.path, i.code) for i in validate(spec) if i.severity == "error"]


def assert_round_trip_fails(spec):
    """The errors validate reported are real: serialization refuses the spec."""
    with pytest.raises(ValueError):
        from_json(to_json(spec))


# --- finding 1: column-level problems after construction ----------------------


def _set_value(spec, value):
    spec.datasets["d"].columns["y"].values[0] = value


def _append(spec, value):
    spec.datasets["d"].columns["y"].values.append(value)
    spec.datasets["d"].columns["t"].values.append(None)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: _set_value(s, "oops"), [("/datasets/d/columns/y/values", "invalid_values")]),
        (lambda s: _append(s, float("inf")),
         [("/datasets/d/columns/y/values/2", "non_finite_number")]),
        (lambda s: setattr(s.datasets["d"].columns["y"], "unit", 5),
         [("/datasets/d/columns/y/unit", "invalid_type")]),
        (lambda s: setattr(s.datasets["d"].columns["y"], "dtype", "flaot"),
         [("/datasets/d/columns/y/dtype", "invalid_value")]),
        (lambda s: setattr(s.datasets["d"].columns["y"], "values", 3),
         [("/datasets/d/columns/y/values", "invalid_type")]),
        (lambda s: s.datasets["d"].columns["t"].values.__setitem__(0, "not a date"),
         [("/datasets/d/columns/t/values", "invalid_values")]),
    ],
)
def test_validate_reports_column_problems_set_after_construction(mutate, expected):
    spec = base()
    mutate(spec)
    assert issues(spec) == expected
    assert_round_trip_fails(spec)


def test_column_of_the_wrong_class_is_reported():
    spec = base()
    spec.datasets["d"].columns["z"] = [1.0, 2.0]
    assert issues(spec) == [("/datasets/d/columns/z", "invalid_type")]


def test_nan_put_into_a_column_later_is_stored_as_null_with_a_warning():
    spec = base()
    _append(spec, float("nan"))
    (issue,) = validate(spec)
    assert (issue.path, issue.code, issue.severity) == (
        "/datasets/d/columns/y/values/2", "non_finite_number", "warning")
    again = from_json(to_json(spec))  # previously: unstructured ValueError from json
    assert again.datasets["d"].columns["y"].values == [1.0, 2.0, None]


@pytest.mark.parametrize(
    "ref, path",
    [
        (DatasetRef(kind=5, id="x"), "/datasets/r/kind"),
        (DatasetRef(kind="query", id=None), "/datasets/r/id"),
        (DatasetRef(kind="query", id="x", version=2), "/datasets/r/version"),
    ],
)
def test_validate_type_checks_dataset_ref_fields(ref, path):
    spec = base()
    spec.datasets["r"] = ref
    assert issues(spec) == [(path, "invalid_type")]
    assert_round_trip_fails(spec)


def test_values_valid_but_not_canonical_after_mutation_serialize_canonically():
    a, b = base(), base()
    a.datasets["d"].columns["y"].values[0] = 1  # int in a float column
    a.datasets["d"].columns["t"].values[1] = "2026-01-02"  # date-only spelling
    assert issues(a) == []
    assert to_json(a) == to_json(b)


# --- finding 2: date/time coordinates -----------------------------------------


def test_equal_date_limits_hash_equally():
    a = base(limits=["2026-01-01", None])
    b = base(limits=["2026-01-01T00:00:00", None])
    assert spec_hash(a) == spec_hash(b)
    assert a.axes["x"].limits == ["2026-01-01T00:00:00", None]


def test_equal_date_annotations_hash_equally_also_when_assigned_later():
    a, b = base(), base()
    a.annotations = [ReferenceLine(axis="x", value="2026-01-01T12:00Z")]
    b.annotations = [ReferenceLine(axis="x", value="2026-01-01T12:00:00+00:00")]
    assert spec_hash(a) == spec_hash(b)
    assert from_dict(to_dict(a)).annotations[0].value == "2026-01-01T12:00:00+00:00"


def test_inferred_time_axis_canonicalises_too():
    # no explicit scale: the bound column is datetime, so the renderer infers time
    a, b = base(), base()
    a.axes = {}
    b.axes = {}
    a.annotations = [ReferenceBand(axis="x", start="2026-01-01", end="2026-01-02")]
    b.annotations = [ReferenceBand(axis="x", start="2026-01-01T00:00", end="2026-01-02T00:00")]
    assert spec_hash(a) == spec_hash(b)


def test_category_axis_keeps_date_like_strings_verbatim():
    spec = base()
    spec.axes["x"] = Axis(scale="category", limits=["2026-01-01", "2026-01-02"])
    assert to_dict(spec)["axes"]["x"]["limits"] == ["2026-01-01", "2026-01-02"]


@pytest.mark.parametrize(
    "build, expected",
    [
        (lambda s: setattr(s, "axes", {"x": Axis(scale="time", limits=["banana", None])}),
         [("/axes/x/limits/0", "invalid_limits")]),
        (lambda s: setattr(s, "axes", {"x": Axis(scale="time",
                                                 limits=["2026-02-01", "2026-01-01"])}),
         [("/axes/x/limits", "invalid_limits")]),
        (lambda s: setattr(s, "axes", {"x": Axis(scale="time",
                                                 limits=["2026-01-01", "2026-01-01T00:00"])}),
         [("/axes/x/limits", "invalid_limits")]),
        (lambda s: setattr(s, "axes", {"x": Axis(scale="time",
                                                 limits=["2026-01-01T00:00Z", "2026-02-01"])}),
         [("/axes/x/limits", "invalid_limits")]),
        (lambda s: setattr(s, "axes", {"x": Axis(scale="time", limits=[5, 10])}),
         [("/axes/x/limits/0", "scale_dtype_mismatch"),
          ("/axes/x/limits/1", "scale_dtype_mismatch")]),
        (lambda s: setattr(s, "annotations", [ReferenceLine(axis="x", value="not a date")]),
         [("/annotations/0/value", "invalid_value")]),
        (lambda s: setattr(s, "annotations", [ReferenceLine(axis="x", value=3)]),
         [("/annotations/0/value", "scale_dtype_mismatch")]),
        (lambda s: setattr(s, "annotations", [
            ReferenceBand(axis="x", start="2026-02-01", end="2026-01-01")]),
         [("/annotations/0", "invalid_limits")]),
        (lambda s: setattr(s, "annotations", [TextAnnotation(text="a", x="soon", y=1.5)]),
         [("/annotations/0/x", "invalid_value")]),
        (lambda s: setattr(s, "annotations", [TextAnnotation(text="a", x="2026-01-01", y="hi")]),
         []),  # y has no explicit scale and float data: not checked
        (lambda s: s.axes.__setitem__("y", Axis(scale="linear", limits=["2026-01-01", None])),
         [("/axes/y/limits/0", "scale_dtype_mismatch")]),
    ],
)
def test_validate_checks_coordinates_against_the_axis_kind(build, expected):
    spec = base()
    build(spec)
    assert issues(spec) == expected


def test_valid_date_coordinates_validate_clean_and_are_canonical_on_construction():
    b = base(limits=["2026-01-01", "2026-01-03T12:00"])
    spec = FigureSpec(datasets=b.datasets, layers=b.layers, axes=b.axes, annotations=[
        ReferenceLine(axis="x", value="2026-01-02"),
        ReferenceBand(axis="x", start="2026-01-01T06:00", end="2026-01-01T18:00"),
        TextAnnotation(text="peak", x="2026-01-02", y=2.0),
    ])
    assert issues(spec) == []
    assert spec.annotations[0].value == "2026-01-02T00:00:00"
    assert from_json(to_json(spec)) == spec


# --- finding 3: numpy datetime64 units -----------------------------------------


@pytest.mark.parametrize(
    "values, expected",
    [
        (np.array(["2026-01-01T05", "NaT"], dtype="datetime64[h]"),
         ["2026-01-01T05:00:00", None]),
        (np.array(["2026-01-01T04"], dtype="datetime64[2h]"), ["2026-01-01T04:00:00"]),
        ([np.datetime64("2026-01-01T05", "h")], ["2026-01-01T05:00:00"]),
        (np.array(["2026-01-01T05:15"], dtype="datetime64[15m]"), ["2026-01-01T05:15:00"]),
        (np.array(["2026"], dtype="datetime64[Y]"), ["2026-01-01T00:00:00"]),
        (np.array(["2026-03"], dtype="datetime64[M]"), ["2026-03-01T00:00:00"]),
        (np.array(["2026-01-01"], dtype="datetime64[W]"), ["2026-01-01T00:00:00"]),
        (np.array(["2026-01-01T05:00:00.5"], dtype="datetime64[ms]"),
         ["2026-01-01T05:00:00.500000"]),
        (np.array([1_000_000], dtype="datetime64[ps]"), ["1970-01-01T00:00:00.000001"]),
    ],
)
def test_every_datetime64_unit_is_stored_canonically(values, expected):
    assert Column(values=values).values == expected


def test_datetime64_year_outside_four_digits_is_refused_clearly():
    with pytest.raises(ValueError, match="year outside 0001-9999"):
        Column(values=np.array(["12026-01-01"], dtype="datetime64[D]"))


# --- finding 4: malformed Python-built specs -----------------------------------


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s.datasets.__setitem__("e", {"type": "snapshot"}),
         [("/datasets/e", "invalid_type")]),
        (lambda s: s.layers.append({"type": "line"}), [("/layers/1", "invalid_type")]),
        (lambda s: s.annotations.append({"type": "text", "text": "a", "x": 1, "y": 2}),
         [("/annotations/0", "invalid_type")]),
        (lambda s: s.axes.__setitem__("y", {"scale": "log"}), [("/axes/y", "invalid_type")]),
        (lambda s: setattr(s, "layers", "line"), [("/layers", "invalid_type")]),
        (lambda s: setattr(s, "legend", {"show": True}), [("/legend", "invalid_type")]),
        (lambda s: setattr(s.datasets["d"], "provenance", None),
         [("/datasets/d/provenance", "invalid_type")]),
        (lambda s: setattr(s.layers[0], "encoding", "y"),
         [("/layers/0/encoding", "invalid_type")]),
    ],
)
def test_validate_reports_wrong_classes_instead_of_raising(mutate, expected):
    spec = base()
    mutate(spec)
    assert issues(spec) == expected


def test_layer_bound_to_malformed_dataset_is_not_checked_further():
    spec = base()
    spec.datasets["d"] = {"type": "snapshot"}
    assert issues(spec) == [("/datasets/d", "invalid_type")]


def test_encoding_shorthand_assigned_after_construction_is_validated_as_a_field():
    spec = base()
    spec.layers[0].encoding["y"] = "y"
    assert issues(spec) == []
    spec.layers[0].encoding["y"] = "nope"
    assert issues(spec) == [("/layers/0/encoding/y/field", "unknown_column")]


# --- finding 5: RFC 6901 array indices -----------------------------------------


@pytest.mark.parametrize("index", ["²", "١", "٣", "01", "+1", " 1", "1 ", "-1", "1e0", ""])
def test_patch_rejects_non_rfc6901_indices_with_a_patch_error(index):
    spec = base()
    with pytest.raises(PatchError) as e:
        apply_patch(spec, [{"op": "replace", "path": f"/layers/{index}/name", "value": "a"}])
    (issue,) = e.value.issues
    assert (issue.path, issue.code) == ("/ops/0", "invalid_pointer")


def test_patch_accepts_ascii_indices():
    spec = base()
    out = apply_patch(spec, [{"op": "add", "path": "/layers/0/name", "value": "a"}])
    assert out.layers[0].name == "a"
