"""Every enumerable spec serializes to the bytes recorded before the model
classes moved to pydantic (``tests/spec_hash_baseline.json``, written by
``tools/spec_hashes.py --write`` on the pre-migration code).

The wire format is a contract: stored specs must keep their ``spec_hash``,
their canonical bytes, their dataset ``content_hash`` values and the issues
``validate()`` reports, and the schema / template catalogue / capability
documents serialized from descriptor classes must not move either.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location("spec_hashes", ROOT / "tools" / "spec_hashes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_enumerated_spec_matches_the_pre_migration_baseline():
    tool = _tool()
    baseline = json.loads((ROOT / "tests" / "spec_hash_baseline.json").read_text("utf-8"))
    current = tool.collect()
    assert len(current["specs"]) >= 34 and len(current["descriptors"]) >= 7
    assert tool.compare(baseline, current) == []
