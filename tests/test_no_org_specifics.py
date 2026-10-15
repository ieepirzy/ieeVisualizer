"""No organisation-specific content in the public repository.

Organisation themes, metrics and wording live in the organisation's own
repository and register through the theme/template registries (see
docs/ROADMAP.md, "Brand and organisation content"). This test scans every
tracked file (``git ls-files``: code, tests, examples, spikes, README, docs,
``.github``, ``pyproject.toml``) for the names of the organisation and the
internal products the library was first built for, in any case and with or
without a separator between the parts of a compound name.

Exactly three owner-authored vision documents are allowed to name them; the
patterns are assembled from parts so that this file does not match itself.
Outside a git checkout (e.g. a ``git archive`` export) the files on disk are
scanned instead, minus generated and installed directories.
"""

import codecs
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {
    "docs/VISION_AND_ARCHITECTURE.md",
    "docs/CONVERSATIONAL_UI_AND_REPORTING.md",
    "docs/REPORTING_DECISION_LOOP.md",
}
_SEP = r"[\W_]?"
FORBIDDEN = re.compile(
    "|".join(("muut" + "to" + _SEP + "365", "mov" + "ing" + _SEP + "firm",
              "mira" + "run", "mira" + "design", "eem" + "eli")).encode(),
    re.IGNORECASE)
_NOT_SOURCE = {".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True)
    except (OSError, subprocess.CalledProcessError):
        out = None
    if out is not None and out.stdout:
        return [p for p in out.stdout.decode().split("\0") if p]
    files = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if (path.is_file() and not _NOT_SOURCE.intersection(rel.parts)
                and rel.parts[:2] != ("examples", "out")):
            files.append(rel.as_posix())
    return files


def _hits(paths) -> list[str]:
    hits = []
    for rel in sorted(paths):
        path = ROOT / rel
        if rel in ALLOWED or not path.is_file():
            continue
        for m in FORBIDDEN.finditer(path.read_bytes()):
            hits.append(f"{rel}: {m.group().decode(errors='replace')!r}")
    return hits


def test_scan_covers_the_whole_repository():
    files = set(_tracked())
    for expected in ("README.md", "pyproject.toml", "docs/ROADMAP.md",
                     "ieeVisualizer/__init__.py", "tests/test_no_org_specifics.py"):
        assert expected in files, expected
    assert ALLOWED <= files  # the allowlist names real files, nothing else


def test_pattern_catches_separator_and_case_variants():
    # rot13, so that this file does not contain the names it looks for
    for text in ("Zhhggb365", "ZHHGGB 365", "zhhggb-365", "zbivatsvez", "Zbivat-Svez",
                 "zbivat_svez", "Zveneha", "ZvenQrfvta", "Rrzryv"):
        assert FORBIDDEN.search(codecs.decode(text, "rot13").encode()), text
    assert not FORBIDDEN.search(b"moving average firmware; muutto")


def test_no_organisation_names_outside_the_allowed_vision_docs():
    hits = _hits(_tracked())
    assert hits == [], "organisation-specific content found:\n" + "\n".join(hits)
