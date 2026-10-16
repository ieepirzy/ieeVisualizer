"""Every FigureSpec the test suite and the gallery build, by a stable key
(imported by ``test_spec_hash_stability.py``; also runnable as a script).

``python tests/spec/baseline_specs.py OUT.json`` adds, for the running
environment (matplotlib, numpy and sympy versions), each spec's
``spec_hash`` and the SHA-256 of its SVG and PNG renders. Run against a ``git archive`` copy of the base branch it
produces ``tests/data/spec_baseline.json``: the hashes and figures the specs
had before a change, which the stability test compares against.

Template examples are built at every version the baseline registry had, so a
newer template version (whose metadata names the new version) does not hide
a change to an older one.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path

#: The checkout whose specs are collected (``BASELINE_ROOT`` points the
#: script at another copy, e.g. a ``git archive`` of the base branch).
ROOT = Path(os.environ.get("BASELINE_ROOT") or Path(__file__).resolve().parent.parent.parent)
#: Gallery template entries are built at the latest version; templates are
#: covered at every baseline version instead (see :func:`collect`).
SKIP_GALLERY_PREFIX = "template-"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _builders(module) -> dict:
    """Zero-argument (or all-defaults) public functions returning a FigureSpec."""
    from ieeVisualizer.spec import FigureSpec

    out = {}
    for name, fn in sorted(vars(module).items()):
        if name.startswith("_") or not inspect.isfunction(fn) or fn.__module__ != module.__name__:
            continue
        params = inspect.signature(fn).parameters.values()
        if any(p.default is inspect.Parameter.empty and p.kind not in (p.VAR_KEYWORD,
                                                                        p.VAR_POSITIONAL)
               for p in params):
            continue
        try:
            spec = fn()
        except Exception:  # noqa: BLE001 - a helper that is not a spec builder
            continue
        if isinstance(spec, FigureSpec):
            out[name] = spec
    return out


def collect(template_versions: dict[str, list[str]] | None = None) -> dict:
    """``{key: FigureSpec}`` for every spec builder in the test helpers, every
    gallery mark/legacy entry, and every FigureSpec template example at each
    version in ``template_versions`` (default: every registered version)."""
    from ieeVisualizer import templates
    from ieeVisualizer.spec import FigureSpec

    sys.path.insert(0, str(ROOT))
    specs = {}
    for rel, prefix in (("tests/spec/spec_examples.py", "spec_examples"),
                        ("tests/render/render_examples.py", "render_examples")):
        mod = _load(ROOT / rel, f"_baseline_{prefix}")
        for name, spec in _builders(mod).items():
            specs[f"{prefix}.{name}"] = spec
    gallery = _load(ROOT / "examples" / "gallery.py", "_baseline_gallery")
    for name, spec in gallery.all_specs().items():
        if isinstance(spec, FigureSpec) and not name.startswith(SKIP_GALLERY_PREFIX):
            specs[f"gallery.{name}"] = spec
    if template_versions is None:
        template_versions = {t["name"]: t["versions"] for t in templates.list_templates()}
    for name, versions in sorted(template_versions.items()):
        for version in versions:
            t = templates.get(name, version)
            spec = t.build_payload(t.example())
            if isinstance(spec, FigureSpec):
                specs[f"template.{name}@{version}"] = spec
    return specs


def fingerprint(spec) -> dict:
    from ieeVisualizer.render import RenderRequest, render
    from ieeVisualizer.spec import spec_hash

    out = {"spec_hash": spec_hash(spec)}
    for fmt in ("svg", "png"):
        try:
            res = render(spec, RenderRequest(format=fmt, width=4.0, height=3.0, dpi=72))
        except Exception as e:  # noqa: BLE001 - e.g. a reference without a resolver
            codes = [i.code for i in getattr(e, "issues", [])]
            out[fmt] = f"error: {type(e).__name__} {codes}"
            continue
        out[fmt] = "sha256:" + hashlib.sha256(res.content).hexdigest()
    return out


def environment() -> str:
    """What the hashes depend on: sampled templates hash numpy/sympy output,
    renders depend on matplotlib."""
    import matplotlib
    import numpy
    import sympy

    return f"matplotlib {matplotlib.__version__}, numpy {numpy.__version__}, sympy " \
           f"{sympy.__version__}"


def main(out_path: str) -> None:
    from ieeVisualizer import templates

    versions = {t["name"]: t["versions"] for t in templates.list_templates()}
    specs = collect(versions)
    path = Path(out_path)
    data = json.loads(path.read_text()) if path.exists() else {}
    data["template_versions"] = versions
    data.setdefault("environments", {})[environment()] = {
        key: fingerprint(spec) for key, spec in sorted(specs.items())}
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main(sys.argv[1])
