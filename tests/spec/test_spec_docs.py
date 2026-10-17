"""docs/SPEC.md examples must stay valid specs."""

import json
import os
import re

import pytest

from ieeVisualizer.spec import from_json, json_schema, to_dict, validate

DOC = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "SPEC.md")


def _blocks():
    with open(DOC, encoding="utf-8") as f:
        text = f.read()
    return re.findall(r"^```json\n(.*?)^```$", text, flags=re.DOTALL | re.MULTILINE)


def test_doc_has_both_examples():
    types = sorted({layer["type"] for b in _blocks() for layer in json.loads(b)["layers"]})
    assert types == ["band", "line", "quiver"]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_doc_example_parses_validates_and_matches_schema(index):
    raw = _blocks()[index]
    spec = from_json(raw)
    assert validate(spec) == []
    jsonschema = pytest.importorskip("jsonschema")
    v = jsonschema.Draft7Validator(json_schema())
    assert list(v.iter_errors(json.loads(raw))) == []  # hand-written form
    assert list(v.iter_errors(to_dict(spec))) == []  # canonical form


def test_doc_timeseries_keeps_its_gap_as_null():
    spec = from_json(_blocks()[0])
    assert spec.datasets["observed"].columns["revenue"].values == [1200.0, None, 1350.5]
