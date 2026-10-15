import json

import pytest
from spec_examples import EXAMPLES, timeseries_spec

from ieeVisualizer.spec import (
    SCHEMA_VERSION,
    FigureSpec,
    LineMark,
    SpecParseError,
    from_dict,
    from_json,
    spec_hash,
    to_dict,
    to_json,
)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_round_trip_dict_json_and_object(name):
    example_spec = EXAMPLES[name]()
    d = to_dict(example_spec)
    again = from_dict(d)
    assert again == example_spec
    assert to_dict(again) == d
    assert from_json(to_json(example_spec)) == example_spec
    assert to_json(from_json(to_json(example_spec))) == to_json(example_spec)


def test_methods_match_module_functions():
    spec = timeseries_spec()
    assert spec.to_dict() == to_dict(spec)
    assert FigureSpec.from_json(spec.to_json()) == spec


def test_canonical_json_is_independent_of_key_order():
    d = to_dict(timeseries_spec())
    reordered = json.loads(json.dumps(d), object_pairs_hook=lambda kv: dict(reversed(kv)))
    assert list(reordered) != list(d)  # really reordered
    assert to_json(from_dict(reordered)) == to_json(from_dict(d))
    assert spec_hash(from_dict(reordered)) == spec_hash(from_dict(d))


def test_canonical_json_sorted_and_compact():
    s = to_json(timeseries_spec())
    assert s == json.dumps(json.loads(s), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    assert "\n" not in s


def test_spec_hash_changes_when_a_data_value_changes():
    a = timeseries_spec()
    b = timeseries_spec()
    b.datasets["observed"].columns["revenue"].values[0] = 1201.0
    assert spec_hash(a) != spec_hash(b)


def test_defaults_are_written_explicitly_and_none_omitted():
    d = to_dict(FigureSpec(layers=[LineMark(data="d", encoding={"x": "a", "y": "b"})]))
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["layers"][0]["role"] == "observed"
    assert d["coordinate_system"] == "cartesian2d"
    assert "title" not in d and "name" not in d["layers"][0]


def test_encoding_shorthand_is_normalised():
    d = to_dict(timeseries_spec())
    d["layers"][0]["encoding"]["y"] = "revenue"
    assert to_dict(from_dict(d))["layers"][0]["encoding"]["y"] == {"field": "revenue"}


def _parse_issue(d):
    with pytest.raises(SpecParseError) as e:
        from_dict(d)
    assert len(e.value.issues) == 1
    return e.value.issues[0]


def test_missing_schema_version_is_refused():
    d = to_dict(timeseries_spec())
    del d["schema_version"]
    issue = _parse_issue(d)
    assert (issue.path, issue.code) == ("/schema_version", "missing_schema_version")
    assert '"schema_version": "1.0"' in issue.message


def test_unknown_schema_version_is_refused_clearly():
    d = to_dict(timeseries_spec())
    d["schema_version"] = "9.0"
    issue = _parse_issue(d)
    assert (issue.path, issue.code) == ("/schema_version", "unsupported_schema_version")
    assert "'9.0'" in issue.message and "not reinterpreted silently" in issue.message


def test_unknown_mark_type_suggests_the_closest():
    d = to_dict(timeseries_spec())
    d["layers"][1]["type"] = "lien"
    issue = _parse_issue(d)
    assert (issue.path, issue.code) == ("/layers/1/type", "unknown_mark_type")
    assert "Did you mean 'line'?" in issue.message


def test_unknown_field_is_refused_not_dropped():
    d = to_dict(timeseries_spec())
    d["layers"][0]["encodings"] = {}
    issue = _parse_issue(d)
    assert (issue.path, issue.code) == ("/layers/0/encodings", "unknown_field")
    assert "Did you mean 'encoding'?" in issue.message


@pytest.mark.parametrize(
    "mutate, path, code",
    [
        (lambda d: d.update(title=5), "/title", "invalid_type"),
        (lambda d: d["datasets"]["observed"].update(type="table"),
         "/datasets/observed/type", "invalid_dataset_type"),
        (lambda d: d["datasets"]["observed"]["columns"]["revenue"].update(values=[1, "x", 2, 3]),
         "/datasets/observed/columns/revenue/values", "invalid_values"),
        (lambda d: d["axes"]["y"].update(limits=[0]), "/axes/y/limits", "invalid_limits"),
        (lambda d: d["annotations"].append({"type": "arrow"}), "/annotations/2/type",
         "unknown_annotation_type"),
    ],
)
def test_structural_errors_carry_paths(mutate, path, code):
    d = to_dict(timeseries_spec())
    mutate(d)
    issue = _parse_issue(d)
    assert (issue.path, issue.code) == (path, code)


def test_invalid_json_text():
    with pytest.raises(SpecParseError) as e:
        from_json("{not json")
    assert e.value.issues[0].code == "invalid_json"


def test_nan_cannot_leak_into_json():
    spec = timeseries_spec()
    spec.metadata["bad"] = float("nan")
    with pytest.raises(ValueError):
        to_json(spec)
