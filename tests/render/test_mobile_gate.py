"""The send gate (``check_legible``) and the figure-level cases it must catch:
content a phone shows less of than a desktop, collapsed plots, legends cut
off, category labels, annotations that vanish, clipped text."""

from __future__ import annotations

import pytest
from test_mobile_profiles import example, gate_refuses, measure, problems

from ieeVisualizer import templates
from ieeVisualizer.render import RenderRequest, get_profile, list_profiles, render
from ieeVisualizer.render.mpl.adapter import build_figure
from ieeVisualizer.spec import (
    Axis,
    BarMark,
    DataSnapshot,
    FigureSpec,
    LineMark,
    TextAnnotation,
)

PHONE = get_profile("email-phone")


def words(texts, ticks=False) -> set:
    return {w for t in texts if ticks or not t["tick"] for w in t["text"].split()}


TEMPLATES = [e["name"] for e in templates.list_templates()]


@pytest.mark.parametrize("profile", [p.name for p in list_profiles()])
@pytest.mark.parametrize("name", TEMPLATES)
def test_a_profile_never_shows_less_than_desktop_unless_refused(monkeypatch, name, profile):
    """Every word a plain desktop render draws outside the tick labels
    (titles, labels, legend, value labels, notes, annotations) is drawn
    outside the tick labels of every profile render too, or that render
    fails the gate."""
    spec = example(name)
    _, desk, _ = measure(monkeypatch, spec, RenderRequest(format="png", width=1920, height=1152,
                                                          units="px", dpi=192), css=960)
    result, texts, _ = measure(monkeypatch, spec, get_profile(profile).figure_request("png"))
    missing = words(desk) - words(texts)
    if missing:
        assert gate_refuses(result), sorted(missing)


def test_value_labels_left_out_on_a_phone_fail_the_gate():
    result = render(example("bar_comparison"), PHONE.figure_request("png"))
    assert "value_labels_omitted" in {w.code for w in result.warnings}
    assert gate_refuses(result)


def _bars(n: int, axis: str = "x") -> FigureSpec:
    names = [f"Region {i:02d}" for i in range(n)]
    other = "y" if axis == "x" else "x"
    return FigureSpec(datasets={"d": DataSnapshot.from_columns({"r": names, "v": list(range(n))})},
                      layers=[BarMark(data="d", encoding={axis: "r", other: "v"},
                                      orientation="vertical" if axis == "x" else "horizontal")],
                      axes={axis: Axis(label="Region")})


def test_a_profile_never_thins_category_labels(monkeypatch):
    result, texts, _ = measure(monkeypatch, _bars(60), PHONE.figure_request("png"))
    assert {f"Region {i:02d}" for i in range(60)} <= {t["text"] for t in texts}
    codes = {w.code for w in result.warnings}
    assert "category_labels_thinned" not in codes and "too_many_categories_for_profile" in codes
    assert gate_refuses(result)


def test_y_category_advice_does_not_suggest_horizontal_bars():
    result = render(_bars(60, "y"), PHONE.figure_request("png"))
    (w,) = [w for w in result.warnings if w.code == "too_many_categories_for_profile"]
    assert "horizontal" not in w.message and "taller" in w.message


def _series(n: int, name: str = "Series number {} with a long descriptive name") -> FigureSpec:
    cols = {"t": list(range(10))}
    cols.update({f"s{i}": [i + k for k in range(10)] for i in range(n)})
    return FigureSpec(
        datasets={"d": DataSnapshot.from_columns(cols)},
        layers=[LineMark(data="d", encoding={"x": "t", "y": f"s{i}"},
                         name=name.format(i))
                for i in range(n)])


def test_a_legend_that_does_not_fit_is_reported():
    result = render(_series(12), PHONE.figure_request("png"))
    assert "legend_not_visible" in {w.code for w in result.warnings}
    assert gate_refuses(result)


def test_narrow_legend_goes_below_the_plot_not_beside_it():
    built = build_figure(_series(6, "S{}"), PHONE.figure_request("png"))  # would fit right
    legend = built.axes.get_legend().get_window_extent()
    plot = built.axes.get_window_extent()
    assert legend.y1 <= plot.y0  # below, so the plot keeps the full width
    assert plot.width > 0.5 * built.figure.bbox.width


def _annotated(text: str, x: float) -> FigureSpec:
    return FigureSpec(
        datasets={"d": DataSnapshot.from_columns({"x": [0, 1, 2, 3], "y": [1, 3, 2, 4]})},
        layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})],
        annotations=[TextAnnotation(text=text, x=x, y=2)])


def test_a_long_annotation_at_the_edge_is_not_squeezed_into_a_column(monkeypatch):
    spec = FigureSpec(
        title="Annotated",
        datasets={"d": DataSnapshot.from_columns({"x": [1, 2, 3, 4], "y": [1, 4, 9, 16]})},
        layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})],
        annotations=[TextAnnotation(
            text="Peak after the long campaign launched in the northern region", x=3.9, y=15)])
    result, texts, size = measure(monkeypatch, spec, PHONE.figure_request("png"))
    built = build_figure(spec, PHONE.figure_request("png"))
    assert built.annotation_artists[0].get_text().count("\n") < 3  # no one-word column
    assert built.axes.get_window_extent().height > 0.5 * built.figure.bbox.height
    if any(problems(texts, size, 12.0).values()):
        assert gate_refuses(result)


def test_text_past_the_image_edge_is_reported_and_measured(monkeypatch):
    spec = _annotated("Unbreakable" * 6, 2.5)
    result, texts, size = measure(monkeypatch, spec, PHONE.figure_request("png"))
    assert problems(texts, size, 12.0)["clipped"]
    assert "legibility_text_clipped" in {w.code for w in result.warnings}


def test_an_annotation_outside_the_limits_is_not_lost_silently():
    spec = _annotated("outlier", 2.0)
    spec.axes = {"x": Axis(limits=[0, 1])}
    for request in (RenderRequest(), PHONE.figure_request("png")):
        result = render(spec, request)
        assert "annotation_outside_view" in {w.code for w in result.warnings}


def test_date_offset_and_axis_label_in_a_narrow_slot_are_legible_or_refused(monkeypatch):
    request = PHONE.figure_request("png", display_width=216)
    result, texts, size = measure(monkeypatch, example("timeseries"), request)
    if any(problems(texts, size, 12.0).values()):
        assert gate_refuses(result)
