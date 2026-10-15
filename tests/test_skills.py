"""Structural checks for the agent-skills library in ``skills/``.

Every skill is ``skills/<name>/SKILL.md`` with YAML frontmatter (``name``,
``description``, ``license``) and a ``SOURCES.md`` whose entries each state a
licence and whether the source was adapted or only cited. Adapting text is
allowed only from licences that permit it. The plugin manifests must be valid,
and the runnable ieeVisualizer examples must still run against the library.
"""

from __future__ import annotations

import codecs
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
NON_SKILL_DIRS = {"LICENSES"}

SKILL_MAX_LINES = 300
OTHER_MAX_LINES = 400
REQUIRED_KEYS = ("name", "description", "license")
ALLOWED_KEYS = set(REQUIRED_KEYS) | {"compatibility", "allowed-tools"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Licences whose terms allow adapting (paraphrasing/restructuring) text here.
ADAPTABLE = {"Apache-2.0", "MIT", "OGL-UK-3.0", "CC-BY-4.0", "CC0-1.0"}
# Adapted third-party material under these licences must be attributed in NOTICE.md
# (under the heading given here) and in the skill's closing "Adapted in part
# from ...; changed: ..." line, which must name the licence (pattern given here).
NEEDS_NOTICE = {
    "Apache-2.0": ("## Apache License 2.0", r"Apache-2\.0"),
    "MIT": ("## MIT License", r"\bMIT\b"),
    "OGL-UK-3.0": ("## Open Government Licence v3.0", r"\bOGL\b"),
    "CC-BY-4.0": ("## Creative Commons Attribution 4.0", r"CC BY 4\.0"),
    "CC0-1.0": ("## CC0 1.0", r"\bCC0\b"),
}

SKILL_DIRS = sorted(p for p in SKILLS.iterdir() if p.is_dir() and p.name not in NON_SKILL_DIRS)
SKILL_IDS = [p.name for p in SKILL_DIRS]


def parse_frontmatter(text: str) -> dict:
    """Parse the flat ``key: value`` YAML frontmatter used by SKILL.md files.

    Deliberately strict: only one-line scalars, optionally double-quoted (JSON
    string syntax, a subset of YAML's). Anything else is an error, so the files
    stay readable by every Agent Skills loader.
    """
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter is not closed with '---'")
    out: dict = {}
    for n, line in enumerate(text[4:end].splitlines(), start=2):
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*): (.+)$", line)
        if not m:
            raise ValueError(f"line {n}: expected 'key: value', got {line!r}")
        key, raw = m.group(1), m.group(2).strip()
        if key in out:
            raise ValueError(f"line {n}: duplicate key {key!r}")
        if raw.startswith('"'):
            value = json.loads(raw)
        else:
            if raw[0] in "'[{&*!|>%@`" or ": " in raw or " #" in raw:
                raise ValueError(f"line {n}: quote the value of {key!r}")
            value = raw
        out[key] = value
    return out


def parse_sources(text: str) -> list[dict]:
    """Entries are top-level bullets ``- **Title** ... <url>`` followed by
    indented ``- Licence: ...`` and ``- Use: ...`` lines."""
    entries: list[dict] = []
    for line in text.splitlines():
        if line.startswith("- "):
            entries.append({"head": line, "licence": None, "use": None})
        elif entries and line.startswith("  - Licence:"):
            entries[-1]["licence"] = line.split(":", 1)[1].strip()
        elif entries and line.startswith("  - Use:"):
            entries[-1]["use"] = line.split(":", 1)[1].strip()
    return entries


def frontmatter(skill_dir: Path) -> dict:
    return parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))


def test_library_has_skills():
    assert len(SKILL_DIRS) >= 18


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_IDS)
def test_skill_md_frontmatter(skill_dir):
    path = skill_dir / "SKILL.md"
    assert path.is_file(), f"{skill_dir.name} has no SKILL.md"
    meta = frontmatter(skill_dir)
    for key in REQUIRED_KEYS:
        assert isinstance(meta.get(key), str) and meta[key].strip(), f"missing {key}"
    assert set(meta) <= ALLOWED_KEYS, f"unexpected keys {set(meta) - ALLOWED_KEYS}"
    assert meta["name"] == skill_dir.name
    assert NAME_RE.match(meta["name"]) and len(meta["name"]) <= 64
    assert 80 <= len(meta["description"]) <= 1024
    assert "use" in meta["description"].lower(), "description should say when to use it"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_IDS)
def test_frontmatter_is_valid_yaml(skill_dir):
    yaml = pytest.importorskip("yaml")
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    block = text[4:text.find("\n---\n", 4)]
    assert yaml.safe_load(block) == parse_frontmatter(text)


def test_names_unique():
    names = [frontmatter(d)["name"] for d in SKILL_DIRS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_IDS)
def test_sources(skill_dir):
    path = skill_dir / "SOURCES.md"
    assert path.is_file(), f"{skill_dir.name} has no SOURCES.md"
    entries = parse_sources(path.read_text(encoding="utf-8"))
    assert entries, "SOURCES.md lists no sources"
    for e in entries:
        assert e["licence"], f"no licence for {e['head']!r}"
        assert re.search(r"<https?://[^>]+>", e["head"]), f"no URL for {e['head']!r}"
        assert e["use"] and re.match(r"^(adapted|cited)\b", e["use"]), e["head"]
        # Original work of this repository is not a source; unpublished or
        # private material cannot be cited in a public library.
        assert not re.search(r"same owner|unpublished|internal|private", e["head"] + e["licence"],
                             re.I), e["head"]
        if e["use"].startswith("adapted"):
            assert e["licence"] in ADAPTABLE, (
                f"{e['head']!r} is marked adapted but its licence {e['licence']!r} "
                "does not allow it; cite it instead")


def notice_sections() -> dict[str, str]:
    """NOTICE.md split into its ``## `` sections, keyed by heading line."""
    text = (SKILLS / "NOTICE.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(## .+)$", text)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def closing_attribution(body: str) -> str | None:
    """The skill's closing "Adapted in part from ...; changed: ..." paragraph."""
    m = re.search(r"(?m)^Adapted in part from (?:.+\n)*?.*changed:", body)
    return m.group(0) if m else None


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_IDS)
def test_adapted_sources_are_attributed(skill_dir):
    entries = parse_sources((skill_dir / "SOURCES.md").read_text(encoding="utf-8"))
    adapted = [e for e in entries if e["use"].startswith("adapted")]
    # Uniform: every adapted source under a licence that requires notice is
    # attributed, this repository's own MIT docs included.
    third_party = [e for e in adapted if e["licence"] in NEEDS_NOTICE]
    if not third_party:
        return
    sections = notice_sections()
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    line = closing_attribution(body)
    assert line, (f"{skill_dir.name} adapts third-party text but has no closing "
                  "'Adapted in part from ...; changed: ...' line")
    for licence in sorted({e["licence"] for e in third_party}):
        heading, pattern = NEEDS_NOTICE[licence]
        assert f"`{skill_dir.name}`" in sections.get(heading, ""), (
            f"{skill_dir.name} adapts {licence} text but is not listed under "
            f"{heading!r} in NOTICE.md")
        assert re.search(pattern, line), (
            f"the closing attribution line of {skill_dir.name} does not name {licence}")


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_IDS)
def test_referenced_files_exist(skill_dir):
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    for ref in set(re.findall(r"`(references/[\w./-]+\.md)`", body)):
        assert (skill_dir / ref).is_file(), f"{ref} is referenced but missing"
    # Backticked names ending like a skill name (e.g. `...-audit`) must be real skills.
    suffixes = tuple("-" + s.rsplit("-", 1)[1] for s in SKILL_IDS)
    for other in set(re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", body)):
        if other.endswith(suffixes):
            assert (SKILLS / other / "SKILL.md").is_file(), f"unknown skill `{other}`"


def test_line_budget():
    for path in SKILLS.rglob("*"):
        if path.is_file():
            n = len(path.read_text(encoding="utf-8").splitlines())
            limit = SKILL_MAX_LINES if path.name == "SKILL.md" else OTHER_MAX_LINES
            assert n <= limit, f"{path.relative_to(ROOT)} has {n} lines (> {limit})"


def test_readme_catalogue_matches_skills():
    readme = (SKILLS / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"\]\(([a-z0-9-]+)/SKILL\.md\)", readme))
    assert listed == set(SKILL_IDS)


# The library is public: no names, paths or metrics from private projects.
# Stored ROT13-encoded so that this file does not itself contain the terms.
# The scan covers every file this library adds (PUBLIC_PATHS below); files
# outside it, such as the package's built-in themes, are out of its scope.
# Matched case-insensitively as substrings; add to the list, never remove.
_BANNED_ROT13 = (
    "zhhggb",                    # a private company / product name
    "zbivatsvez",                # private repository names
    "ohqtrg_gbgny_pragf",        # a private schema column
    "vagreany abgr",             # private-notes framing in SOURCES.md
    "yrtnpl_qnfuobneq_nhqvg",    # a private audit document
    "nqvagry",                   # a private system name
    "zveneha",                  # a private system name
)
BANNED_TERMS = tuple(codecs.decode(t, "rot13") for t in _BANNED_ROT13)
PUBLIC_PATHS = ("skills", ".claude-plugin", ".github", "tools", "docs/SKILLS_MIRROR.md",
                "tests/test_skills.py", "tests/test_mirror_skills.py",
                "tests/test_mirror_workflow.py")
TEXT_SUFFIXES = {".md", ".json", ".txt", ".yml", ".yaml", ".py", ".sh"}


def banned_term_hits(root: Path) -> list[str]:
    """``path:line: term`` for every banned term in the public skill files."""
    hits = []
    for name in PUBLIC_PATHS:
        base = root / name
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                low = line.lower()
                hits += [f"{path.relative_to(root)}:{n}: {t}" for t in BANNED_TERMS if t in low]
    return hits


def test_no_internal_references():
    hits = banned_term_hits(ROOT)
    assert not hits, "internal references in the public skills library:\n" + "\n".join(hits)


@pytest.mark.parametrize("rel", ["skills/x/SKILL.md", ".github/workflows/w.yml",
                                 "tools/t.py", "docs/SKILLS_MIRROR.md"])
def test_banned_terms_are_detected(tmp_path, rel):
    """Guard the guard: a planted term (in any case) is found in each scope."""
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text("ok\nsee the " + BANNED_TERMS[0].upper() + " notes\n", encoding="utf-8")
    assert banned_term_hits(tmp_path) == [f"{rel}:2: {BANNED_TERMS[0]}"]


def test_licence_files_present():
    for name in ("LICENSE.md", "NOTICE.md", "LICENSES/Apache-2.0.txt",
                 "LICENSES/MIT-K-Dense.txt", "LICENSES/MIT-jamditis.txt"):
        assert (SKILLS / name).is_file(), name
    assert "Apache License" in (SKILLS / "LICENSES/Apache-2.0.txt").read_text(encoding="utf-8")


def test_plugin_manifests():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert NAME_RE.match(plugin["name"])
    assert re.match(r"^\d+\.\d+\.\d+$", plugin["version"])
    assert plugin["description"].strip()
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert NAME_RE.match(market["name"]) and market["owner"]["name"]
    (entry,) = market["plugins"]
    assert entry["name"] == plugin["name"]
    assert entry["source"] == "./"
    assert entry["version"] == plugin["version"]


PACKAGE = ROOT / "ieeVisualizer"

# Backticked identifiers in the skills that are *not* ieeVisualizer API, so are
# exempt from test_library_facts_exist. Keep each entry commented.
NOT_LIBRARY = {
    # Example data: query, column and flag names from the invented company cases.
    "weekly_paid_orders", "is_internal", "conv_rate",
    # Matplotlib colormap name, not an ieeVisualizer identifier.
    "RdBu",
    # Floating-point "not a number", not an identifier.
    "NaN",
}
# Documented in the skills as *planned* (docs/ROADMAP.md "Phase 2 onward").
# test_planned_names_not_shipped fails as soon as one appears in the package:
# then update the skills that call it planned and drop it from this set.
PLANNED = {"interval_level", "interval_kind", "y_lower", "y_upper"}

SNAKE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
CAMEL_RE = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+$")
FILE_RE = re.compile(r"^[\w./-]+\.(?:py|md|json|txt|toml)$")


def skill_texts() -> dict[Path, str]:
    """Every SKILL.md and references/*.md, keyed by path."""
    paths = [*SKILLS.glob("*/SKILL.md"), *SKILLS.glob("*/references/*.md")]
    return {p: p.read_text(encoding="utf-8") for p in sorted(paths)}


def backticked_identifiers(text: str) -> set[str]:
    """snake_case and CamelCase identifiers inside inline code spans.

    Fenced code blocks are dropped first (the runnable ones are executed by
    test_ieevisualizer_examples_run). Inside a span, every identifier token is
    considered, so `style.color_of` yields ``color_of``.
    """
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    out: set[str] = set()
    for span in re.findall(r"`([^`\n]+)`", text):
        if FILE_RE.match(span):  # a file path such as `tests/test_skills.py`
            continue
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", span):
            if SNAKE_RE.match(tok) or CAMEL_RE.match(tok):
                out.add(tok)
    return out


def package_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(PACKAGE.rglob("*.py")))


def in_source(name: str, source: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", source) is not None


def test_library_facts_exist():
    """Warning/error codes, field names and API names the skills cite in
    backticks must exist in the ieeVisualizer package, so a rename in the
    library fails here instead of silently leaving the skills wrong."""
    source = package_source()
    missing = {}
    for path, text in skill_texts().items():
        for name in backticked_identifiers(text) - NOT_LIBRARY - PLANNED:
            if not in_source(name, source):
                missing.setdefault(name, []).append(str(path.relative_to(ROOT)))
    assert not missing, f"cited in skills but absent from ieeVisualizer/: {missing}"


def test_library_facts_are_collected():
    """Guard the collector itself: a known code from each family is found."""
    found = set().union(*(backticked_identifiers(t) for t in skill_texts().values()))
    for name in ("null_values_omitted", "mixed_units", "log_scale_nonpositive",
                 "RenderResult", "SpecValidationError", "apply_patch"):
        assert name in found, name
    assert len(found - NOT_LIBRARY - PLANNED) >= 30


def test_planned_names_not_shipped():
    source = package_source()
    shipped = sorted(n for n in PLANNED if in_source(n, source))
    assert not shipped, (f"{shipped} now exist in ieeVisualizer/: update the skills "
                         "that call them planned, then remove them from PLANNED")


def test_planned_names_are_called_planned():
    """Each PLANNED name is cited somewhere, and every mention says 'planned'
    (in its table row, or in its paragraph for prose)."""
    seen = set()
    for path, text in skill_texts().items():
        for block in re.split(r"\n\s*\n", text):
            for unit in ([ln for ln in block.splitlines()]
                         if block.lstrip().startswith("|") else [block]):
                for name in PLANNED & backticked_identifiers(unit):
                    seen.add(name)
                    assert "planned" in unit.lower(), (
                        f"{path.relative_to(ROOT)} cites `{name}` without saying it is planned")
    assert seen == PLANNED, f"PLANNED names never cited: {PLANNED - seen}"


def _python_blocks(path: Path) -> list[str]:
    return re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.S)


EXAMPLES = SKILLS / "ieevisualizer-figures" / "references" / "examples.md"


def test_ieevisualizer_examples_exist():
    # Without this, losing every block would turn the test below into zero
    # parametrized cases and pass silently.
    assert len(_python_blocks(EXAMPLES)) >= 1


@pytest.mark.parametrize("index", range(len(_python_blocks(EXAMPLES))))
def test_ieevisualizer_examples_run(index):
    code = _python_blocks(EXAMPLES)[index]
    exec(compile(code, f"{EXAMPLES.name}[{index}]", "exec"), {"__name__": "skill_example"})
