#!/usr/bin/env python3
"""Lay out the skills plugin mirror: a one-way copy of this repository's
``skills/`` and ``.claude-plugin/`` (plus the licence the manifests need) into
a destination directory, with a generated README.

The destination's previous contents are deleted (everything except ``.git``),
so the result is exactly the mirror of the source commit; running it twice
with the same inputs changes nothing. The marketplace's plugin ``source``
paths are checked against the *mirror's* layout before anything is written to
the destination, and a broken one is an error (exit code 2).

Used by ``.github/workflows/mirror-skills.yml``; runnable locally::

    python tools/mirror_skills.py --dest /tmp/mirror --sha "$(git rev-parse HEAD)" \
        --target-repo OWNER/REPO

Standard library only; Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

SOURCE_REPO = "ieepirzy/ieeVisualizer"
# What the mirror contains: the plugin (manifests + skills, which carry their
# own LICENSE.md, NOTICE.md and LICENSES/) and the repository's MIT LICENSE,
# which covers the manifests and code in the skills.
MIRROR_DIRS = ("skills", ".claude-plugin")
MIRROR_FILES = ("LICENSE",)
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}


class MirrorError(Exception):
    pass


def source_files(source: Path) -> list[str]:
    """Relative POSIX paths to mirror. Git-tracked files when ``source`` is a
    git work tree (so stray local files never leak into the mirror), else a
    directory walk."""
    try:
        out = subprocess.run(
            ["git", "-C", str(source), "ls-files", "-z", "--", *MIRROR_DIRS, *MIRROR_FILES],
            check=True, capture_output=True,
        ).stdout.decode("utf-8")
        tracked = [p for p in out.split("\0") if p]
        if tracked:
            return sorted(tracked)
    except (OSError, subprocess.CalledProcessError):
        pass
    files = [f for f in MIRROR_FILES if (source / f).is_file()]
    for d in MIRROR_DIRS:
        for p in (source / d).rglob("*"):
            if p.is_file() and not SKIP_PARTS & set(p.parts):
                files.append(p.relative_to(source).as_posix())
    return sorted(files)


def readme(sha: str, target_repo: str) -> str:
    return f"""# Skills plugin marketplace (generated mirror)

**Do not edit this repository.** It is a generated, one-way mirror of
`skills/`, `.claude-plugin/` and `LICENSE` from
[{SOURCE_REPO}](https://github.com/{SOURCE_REPO}) at commit
[`{sha}`](https://github.com/{SOURCE_REPO}/commit/{sha}).
Every mirror run replaces the whole contents, so changes made here are lost;
change the source repository instead.

## Install

```text
/plugin marketplace add {target_repo}
/plugin install ieevisualizer-skills@ieevisualizer
```

## Licence

Skill text is CC BY 4.0 and code is MIT; see `skills/LICENSE.md`,
`skills/NOTICE.md`, `skills/LICENSES/` and `LICENSE`.
"""


def check_marketplace(root: Path) -> list[str]:
    """Problems with the marketplace's plugin sources, resolved against ``root``."""
    problems = []
    market_path = root / ".claude-plugin" / "marketplace.json"
    if not market_path.is_file():
        return [".claude-plugin/marketplace.json is missing"]
    market = json.loads(market_path.read_text(encoding="utf-8"))
    plugins = market.get("plugins") or []
    if not plugins:
        problems.append("marketplace.json lists no plugins")
    for entry in plugins:
        name, src = entry.get("name"), entry.get("source")
        if not isinstance(src, str):
            problems.append(f"plugin {name!r}: source {src!r} is not a relative path")
            continue
        rel = PurePosixPath(src)
        if not src.startswith("./") or rel.is_absolute() or ".." in rel.parts:
            problems.append(f"plugin {name!r}: source {src!r} must start with './' "
                            "and stay inside the marketplace root")
            continue
        plugin_root = (root / rel).resolve()
        if not plugin_root.is_dir():
            problems.append(f"plugin {name!r}: source {src!r} is not a directory in the mirror")
            continue
        manifest = plugin_root / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            problems.append(f"plugin {name!r}: {src!r} has no .claude-plugin/plugin.json")
            continue
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("name")
        if declared != name:
            problems.append(f"plugin {name!r}: plugin.json at {src!r} is named {declared!r}")
        if not any((plugin_root / "skills").glob("*/SKILL.md")):
            problems.append(f"plugin {name!r}: no skills/<name>/SKILL.md under {src!r}")
    return problems


def build(source: Path, dest: Path, sha: str, target_repo: str) -> list[str]:
    """Replace ``dest``'s contents (except ``.git``) with the mirror. Returns
    the mirrored paths. Validates a staged copy first, so a broken manifest
    never touches ``dest``."""
    files = source_files(source)
    if not any(f.startswith("skills/") for f in files):
        raise MirrorError(f"no files under skills/ in {source}")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for rel in files:
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, stage / rel)
        (stage / "README.md").write_text(readme(sha, target_repo), encoding="utf-8")
        problems = check_marketplace(stage)
        if problems:
            raise MirrorError("invalid marketplace for the mirror layout:\n  "
                              + "\n  ".join(problems))
        dest.mkdir(parents=True, exist_ok=True)
        for child in dest.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for item in stage.iterdir():
            shutil.move(str(item), str(dest / item.name))
    return [*files, "README.md"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent,
                    help="source repository root (default: this repository)")
    ap.add_argument("--dest", type=Path, required=True, help="mirror directory (may be a clone)")
    ap.add_argument("--sha", required=True, help="full source commit SHA, named in the README")
    ap.add_argument("--target-repo", required=True,
                    help="owner/name of the mirror repository, for the README's install line")
    args = ap.parse_args(argv)
    if len(args.sha) != 40 or any(c not in "0123456789abcdef" for c in args.sha):
        print(f"error: --sha must be a full 40-character lowercase commit SHA, got {args.sha!r}",
              file=sys.stderr)
        return 2
    try:
        files = build(args.source.resolve(), args.dest.resolve(), args.sha, args.target_repo)
    except MirrorError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"mirrored {len(files)} files from {SOURCE_REPO}@{args.sha} into {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
