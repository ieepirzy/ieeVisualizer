"""The README's "Rendering a spec" snippet runs as written."""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _snippet() -> str:
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        text = f.read()
    section = text.split("## Rendering a spec", 1)[1].split("\n## ", 1)[0]
    (code,) = re.findall(r"```python\n(.*?)```", section, flags=re.S)
    return code


def test_readme_rendering_snippet_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    exec(compile(_snippet(), "README.md", "exec"), {})
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "image/svg+xml ['null_values_omitted']"
    artifact_id = out[1].split()[0]
    assert sorted(os.listdir(tmp_path / "out" / artifact_id)) == [
        "figure.png", "figure.svg", "manifest.json", "spec.json"]
