"""Regression tests for review findings on the FigureSpec core.

Each section names the finding it pins. Hash equality is the contract under
test throughout: specs that are equal must serialize to identical bytes.
"""

import datetime as dt
import json

import numpy as np
import pytest
from spec_examples import EXAMPLES

from ieeVisualizer.spec import (
    Axis,
    Column,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    HistogramMark,
    LineMark,
    PatchError,
    QuiverMark,
    ReferenceBand,
    ReferenceLine,
    SpecParseError,
    TextAnnotation,
    apply_patch,
    from_dict,
    from_json,
    json_schema,
    spec_hash,
    to_dict,
    to_json,
    validate,
)


def fn_spec(**kw):
    kw.setdefault("domain", {"x": [0, 1]})
    return FigureSpec(layers=[FunctionMark(expression="sin(x)", **kw)])


def fn_dict(**kw):
    layer = {"type": "function", "expression": "x", "domain": {"x": [0, 1]}}
    layer.update(kw)
    return {"schema_version": "1.0", "layers": [layer]}


def parse_issue(d):
    with pytest.raises(SpecParseError) as e:
        from_dict(d)
    (issue,) = e.value.issues
    return issue


def error_codes(spec):
    return [i.code for i in validate(spec) if i.severity == "error"]


# --- finding 2: non-finite numbers -------------------------------------------


@pytest.mark.parametrize("const", ["Infinity", "-Infinity", "NaN"])
def test_from_json_refuses_nan_and_infinity_constants(const):
    text = '{"schema_version":"1.0","metadata":{"a":CONST},"layers":[]}'.replace("CONST", const)
    with pytest.raises(SpecParseError) as e:
        from_json(text)
    assert e.value.issues[0].code == "non_finite_number"


@pytest.mark.parametrize(
    "mutate, path",
    [
        (lambda d: d["layers"][0]["domain"].update(x=[0, float("inf")]), "/layers/0/domain/x/1"),
        (lambda d: d["layers"][0].update(parameters={"a": float("nan")}),
         "/layers/0/parameters/a"),
        (lambda d: d.update(axes={"x": {"limits": [float("nan"), 1]}}), "/axes/x/limits/0"),
        (lambda d: d.update(annotations=[{"type": "reference_line", "value": float("-inf")}]),
         "/annotations/0/value"),
        (lambda d: d["layers"][0].update(style={"lw": [1, float("inf")]}),
         "/layers/0/style/lw/1"),
        (lambda d: d.update(metadata={"m": {"x": float("nan")}}), "/metadata/m/x"),
    ],
)
def test_from_dict_rejects_non_finite_numbers_with_path(mutate, path):
    d = fn_dict()
    mutate(d)
    issue = parse_issue(d)
    assert (issue.path, issue.code) == (path, "non_finite_number")


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_hist_and_quiver_numbers_reject_non_finite(bad):
    d = {"schema_version": "1.0", "layers": [{"type": "histogram", "bin_edges": [0, bad]}]}
    assert parse_issue(d).path == "/layers/0/bin_edges/1"
    d = {"schema_version": "1.0", "layers": [{"type": "quiver", "scale": bad}]}
    assert parse_issue(d).path == "/layers/0/scale"


@pytest.mark.parametrize(
    "build",
    [
        lambda: FunctionMark(expression="x", domain={"x": [0, float("inf")]}),
        lambda: FunctionMark(expression="x", domain={"x": [0, 1]}, parameters={"a": np.nan}),
        lambda: HistogramMark(bin_edges=[0.0, float("inf")]),
        lambda: QuiverMark(scale=float("nan")),
        lambda: Axis(limits=[0, float("inf")]),
        lambda: TextAnnotation(text="t", x=float("nan"), y=0),
        lambda: ReferenceLine(value=float("inf")),
        lambda: ReferenceBand(start=0, end=float("inf")),
    ],
)
def test_construction_rejects_non_finite_numbers(build):
    with pytest.raises(ValueError, match="finite"):
        build()


def test_validate_reports_non_finite_set_after_construction():
    spec = fn_spec()
    spec.layers[0].domain["x"][1] = float("inf")
    spec.metadata["bad"] = float("nan")
    issues = {(i.path, i.code) for i in validate(spec)}
    assert ("/layers/0/domain/x/1", "non_finite_number") in issues
    assert ("/metadata/bad", "non_finite_number") in issues


# --- findings 3 and 11: equal specs hash equally -----------------------------


def _int_float_pairs():
    def fn(n):
        return FigureSpec(layers=[FunctionMark(
            expression="a*x", domain={"x": [-n, n]}, parameters={"a": n}, samples=200)])

    def hist(n):
        return FigureSpec(layers=[HistogramMark(data="d", encoding={"x": "x"},
                                                bin_edges=[0, n, 2 * n])])

    def quiver(n):
        return FigureSpec(layers=[QuiverMark(scale=n)])

    def axes(n):
        return FigureSpec(axes={"x": Axis(limits=[0, n])}, layers=[LineMark()])

    def ann(n):
        return FigureSpec(layers=[LineMark()], annotations=[
            TextAnnotation(text="t", x=n, y=n), ReferenceLine(value=n),
            ReferenceBand(start=0, end=n)])

    return [fn, hist, quiver, axes, ann]


@pytest.mark.parametrize("build", _int_float_pairs())
def test_int_and_float_spellings_hash_identically(build):
    a, b = build(5), build(5.0)
    assert a == b
    assert to_json(a) == to_json(b)
    assert spec_hash(a) == spec_hash(b)


@pytest.mark.parametrize("build", _int_float_pairs())
def test_js_style_number_rewrite_keeps_the_hash(build):
    spec = build(5.0)
    js = json.loads(to_json(spec).replace("5.0", "5").replace("10.0", "10"))
    assert spec_hash(from_dict(js)) == spec_hash(spec)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_survive_js_number_rewrite(name):
    spec = EXAMPLES[name]()
    # JSON.parse + JSON.stringify in a browser writes every integral float as an int
    rewritten = json.loads(to_json(spec), parse_float=lambda s: int(float(s))
                           if float(s).is_integer() else float(s))
    assert spec_hash(from_dict(rewritten)) == spec_hash(spec)


def test_negative_zero_hashes_like_zero():
    assert Column(values=[-0.0]).values == [0.0]
    h = DataSnapshot.from_columns
    assert h({"v": [0.0]}).content_hash() == h({"v": [-0.0]}).content_hash()
    a = fn_spec(domain={"x": [-0.0, 1]})
    assert spec_hash(a) == spec_hash(fn_spec(domain={"x": [0, 1]}))


# --- findings 4 and 5: datetime columns ---------------------------------------

_INSTANT = "2026-01-01T00:00:00"


@pytest.mark.parametrize(
    "values",
    [
        [dt.datetime(2026, 1, 1)],
        [dt.date(2026, 1, 1)],
        np.array(["2026-01-01"], dtype="datetime64[D]"),
        np.array(["2026-01-01"], dtype="datetime64[s]"),
        np.array(["2026-01-01"], dtype="datetime64[ns]"),
        np.array(["2026-01"], dtype="datetime64[M]"),
        [np.datetime64("2026-01-01T00:00:00.000000000")],
    ],
)
def test_same_instant_stores_one_canonical_string(values):
    c = Column(values=values)
    assert (c.values, c.dtype) == ([_INSTANT], "datetime")


@pytest.mark.parametrize(
    "s",
    ["2026-01-01", "2026-01-01T00:00", "2026-01-01T00:00:00", "2026-01-01 00:00:00",
     "2026-01-01T00:00:00.000", "2026-01-01T00:00:00.000000000"],
)
def test_datetime_strings_are_canonicalized(s):
    assert Column(values=[s], dtype="datetime").values == [_INSTANT]


def test_datetime_canonical_form_keeps_fraction_and_offset():
    vals = ["2026-01-01T12:30:05.5", "2026-01-01T12:30:05Z", "2026-01-01T12:30:05+02:00"]
    assert Column(values=vals[:1], dtype="datetime").values == ["2026-01-01T12:30:05.500000"]
    assert Column(values=vals[1:], dtype="datetime").values == [
        "2026-01-01T12:30:05+00:00", "2026-01-01T12:30:05+02:00"]
    aware = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    assert Column(values=[aware]).values == ["2026-01-01T12:00:00-05:00"]


def test_canonical_datetimes_hash_the_same_whatever_the_input_type():
    h = lambda v: DataSnapshot.from_columns({"t": v}).content_hash()  # noqa: E731
    hashes = {h([dt.datetime(2026, 1, 1)]), h(np.array(["2026-01-01"], dtype="datetime64[D]")),
              h(np.array(["2026-01-01"], dtype="datetime64[ns]"))}
    assert len(hashes) == 1


@pytest.mark.parametrize(
    "s", ["not a date", "2026-13-45", "2026-02-30", "2026-01-01T25:00", "20260101",
          "2026-01-01T00:00:00.0000001", "2026-01-01T00:00:00+2"],
)
def test_invalid_datetime_strings_are_rejected(s):
    with pytest.raises(ValueError, match="index 0"):
        Column(values=[s], dtype="datetime")


def test_offset_with_seconds_is_rejected_since_it_could_not_be_reparsed():
    lmt = dt.timezone(dt.timedelta(hours=1, minutes=39, seconds=49))
    with pytest.raises(ValueError, match="whole number of minutes"):
        Column(values=[dt.datetime(1900, 1, 1, tzinfo=lmt)])


def test_invalid_datetime_string_is_a_parse_error_with_path():
    d = fn_dict()
    d["datasets"] = {"d": {"type": "snapshot",
                           "columns": {"t": {"dtype": "datetime", "values": ["nope"]}}}}
    issue = parse_issue(d)
    assert (issue.path, issue.code) == ("/datasets/d/columns/t/values", "invalid_values")


def test_sub_microsecond_datetime64_is_rejected_not_truncated():
    with pytest.raises(ValueError, match="microsecond"):
        Column(values=np.array(["2026-01-01T00:00:00.000000001"], dtype="datetime64[ns]"))


@pytest.mark.parametrize(
    "values",
    [
        [dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 1, 1)],
        ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00"],
    ],
)
def test_mixed_aware_and_naive_datetimes_are_rejected(values):
    with pytest.raises(ValueError, match="timezone"):
        Column(values=values, dtype="datetime")


# --- findings 6 and 7: validate agrees with the parser ------------------------


@pytest.mark.parametrize(
    "attr, value",
    [
        ("samples", 2.5), ("samples", True), ("samples", "200"),
        ("parameters", {"a": "1"}), ("parameters", {"a": True}),
        ("domain", {"x": [0, "1"]}), ("domain", {"x": [False, 1]}),
        ("variables", "x"), ("expression", 3),
    ],
)
def test_validate_flags_function_fields_that_would_not_round_trip(attr, value):
    spec = fn_spec()
    setattr(spec.layers[0], attr, value)
    assert error_codes(spec)
    with pytest.raises(SpecParseError):
        from_dict(to_dict(spec))


@pytest.mark.parametrize(
    "mark",
    [
        HistogramMark(data="d", encoding={"x": "x"}, bins=2.5),
        HistogramMark(data="d", encoding={"x": "x"}, bins=True),
        HistogramMark(data="d", encoding={"x": "x"}, density=1),
        HistogramMark(data="d", encoding={"x": "x"}, bin_edges=[0, "1"]),
        QuiverMark(data="d", encoding={"x": "x", "y": "x", "u": "x", "v": "x"}, normalize="no"),
        QuiverMark(data="d", encoding={"x": "x", "y": "x", "u": "x", "v": "x"}, scale="2"),
    ],
)
def test_validate_flags_mark_options_that_would_not_round_trip(mark):
    spec = FigureSpec(datasets={"d": DataSnapshot.from_columns({"x": [1.0, 2.0]})},
                      layers=[mark])
    assert error_codes(spec)
    with pytest.raises(SpecParseError):
        from_dict(to_dict(spec))


def test_validate_flags_legend_and_axis_types():
    spec = fn_spec()
    spec.legend.show = 1
    spec.axes["x"] = Axis()
    spec.axes["x"].limits = [0, [1]]
    paths = {i.path for i in validate(spec) if i.code == "invalid_type"}
    assert {"/legend/show", "/axes/x/limits/1"} <= paths


def test_valid_python_spec_round_trips_and_validates():
    for spec in (fn_spec(samples=200.0), fn_spec(samples=np.int64(50))):
        assert error_codes(spec) == []
        assert from_dict(to_dict(spec)) == spec


@pytest.mark.parametrize("field, value, expected", [("samples", 200.0, 200), ("samples", 3, 3)])
def test_parser_accepts_integer_valued_floats_for_integer_fields(field, value, expected):
    spec = from_dict(fn_dict(**{field: value}))
    assert getattr(spec.layers[0], field) == expected
    assert type(getattr(spec.layers[0], field)) is int


def test_histogram_bins_integer_valued_float_is_accepted_and_schema_agrees():
    d = {"schema_version": "1.0", "layers": [{"type": "histogram", "bins": 3.0}]}
    m = from_dict(d).layers[0]
    assert m.bins == 3 and type(m.bins) is int
    jsonschema = pytest.importorskip("jsonschema")
    assert list(jsonschema.Draft7Validator(json_schema()).iter_errors(d)) == []
    with pytest.raises(SpecParseError):
        from_dict({"schema_version": "1.0", "layers": [{"type": "histogram", "bins": 3.5}]})


def test_schema_rejects_invalid_datetime_strings_like_the_parser():
    jsonschema = pytest.importorskip("jsonschema")
    v = jsonschema.Draft7Validator(json_schema())
    d = fn_dict()
    col = {"dtype": "datetime", "values": ["2026-01-01", None]}
    d["datasets"] = {"d": {"type": "snapshot", "columns": {"t": col}}}
    assert list(v.iter_errors(d)) == []
    col["values"] = ["yesterday"]
    assert list(v.iter_errors(d))


# --- finding 8: patch ``test`` is type-aware -----------------------------------


def _patch_spec():
    return FigureSpec(
        datasets={"d": DataSnapshot.from_columns({"x": [1, 2], "y": [1.0, 2.0]})},
        layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})],
        metadata={"flags": [True, 0]},
    )


@pytest.mark.parametrize(
    "path, value",
    [
        ("/legend/show", 1),
        ("/datasets/d/columns/x/values/0", True),
        ("/metadata/flags", [1, False]),
        ("/metadata/flags", [True, 0, None]),
        ("/datasets/d/columns/x/values/0", "1"),
        ("/legend", {"show": True, "position": "auto", "extra": None}),
    ],
)
def test_patch_test_op_does_not_conflate_types(path, value):
    with pytest.raises(PatchError) as e:
        apply_patch(_patch_spec(), [{"op": "test", "path": path, "value": value}])
    assert e.value.issues[0].code == "test_failed"


@pytest.mark.parametrize(
    "path, value",
    [
        ("/datasets/d/columns/y/values/0", 1),  # RFC 6902: numbers compare by value
        ("/datasets/d/columns/x/values/0", 1.0),
        ("/legend/show", True),
        ("/metadata/flags", [True, 0.0]),
    ],
)
def test_patch_test_op_matches_equal_json_values(path, value):
    assert apply_patch(_patch_spec(), [{"op": "test", "path": path, "value": value}])


def test_patch_test_on_whole_document_is_type_aware():
    doc = to_dict(_patch_spec())
    doc["legend"]["show"] = 1
    with pytest.raises(PatchError):
        apply_patch(_patch_spec(), [{"op": "test", "path": "", "value": doc}])


# --- finding 10: from_dict does not alias its input ----------------------------


def test_from_dict_deep_copies_free_form_values():
    d = fn_dict(style={"dash": [1, 2]})
    d["metadata"] = {"run": {"id": "r1"}}
    d["datasets"] = {"d": {"type": "snapshot", "columns": {"x": {"values": [1.0]}},
                           "provenance": {"extra": {"scope": {"teams": ["a"]}}}}}
    spec = from_dict(d)
    h0 = spec_hash(spec)
    d["metadata"]["run"]["id"] = "r2"
    d["layers"][0]["style"]["dash"].append(9)
    d["layers"][0]["domain"]["x"][1] = 7
    d["datasets"]["d"]["provenance"]["extra"]["scope"]["teams"].append("b")
    assert spec.metadata == {"run": {"id": "r1"}}
    assert spec.layers[0].style == {"dash": [1, 2]}
    assert spec.datasets["d"].provenance.extra == {"scope": {"teams": ["a"]}}
    assert spec_hash(spec) == h0
