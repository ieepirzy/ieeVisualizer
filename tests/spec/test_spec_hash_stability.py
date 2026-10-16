"""Specs written before intervals existed keep their meaning, hash and figure.

``tests/data/spec_baseline.json`` was produced by ``baseline_specs.py`` from
a ``git archive`` of the base branch (before band/errorbar marks and interval
encodings), once per environment: every spec builder in the test helpers,
every gallery mark and legacy entry, and every template example at every
version that branch had. Each must still hash the same, and render to the
same SVG and PNG bytes.

In an environment the baseline did not record, only hashes that were equal
in every recorded environment are compared (a template that samples with
numpy hashes numpy's output) and renders are skipped.
"""

import json
from pathlib import Path

import pytest

from ieeVisualizer.spec import from_json, spec_hash, to_json

from baseline_specs import collect, environment, fingerprint

BASELINE = json.loads((Path(__file__).resolve().parent.parent / "data" /
                       "spec_baseline.json").read_text())
SPECS = collect(BASELINE["template_versions"])
RECORDED = BASELINE["environments"]
HERE = RECORDED.get(environment())
KEYS = sorted(set().union(*RECORDED.values()))


def _expected_hash(key: str) -> str | None:
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
        pytest.skip(f"{key} hashes differently in each recorded environment")
    spec = SPECS[key]
    assert spec_hash(spec) == want
    assert to_json(from_json(to_json(spec))) == to_json(spec)


@pytest.mark.skipif(HERE is None, reason=f"no baseline renders for {environment()} "
                    f"(recorded: {sorted(RECORDED)})")
@pytest.mark.parametrize("key", KEYS)
def test_figure_without_intervals_is_byte_identical(key):
    got = fingerprint(SPECS[key])
    assert {"svg": got["svg"], "png": got["png"]} == {
        "svg": HERE[key]["svg"], "png": HERE[key]["png"]}
