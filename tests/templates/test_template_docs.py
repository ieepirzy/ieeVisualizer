"""docs/TEMPLATES.md must stay in step with the code."""

import glob
import json
import os
import re

from ieeVisualizer import templates
from ieeVisualizer.spec import validate

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def _codes_in_source():
    codes = set()
    for path in glob.glob(os.path.join(ROOT, "ieeVisualizer", "templates", "*.py")):
        with open(path, encoding="utf-8") as f:
            s = f.read()
        codes |= set(re.findall(
            r'(?:issues\.(?:error|warn)\(|ValidationIssue\()\s*(?:[^,]+?|\w+\([^)]*\)),\s*'
            r'"([a-z]+(?:_[a-z]+)+)"', s, re.S))
        codes |= set(re.findall(r'return \("([a-z]+(?:_[a-z]+)+)"', s))
    return codes


def test_every_issue_code_is_documented():
    codes = _codes_in_source()
    assert {"unknown_parameter", "missing_column", "wrong_dtype", "duplicate_template"} <= codes
    doc = _read("docs/TEMPLATES.md")
    assert sorted(c for c in codes if f"`{c}`" not in doc) == []
    table = templates.base.__doc__
    registry_codes = {"duplicate_template", "invalid_name", "invalid_version",
                      "unknown_template", "unknown_template_version"}
    assert sorted(c for c in codes - registry_codes if c not in table) == []


def test_doc_example_payload_builds():
    blocks = re.findall(r"^```json\n(.*?)^```$", _read("docs/TEMPLATES.md"),
                        flags=re.DOTALL | re.MULTILINE)
    payload = json.loads(blocks[-1])
    spec = templates.get("vector_field_2d").build_payload(payload)
    assert validate(spec) == []
    assert all(t["name"] in _read("docs/TEMPLATES.md") for t in templates.list_templates())
