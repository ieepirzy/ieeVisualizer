"""Canonical-bytes baseline for every FigureSpec this repository can enumerate.

The wire format is a contract: a stored spec must serialize to the same bytes
and hash to the same ``spec_hash`` after any refactor of the model classes.
This tool builds every spec it can find without running the test suite (the
shared test examples, the renderer test examples, the gallery, which covers
every mark type, every built-in template example and the legacy bridge) and
records, per spec:

* ``spec_hash`` (the SHA-256 of ``to_json(spec)``, i.e. of the hashing bytes),
* ``indent2``: SHA-256 of ``to_json(spec, indent=2)`` (the human form),
* ``round_trip``: ``spec_hash(from_json(to_json(spec)))`` (the parse path),
* ``content_hashes``: ``content_hash()`` of every inline dataset,
* ``issues``: the ``(path, code, severity)`` triples ``validate()`` returns.

It also pins the JSON schema, the template catalogue and the adapter
capability/request dicts, which are serialized from the descriptor classes.

Usage::

    python tools/spec_hashes.py --write tests/spec_hash_baseline.json
    python tools/spec_hashes.py --compare tests/spec_hash_baseline.json

``tests/test_spec_hash_baseline.py`` runs the comparison on every test run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
_ENV = ""  # set by collect(); see _env_suffix


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses with string annotations need this
    spec.loader.exec_module(module)
    return module


def _builders() -> dict[str, Callable[[], Any]]:
    """``{name: zero-argument builder}`` for every enumerable spec."""
    out: dict[str, Callable[[], Any]] = {}
    spec_examples = _load(ROOT / "tests" / "spec" / "spec_examples.py", "_baseline_spec_examples")
    for name, build in sorted(spec_examples.EXAMPLES.items()):
        out[f"spec_examples:{name}"] = build
    render = _load(ROOT / "tests" / "render" / "render_examples.py", "_baseline_render_examples")
    for name, fn in sorted(vars(render).items()):
        if not (name.endswith("_spec") and inspect.isfunction(fn)):
            continue
        params = inspect.signature(fn).parameters.values()
        if all(p.default is not p.empty or p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
               for p in params):
            out[f"render_examples:{name}"] = fn
    out["render_examples:bar_spec(horizontal)"] = lambda: render.bar_spec(horizontal=True)
    out["render_examples:polar_spec(deg)"] = lambda: render.polar_spec(unit="deg")
    gallery = _load(ROOT / "examples" / "gallery.py", "_baseline_gallery")
    specs = gallery.all_specs()  # builds templates and the legacy bridge once
    for name in sorted(specs):
        out[f"gallery:{name}"] = (lambda s=specs[name]: s)
    return out


def _record(spec: Any) -> dict[str, Any]:
    from ieeVisualizer.spec import DataSnapshot, from_json, spec_hash, to_json, validate

    text = to_json(spec)
    return {
        "spec_hash": spec_hash(spec),
        "to_json": _sha(text),
        "indent2": _sha(to_json(spec, indent=2)),
        "round_trip": spec_hash(from_json(text)),
        "content_hashes": {k: v.content_hash() for k, v in sorted(spec.datasets.items())
                           if isinstance(v, DataSnapshot)},
        "issues": [[i.path, i.code, i.severity] for i in validate(spec)],
    }


def _descriptors() -> dict[str, str]:
    """Hashes of the JSON documents serialized from descriptor classes."""
    from ieeVisualizer import templates
    from ieeVisualizer.render import RenderRequest, default_registry
    from ieeVisualizer.spec import json_schema

    out = {"json_schema": _sha(json.dumps(json_schema(), sort_keys=True))}
    out["template_catalog"] = _sha(json.dumps(templates.catalog(), sort_keys=True))
    for entry in templates.list_templates():
        t = templates.get(entry["name"])
        out[f"template:{t.name}:describe"] = _sha(json.dumps(t.describe(), sort_keys=True))
    for adapter in default_registry:
        out[f"adapter:{adapter.name}:capabilities"] = _sha(
            json.dumps(adapter.capabilities.to_dict(), sort_keys=True))
    for req in (RenderRequest(), RenderRequest(format="svg", width=800, height=600, units="px",
                                                theme="t", adapter="a")):
        out[f"request:{req.format}"] = _sha(json.dumps(req.to_dict(), sort_keys=True))
    return out


def _env_suffix(spec: Any) -> str:
    """Specs that stamp library versions into their provenance (the
    ``vector_field_2d`` template records numpy's and sympy's) hash differently
    per environment; their key carries those versions."""
    from ieeVisualizer.spec import to_json

    return _ENV if "numpy_version" in to_json(spec) else ""


def _env() -> str:
    import numpy as np
    import sympy

    return f" [numpy {np.__version__}, sympy {sympy.__version__}]"


def collect() -> dict[str, Any]:
    global _ENV
    import matplotlib

    _ENV = _env()

    matplotlib.use("Agg")
    specs = {}
    for name, build in _builders().items():
        spec = build()
        suffix = _env_suffix(spec)
        specs[name + suffix] = _record(spec)
        if suffix:  # also comparable across environments, versions blanked out
            specs[name + " [versions blanked]"] = _blanked_record(spec)
    return {"specs": specs, "descriptors": _descriptors()}


def _blanked_record(spec: Any) -> dict[str, Any]:
    from ieeVisualizer.spec import to_dict
    from ieeVisualizer.spec._common import canonical_json

    def blank(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: ("<version>" if k.endswith("_version") and k != "schema_version"
                        else blank(x)) for k, x in v.items()}
        if isinstance(v, list):
            return [blank(x) for x in v]
        return v

    d = blank(to_dict(spec))
    return {"to_json": _sha(canonical_json(d)), "indent2": _sha(canonical_json(d, indent=2))}


def _other_env(key: str) -> bool:
    return " [numpy " in key and not key.endswith(_ENV)


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Human-readable differences (empty list = identical)."""
    diffs = []
    for section in ("specs", "descriptors"):
        old, new = baseline.get(section, {}), current.get(section, {})
        for key in sorted(set(old) | set(new)):
            if _other_env(key) or (_ENV and key.endswith(_ENV) and key not in old):
                continue  # recorded per environment; this one has no baseline entry
            if key not in new:
                diffs.append(f"{section}/{key}: missing now")
            elif key not in old:
                diffs.append(f"{section}/{key}: not in the baseline")
            elif old[key] != new[key]:
                diffs.append(f"{section}/{key}: {old[key]!r} != {new[key]!r}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path)
    group.add_argument("--compare", type=Path)
    group.add_argument("--merge", type=Path,
                       help="add this environment's entries to an existing baseline")
    args = ap.parse_args(argv)
    current = collect()
    if args.merge:
        merged = json.loads(args.merge.read_text(encoding="utf-8"))
        added = [k for k in current["specs"] if k not in merged["specs"]]
        diffs = compare(merged, current)
        if [d for d in diffs if "not in the baseline" not in d]:
            print("refusing to merge; this environment disagrees with the baseline:")
            for d in diffs:
                print("  " + d)
            return 1
        for k in added:
            merged["specs"][k] = current["specs"][k]
        args.merge.write_text(json.dumps(merged, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
        print(f"merged {len(added)} environment-specific spec(s) into {args.merge}")
        return 0
    if args.write:
        args.write.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
        print(f"wrote {len(current['specs'])} specs and {len(current['descriptors'])} "
              f"descriptor documents to {args.write}")
        return 0
    diffs = compare(json.loads(args.compare.read_text(encoding="utf-8")), current)
    print(f"compared {len(current['specs'])} specs and {len(current['descriptors'])} "
          f"descriptor documents: {len(diffs)} difference(s)")
    for d in diffs:
        print("  " + d)
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
