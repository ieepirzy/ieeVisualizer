"""Every enumerable spec serializes to the bytes recorded before the model
classes moved to pydantic (``tests/spec_hash_baseline.json``, written by
``tools/spec_hashes.py`` on the pre-migration code; its ``generated_from``
field has the commands).

The wire format is a contract: stored specs must keep their ``spec_hash``,
their canonical bytes, their dataset ``content_hash`` values and the issues
``validate()`` reports, and the schema / template catalogue / capability
documents serialized from descriptor classes must not move either.

Specs built from literal data or basic arithmetic are compared byte for byte
in every environment. Specs whose data comes from transcendental numpy
functions (``PLATFORM_SENSITIVE`` in the tool) differ in the last bit between
CPUs, so they are compared byte for byte only in a recorded environment and
otherwise through the portable record (floats to 12 significant digits, every
other byte exact); see the tool's docstring.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location("spec_hashes", ROOT / "tools" / "spec_hashes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline():
    return json.loads((ROOT / "tests" / "spec_hash_baseline.json").read_text("utf-8"))


def test_every_enumerated_spec_matches_the_pre_migration_baseline():
    tool = _tool()
    baseline = _baseline()
    current = tool.collect()
    assert len(current["specs"]) >= 34 and len(current["descriptors"]) >= 7
    assert tool.compare(baseline, current) == []
    modes = tool.modes(baseline, current)
    exact = {k for k, m in modes.items() if m == "exact+portable"}
    # Only the declared platform-sensitive specs may fall back to portable.
    assert set(modes) - exact <= set(tool.PLATFORM_SENSITIVE)
    assert len(set(modes) - set(tool.PLATFORM_SENSITIVE)) >= 25
    for record in current["specs"].values():
        assert all(record["portable"]["consistent"].values())


def test_platform_sensitive_names_exist_and_insensitive_specs_agree_everywhere():
    tool = _tool()
    baseline = _baseline()
    assert set(tool.PLATFORM_SENSITIVE) <= set(baseline["portable"])
    envs = baseline["environments"]
    assert len(envs) >= 2
    for name in set(baseline["portable"]) - set(tool.PLATFORM_SENSITIVE):
        assert len({json.dumps(e["exact"][name], sort_keys=True) for e in envs}) == 1, name


def test_portable_form_ignores_the_last_bits_and_nothing_else():
    tool = _tool()
    x = 1.0 / 3.0
    ulp = float(np.nextafter(x, 1.0))
    doc = '{"a":[X,1,"0.3","v"],"numpy_version":"2.0.2","schema_version":"1.0"}'

    def fill(template, v):
        return template.replace("X", repr(v))

    a, b = fill(doc, x), fill(doc, ulp)
    assert a != b and tool.portable_sha(a) == tool.portable_sha(b)
    assert tool.portable_text(a) == ('{"a":[3.33333333333e-01,1,"0.3","v"],'
                                     '"numpy_version":"<version>","schema_version":"1.0"}')
    for changed in (fill(doc, round(x, 6)),                 # shorter float formatting
                    fill(doc.replace("X,", "X ,"), x),      # whitespace
                    fill(doc.replace(",1,", ",1.0,"), x),   # int written as float
                    fill(doc.replace('"v"', '"w"'), x),     # a string
                    fill('{"numpy_version":"2.0.2","a":[X,1,"0.3","v"],'
                         '"schema_version":"1.0"}', x)):    # key order
        assert tool.portable_sha(changed) != tool.portable_sha(a), changed
