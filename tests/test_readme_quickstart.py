"""Every snippet in the README's "Quick start" runs as written and prints
what its comments say."""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _snippets() -> list:
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        text = f.read()
    section = text.split("\n## Quick start\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"```python\n(.*?)```", section, flags=re.S)


def _run(code, capsys) -> list:
    exec(compile(code, "README.md", "exec"), {})
    return capsys.readouterr().out.splitlines()


def test_quick_start_has_the_three_entry_points():
    assert len(_snippets()) == 3


def test_legacy_snippet(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (line,) = _run(_snippets()[0], capsys)
    artifact_id, formats = line.split(" ", 1)
    assert formats == "['png', 'svg']"
    assert (tmp_path / "field.png").exists()
    assert sorted(os.listdir(tmp_path / "out" / artifact_id)) == [
        "figure.png", "figure.svg", "manifest.json", "spec.json", "theme.json"]


def test_spec_snippet(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (line,) = _run(_snippets()[1], capsys)
    assert re.fullmatch(r"sha256:[0-9a-f]{64} \['null_values_omitted'\]", line)
    # byte-identical on a second run
    (again,) = _run(_snippets()[1], capsys)
    assert again == line


def test_spec_snippet_labels_carry_units(capsys):
    from ieeVisualizer.render import build_figure

    ns: dict = {}
    exec(compile(_snippets()[1], "README.md", "exec"), ns)
    capsys.readouterr()
    built = build_figure(ns["spec"])
    assert (built.axes.get_xlabel(), built.axes.get_ylabel()) == ("t (min)", "T (°C)")


def test_template_snippet(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    names, artifact_id = _run(_snippets()[2], capsys)
    assert names == ("['bar_comparison', 'dependency_map', 'distribution', "
                     "'estimate_comparison', 'fan_chart', 'funnel', 'network', 'org_chart', "
                     "'sample_distribution', 'timeseries', 'vector_field_2d']")
    assert sorted(os.listdir(tmp_path / "out" / artifact_id)) == [
        "figure.png", "figure.svg", "manifest.json", "spec.json", "theme.json"]


@pytest.mark.parametrize("doc", ["README.md"])
def test_readme_links_exist(doc):
    with open(os.path.join(ROOT, doc), encoding="utf-8") as f:
        links = re.findall(r"\]\(((?:docs|examples)/[^)#]+)\)", f.read())
    assert links
    assert [p for p in links if not os.path.exists(os.path.join(ROOT, p))] == []
