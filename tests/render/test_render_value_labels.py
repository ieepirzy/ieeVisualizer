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
    """The figure as build_figure laid it out -- inside the theme's render
    context, as render() lays it out when saving -- and a renderer to
    measure it with. It is not drawn again here: a draw outside that
    context could lay it out differently from what render() ships."""
    built = build_figure(spec, request)
    return built, FigureCanvasAgg(built.figure).get_renderer()


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


def inside(box, ax):
    return (ax.x0 - TOL <= box.x0 and box.x1 <= ax.x1 + TOL
            and ax.y0 - TOL <= box.y0 and box.y1 <= ax.y1 + TOL)


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("limits", [[50, 100], [-100, -50]])
def test_no_data_note_is_inside_the_axes_when_the_limits_exclude_zero(horizontal, limits):
    # zero is outside the fixed limits: a note anchored at zero would be
    # clipped away and the empty slot read as "below 50"
    from ieeVisualizer.spec import Axis

    lo, hi = limits
    data = DataSnapshot.from_columns({"k": ["a", "b", "c"], "v": [lo + 10.0, None, hi - 10.0]})
    enc = {"x": "v", "y": "k"} if horizontal else {"x": "k", "y": "v"}
    val_ch = "x" if horizontal else "y"
    spec = FigureSpec(datasets={"d": data}, axes={val_ch: Axis(limits=limits)}, layers=[
        BarMark(data="d", encoding=enc, orientation="horizontal" if horizontal else "vertical",
                style={"value_labels": True})])
    built, r = drawn(spec, RenderRequest(width=6.0, height=3.75, dpi=100))
    ((_, note),) = texts(built, "notes")
    box, ax = note.get_window_extent(r), built.axes.get_window_extent(r)
    assert inside(box, ax), (box, ax)
    # it starts at the plot edge nearest to zero and reads into the plot
    if horizontal:
        assert abs((box.x0 - ax.x0) if lo > 0 else (ax.x1 - box.x1)) < 10, (box, ax)
    else:
        assert abs((box.y0 - ax.y0) if lo > 0 else (ax.y1 - box.y1)) < 10, (box, ax)
    assert "value_label_outside_axes" not in [w.code for w in built.warnings]


def polar_bars(width, height, labels=False):
    data = DataSnapshot.from_columns({"k": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                                      "v": [3.0, None, 5.0, None, 2.0, 4.0],
                                      "w": [1.0, 2.0, 3.0, 1.0, None, 2.0]})
    spec = FigureSpec(datasets={"d": data}, coordinate_system="polar", layers=[
        BarMark(data="d", encoding={"x": "k", "y": c}, name=c,
                style={"value_labels": True} if labels else {}) for c in "vw"])
    return spec, RenderRequest(width=width, height=height, dpi=100)


def test_polar_bars_draw_no_value_labels():
    spec, request = polar_bars(5.0, 5.0, labels=True)
    built, _ = drawn(spec, request)
    assert texts(built, "labels") == []
    assert [w.path for w in built.warnings if w.code == "style_key_ignored"] == [
        "/layers/0/style/value_labels", "/layers/1/style/value_labels"]


def test_polar_no_data_notes_lie_along_their_slots_apart_from_each_other():
    spec, request = polar_bars(5.0, 5.0)
    built, r = drawn(spec, request)
    notes = texts(built, "notes")
    assert len(notes) == 3  # v at 1 and 3, w at 4: none left out at this size
    assert "no_data_unplaceable" not in [w.code for w in built.warnings]
    ax = built.axes
    centre = ax.transData.transform((0.0, ax.get_ylim()[0]))
    boxes = [a.get_window_extent(r) for _, a in notes]
    for k, b in enumerate(boxes):
        for c in boxes[k + 1:]:
            assert not intersects(b, c)
    for (_, a), b in zip(notes, boxes):
        # the note is off the centre, in the direction of its slot's angle
        mid = np.array([(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2]) - centre
        theta = a.xy[0]
        direction = ax.transData.transform((theta, ax.get_ylim()[1])) - centre
        cos = mid @ direction / np.linalg.norm(mid) / np.linalg.norm(direction)
        assert np.linalg.norm(mid) > 10 and cos > 0.95, (theta, mid, direction)
    # and inside the plot circle, clear of every bar but its own (absent) one
    radius = np.linalg.norm(ax.transData.transform((0.0, ax.get_ylim()[1])) - centre)
    for b in boxes:
        for x, y in ((b.x0, b.y0), (b.x1, b.y1), (b.x0, b.y1), (b.x1, b.y0)):
            assert np.hypot(x - centre[0], y - centre[1]) <= radius


def test_polar_no_data_notes_that_do_not_fit_are_listed_not_stacked():
    # a small figure: the plot circle is too small for any note
    spec, request = polar_bars(2.5, 2.5)
    built, _ = drawn(spec, request)
    unplaced = [w for w in built.warnings if w.code == "no_data_unplaceable"]
    assert [(w.path, w.message.split(";")[0]) for w in unplaced] == [
        ("/layers/0", "layer 0 has no value at 1, 3"), ("/layers/1", "layer 1 has no value at 4")]
    assert texts(built, "notes") == []  # none left stacked at the centre


@pytest.mark.parametrize("theme", ["default", DARK, LIGHT])
def test_horizontal_legend_lists_layers_in_bar_order(theme):
    spec = bar_comparison(theme, "horizontal")
    request = (themes.get(theme).request("email") if theme != "default"
               else RenderRequest(width=6.4, height=4.4, dpi=100))
    built, r = drawn(spec, request)
    legend = [t.get_text() for t in built.axes.get_legend().get_texts()]
    # the bars of the first category, top to bottom
    first = []
    for arts in built.layer_artists.values():
        for a in arts:
            if hasattr(a, "patches"):
                p = a.patches[0]
                first.append((p.get_window_extent(r).y1, a.get_label()))
    assert legend == [lab for _, lab in sorted(first, reverse=True)]


def many_labels():
    n = 8
    data = DataSnapshot.from_columns({
        "k": [f"c{k}" for k in range(n)], "a": [1.0 + 0.1 * k for k in range(n)],
        "b": [None if k == 3 else 0.9 + 0.1 * k for k in range(n)],
        "c": [1.05 + 0.1 * k for k in range(n)]})
    return FigureSpec(datasets={"d": data}, layers=[
        BarMark(data="d", encoding={"x": "k", "y": c}, name=c, style={"value_labels": True})
        for c in "abc"])


def test_value_labels_omitted_describes_the_layout_that_ships():
    # at this size the labels only just do not fit
    spec, request = many_labels(), RenderRequest(width=4.5, height=3.0, dpi=100)
    built, _ = drawn(spec, request)
    codes = [w.code for w in built.warnings]
    assert hidden_labels(built) and codes.count("value_labels_omitted") == 1
    assert codes == [w.code for w in render(spec, request).warnings]
    # drawn again at a size where they fit, the warning is withdrawn
    from ieeVisualizer.render.mpl import render_context

    for width in (8.0, 3.0):
        built.figure.set_size_inches(width, 3.0)
        with render_context(themes.get("default")):
            built.figure.draw_without_rendering()
        codes = [w.code for w in built.warnings]
        assert codes.count("value_labels_omitted") == (1 if hidden_labels(built) else 0)
        assert bool(hidden_labels(built)) == (width < 4.5), width


def hidden_labels(built):
    return [a for arts in built.layer_artists.values() for a in arts
            if hasattr(a, "get_text") and a.get_text() != "no data" and not a.get_visible()]
