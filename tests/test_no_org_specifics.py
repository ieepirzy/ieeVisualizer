"""The public library carries no organisation-specific content.

Organisation themes, metrics and wording live in the organisation's own
repository and register through the theme/template registries (see
docs/ROADMAP.md, "Brand"). This test scans the package, the tests, the
examples, skills (if present) and the spikes, as files on disk, for the names
of the organisation the library was first built for. The patterns are
assembled from parts so that this file does not match itself, and it is
skipped by path as well.

Generated or installed directories (``node_modules``, ``__pycache__``,
``examples/out``) are not part of the source and are not scanned.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("ieeVisualizer", "tests", "examples", "skills", "spikes")
SKIPPED_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
SKIPPED_PATHS = {ROOT / "examples" / "out"}
FORBIDDEN = ("muut" + "to365", "moving" + "firm")


def _files():
    for top in SCANNED:
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path == Path(__file__).resolve():
                continue
            rel = path.relative_to(ROOT)
            if SKIPPED_DIRS.intersection(rel.parts):
                continue
            if any(p == path or p in path.parents for p in SKIPPED_PATHS):
                continue
            yield path


def test_scanned_tree_is_not_empty():
    # guard against the scan silently looking at nothing
    assert any(p.suffix == ".py" for p in _files())


def test_no_organisation_names_in_code_tests_examples_or_spikes():
    hits = []
    for path in _files():
        text = path.read_bytes().lower()
        for word in FORBIDDEN:
            if word.encode() in text:
                hits.append(f"{path.relative_to(ROOT)}: {word}")
    assert hits == [], "organisation-specific content found:\n" + "\n".join(hits)
