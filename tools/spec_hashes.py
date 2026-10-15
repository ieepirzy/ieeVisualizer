"""Canonical-bytes baseline for every FigureSpec this repository can enumerate.

The wire format is a contract: a stored spec must serialize to the same bytes
and hash to the same ``spec_hash`` after any refactor of the model classes.
This tool builds every spec it can find without running the test suite (the
shared test examples, the renderer test examples, the gallery, which covers
every mark type, every built-in template example and the legacy bridge) and
records two things per spec.

The **exact** record:

* ``spec_hash`` (the SHA-256 of ``to_json(spec)``, i.e. of the hashing bytes),
* ``indent2``: SHA-256 of ``to_json(spec, indent=2)`` (the human form),
* ``round_trip``: ``spec_hash(from_json(to_json(spec)))`` (the parse path),
* ``content_hashes``: ``content_hash()`` of every inline dataset,
* ``issues``: the ``(path, code, severity)`` triples ``validate()`` returns.

The **portable** record: the same documents (compact bytes, indent=2 bytes,
re-serialized parse, dataset columns) with every float literal rewritten to 12
significant digits and library version strings in provenance blanked, then
hashed. Everything else -- key order, separators, whitespace, escaping, which
tokens are ints/floats/strings/nulls, every string and int -- is kept byte for
byte. It also records environment-independent consistency checks
(``spec_hash`` is the hash of ``to_json``; parsing and re-serializing gives the
same hash; ``content_hash`` is the hash of the serialized columns).

Why two: specs whose data comes from transcendental functions (``np.exp``,
``np.sin``, ``np.hypot``, the seeded normal generator, lambdified SymPy) do
not have portable bits. numpy picks a SIMD kernel at run time per CPU, and its
AVX-512 ``exp`` differs from the AVX2/baseline one by 1 ulp on some inputs, so
the same numpy wheel produces different float64 bits, and so a different
``spec_hash``, on a different machine. Those specs are listed in
:data:`PLATFORM_SENSITIVE`; their exact record is compared only when the
:func:`environment` (numpy/sympy versions, platform, libc and the CPU features
numpy dispatches on) equals one the baseline was recorded in, and otherwise
only the portable record is. Every other spec is compared exactly everywhere,
and the portable record is compared for every spec everywhere.

The JSON schema, the template catalogue and the adapter capability/request
dicts serialized from the descriptor classes are pinned exactly.

Usage::

    python tools/spec_hashes.py --compare tests/spec_hash_baseline.json
    python tools/spec_hashes.py --write OUT.json --source "<what code this is>"
    python tools/spec_hashes.py --merge OUT.json   # add this environment

``--write`` / ``--merge`` refuse to record a :data:`PLATFORM_SENSITIVE` name
that is not enumerated and, on merge, any spec outside that set whose exact
record differs between environments. ``tests/spec_hash_baseline.json`` was
recorded on the pre-migration code; its ``generated_from`` field holds the
commands. ``tests/test_spec_hash_baseline.py`` runs the comparison on every
test run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import platform
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent

#: Specs whose float data depends on the CPU / libm / numpy build. Each entry
#: names the operation; anything not listed must be bit-identical everywhere
#: (literal data, or linspace/arange/meshgrid and + - * / ** sqrt, which IEEE
#: 754 rounds correctly).
PLATFORM_SENSITIVE = {
    "gallery:mark-line": "np.exp, np.cos",
    "gallery:mark-scatter": "Generator.normal (libm log/exp)",
    "gallery:mark-area": "np.sin",
    "gallery:mark-histogram": "Generator.normal (libm log/exp)",
    "gallery:mark-heatmap": "np.exp",
    "gallery:mark-quiver": "np.hypot",
    "gallery:template-vector_field_2d": "lambdified SymPy; stamps numpy/sympy versions",
    "gallery:legacy-function-2d": "np.sin",
    "gallery:legacy-surface": "sin, cos via lambdify",
}

#: Significant digits kept by the portable record. A 1-ulp difference changes
#: the 16th-17th digit; formatting regressions (shorter repr, int-for-float)
#: change far earlier ones.
PORTABLE_DIGITS = 12


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses with string annotations need this
    spec.loader.exec_module(module)
    return module


def environment() -> dict[str, Any]:
    """What decides the bits of transcendental numpy results here."""
    import numpy as np
    import sympy

    try:
        from numpy._core._multiarray_umath import __cpu_features__
    except ImportError:  # numpy < 2
        from numpy.core._multiarray_umath import __cpu_features__
    return {
        "numpy": np.__version__,
        "sympy": sympy.__version__,
        "system": platform.system(),
        "machine": platform.machine(),
        "libc": " ".join(platform.libc_ver()),
        # NPY_DISABLE_CPU_FEATURES is reflected here, so a masked run is its
        # own environment.
        "numpy_cpu_features": sorted(k for k, v in __cpu_features__.items() if v),
    }


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


# --------------------------------------------------------------------------
# portable form: float literals to 12 significant digits, versions blanked
# --------------------------------------------------------------------------


def _blank_key(key: Any) -> bool:
    return isinstance(key, str) and key.endswith("_version") and key != "schema_version"


def _round(v: float) -> str:
    return format(v, f".{PORTABLE_DIGITS - 1}e")


def portable_text(text: str) -> str:
    """``text`` (JSON) with every float literal rewritten to
    :data:`PORTABLE_DIGITS` significant digits and every string value of a
    ``*_version`` key (other than ``schema_version``) replaced by
    ``"<version>"``. All other bytes are copied unchanged.

    A token scanner, not a regex: strings (with escapes) are consumed whole, so
    digits inside a string are never touched. :func:`portable_sha` checks the
    result against a structural rewrite of the parsed document."""
    out: list[str] = []
    i, n = 0, len(text)
    key: str | None = None  # the object key whose value comes next
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            tok, i = text[i:j + 1], j + 1
            k = i
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] == ":":
                key = json.loads(tok)
                out.append(tok)
            else:
                out.append('"<version>"' if _blank_key(key) else tok)
                key = None
        elif c == "-" or c.isdigit():
            j = i
            while j < n and text[j] in "+-0123456789.eE":
                j += 1
            tok, i = text[i:j], j
            is_float = any(ch in tok for ch in ".eE")
            out.append(_round(float(tok)) if is_float else tok)
            key = None
        else:
            if c not in " \t\r\n:":
                key = None
            out.append(c)
            i += 1
    return "".join(out)


def _portable_struct(v: Any, key: Any = None) -> Any:
    if isinstance(v, dict):
        return {k: _portable_struct(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_portable_struct(x) for x in v]
    if isinstance(v, float):
        return float(_round(v))
    if isinstance(v, str) and _blank_key(key):
        return "<version>"
    return v


def portable_sha(text: str) -> str:
    out = portable_text(text)
    # The scanner and a structural rewrite of the parsed document must agree
    # (dict equality ignores key order; the bytes themselves carry it).
    if json.loads(out) != _portable_struct(json.loads(text)):
        raise AssertionError("portable_text disagrees with the parsed document")
    return _sha(out)


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


def _canonical(obj: Any) -> str:
    # The documented canonical form, spelled out independently of the library.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _records(spec: Any) -> dict[str, Any]:
    from ieeVisualizer.spec import DataSnapshot, from_json, spec_hash, to_json, validate

    text = to_json(spec)
    indent2 = to_json(spec, indent=2)
    parsed = from_json(text)
    reserialized = to_json(parsed)
    columns = json.loads(text).get("datasets", {})
    snapshots = {k: v for k, v in sorted(spec.datasets.items()) if isinstance(v, DataSnapshot)}
    issues = [[i.path, i.code, i.severity] for i in validate(spec)]
    exact = {
        "spec_hash": spec_hash(spec),
        "to_json": _sha(text),
        "indent2": _sha(indent2),
        "round_trip": spec_hash(parsed),
        "content_hashes": {k: v.content_hash() for k, v in snapshots.items()},
        "issues": issues,
    }
    col_docs = {k: _canonical({"columns": columns[k]["columns"]}) for k in snapshots}
    portable = {
        "to_json": portable_sha(text),
        "indent2": portable_sha(indent2),
        "round_trip": portable_sha(reserialized),
        "content_hashes": {k: portable_sha(doc) for k, doc in col_docs.items()},
        "issues": issues,
        "consistent": {
            "spec_hash == sha(to_json)": exact["spec_hash"] == exact["to_json"],
            "round trip keeps the bytes": reserialized == text,
            "content_hash == sha(serialized columns)": all(
                exact["content_hashes"][k] == _sha(doc) for k, doc in col_docs.items()),
        },
    }
    return {"exact": exact, "portable": portable}


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


def collect() -> dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    specs = {name: _records(build()) for name, build in _builders().items()}
    return {"environment": environment(), "specs": specs, "descriptors": _descriptors()}


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def _matching_env(baseline: dict[str, Any], env: dict[str, Any]) -> dict[str, Any] | None:
    return next((e for e in baseline["environments"] if e["environment"] == env), None)


def modes(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, str]:
    """``{spec: "exact+portable" | "portable"}``: how :func:`compare` checks it."""
    exact_env = _matching_env(baseline, current["environment"]) is not None
    return {k: ("exact+portable" if exact_env or k not in PLATFORM_SENSITIVE else "portable")
            for k in current["specs"]}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Human-readable differences (empty list = identical)."""
    diffs = []
    old, new = baseline["descriptors"], current["descriptors"]
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            diffs.append(f"descriptors/{key}: {old.get(key)!r} != {new.get(key)!r}")
    match = _matching_env(baseline, current["environment"])
    envs = baseline["environments"]
    names = set(baseline["portable"]) | set(current["specs"])
    for key in sorted(names):
        if key not in current["specs"]:
            diffs.append(f"specs/{key}: missing now")
            continue
        if key not in baseline["portable"]:
            diffs.append(f"specs/{key}: not in the baseline")
            continue
        cur = current["specs"][key]
        if baseline["portable"][key] != cur["portable"]:
            diffs.append(f"specs/{key} (portable): {baseline['portable'][key]!r} != "
                         f"{cur['portable']!r}")
        if key in PLATFORM_SENSITIVE:
            refs = [match] if match is not None else []
        else:
            refs = envs  # recorded identical in every environment (checked on merge)
        for env in refs:
            if env["exact"][key] != cur["exact"]:
                diffs.append(f"specs/{key} (exact): {env['exact'][key]!r} != {cur['exact']!r}")
                break
    return diffs


def _check_classification(current: dict[str, Any]) -> list[str]:
    stale = sorted(set(PLATFORM_SENSITIVE) - set(current["specs"]))
    return [f"PLATFORM_SENSITIVE names a spec that is not enumerated: {k}" for k in stale]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path)
    group.add_argument("--compare", type=Path)
    group.add_argument("--merge", type=Path,
                       help="add this environment's exact records to an existing baseline")
    ap.add_argument("--source", default="", help="with --write: what code was recorded")
    args = ap.parse_args(argv)
    import ieeVisualizer

    print(f"ieeVisualizer from {Path(ieeVisualizer.__file__).parent}")
    current = collect()
    problems = _check_classification(current)
    if problems and (args.write or args.merge):
        print("\n".join(problems))
        return 1
    env_record = {"environment": current["environment"],
                  "exact": {k: v["exact"] for k, v in current["specs"].items()}}
    if args.write:
        out = {"generated_from": [args.source] if args.source else [],
               "descriptors": current["descriptors"],
               "portable": {k: v["portable"] for k, v in current["specs"].items()},
               "environments": [env_record]}
        args.write.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {len(current['specs'])} specs and {len(current['descriptors'])} "
              f"descriptor documents to {args.write}")
        return 0
    path = args.merge or args.compare
    baseline = json.loads(path.read_text(encoding="utf-8"))
    diffs = problems + compare(baseline, current)
    if args.merge:
        # Every spec outside PLATFORM_SENSITIVE must agree exactly with every
        # recorded environment (compare() checks them all), and so must the
        # portable records: otherwise the classification is wrong.
        if diffs:
            print("refusing to merge; this environment disagrees with the baseline:")
            for d in diffs:
                print("  " + d)
            return 1
        if _matching_env(baseline, current["environment"]) is not None:
            print("this environment is already recorded; nothing to merge")
            return 0
        baseline["environments"].append(env_record)
        path.write_text(json.dumps(baseline, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"merged environment {len(baseline['environments'])} into {path}")
        return 0
    m = modes(baseline, current)
    n_exact = sum(v == "exact+portable" for v in m.values())
    print(f"compared {len(current['specs'])} specs ({n_exact} exactly and portably, "
          f"{len(m) - n_exact} portably only: no recorded environment matches this one) and "
          f"{len(current['descriptors'])} descriptor documents: {len(diffs)} difference(s)")
    for d in diffs:
        print("  " + d)
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
