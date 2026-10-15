"""The pydantic model layer (``ieeVisualizer.spec._model``, docs/MODELS.md).

Pinned here:

* parse errors are the library's structured issues: a ``SpecParseError``
  with one RFC-6901 pointer from the document root and the library's issue
  code, never a raw ``pydantic.ValidationError``;
* the first problem is reported in the parser's field order, per object;
* parsing is strict (no coercion), Python construction is permissive;
* constructors raise the original exception (or ``TypeError`` for bad
  arguments, as stdlib dataclasses do);
* every model class's wire keys agree with the hand-written JSON schema.
"""

import copy
import dataclasses

import pydantic
import pytest

from ieeVisualizer.artifacts.manifest import (
    DatasetRecord,
    FileEntry,
    RenderVariant,
    VisualizationArtifact,
)
from ieeVisualizer.render import Capabilities, RenderRequest, RenderResult, RenderWarning
from ieeVisualizer.spec import (
    ANNOTATION_TYPES,
    MARK_TYPES,
    Axis,
    Column,
    ColumnSchema,
    DatasetRef,
    DataSnapshot,
    Encoding,
    FigureSpec,
    Legend,
    LineMark,
    Provenance,
    SpecParseError,
    ValidationIssue,
    from_dict,
    json_schema,
)
from ieeVisualizer.spec._model import build, parse_model, wire_keys
from ieeVisualizer.templates.base import ColumnRequirement, Parameter

SPEC_CLASSES = [Axis, Column, ColumnSchema, DatasetRef, DataSnapshot, Encoding, FigureSpec,
                Legend, Provenance, *MARK_TYPES.values(), *ANNOTATION_TYPES.values()]
DESCRIPTOR_CLASSES = [ValidationIssue, RenderWarning, RenderRequest, RenderResult, Capabilities,
                      FileEntry, RenderVariant, DatasetRecord, VisualizationArtifact, Parameter,
                      ColumnRequirement]


def doc():
    return {
        "schema_version": "1.0",
        "datasets": {"d": {"type": "snapshot", "columns": {
            "t": {"values": ["2026-01-01", "2026-01-02"], "dtype": "datetime"},
            "y": {"values": [1, 2], "unit": "EUR"}}}},
        "layers": [{"type": "line", "data": "d", "encoding": {"x": "t", "y": {"field": "y"}}}],
        "axes": {"y": {"label": "Revenue", "limits": [0, 3]}},
        "annotations": [{"type": "reference_line", "value": 2}],
    }


def parse_issue(d):
    with pytest.raises(SpecParseError) as e:
        from_dict(d)
    assert not isinstance(e.value, pydantic.ValidationError)
    (issue,) = e.value.issues
    return issue.path, issue.code


def test_every_model_class_is_a_pydantic_dataclass():
    for cls in SPEC_CLASSES + DESCRIPTOR_CLASSES:
        # pydantic.dataclasses.is_pydantic_dataclass is not in every supported version
        assert dataclasses.is_dataclass(cls) and hasattr(cls, "__pydantic_validator__"), cls


def test_the_unbroken_document_parses():
    spec = from_dict(doc())
    assert spec.layers[0].encoding["x"] == Encoding("t")
    assert spec.axes["y"].limits == [0.0, 3.0]


# --- pydantic errors -> structured issues with pointers -----------------------


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["datasets"]["d"]["columns"]["y"].update(unit=5),
         ("/datasets/d/columns/y/unit", "invalid_type")),
        (lambda d: d["datasets"]["d"]["columns"]["y"].update(unti="EUR"),
         ("/datasets/d/columns/y/unti", "unknown_field")),
        (lambda d: d["datasets"]["d"]["columns"]["y"].pop("values"),
         ("/datasets/d/columns/y/values", "missing_field")),
        (lambda d: d["datasets"]["d"]["columns"]["y"].update(values=[1, "x"]),
         ("/datasets/d/columns/y/values", "invalid_values")),
        (lambda d: d["datasets"]["d"]["columns"]["y"].update(values=[1.5, 10**400]),
         ("/datasets/d/columns/y/values/1", "number_out_of_range")),
        (lambda d: d["datasets"]["d"].update(type="table"), ("/datasets/d/type",
                                                             "invalid_dataset_type")),
        (lambda d: d["datasets"].update(r={"type": "ref", "kind": "query"}),
         ("/datasets/r/id", "missing_field")),
        (lambda d: d["layers"][0]["encoding"]["y"].update(titel="Revenue"),
         ("/layers/0/encoding/y/titel", "unknown_field")),
        (lambda d: d["layers"][0]["encoding"].update(color={"title": "c"}),
         ("/layers/0/encoding/color/field", "missing_field")),
        (lambda d: d["layers"].append({"type": "sankey"}), ("/layers/1/type",
                                                           "unknown_mark_type")),
        (lambda d: d["layers"].append({"type": "function", "domain": {"x": [0]}}),
         ("/layers/1/domain/x", "invalid_domain")),
        (lambda d: d["layers"].append({"type": "function", "parameters": {"a": float("nan")}}),
         ("/layers/1/parameters/a", "non_finite_number")),
        (lambda d: d["axes"]["y"].update(limits=[0]), ("/axes/y/limits", "invalid_limits")),
        (lambda d: d["axes"]["y"].update(limits=[0, True]), ("/axes/y/limits/1", "invalid_type")),
        (lambda d: d["annotations"].append({"type": "arrow"}),
         ("/annotations/1/type", "unknown_annotation_type")),
        (lambda d: d["annotations"][0].update(value="soon", axis=3),
         ("/annotations/0/axis", "invalid_type")),
        (lambda d: d.update(legend={"show": "yes"}), ("/legend/show", "invalid_type")),
        (lambda d: d.update(metadata={"k": [1, float("inf")]}),
         ("/metadata/k/1", "non_finite_number")),
        (lambda d: d.pop("schema_version"), ("/schema_version", "missing_schema_version")),
        (lambda d: d.update(schema_version="2.0"),
         ("/schema_version", "unsupported_schema_version")),
    ],
)
def test_parse_errors_map_to_issue_codes_with_pointers(mutate, expected):
    d = doc()
    mutate(d)
    assert parse_issue(d) == expected


@pytest.mark.parametrize(
    "layer, path",
    [
        ({"samples": "5"}, "/layers/1/samples"),     # a string is not a number
        ({"samples": True}, "/layers/1/samples"),    # a bool is not an integer
        ({"samples": 5.5}, "/layers/1/samples"),     # not integral
        ({"variables": "x"}, "/layers/1/variables"),
        ({"domain": {"x": [0, "1"]}}, "/layers/1/domain/x/1"),
        ({"role": None}, "/layers/1/role"),          # null only where optional
        ({"expression": 3}, "/layers/1/expression"),
    ],
)
def test_parsing_never_coerces(layer, path):
    d = doc()
    d["layers"].append(dict({"type": "function", "expression": "x"}, **layer))
    assert parse_issue(d) == (path, "invalid_type")


def test_integer_valued_floats_and_optional_nulls_are_accepted():
    d = doc()
    d["layers"].append({"type": "function", "expression": "x", "samples": 50.0, "name": None,
                        "domain": {"x": [0, 1]}})
    m = from_dict(d).layers[1]
    assert m.samples == 50 and type(m.samples) is int and m.name is None


def test_unknown_key_message_suggests_the_field():
    d = doc()
    d["layers"][0]["encodng"] = {}
    with pytest.raises(SpecParseError) as e:
        from_dict(d)
    assert "Did you mean 'encoding'?" in e.value.issues[0].message


def test_first_problem_is_reported_in_parse_order():
    d = doc()
    d["layers"][0]["data"] = 5                       # field order: layers come first,
    d["datasets"]["d"]["columns"]["y"]["unit"] = 5   # parse order: datasets come first
    assert parse_issue(d) == ("/datasets/d/columns/y/unit", "invalid_type")
    d["caption"] = 5                                 # and caption before both
    assert parse_issue(d) == ("/caption", "invalid_type")
    d = doc()
    d["axes"]["y"].update(label=5, limits=[0])       # the axis parser reads limits first
    assert parse_issue(d) == ("/axes/y/limits", "invalid_limits")
    d = doc()
    d["datasets"]["r"] = {"type": "ref", "kind": 5, "id": "x", "columns": {"c": 3}}
    assert parse_issue(d) == ("/datasets/r/columns/c", "invalid_type")


def test_parse_model_points_relative_to_the_object():
    with pytest.raises(SpecParseError) as e:
        parse_model(Axis, {"limits": [0, "x", 1]})
    assert [(i.path, i.code) for i in e.value.issues] == [("/limits", "invalid_limits")]


def test_build_reraises_post_init_errors():
    with pytest.raises(SpecParseError) as e:  # Column declares __post_init_issue__
        build(Column, {"values": [1, "a"]})
    assert [(i.path, i.code) for i in e.value.issues] == [("/values", "invalid_values")]
    with pytest.raises(ValueError) as e:  # Axis does not: the original exception
        build(Axis, {"scale": "time", "limits": [0, float("inf")]})
    assert type(e.value) is ValueError and "axis limits" in str(e.value)


# --- construction: permissive, original exceptions, never pydantic errors ----


def test_python_construction_stays_permissive_for_validate_to_report():
    ref = DatasetRef(kind=5, id=None)
    assert (ref.kind, ref.id) == (5, None)
    layers = [LineMark(data="d")]
    spec = FigureSpec(layers=layers, metadata={"k": object})
    assert spec.layers is layers  # not copied by validation


@pytest.mark.parametrize(
    "build, exc",
    [
        (lambda: Column([1, "a"]), ValueError),
        (lambda: Column(3), TypeError),
        (lambda: Axis(limits=[0, float("inf")]), ValueError),
        (lambda: Axis(limits=[0, 10**400]), SpecParseError),
        (lambda: Encoding(), TypeError),
        (lambda: LineMark(type="bar"), TypeError),
        (lambda: Legend(True, "auto", None, "extra"), TypeError),
    ],
)
def test_constructors_raise_the_original_exception(build, exc):
    with pytest.raises(exc) as e:
        build()
    assert type(e.value) is exc  # not a wrapper, not a pydantic.ValidationError


def test_dataclass_protocols_still_work():
    spec = from_dict(doc())
    assert copy.deepcopy(spec) == spec
    assert dataclasses.replace(spec.axes["y"], label="New").label == "New"
    assert [f.name for f in dataclasses.fields(Encoding)] == ["field", "title"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ValidationIssue("", "c", "m").code = "x"


@pytest.mark.parametrize(
    "make, value",
    [
        (lambda: RenderRequest(theme=5), 5),
        (lambda: RenderWarning(1, "m"), 1),
        (lambda: FileEntry("f", "image/png", "sha256:x", "12"), "12"),
        (lambda: Parameter("p", "string", "d", allowed="ab"), "ab"),
        (lambda: ColumnRequirement("r", "p", 5, "d"), 5),
        (lambda: ValidationIssue("", "c", "m", "fatal"), "fatal"),
    ],
)
def test_descriptors_store_what_they_are_given(make, value):
    obj = make()  # as the stdlib dataclasses did: no check, no conversion
    assert value in [getattr(obj, f.name) for f in dataclasses.fields(obj)]


def test_descriptor_post_init_checks_keep_their_messages_and_python_types():
    with pytest.raises(ValueError, match="positive finite number, got '5'"):
        RenderRequest(width="5")
    assert type(RenderRequest(width=11).width) is int  # not coerced to 11.0


# --- the model classes and the hand-written JSON schema agree -----------------


def _schema_pairs():
    s = json_schema()
    defs = s["definitions"]
    props = s["properties"]
    marks = {m["properties"]["type"]["const"]: m for m in defs["mark"]["oneOf"]}
    anns = {a["properties"]["type"]["const"]: a for a in defs["annotation"]["oneOf"]}
    yield FigureSpec, s
    yield Axis, defs["axis"]
    yield Legend, props["legend"]
    yield Encoding, defs["encoding"]["oneOf"][1]
    yield DataSnapshot, defs["snapshot"]
    yield DatasetRef, defs["ref"]
    yield Provenance, defs["snapshot"]["properties"]["provenance"]
    yield Column, defs["column"]
    yield ColumnSchema, defs["ref"]["properties"]["columns"]["additionalProperties"]
    for name, cls in MARK_TYPES.items():
        yield cls, marks[name]
    for name, cls in ANNOTATION_TYPES.items():
        yield cls, anns[name]


@pytest.mark.parametrize("cls, schema", list(_schema_pairs()),
                         ids=lambda v: getattr(v, "__name__", ""))
def test_model_wire_keys_match_the_json_schema(cls, schema):
    assert set(wire_keys(cls)) == set(schema["properties"])
    assert schema.get("additionalProperties") is False
    required = set(cls.__wire__.required) | {"type"} & set(cls.__wire__.drop)
    if cls is FigureSpec:
        required.add("schema_version")  # checked before the keys, with its own code
    assert required == set(schema.get("required", ()))
