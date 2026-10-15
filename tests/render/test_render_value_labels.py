"""Bar value labels and "no data" notes as drawn at the Muutto365 themes'
email size: labels never overlap and each sits on its own bar; "no data"
notes stay inside the axes on negative data; the auto legend never covers a
value label; the result is deterministic."""

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg

from ieeVisualizer import templates, themes
from ieeVisualizer.render import RenderRequest, build_figure, render
from ieeVisualizer.spec import BarMark, DataSnapshot, FigureSpec

DARK = "muutto365-admin-dark"
LIGHT = "muutto365-report-light"
TOL = 0.5  # px


def bar_comparison(theme, orientation, sign=1):
    t = templates.get("bar_comparison")
    payload = t.example()
    payload["params"].update(theme=theme, orientation=orientation)
    if sign < 0:
        for c in ("revenue", "revenue_prev"):
            col = payload["data"]["columns"][c]
            col["values"] = [None if v is None else -v for v in col["values"]]
    return t.build_payload(payload)


def drawn(spec, request):
    built = build_figure(spec, request)
    canvas = FigureCanvasAgg(built.figure)
    canvas.draw()
    return built, canvas.get_renderer()


def texts(built, which):
    out = []
    for i, arts in built.layer_artists.items():
        for a in arts:
            if hasattr(a, "get_text") and a.get_visible():
                is_note = a.get_text() == "no data"
                if is_note == (which == "notes"):
                    out.append((i, a))
    return out


def bars_of(built):
    """(layer, rectangle) for every drawn bar."""
    return [(i, p) for i, arts in built.layer_artists.items() for a in arts
            if hasattr(a, "patches") for p in a.patches]


def intersects(a, b):
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


EMAIL_CASES = [(theme, orientation, sign) for theme in (DARK, LIGHT)
               for orientation in ("vertical", "horizontal") for sign in (1, -1)]


@pytest.mark.parametrize("theme, orientation, sign", EMAIL_CASES)
def test_value_labels_never_overlap_and_sit_on_their_own_bar(theme, orientation, sign):
    built, r = drawn(bar_comparison(theme, orientation, sign), themes.get(theme).request("email"))
    labels = texts(built, "labels")
    assert len(labels) == 9  # 5 + 5 bars, one current value is null
    assert "value_labels_omitted" not in [w.code for w in built.warnings]
    boxes = [a.get_window_extent(r) for _, a in labels]
    for k, a in enumerate(boxes):
        for b in boxes[k + 1:]:
            assert not intersects(a, b), (labels[k][1].get_text(), a, b)
    horizontal = orientation == "horizontal"
    bars = bars_of(built)
    for (i, a), box in zip(labels, boxes):
        # the bar this label belongs to: same layer, its end at the label's anchor
        x, y = a.xy
        (bar,) = [p for j, p in bars if j == i and (
            np.isclose(p.get_y() + p.get_height() / 2, y) and np.isclose(p.get_width(), x)
            if horizontal else
            np.isclose(p.get_x() + p.get_width() / 2, x) and np.isclose(p.get_height(), y))]
        bb = bar.get_window_extent(r)
        # across the category axis the label lies within its own bar
        if horizontal:
            assert bb.y0 - TOL <= box.y0 and box.y1 <= bb.y1 + TOL, (a.get_text(), box, bb)
        else:
            assert bb.x0 - TOL <= box.x0 and box.x1 <= bb.x1 + TOL, (a.get_text(), box, bb)


@pytest.mark.parametrize("theme", [DARK, LIGHT])
def test_vertical_labels_wider_than_their_bar_are_turned(theme):
    built, _ = drawn(bar_comparison(theme, "vertical"), themes.get(theme).request("email"))
    assert {a.get_rotation() for _, a in texts(built, "labels")} == {90.0}
    # wide bars with short labels stay horizontal
    built, _ = drawn(bar_comparison(theme, "horizontal"), themes.get(theme).request("email"))
    assert {a.get_rotation() for _, a in texts(built, "labels")} == {0.0}


@pytest.mark.parametrize("theme, orientation, sign", EMAIL_CASES)
def test_no_data_note_is_inside_the_axes_and_clear_of_the_title(theme, orientation, sign):
    built, r = drawn(bar_comparison(theme, orientation, sign), themes.get(theme).request("email"))
    notes = texts(built, "notes")
    assert len(notes) == 1
    box = notes[0][1].get_window_extent(r)
    ax = built.axes.get_window_extent(r)
    assert ax.x0 - TOL <= box.x0 and box.x1 <= ax.x1 + TOL, (box, ax)
    assert ax.y0 - TOL <= box.y0 and box.y1 <= ax.y1 + TOL, (box, ax)
    assert not intersects(box, built.axes.title.get_window_extent(r))
    # it starts at the zero line, on the side the data is on
    zero = built.axes.transData.transform((0.0, 0.0))
    if orientation == "vertical":
        assert (box.y0 >= zero[1] - TOL) if sign > 0 else (box.y1 <= zero[1] + TOL)
    else:
        assert (box.x0 >= zero[0] - TOL) if sign > 0 else (box.x1 <= zero[0] + TOL)


@pytest.mark.parametrize("horizontal", [False, True])
def test_no_data_note_on_an_all_negative_layer_points_into_the_plot(horizontal):
    data = DataSnapshot.from_columns({"k": ["a", "b", "c"], "v": [-5.0, None, -3.0]})
    enc = {"x": "v", "y": "k"} if horizontal else {"x": "k", "y": "v"}
    spec = FigureSpec(datasets={"d": data}, title="All negative", layers=[BarMark(
        data="d", encoding=enc, orientation="horizontal" if horizontal else "vertical")])
    built, r = drawn(spec, RenderRequest(width=6.0, height=3.75, dpi=200))
    ((_, note),) = texts(built, "notes")
    box, ax = note.get_window_extent(r), built.axes.get_window_extent(r)
    assert ax.x0 - TOL <= box.x0 and box.x1 <= ax.x1 + TOL, (box, ax)
    assert ax.y0 - TOL <= box.y0 and box.y1 <= ax.y1 + TOL, (box, ax)
    assert not intersects(box, built.axes.title.get_window_extent(r))


@pytest.mark.parametrize("theme, orientation, sign", EMAIL_CASES)
def test_auto_legend_never_covers_a_value_label(theme, orientation, sign):
    built, r = drawn(bar_comparison(theme, orientation, sign), themes.get(theme).request("email"))
    legend = built.axes.get_legend().get_window_extent(r)
    for _, a in texts(built, "labels") + texts(built, "notes"):
        assert not intersects(legend, a.get_window_extent(r)), a.get_text()


@pytest.mark.parametrize("theme", [DARK, LIGHT])
def test_value_label_layout_is_deterministic(theme):
    spec = bar_comparison(theme, "vertical", -1)
    request = themes.get(theme).request("email")
    first = render(spec, request)
    assert render(spec, request).content == first.content
    svg = themes.get(theme).request("email", "svg")
    assert render(spec, svg).content == render(spec, svg).content


def test_labels_that_cannot_fit_beside_each_other_are_left_out_with_a_warning():
    cats = [f"c{k}" for k in range(30)]
    data = DataSnapshot.from_columns(
        {"k": cats, **{s: [1000.0 + 37 * k for k in range(30)] for s in "abc"}},
        units={s: "EUR" for s in "abc"})
    spec = FigureSpec(datasets={"d": data}, theme=LIGHT, layers=[
        BarMark(data="d", encoding={"x": "k", "y": s}, style={"value_labels": True})
        for s in "abc"])
    built, r = drawn(spec, RenderRequest(width=3.0, height=2.0, dpi=100))
    assert [w.code for w in built.warnings].count("value_labels_omitted") == 1
    assert texts(built, "labels") == []  # none drawn on top of another
    assert "value_labels_omitted" in [w.code for w in render(
        spec, RenderRequest(width=3.0, height=2.0, dpi=100)).warnings]
