"""The skills mirror script lays out exactly the plugin, idempotently, and
refuses a marketplace whose plugin source would be broken in the mirror."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "mirror_skills.py"
SHA = "0123456789abcdef0123456789abcdef01234567"
TARGET = "example-org/skills-mirror"


def load():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    spec = importlib.util.spec_from_file_location("mirror_skills", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(mirror, *args: str) -> int:
    return mirror.main([*args, "--target-repo", TARGET])


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def test_mirror_tree(tmp_path):
    mirror = load()
    dest = tmp_path / "mirror"
    dest.mkdir()
    (dest / ".git").mkdir()                        # a clone's .git survives
    (dest / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (dest / "stale.txt").write_text("from an older mirror")   # deleted
    (dest / "skills").mkdir()
    (dest / "skills" / "removed-skill.md").write_text("gone")  # deleted

    assert run(mirror, "--dest", str(dest), "--sha", SHA) == 0
    files = tree(dest)

    top = {p.split("/", 1)[0] for p in files}
    assert top == {"skills", ".claude-plugin", "LICENSE", "README.md"}
    for required in ("skills/README.md", "skills/LICENSE.md", "skills/NOTICE.md",
                     "skills/LICENSES/Apache-2.0.txt", ".claude-plugin/plugin.json",
                     ".claude-plugin/marketplace.json", "LICENSE",
                     "skills/figure-is-a-claim/SKILL.md"):
        assert required in files, required
    assert "stale.txt" not in files and "skills/removed-skill.md" not in files
    assert not any("__pycache__" in p for p in files)
    assert (dest / ".git" / "HEAD").is_file()
    # Same bytes as the source for everything mirrored.
    for rel, data in files.items():
        if rel != "README.md":
            assert (ROOT / rel).read_bytes() == data, rel

    readme = files["README.md"].decode()
    assert "ieepirzy/ieeVisualizer" in readme and SHA in readme
    assert "Do not edit" in readme
    assert f"/plugin marketplace add {TARGET}" in readme
    assert mirror.check_marketplace(dest) == []

    # Idempotent: a second run leaves every file byte-identical.
    assert run(mirror, "--dest", str(dest), "--sha", SHA) == 0
    assert tree(dest) == files


def make_source(tmp_path: Path, source_path: str) -> Path:
    src = tmp_path / "src"
    shutil.copytree(ROOT / "skills", src / "skills",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / ".claude-plugin", src / ".claude-plugin")
    shutil.copy2(ROOT / "LICENSE", src / "LICENSE")
    market = src / ".claude-plugin" / "marketplace.json"
    data = json.loads(market.read_text(encoding="utf-8"))
    data["plugins"][0]["source"] = source_path
    market.write_text(json.dumps(data), encoding="utf-8")
    return src


@pytest.mark.parametrize("bad", ["./plugins/ieevisualizer-skills", "../ieeVisualizer", "skills"])
def test_broken_marketplace_source_is_refused(tmp_path, bad, capsys):
    mirror = load()
    src = make_source(tmp_path, bad)
    dest = tmp_path / "mirror"
    dest.mkdir()
    (dest / "keep.txt").write_text("untouched")
    assert run(mirror, "--source", str(src), "--dest", str(dest), "--sha", SHA) == 2
    assert "invalid marketplace" in capsys.readouterr().err
    assert (dest / "keep.txt").read_text() == "untouched"   # nothing written


def test_valid_source_in_copy_passes(tmp_path):
    """Control for the test above: the same copy with the real source works."""
    mirror = load()
    src = make_source(tmp_path, "./")
    assert run(mirror, "--source", str(src), "--dest", str(tmp_path / "m"), "--sha", SHA) == 0


def test_rejects_short_sha(tmp_path):
    mirror = load()
    assert run(mirror, "--dest", str(tmp_path / "m"), "--sha", "abc123") == 2


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def make_git_source(tmp_path: Path) -> Path:
    src = make_source(tmp_path, "./")
    git(src, "init", "-q")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "source")
    return src


def assert_refused(mirror, src: Path, dest: Path, capsys, needle: str) -> None:
    dest.mkdir(exist_ok=True)
    (dest / "keep.txt").write_text("untouched")
    assert run(mirror, "--source", str(src), "--dest", str(dest), "--sha", SHA) == 2
    err = capsys.readouterr().err
    assert needle in err, err
    assert sorted(p.name for p in dest.iterdir()) == ["keep.txt"]   # nothing written
    assert (dest / "keep.txt").read_text() == "untouched"


SYMLINK_TARGETS = {
    # A committed link to where CI writes the deploy key, and a relative one
    # that climbs out of skills/.
    "absolute": "/home/runner/work/_temp/ssh/key",
    "relative": "../../../outside-secret.txt",
}


@pytest.mark.parametrize("kind", sorted(SYMLINK_TARGETS))
def test_committed_symlink_is_refused(tmp_path, kind, capsys):
    mirror = load()
    src = make_source(tmp_path, "./")
    (tmp_path / "outside-secret.txt").write_text("SECRET")
    os.symlink(SYMLINK_TARGETS[kind], src / "skills" / "figure-is-a-claim" / "leak.md")
    git(src, "init", "-q")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "source with a symlink")
    assert_refused(mirror, src, tmp_path / "mirror", capsys, "symlink")


@pytest.mark.parametrize("kind", sorted(SYMLINK_TARGETS))
def test_symlink_in_plain_directory_is_refused(tmp_path, kind, capsys):
    """The non-git fallback walk refuses symlinks too (file and directory links)."""
    mirror = load()
    src = make_source(tmp_path, "./")
    (tmp_path / "outside-secret.txt").write_text("SECRET")
    os.symlink(SYMLINK_TARGETS[kind], src / "skills" / "figure-is-a-claim" / "leak.md")
    assert_refused(mirror, src, tmp_path / "mirror", capsys, "symlink")


def test_symlinked_directory_is_refused(tmp_path, capsys):
    mirror = load()
    src = make_source(tmp_path, "./")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "SKILL.md").write_text("SECRET")
    os.symlink(tmp_path / "outside", src / "skills" / "linked-skill")
    assert_refused(mirror, src, tmp_path / "mirror", capsys, "symlink")


def test_untracked_and_cache_files_are_not_mirrored(tmp_path):
    mirror = load()
    src = make_git_source(tmp_path)
    (src / "skills" / "figure-is-a-claim" / "draft-notes.md").write_text("untracked")
    cache = src / "skills" / "__pycache__"
    cache.mkdir()
    (cache / "x.cpython-313.pyc").write_bytes(b"\0")
    dest = tmp_path / "mirror"
    assert run(mirror, "--source", str(src), "--dest", str(dest), "--sha", SHA) == 0
    files = tree(dest)
    assert "skills/figure-is-a-claim/draft-notes.md" not in files
    assert not any("__pycache__" in p for p in files)
    assert "skills/figure-is-a-claim/SKILL.md" in files


@pytest.mark.parametrize("which", ["marketplace.json", "plugin.json"])
def test_malformed_manifest_is_a_clean_error(tmp_path, which, capsys):
    mirror = load()
    src = make_source(tmp_path, "./")
    (src / ".claude-plugin" / which).write_text('{"plugins": [', encoding="utf-8")
    assert_refused(mirror, src, tmp_path / "mirror", capsys, "not valid JSON")
