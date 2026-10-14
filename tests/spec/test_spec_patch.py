import pytest
from spec_examples import quiver_spec, timeseries_spec

from ieeVisualizer.spec import (
    LineMark,
    PatchError,
    SpecParseError,
    SpecValidationError,
    apply_patch,
    to_dict,
    validate,
)


def test_replace_returns_new_spec_and_leaves_input_untouched():
    spec = timeseries_spec()
    before = to_dict(spec)
    new = apply_patch(spec, [{"op": "replace", "path": "/title", "value": "Revenue (EUR)"},
                             {"op": "add", "path": "/axes/y/scale", "value": "symlog"}])
    assert new.title == "Revenue (EUR)" and new.axes["y"].scale == "symlog"
    assert to_dict(spec) == before
    assert new is not spec and new.datasets["observed"] is not spec.datasets["observed"]


def test_value_objects_are_copied_not_aliased():
    spec = timeseries_spec()
    value = {"type": "reference_line", "axis": "y", "value": 1000}
    new = apply_patch(spec, [{"op": "add", "path": "/annotations/-", "value": value}])
    value["value"] = -1
    assert new.annotations[-1].value == 1000


def test_add_append_insert_and_remove():
    spec = timeseries_spec()
    layer = to_dict(spec)["layers"][0]
    layer["name"] = "Observed copy"
    new = apply_patch(spec, [
        {"op": "add", "path": "/layers/0", "value": layer},
        {"op": "remove", "path": "/layers/2"},
        {"op": "add", "path": "/metadata/report_run", "value": "2026-W42"},
        {"op": "remove", "path": "/caption"},
    ])
    assert [m.name for m in new.layers] == ["Observed copy", "Observed"]
    assert new.metadata["report_run"] == "2026-W42"
    assert new.caption is None
    assert isinstance(new.layers[0], LineMark)


def test_pointer_escapes():
    spec = timeseries_spec()
    new = apply_patch(spec, [{"op": "add", "path": "/metadata/a~1b~0c", "value": 1}])
    assert new.metadata == dict(spec.metadata, **{"a/b~c": 1})


def test_test_op_guards_concurrent_edits():
    spec = timeseries_spec()
    ok = apply_patch(spec, [{"op": "test", "path": "/title", "value": "Weekly revenue"},
                            {"op": "replace", "path": "/title", "value": "x"}])
    assert ok.title == "x"
    with pytest.raises(PatchError) as e:
        apply_patch(spec, [{"op": "test", "path": "/title", "value": "Something else"}])
    assert (e.value.issues[0].path, e.value.issues[0].code) == ("/ops/0", "test_failed")


@pytest.mark.parametrize(
    "ops, code",
    [
        ([{"op": "replace", "path": "/nope", "value": 1}], "path_not_found"),
        ([{"op": "remove", "path": "/layers/7"}], "path_not_found"),
        ([{"op": "add", "path": "title", "value": 1}], "invalid_pointer"),
        ([{"op": "move", "path": "/title", "from": "/caption"}], "invalid_op"),
        ([{"op": "replace", "path": "/title"}], "invalid_op"),
        ([{"op": "replace", "path": "/layers/01", "value": {}}], "invalid_pointer"),
        ({"op": "replace"}, "invalid_op"),
    ],
)
def test_bad_ops_raise_patch_error_with_op_index(ops, code):
    spec = timeseries_spec()
    before = to_dict(spec)
    with pytest.raises(PatchError) as e:
        apply_patch(spec, ops)
    assert e.value.issues[0].code == code
    assert e.value.issues[0].path.startswith("/ops")
    assert to_dict(spec) == before


def test_atomic_failure_in_later_op_leaves_input_untouched():
    spec = timeseries_spec()
    before = to_dict(spec)
    with pytest.raises(PatchError) as e:
        apply_patch(spec, [{"op": "replace", "path": "/title", "value": "x"},
                           {"op": "remove", "path": "/missing"}])
    assert e.value.issues[0].path == "/ops/1"
    assert to_dict(spec) == before


def test_result_is_revalidated():
    spec = quiver_spec()
    with pytest.raises(SpecValidationError) as e:
        apply_patch(spec, [{"op": "remove", "path": "/layers/0/encoding/u"}])
    assert [(i.path, i.code) for i in e.value.issues] == [
        ("/layers/0/encoding/u", "missing_channel")]


def test_check_false_allows_intermediate_invalid_state():
    spec = quiver_spec()
    new = apply_patch(spec, [{"op": "remove", "path": "/layers/0/encoding/u"}], check=False)
    assert [i.code for i in validate(new)] == ["missing_channel"]


def test_result_is_reparsed():
    with pytest.raises(SpecParseError) as e:
        apply_patch(timeseries_spec(), [{"op": "replace", "path": "/layers/0/type",
                                         "value": "lines"}])
    assert e.value.issues[0].code == "unknown_mark_type"


def test_fixing_a_reported_issue_by_its_path():
    spec = timeseries_spec()
    spec.layers[1].encoding["y"].field = "revenu"
    (issue,) = validate(spec)
    fixed = apply_patch(spec, [{"op": "replace", "path": issue.path, "value": "revenue"}])
    assert validate(fixed) == []


def test_nested_free_form_values_are_not_aliased():
    spec = timeseries_spec()
    spec.metadata["template"] = {"name": "timeseries", "version": "1"}
    spec.layers[0].style["dash"] = [4, 2]
    new = apply_patch(spec, [
        {"op": "replace", "path": "/metadata/template/version", "value": "2"},
        {"op": "replace", "path": "/layers/0/style/dash/0", "value": 9},
    ])
    assert new.metadata["template"]["version"] == "2" and new.layers[0].style["dash"] == [9, 2]
    assert spec.metadata["template"]["version"] == "1"
    assert spec.layers[0].style["dash"] == [4, 2]
