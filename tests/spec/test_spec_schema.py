import copy

import pytest
from spec_examples import EXAMPLES, timeseries_spec

from ieeVisualizer.spec import MARK_TYPES, from_dict, json_schema, to_dict

jsonschema = pytest.importorskip("jsonschema")


def _validator():
    schema = json_schema()
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_round_tripped_examples_validate_against_schema(name):
    example_spec = EXAMPLES[name]()
    d = to_dict(from_dict(to_dict(example_spec)))
    errors = sorted(_validator().iter_errors(d), key=str)
    assert errors == []


def test_schema_covers_every_mark_type():
    consts = {m["properties"]["type"]["const"]
              for m in json_schema()["definitions"]["mark"]["oneOf"]}
    assert consts == set(MARK_TYPES)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version="2.0"),
        lambda d: d["layers"][0].update(type="sankey"),
        lambda d: d["layers"][0].update(encodings={}),
        lambda d: d["axes"]["y"].update(scale="logarithmic"),
        lambda d: d["datasets"]["observed"]["columns"]["revenue"].update(dtype="money"),
    ],
)
def test_schema_rejects_structural_mistakes(mutate):
    d = to_dict(timeseries_spec())
    mutate(d)
    assert list(_validator().iter_errors(d))


def test_json_schema_returns_a_copy():
    s = json_schema()
    s["title"] = "changed"
    assert json_schema()["title"] != "changed"
    assert copy.deepcopy(json_schema()) == json_schema()
