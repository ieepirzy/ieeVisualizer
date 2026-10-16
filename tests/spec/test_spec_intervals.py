"""Intervals in the spec: band / errorbar marks, y_lower / y_upper encodings,
and the required interval_kind / interval_level / interval_multiplier.

Specs are built from their JSON form so each test exercises the parser,
the validator and the schema exactly as an agent's JSON would."""

import copy
import json

import pytest

from ieeVisualizer.spec import (
    SpecParseError,
    apply_patch,
    from_dict,
    json_schema,
    spec_hash,
    to_dict,
    validate,
)

ROWS = {"t": [0.0, 1.0, 2.0, 3.0], "y": [1.0, 2.0, 3.0, 4.0],
        "lo": [0.5, 1.5, 2.5, 3.5], "hi": [1.5, 2.5, 3.5, 4.5]}


def spec_dict(layer: dict, units=("kg", "kg", "kg"), rows=None, **figure) -> dict:
    rows = rows or ROWS
    cols = {"t": {"unit": "d", "values": rows["t"]}}
    for name, unit in zip(("y", "lo", "hi"), units):
        cols[name] = {"values": rows[name]}
        if unit is not None:
            cols[name]["unit"] = unit
    layer = dict({"data": "d"}, **layer)
    return dict({"schema_version": "1.0", "datasets": {"d": {"type": "snapshot", "columns": cols}},
                 "layers": [layer]}, **figure)


def band(**kw) -> dict:
    return dict({"type": "band", "encoding": {"x": "t", "y_lower": "lo", "y_upper": "hi"},
                 "interval_kind": "prediction", "interval_level": 0.95}, **kw)


def on(mark: str, **kw) -> dict:
    """A line/scatter/bar/errorbar layer with a 95 % confidence interval."""
    return dict({"type": mark, "encoding": {"x": "t", "y": "y", "y_lower": "lo", "y_upper": "hi"},
                 "interval_kind": "confidence", "interval_level": 0.95}, **kw)


def errors(d: dict) -> list[tuple[str, str]]:
    return [(i.path, i.code) for i in validate(from_dict(d)) if i.severity == "error"]


@pytest.mark.parametrize("mark", ["band", "errorbar", "line", "scatter", "bar"])
def test_a_stated_interval_validates_and_round_trips(mark):
    d = spec_dict(band() if mark == "band" else on(mark))
    spec = from_dict(d)
    assert validate(spec) == []
    assert to_dict(from_dict(to_dict(spec))) == to_dict(spec)
    assert to_dict(spec)["layers"][0]["interval_kind"] == d["layers"][0]["interval_kind"]


@pytest.mark.parametrize("layer, path, code", [
    # no kind at all: on each mark that can draw an interval
    (band(interval_kind=None, interval_level=None), "/layers/0/interval_kind",
     "missing_interval_kind"),
    ({"type": "band", "encoding": {"x": "t", "y_lower": "lo", "y_upper": "hi"}},
     "/layers/0/interval_kind", "missing_interval_kind"),
    (on("errorbar", interval_kind=None), "/layers/0/interval_kind", "missing_interval_kind"),
    (on("line", interval_kind=None), "/layers/0/interval_kind", "missing_interval_kind"),
    (on("scatter", interval_kind=None), "/layers/0/interval_kind", "missing_interval_kind"),
    (on("bar", interval_kind=None), "/layers/0/interval_kind", "missing_interval_kind"),
    # a kind that is not one
    (band(interval_kind="confidense"), "/layers/0/interval_kind", "invalid_interval_kind"),
    (band(interval_kind="uncertainty"), "/layers/0/interval_kind", "invalid_interval_kind"),
    # a kind with a coverage level, without it or with a percentage
    (band(interval_kind="confidence", interval_level=None), "/layers/0/interval_level",
     "missing_interval_level"),
    (band(interval_kind="credible", interval_level=None), "/layers/0/interval_level",
     "missing_interval_level"),
    (band(interval_kind="prediction", interval_level=None), "/layers/0/interval_level",
     "missing_interval_level"),
    (band(interval_kind="quantile", interval_level=None), "/layers/0/interval_level",
     "missing_interval_level"),
    (band(interval_level=95), "/layers/0/interval_level", "invalid_interval_level"),
    (band(interval_level=0), "/layers/0/interval_level", "invalid_interval_level"),
    (band(interval_level=1), "/layers/0/interval_level", "invalid_interval_level"),
    (band(interval_level=-0.5), "/layers/0/interval_level", "invalid_interval_level"),
    # kinds without a level
    (band(interval_kind="min-max", interval_level=0.95), "/layers/0/interval_level",
     "unexpected_interval_level"),
    (band(interval_kind="standard-error", interval_level=0.68, interval_multiplier=1),
     "/layers/0/interval_level", "unexpected_interval_level"),
    # standard-error / -deviation bars: how many SE/SD
    (band(interval_kind="standard-error", interval_level=None), "/layers/0/interval_multiplier",
     "missing_interval_multiplier"),
    (band(interval_kind="standard-deviation", interval_level=None),
     "/layers/0/interval_multiplier", "missing_interval_multiplier"),
    (band(interval_kind="standard-error", interval_level=None, interval_multiplier=0),
     "/layers/0/interval_multiplier", "invalid_interval_multiplier"),
    (band(interval_multiplier=2), "/layers/0/interval_multiplier",
     "unexpected_interval_multiplier"),
    # half an interval, or a statement without one
    (dict(on("line"), encoding={"x": "t", "y": "y", "y_lower": "lo"}),
     "/layers/0/encoding/y_upper", "missing_channel"),
    ({"type": "band", "encoding": {"x": "t", "y_upper": "hi"}, "interval_kind": "min-max"},
     "/layers/0/encoding/y_lower", "missing_channel"),
    (dict(on("line"), encoding={"x": "t", "y": "y"}), "/layers/0/interval_kind",
     "interval_without_bounds"),
    # bounds that cannot bound this value
    (on("bar", orientation="horizontal", encoding={"y": "t", "x": "y", "y_lower": "lo",
                                                    "y_upper": "hi"}),
     "/layers/0/encoding/y_lower", "unsupported_channel"),
])
def test_interval_problems_are_structured_errors(layer, path, code):
    found = errors(spec_dict(layer))
    assert (path, code) in found, found


@pytest.mark.parametrize("kind, level, mult, text", [
    ("confidence", 0.95, None, "95 % confidence interval"),
    ("credible", 0.9, None, "90 % credible interval"),
    ("prediction", 0.8, None, "80 % prediction interval"),
    ("quantile", 0.5, None, "50 % quantile interval"),
    ("quantile", 0.975, None, "97.5 % quantile interval"),
    ("min-max", None, None, "min-max range"),
    ("standard-error", None, 1, "±1 standard error"),
    ("standard-deviation", None, 1.96, "±1.96 standard deviations"),
])
def test_every_kind_is_stated_in_words(kind, level, mult, text):
    from ieeVisualizer.spec import interval_label

    layer = band(interval_kind=kind, interval_level=level, interval_multiplier=mult)
    assert errors(spec_dict(layer)) == []
    assert interval_label(kind, level, mult) == text
    assert interval_label("quantile", 0.975, None, decimal=",") == "97,5 % quantile interval"


def test_inverted_row_is_an_error_at_the_value():
    rows = copy.deepcopy(ROWS)
    rows["lo"][2], rows["hi"][2] = 3.6, 3.4
    d = spec_dict(band(), rows=rows)
    (issue,) = [i for i in validate(from_dict(d)) if i.code == "interval_inverted"]
    assert issue.path == "/datasets/d/columns/lo/values/2"
    assert "3.6" in issue.message and "3.4" in issue.message
    # The pointer is where the fix goes.
    fixed = apply_patch(from_dict(d), [{"op": "replace", "path": issue.path, "value": 3.3}])
    assert validate(fixed) == []


def test_missing_bounds_are_not_errors_and_stay_null():
    rows = copy.deepcopy(ROWS)
    rows["lo"][1] = None
    rows["hi"][2] = None
    spec = from_dict(spec_dict(on("line"), rows=rows))
    assert validate(spec) == []
    assert spec.datasets["d"].columns["lo"].values == [0.5, None, 2.5, 3.5]


@pytest.mark.parametrize("units, where", [
    (("kg", "g", "kg"), "/layers/0/encoding/y_lower"),
    (("kg", "kg", "g"), "/layers/0/encoding/y_upper"),
    (("kg", None, "kg"), "/layers/0/encoding/y_lower"),  # no unit is not the value's unit
    ((None, "kg", "kg"), "/layers/0/encoding/y_lower"),
])
def test_bound_units_must_match_the_value(units, where):
    assert (where, "interval_unit_mismatch") in errors(spec_dict(on("line"), units=units))


def test_band_bounds_must_share_a_unit_and_meet_the_axis_unit():
    assert ("/layers/0/encoding/y_upper", "interval_unit_mismatch") in errors(
        spec_dict(band(), units=("kg", "kg", "g")))
    d = spec_dict(band(), axes={"y": {"unit": "g"}})
    assert ("/layers/0/encoding/y_lower", "axis_unit_conflict") in errors(d)


def test_bounds_must_be_numeric():
    d = spec_dict(band())
    d["datasets"]["d"]["columns"]["lo"] = {"dtype": "str", "values": ["a", "b", "c", "d"],
                                           "unit": "kg"}
    assert ("/layers/0/encoding/y_lower", "interval_not_numeric") in errors(d)


def test_intervals_are_two_dimensional_only():
    polar = spec_dict(on("line"), coordinate_system="polar")
    assert ("/layers/0/encoding/y_lower", "unsupported_channel") in errors(polar)
    assert ("/layers/0/type", "unsupported_in_coordinate_system") in errors(
        spec_dict(band(), coordinate_system="polar"))


def test_log_axis_bound_at_or_below_zero():
    rows = copy.deepcopy(ROWS)
    rows["lo"][0] = 0.0
    d = spec_dict(band(), rows=rows, axes={"y": {"scale": "log"}})
    assert ("/layers/0/encoding/y_lower", "log_scale_nonpositive") in errors(d)


def test_value_outside_its_interval_is_a_warning():
    rows = copy.deepcopy(ROWS)
    rows["y"][3] = 9.0
    issues = validate(from_dict(spec_dict(on("errorbar"), rows=rows)))
    assert [(i.path, i.code, i.severity) for i in issues] == [
        ("/datasets/d/columns/y/values/3", "value_outside_interval", "warning")]


def test_interval_fields_are_typed_by_the_parser():
    with pytest.raises(SpecParseError) as e:
        from_dict(spec_dict(band(interval_level="95%")))
    assert e.value.issues[0].path == "/layers/0/interval_level"
    with pytest.raises(SpecParseError):
        from_dict(spec_dict({"type": "area", "encoding": {"x": "t", "y": "y"},
                             "interval_kind": "confidence"}))  # area carries no interval


def test_unset_interval_fields_are_not_written_and_numbers_are_canonical():
    plain = spec_dict({"type": "line", "encoding": {"x": "t", "y": "y"}})
    out = to_dict(from_dict(plain))["layers"][0]
    assert not any(k.startswith("interval_") for k in out)
    a = spec_dict(band(interval_kind="standard-error", interval_level=None,
                       interval_multiplier=1))
    b = spec_dict(band(interval_kind="standard-error", interval_level=None,
                       interval_multiplier=1.0))
    assert spec_hash(from_dict(a)) == spec_hash(from_dict(b))


def test_json_schema_requires_the_statement():
    jsonschema = pytest.importorskip("jsonschema")
    v = jsonschema.Draft7Validator(json_schema())

    def ok(layer):
        d = spec_dict(layer)
        return not list(v.iter_errors(json.loads(json.dumps(d))))

    assert ok(band()) and ok(on("line")) and ok(on("bar"))
    assert ok(band(interval_kind="min-max", interval_level=None))
    assert ok(band(interval_kind="standard-error", interval_level=None, interval_multiplier=1))
    assert not ok({"type": "band", "encoding": {"x": "t", "y_lower": "lo", "y_upper": "hi"}})
    assert not ok(dict(on("line"), interval_kind=None))
    assert not ok(band(interval_level=None))
    assert not ok(band(interval_level=95))
    assert not ok(band(interval_kind="min-max", interval_level=0.5))
    assert not ok(band(interval_kind="standard-error", interval_level=None))
    assert ok({"type": "line", "encoding": {"x": "t", "y": "y"}})  # no interval, no statement
