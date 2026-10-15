"""pytest plugin: record the hash of every spec serialized during a test run.

The enumerable specs are pinned by ``tests/spec_hash_baseline.json``; this
plugin widens the net to every spec any test builds, parses, patches or
mutates. Each call of :func:`ieeVisualizer.spec.to_dict` (which ``to_json``,
``spec_hash`` and ``FigureSpec.to_dict`` go through) and of
:func:`ieeVisualizer.spec.validate` (which every render and template build
goes through) is recorded as the SHA-256 of the canonical JSON of the spec,
grouped by test id::

    SPEC_HASH_CAPTURE=before.json python -m pytest -p tools.capture_spec_hashes
    # ... change the model classes ...
    SPEC_HASH_CAPTURE=after.json python -m pytest -p tools.capture_spec_hashes
    python tools/capture_spec_hashes.py before.json after.json

The comparison reports, per test, the hashes that appear in only one run.
Run it from the repository root with the repository on ``PYTHONPATH``.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_captured: dict[str, set[str]] = {}
_current = ["<collection>"]


def _install() -> None:
    import ieeVisualizer.artifacts
    import ieeVisualizer.render
    import ieeVisualizer.spec as spec_pkg
    import ieeVisualizer.templates  # noqa: F401 - imported so the sweep below reaches it
    from ieeVisualizer.spec import serialize
    from ieeVisualizer.spec._common import sha256_of

    validate_mod = sys.modules["ieeVisualizer.spec.validate"]  # the package attr is the function

    original_to_dict = serialize.to_dict
    original_validate = validate_mod.validate

    def record(d: Any) -> None:
        try:
            digest = sha256_of(d)
        except ValueError:  # a spec that cannot be stored; to_json raises for it too
            digest = "unserializable"
        _captured.setdefault(_current[0], set()).add(digest)

    def to_dict(spec: Any) -> dict[str, Any]:
        out = original_to_dict(spec)
        record(out)
        return out

    def validate(spec: Any) -> Any:
        try:
            record(original_to_dict(spec))
        except Exception:  # noqa: BLE001 - a malformed spec; validate reports it
            pass
        return original_validate(spec)

    swaps = {id(original_to_dict): to_dict, id(original_validate): validate}
    for module in list(sys.modules.values()):
        if (getattr(module, "__name__", "") or "").startswith("ieeVisualizer"):
            for name, value in list(vars(module).items()):
                if id(value) in swaps:
                    setattr(module, name, swaps[id(value)])
    serialize.to_dict = spec_pkg.to_dict = to_dict
    validate_mod.validate = spec_pkg.validate = validate


def pytest_configure(config: Any) -> None:
    if os.environ.get("SPEC_HASH_CAPTURE"):
        _install()


def pytest_runtest_setup(item: Any) -> None:
    _current[0] = item.nodeid


def pytest_unconfigure(config: Any) -> None:
    path = os.environ.get("SPEC_HASH_CAPTURE")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in sorted(_captured.items())}, f, indent=1)


def compare(before: dict[str, list[str]], after: dict[str, list[str]]) -> list[str]:
    """Differences for every test of the ``before`` run (tests added since
    have no before-state and are not compared)."""
    diffs = []
    for test in sorted(before):
        old, new = set(before[test]), set(after.get(test, ()))
        if old != new:
            diffs.append(f"{test}: only before {sorted(old - new)}, only after "
                         f"{sorted(new - old)}")
    return diffs


if __name__ == "__main__":
    a, b = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
    diffs = compare(a, b)
    n_hashes = len({h for v in a.values() for h in v})
    n_after = len({h for k, v in b.items() if k in a for h in v})
    print(f"compared {len(a)} tests ({n_hashes} distinct spec hashes before, {n_after} after; "
          f"{len(set(b) - set(a))} new test(s) not compared): {len(diffs)} difference(s)")
    for d in diffs:
        print("  " + d)
    raise SystemExit(1 if diffs else 0)
