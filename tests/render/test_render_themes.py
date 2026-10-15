"""Themes as drawn: palette on artists, locale on tick and value labels, role
styling, colour maps, and the theme recorded in results and manifests."""

import json

import numpy as np
import pytest
from matplotlib import colors as mcolors

from ieeVisualizer import templates, themes
from ieeVisualizer.artifacts import MemoryWriter, export
from ieeVisualizer.render import RenderRequest, build_figure, render
from ieeVisualizer.spec import (
    BarMark,
    DataSnapshot,
    FigureSpec,
    HeatmapMark,
    LineMark,
    ReferenceLine,
)
from ieeVisualizer.themes import RoleStyle, Theme

S = " "
DARK = "muutto365-admin-dark"
LIGHT = "muutto365-report-light"


def hexes(artists):
    return [mcolors.to_hex(a.get_color()) for a in artists]


def three_lines(theme=None):
    data = DataSnapshot.from_columns({"t": [0, 1, 2], "a": [1.0, 2, 3], "b": [2.0, 3, 4],
                                      "c": [3.0, 4, 5]}, units={"t": "d"})
    return FigureSpec(datasets={"d": data}, theme=theme, layers=[
        LineMark(data="d", encoding={"x": "t", "y": c}, name=c) for c in "abc"])


def bars(values, unit="EUR", theme=LIGHT, **style):
    data = DataSnapshot.from_columns({"k": ["x", "y", "z"][:len(values)], "v": values},
                                     units={"v": unit})
    return FigureSpec(datasets={"d": data}, theme=theme, layers=[
        BarMark(data="d", encoding={"x": "k", "y": "v"}, style=dict(value_labels=True,
                                                                    **style))])


def label_texts(built):
    return [a.get_text() for a in built.layer_artists[0] if hasattr(a, "get_text")]


def test_palette_and_ground_come_from_the_theme():
    t = themes.get(DARK)
    built = build_figure(three_lines(DARK))
    lines = [built.layer_artists[i][0] for i in range(3)]
    assert hexes(lines) == list(t.palette)
    assert mcolors.to_hex(built.figure.get_facecolor()) == t.background
    assert mcolors.to_hex(built.axes.get_facecolor()) == t.background
    built.figure.canvas.draw()
    tick = built.axes.get_yticklabels()[0]
    assert mcolors.to_hex(tick.get_color()) == t.foreground
    assert not built.axes.spines["top"].get_visible()


def test_default_theme_keeps_matplotlib_colours():
    built = build_figure(three_lines())
    assert hexes([built.layer_artists[i][0] for i in range(3)]) == [
        "#1f77b4", "#ff7f0e", "#2ca02c"]
    assert render(three_lines("default"), RenderRequest(format="svg")).content == render(
        three_lines(), RenderRequest(format="svg")).content


def test_more_series_than_palette_colours_warns():
    data = DataSnapshot.from_columns({"t": [0, 1], **{c: [1.0, 2.0] for c in "abcd"}})
    spec = FigureSpec(datasets={"d": data}, theme=DARK, layers=[
        LineMark(data="d", encoding={"x": "t", "y": c}, name=c) for c in "abcd"])
    built = build_figure(spec)
    assert [w.code for w in built.warnings].count("palette_exhausted") == 1
    assert "palette_exhausted" not in [w.code for w in build_figure(three_lines(DARK)).warnings]


def test_tick_labels_use_the_theme_locale():
    spec = bars([1234.5, 2500.0, 600.0])
    built = build_figure(spec)
    built.figure.canvas.draw()
    ticks = [t.get_text() for t in built.axes.get_yticklabels() if t.get_text()]
    assert f"2{S}500" in ticks and "0" in ticks
    assert all("," not in t or t.count(",") == 1 for t in ticks)
    plain = build_figure(bars([1234.5, 2500.0, 600.0], theme=None))
    plain.figure.canvas.draw()
    assert "2500" in [t.get_text() for t in plain.axes.get_yticklabels()]


def test_value_labels_use_the_locale_and_sit_outside_negative_bars():
    built = build_figure(bars([1234.5, -40.0, None]))
    assert label_texts(built) == [f"1{S}234,50{S}€", f"−40,00{S}€", "no data"]
    pos, neg = [a for a in built.layer_artists[0] if hasattr(a, "get_text")][:2]
    assert pos.get_va() == "bottom" and neg.get_va() == "top"
    assert label_texts(build_figure(bars([1234.5], unit="kg")))[0] == f"1{S}234,5"
    assert label_texts(build_figure(bars([1234.5], theme=None)))[0] == f"1,234.50{S}EUR"


def test_bar_width_and_offset_place_layers_side_by_side():
    data = DataSnapshot.from_columns({"k": ["x", "y"], "a": [1.0, 2.0], "b": [3.0, 4.0]})
    spec = FigureSpec(datasets={"d": data}, layers=[
        BarMark(data="d", encoding={"x": "k", "y": "a"}, style={"bar_width": 0.4,
                                                                 "bar_offset": -0.2}),
        BarMark(data="d", encoding={"x": "k", "y": "b"}, style={"bar_width": 0.4,
                                                                 "bar_offset": 0.2})])
    built = build_figure(spec)
    (left,), (right,) = built.layer_artists[0], built.layer_artists[1]
    lx = [p.get_x() + p.get_width() / 2 for p in left.patches]
    rx = [p.get_x() + p.get_width() / 2 for p in right.patches]
    np.testing.assert_allclose(lx, [-0.2, 0.8])
    np.testing.assert_allclose(rx, [0.2, 1.2])
    np.testing.assert_allclose([p.get_width() for p in left.patches], [0.4, 0.4])


def test_forecast_and_reference_styling_come_from_the_theme():
    reg_theme = Theme(name="dotted-forecast", version="1.0.0", palette=("#2f4380",),
                      roles=RoleStyle(forecast_linestyle="dotted", reference_color="#545454",
                                      reference_alpha=0.9))
    if "dotted-forecast" not in themes.default_registry:
        themes.register(reg_theme)
    data = DataSnapshot.from_columns({"t": [0, 1, 2], "v": [1.0, 2.0, 3.0]})
    spec = FigureSpec(datasets={"d": data}, theme="dotted-forecast", layers=[
        LineMark(data="d", encoding={"x": "t", "y": "v"}, role="forecast", name="v"),
        LineMark(data="d", encoding={"x": "t", "y": "v"}, role="reference", name="r")],
        annotations=[ReferenceLine(axis="y", value=2.0, label="target")])
    built = build_figure(spec)
    fc, ref = built.layer_artists[0][0], built.layer_artists[1][0]
    assert fc.get_linestyle() == ":"
    assert mcolors.to_hex(ref.get_color()) == "#545454" and ref.get_alpha() == 0.9
    assert mcolors.to_hex(built.annotation_artists[0].get_color()) == "#545454"
    # the default theme keeps the former look
    built = build_figure(FigureSpec(datasets={"d": data}, layers=spec.layers))
    assert built.layer_artists[0][0].get_linestyle() == "--"
    # a forecast is never solid, whatever the theme
    with pytest.raises(themes.ThemeError):
        themes.ThemeRegistry().register(Theme(name="x", version="1.0.0", roles=RoleStyle(
            forecast_linestyle="solid")))


def test_heatmap_uses_the_theme_sequential_map():
    gx, gy = np.meshgrid(np.arange(3.0), np.arange(2.0))
    data = DataSnapshot.from_columns({"x": gx.ravel(), "y": gy.ravel(), "c": gx.ravel()})
    spec = FigureSpec(datasets={"d": data}, theme=DARK, layers=[
        HeatmapMark(data="d", encoding={"x": "x", "y": "y", "color": "c"})])
    (mesh,) = build_figure(spec).layer_artists[0]
    t = themes.get(DARK)
    assert mcolors.to_hex(mesh.cmap(0.0)) == t.sequential[0]
    assert mcolors.to_hex(mesh.cmap(1.0)) == t.sequential[-1]
    spec.layers[0].style = {"colormap": "diverging"}
    (mesh,) = build_figure(spec).layer_artists[0]
    assert mcolors.to_hex(mesh.cmap(0.0)) == t.diverging[0]
    spec.theme = None
    spec.layers[0].style = {}
    assert build_figure(spec).layer_artists[0][0].cmap.name == "viridis"


def test_result_and_manifest_record_the_theme():
    spec = three_lines(LIGHT)
    t = themes.get(LIGHT)
    assert render(spec, RenderRequest(format="svg")).theme == t.record()
    assert render(spec, RenderRequest(format="svg", theme=DARK)).theme["name"] == DARK
    w = MemoryWriter()
    art = export(spec, ["svg"], w)
    assert {k: art.theme[k] for k in ("name", "version", "sha256")} == t.record()
    stored = w.files[(art.artifact_id, "theme.json")]
    assert Theme.from_json(stored.decode()).sha256 == t.sha256
    assert art.theme["file"]["name"] == "theme.json"
    manifest = json.loads(w.files[(art.artifact_id, "manifest.json")])
    assert manifest["theme"]["sha256"] == t.sha256
    # the id depends on the resolved theme, not only on its name
    dark = export(spec, ["svg"], MemoryWriter(), request=RenderRequest(theme=DARK))
    assert dark.artifact_id != art.artifact_id


def test_a_new_theme_version_gives_a_new_artifact_id():
    reg = themes.default_registry
    v1 = Theme(name="versioned-test", version="1.0.0", palette=("#2f4380",))
    if "versioned-test" not in reg:
        reg.register(v1)
    spec = three_lines("versioned-test")
    a = export(spec, ["svg"], MemoryWriter())
    if "versioned-test@1.1.0" not in reg:
        reg.register(Theme(name="versioned-test", version="1.1.0", palette=("#007c47",)))
    b = export(spec, ["svg"], MemoryWriter())
    assert a.theme["version"] == "1.0.0" and b.theme["version"] == "1.1.0"
    assert a.artifact_id != b.artifact_id
    pinned = three_lines("versioned-test@1.0.0")
    assert export(pinned, ["svg"], MemoryWriter()).theme["sha256"] == a.theme["sha256"]


def test_unknown_theme_still_warns_and_renders_default():
    r = render(three_lines("corporate-blue"), RenderRequest(format="svg"))
    assert [w.path for w in r.warnings if w.code == "unknown_theme"] == ["/theme"]
    assert r.theme["name"] == "default"


def test_template_text_locale_mismatch_is_reported():
    t = templates.get("funnel")
    payload = t.example()
    payload["params"]["theme"] = LIGHT
    spec = t.build_payload(payload)
    assert [w.code for w in render(spec, RenderRequest(format="svg")).warnings] == []
    r = render(spec, RenderRequest(format="svg", theme="default"))
    assert "label_locale_mismatch" in [w.code for w in r.warnings]
