"""Structure and step behaviour of .github/workflows/mirror-skills.yml.

The shell steps that guard the push are executed here with stub commands, so
their failure modes are tested, not just their text.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "mirror-skills.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
BASH = shutil.which("bash")


def load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps(job: str) -> list[dict]:
    return load()["jobs"][job]["steps"]


def step(job: str, name: str) -> dict:
    (found,) = [s for s in steps(job) if s.get("name") == name]
    return found


def test_actions_pinned_to_full_sha():
    for job in load()["jobs"].values():
        for s in job["steps"]:
            if "uses" in s:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", s["uses"]), s["uses"]


def test_token_is_read_only():
    assert load()["permissions"] == {"contents": "read"}


def test_mirror_job_uses_protected_environment():
    env = load()["jobs"]["mirror"].get("environment")
    name = env.get("name") if isinstance(env, dict) else env
    assert name == "skills-mirror"


def test_target_comes_from_a_repository_variable():
    assert load()["jobs"]["mirror"]["env"]["TARGET_REPO"] == "${{ vars.SKILLS_MIRROR_TARGET }}"
    assert "TARGET_REPO" not in (load().get("env") or {})


def test_dry_run_never_touches_the_secret():
    dry = WORKFLOW.read_text(encoding="utf-8")
    job = yaml.safe_dump(load()["jobs"]["dry-run"])
    assert "secrets." not in job and "ssh" not in job.lower()
    assert "secrets.SKILLS_MIRROR_DEPLOY_KEY" in dry          # used by the mirror job only


def test_mirror_is_built_before_the_key_exists():
    """The key is written only after mirror_skills.py has run, and never in a
    step that runs it, so nothing the script copies can be the key."""
    names = [s.get("name", s.get("uses", "")) for s in steps("mirror")]
    build = [i for i, s in enumerate(steps("mirror")) if "mirror_skills.py" in s.get("run", "")]
    key = [i for i, s in enumerate(steps("mirror")) if "ssh/key" in s.get("run", "")
           and "rm -rf" not in s.get("run", "")]
    assert build and key, names
    assert max(build) < min(key), names
    for i in build:
        assert "secrets." not in yaml.safe_dump(steps("mirror")[i]), names[i]


def run_step(script: str, env: dict, tmp_path: Path, stubs: dict[str, str]) -> tuple[int, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in stubs.items():
        p = bindir / name
        p.write_text("#!/usr/bin/env bash\n" + body + "\n")
        p.chmod(0o755)
    out = tmp_path / "github_output"
    out.write_text("")
    full = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "GITHUB_OUTPUT": str(out),
            "GITHUB_REPOSITORY": "owner/source", "GITHUB_SHA": SHA, **env}
    proc = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", script], env=full,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr + "\nOUTPUT:" + out.read_text()


TIP = "Skip unless this commit is still the tip of main"


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("case, git_stub, expect_rc, expect_skip", [
    ("ls-remote fails", "exit 128", "fail", False),
    ("ls-remote prints nothing", "exit 0", "fail", False),
    ("commit is the tip", f"printf '{SHA}\\trefs/heads/main\\n'", "ok", False),
    ("main moved on", f"printf '{OTHER}\\trefs/heads/main\\n'", "ok", True),
])
def test_tip_check(tmp_path, case, git_stub, expect_rc, expect_skip):
    rc, out = run_step(step("mirror", TIP)["run"], {}, tmp_path, {"git": git_stub})
    assert (rc == 0) == (expect_rc == "ok"), (case, out)
    assert ("skip=true" in out) == expect_skip, (case, out)


REQUIRE = "Require the target variable and the deploy key"


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("target, key, rc_ok, needle", [
    ("", "k", False, "SKILLS_MIRROR_TARGET"),
    ("not a repo", "k", False, "SKILLS_MIRROR_TARGET"),
    ("example-org/skills", "", False, "SKILLS_MIRROR_DEPLOY_KEY"),
    ("example-org/skills", "k", True, ""),
])
def test_requirements_fail_clearly(tmp_path, target, key, rc_ok, needle):
    rc, out = run_step(step("mirror", REQUIRE)["run"],
                       {"TARGET_REPO": target, "DEPLOY_KEY": key}, tmp_path, {})
    assert (rc == 0) == rc_ok, out
    assert needle in out
    if needle == "SKILLS_MIRROR_DEPLOY_KEY":
        assert "skills-mirror" in out            # names the environment to put it in
