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
DARK = "dashboard-dark"
LIGHT = "report-light"


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
    assert hexes(lines) == list(t.palette)[:3]
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
    # one warning for the wrap, whatever the palette's length (the theme's
    # palette is the colour cycle; there is no second, theme-specific code)
    palette = list(themes.get(DARK).palette)
    names = [f"s{i}" for i in range(len(palette) + 1)]
    data = DataSnapshot.from_columns({"t": [0, 1], **{c: [1.0, 2.0] for c in names}})
    spec = FigureSpec(datasets={"d": data}, theme=DARK, layers=[
        LineMark(data="d", encoding={"x": "t", "y": c}, name=c) for c in names])
    built = build_figure(spec)
    assert [w.code for w in built.warnings].count("colour_cycle_wrapped") == 1
    # the extra line repeats the first colour and is told apart by its dashes
    lines = [built.layer_artists[i][0] for i in range(len(names))]
    assert hexes(lines) == palette + [palette[0]]
    assert [ln.get_linestyle() for ln in lines] == ["-"] * len(palette) + ["--"]
    assert "colour_cycle_wrapped" not in [
        w.code for w in build_figure(three_lines(DARK)).warnings]


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


def test_explicit_bar_placement_takes_a_layer_out_of_dodging():
    # Two layers without placement share categories and are dodged; a third
    # placing itself explicitly is drawn where it says and does not shift them.
    data = DataSnapshot.from_columns({"k": ["x", "y"], "a": [1.0, 2.0], "b": [3.0, 4.0],
                                      "c": [5.0, 6.0]})
    spec = FigureSpec(datasets={"d": data}, layers=[
        BarMark(data="d", encoding={"x": "k", "y": "a"}),
        BarMark(data="d", encoding={"x": "k", "y": "c"}, style={"bar_width": 0.1,
                                                                 "bar_offset": 0.45}),
        BarMark(data="d", encoding={"x": "k", "y": "b"})])
    built = build_figure(spec)
    centres = [[p.get_x() + p.get_width() / 2 for p in built.layer_artists[i][0].patches]
               for i in range(3)]
    np.testing.assert_allclose(centres[0], [-0.2, 0.8])
    np.testing.assert_allclose(centres[2], [0.2, 1.2])
    np.testing.assert_allclose(centres[1], [0.45, 1.45])
    np.testing.assert_allclose([p.get_width() for p in built.layer_artists[1][0].patches],
                               [0.1, 0.1])
    # it only touches the dodged bars (0.4 = 0.45 - 0.1 / 2): no overlap
    assert "bars_overlap" not in [w.code for w in built.warnings]


def test_an_explicit_layer_overlapping_dodged_layers_is_reported():
    data = DataSnapshot.from_columns({"k": ["a", "b", "c"], "v1": [3.0, 4.0, 5.0],
                                      "v2": [2.0, 3.0, 4.0], "v3": [1.0, 2.0, 6.0]})
    spec = FigureSpec(datasets={"d": data}, layers=[
        BarMark(data="d", encoding={"x": "k", "y": "v1"}, name="v1"),
        BarMark(data="d", encoding={"x": "k", "y": "v2"}, name="v2"),
        # centred at +0.25, 0.3 wide: covers the right half of v2's bars
        BarMark(data="d", encoding={"x": "k", "y": "v3"}, name="v3",
                style={"bar_offset": 0.25, "bar_width": 0.3})])
    built = build_figure(spec)
    (w,) = [w for w in built.warnings if w.code == "bars_overlap"]
    assert w.path == "/layers/2/style" and "[1]" in w.message and "3 of its bars" in w.message
    # with value labels, the omission is put down to the overlap, not to narrow bars
    for m in spec.layers:
        m.style = {**m.style, "value_labels": True}
    built = build_figure(spec)
    (w,) = [w for w in built.warnings if w.code == "value_labels_omitted"]
    assert "bars_overlap" in w.message and "too narrow" not in w.message
    # without the overlap the labels fit
    spec.layers[2].style = {"bar_offset": 0.45, "bar_width": 0.1, "value_labels": True}
    codes = [w.code for w in build_figure(spec).warnings]
    assert "bars_overlap" not in codes and "value_labels_omitted" not in codes


def test_bar_layers_sharing_no_category_are_not_dodged():
    d1 = DataSnapshot.from_columns({"k": ["a", "b"], "v": [3.0, 4.0], "w": [2.0, 5.0]})
    d2 = DataSnapshot.from_columns({"k": ["c", "d"], "u": [1.0, 2.0]})
    spec = FigureSpec(datasets={"d1": d1, "d2": d2}, layers=[
        BarMark(data="d1", encoding={"x": "k", "y": "v"}, name="v"),
        BarMark(data="d1", encoding={"x": "k", "y": "w"}, name="w"),
        BarMark(data="d2", encoding={"x": "k", "y": "u"}, name="u")])
    built = build_figure(spec)
    bars = {i: built.layer_artists[i][0].patches for i in range(3)}
    centre = {i: [p.get_x() + p.get_width() / 2 for p in ps] for i, ps in bars.items()}
    # v and w share a and b: dodged; u shares nothing: a single layer at c and d
    np.testing.assert_allclose(centre[0], [-0.2, 0.8])
    np.testing.assert_allclose(centre[1], [0.2, 1.2])
    np.testing.assert_allclose(centre[2], [2.0, 3.0])
    np.testing.assert_allclose([p.get_width() for p in bars[2]], [0.8, 0.8])
    # and nobody gets a "no data" note for a category it never had
    assert not [a for arts in built.layer_artists.values() for a in arts
                if hasattr(a, "get_text") and a.get_text() == "no data"]
    # a layer linking the two groups dodges all four together
    d3 = DataSnapshot.from_columns({"k": ["b", "c"], "x": [1.0, 1.0]})
    spec.datasets["d3"] = d3
    spec.layers.append(BarMark(data="d3", encoding={"x": "k", "y": "x"}, name="x"))
    built = build_figure(spec)
    assert {round(p.get_width(), 6) for i in range(4)
            for p in built.layer_artists[i][0].patches} == {0.2}


def forecast_dashes_default(linestyle):
    import matplotlib

    from ieeVisualizer.render.mpl.style import forecast_dashes

    with matplotlib.style.context("default"):
        return forecast_dashes(linestyle)


def test_forecast_and_reference_styling_come_from_the_theme():
    reg_theme = Theme(name="dotted-forecast", version="1.0.0", palette=("#332288",),
                      roles=RoleStyle(forecast_linestyle="dotted", reference_color="#616161",
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
    # the theme's forecast pattern (dotted), with its gaps doubled
    assert fc._unscaled_dash_pattern == forecast_dashes_default(":")
    assert mcolors.to_hex(ref.get_color()) == "#616161" and ref.get_alpha() == 0.9
    assert mcolors.to_hex(built.annotation_artists[0].get_color()) == "#616161"
    # the default theme keeps the former look
    built = build_figure(FigureSpec(datasets={"d": data}, layers=spec.layers))
    assert built.layer_artists[0][0]._unscaled_dash_pattern == forecast_dashes_default("--")
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
    v1 = Theme(name="versioned-test", version="1.0.0", palette=("#332288",))
    if "versioned-test" not in reg:
        reg.register(v1)
    spec = three_lines("versioned-test")
    a = export(spec, ["svg"], MemoryWriter())
    if "versioned-test@1.1.0" not in reg:
        reg.register(Theme(name="versioned-test", version="1.1.0", palette=("#117733",)))
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


def _x_tick_gaps(built):
    """Rendered gaps (px) between neighbouring in-view x tick labels, and the
    rendered width of the no-break space inside a grouped label."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    canvas = FigureCanvasAgg(built.figure)
    canvas.draw()
    r = canvas.get_renderer()
    lo, hi = built.axes.get_xlim()
    labs = [t for t in built.axes.get_xticklabels()
            if t.get_text() and lo <= t.get_position()[0] <= hi]
    boxes = [t.get_window_extent(r) for t in labs]
    gaps = [boxes[i + 1].x0 - boxes[i].x1 for i in range(len(boxes) - 1)]
    grouped = next(t for t in labs if S in t.get_text())
    full = grouped.get_window_extent(r).width
    grouped.set_text(grouped.get_text().replace(S, ""))
    sep = full - grouped.get_window_extent(r).width
    return [t.get_text() for t in labs], gaps, sep


@pytest.mark.parametrize("name", ["funnel", "bar_comparison"])
@pytest.mark.parametrize("theme", [DARK, LIGHT])
def test_grouped_tick_labels_do_not_run_together_at_email_size(name, theme):
    t = templates.get(name)
    payload = t.example()
    payload["params"]["theme"] = theme
    built = build_figure(t.build_payload(payload), themes.get(theme).request("email"))
    labels, gaps, sep = _x_tick_gaps(built)
    assert sep > 0 and len(labels) >= 3, labels
    # a gap between labels must read as wider than the space inside "10 000"
    assert min(gaps) >= 1.5 * sep, (labels, gaps, sep)


def test_default_theme_keeps_matplotlibs_tick_locator():
    from matplotlib.ticker import AutoLocator

    t = templates.get("funnel")
    built = build_figure(t.build_payload(t.example()))
    assert type(built.axes.xaxis.get_major_locator()) is AutoLocator


def _value_label_boxes(built):
    # measured as build_figure laid the figure out (as render() does); not
    # drawn again outside the theme's render context
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    r = FigureCanvasAgg(built.figure).get_renderer()
    labels = [a for arts in built.layer_artists.values() for a in arts
              if hasattr(a, "get_text") and a.get_text() not in ("", "no data")]
    return ([(a.get_text(), a.get_window_extent(r)) for a in labels],
            built.axes.get_window_extent(r), built.figure.bbox)


@pytest.mark.parametrize("theme", [DARK, LIGHT])
@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("sign", [1, -1])
def test_value_labels_stay_inside_the_axes_at_email_size(theme, orientation, sign):
    t = templates.get("bar_comparison")
    payload = t.example()
    payload["params"].update(theme=theme, orientation=orientation)
    if sign < 0:
        for c in ("revenue", "revenue_prev"):
            col = payload["data"]["columns"][c]
            col["values"] = [None if v is None else -v for v in col["values"]]
    built = build_figure(t.build_payload(payload), themes.get(theme).request("email"))
    boxes, ax_box, fig_box = _value_label_boxes(built)
    assert len(boxes) == 9  # 5 + 5 bars, one current value is null
    for text, b in boxes:
        assert fig_box.x0 <= b.x0 and b.x1 <= fig_box.x1, (text, b, fig_box)
        assert fig_box.y0 <= b.y0 and b.y1 <= fig_box.y1, (text, b, fig_box)
        assert ax_box.x0 <= b.x0 and b.x1 <= ax_box.x1, (text, b, ax_box)
        assert ax_box.y0 <= b.y0 and b.y1 <= ax_box.y1, (text, b, ax_box)
    assert "value_label_outside_axes" not in [w.code for w in built.warnings]


def test_a_value_label_past_a_fixed_axis_limit_is_reported():
    from ieeVisualizer.spec import Axis

    spec = bars([18450.5, 900.0])
    spec.axes["y"] = Axis(limits=[0, 18500])
    request = themes.get(LIGHT).request("email")
    built = build_figure(spec, request)  # laid out as render() lays it out
    assert built.axes.get_ylim()[1] == 18500  # the spec's limit is kept
    assert "value_label_outside_axes" in [w.code for w in built.warnings]
    assert [w.code for w in render(spec, request).warnings].count(
        "value_label_outside_axes") == 1


@pytest.mark.parametrize("theme", [DARK, LIGHT])
@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("values", [[18450.5, 900.0, -3000.0], [-18450.5, -900.0, 3000.0]])
def test_value_labels_on_both_sides_of_zero_stay_inside_a_short_figure(theme, horizontal,
                                                                       values):
    # a wide, short figure: the 12 % value margin is a few pixels on y
    data = DataSnapshot.from_columns({"k": ["x", "y", "z"], "v": values}, units={"v": "EUR"})
    enc = {"x": "v", "y": "k"} if horizontal else {"x": "k", "y": "v"}
    spec = FigureSpec(datasets={"d": data}, theme=theme, layers=[BarMark(
        data="d", encoding=enc, orientation="horizontal" if horizontal else "vertical",
        style={"value_labels": True})])
    size = dict(width=3, height=4) if horizontal else dict(width=6, height=2)
    built = build_figure(spec, RenderRequest(dpi=200, **size))
    boxes, ax_box, _ = _value_label_boxes(built)
    assert len(boxes) == 3
    for text, b in boxes:
        assert ax_box.x0 <= b.x0 and b.x1 <= ax_box.x1, (text, b, ax_box)
        assert ax_box.y0 <= b.y0 and b.y1 <= ax_box.y1, (text, b, ax_box)
