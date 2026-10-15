"""Structural checks for the agent-skills library in ``skills/``.

Every skill is ``skills/<name>/SKILL.md`` with YAML frontmatter (``name``,
``description``, ``license``) and a ``SOURCES.md`` whose entries each state a
licence and whether the source was adapted or only cited. Adapting text is
allowed only from licences that permit it. The plugin manifests must be valid,
and the runnable ieeVisualizer examples must still run against the library.
"""

from __future__ import annotations

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
ADAPTABLE = {"Apache-2.0", "MIT", "OGL-UK-3.0", "CC-BY-4.0", "CC0-1.0", "Same owner (internal)"}
# Adapted third-party material under these licences must be attributed in NOTICE.md.
NEEDS_NOTICE = {"Apache-2.0", "MIT", "OGL-UK-3.0", "CC-BY-4.0", "CC0-1.0"}

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
        if e["use"].startswith("adapted"):
            assert e["licence"] in ADAPTABLE, (
                f"{e['head']!r} is marked adapted but its licence {e['licence']!r} "
                "does not allow it; cite it instead")


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_IDS)
def test_adapted_sources_are_attributed(skill_dir):
    entries = parse_sources((skill_dir / "SOURCES.md").read_text(encoding="utf-8"))
    adapted = [e for e in entries if e["use"].startswith("adapted")]
    third_party = [e for e in adapted if e["licence"] in NEEDS_NOTICE
                   and "github.com/ieepirzy" not in e["head"]]
    notice = (SKILLS / "NOTICE.md").read_text(encoding="utf-8")
    if third_party:
        assert f"`{skill_dir.name}`" in notice, f"{skill_dir.name} missing from NOTICE.md"
    if any(e["licence"] == "Apache-2.0" for e in adapted):
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert re.search(r"Adapted in part from[^\n]*(\n[^\n]+)*?Apache-2\.0[\s\S]*?changed:",
                         body), "a skill adapting Apache-2.0 text must state that it was changed"


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


def _python_blocks(path: Path) -> list[str]:
    return re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.S)


EXAMPLES = SKILLS / "ieevisualizer-figures" / "references" / "examples.md"


@pytest.mark.parametrize("index", range(len(_python_blocks(EXAMPLES))))
def test_ieevisualizer_examples_run(index):
    code = _python_blocks(EXAMPLES)[index]
    exec(compile(code, f"{EXAMPLES.name}[{index}]", "exec"), {"__name__": "skill_example"})
