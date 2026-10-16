"""Specs written before intervals existed keep their meaning, hash and figure.

``tests/data/spec_baseline.json`` was produced by ``baseline_specs.py`` from
a ``git archive`` of the base branch (before band/errorbar marks and interval
encodings), once per environment: every spec builder in the test helpers,
every gallery mark and legacy entry, and every template example at every
version that branch had. Each must still hash the same, and render to the
same SVG and PNG bytes.

Specs whose data is computed with numpy (gallery formulas, sampled
expressions) are built but not pinned: their float bits depend on the CPU.
In an environment the baseline did not record, literal-data specs are
compared when every recorded environment agreed, and renders are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from baseline_specs import UNSTABLE, collect, environment, fingerprint

from ieeVisualizer.spec import from_json, spec_hash, to_json

BASELINE = json.loads((Path(__file__).resolve().parent.parent / "data" /
                       "spec_baseline.json").read_text())
SPECS = collect(BASELINE["template_versions"])
RECORDED = BASELINE["environments"]
HERE = RECORDED.get(environment())
KEYS = sorted(set().union(*RECORDED.values()))


#: Specs whose data is computed with numpy/sympy (gallery formulas, sampled
#: expressions): numpy's exp/cos/... take CPU-specific SIMD paths, so their
#: last bits (and hash, and figure) depend on the machine, not on this code.
#: Seen on CI: gallery.mark-line hashed differently on two runners of one
#: environment. They are built here (so a crash still fails) but not pinned.
COMPUTED = ("gallery.", "template.vector_field_2d@")


def _expected_hash(key: str) -> str | None:
    if key.startswith(COMPUTED):
        return None
    if HERE is not None:
        return HERE[key]["spec_hash"]
    seen = {env[key]["spec_hash"] for env in RECORDED.values()}
    return seen.pop() if len(seen) == 1 else None


def test_every_baseline_spec_is_still_built():
    assert set(KEYS) <= set(SPECS), sorted(set(KEYS) - set(SPECS))
    assert len(KEYS) >= 30


@pytest.mark.parametrize("key", KEYS)
def test_spec_hash_is_unchanged(key):
    want = _expected_hash(key)
    if want is None:
        pytest.skip(f"{key}: data computed with numpy, whose bits depend on the CPU; not "
                    "pinned")
    spec = SPECS[key]
    assert spec_hash(spec) == want
    assert to_json(from_json(to_json(spec))) == to_json(spec)


@pytest.mark.skipif(HERE is None, reason=f"no baseline renders for {environment()} "
                    f"(recorded: {sorted(RECORDED)})")
@pytest.mark.parametrize("key", KEYS)
def test_figure_without_intervals_is_byte_identical(key):
    if key.startswith(COMPUTED):
        pytest.skip(f"{key}: data computed with numpy, whose bits depend on the CPU; not "
                    "pinned")
    want = {fmt: HERE[key][fmt] for fmt in ("svg", "png") if HERE[key][fmt] != UNSTABLE}
    if not want:
        pytest.skip(f"{key}: main's own renders differ between processes here")
    got = fingerprint(SPECS[key])
    assert {fmt: got[fmt] for fmt in want} == want
