"""The send gate (``check_legible``) and the figure-level cases it must catch:
content a phone shows less of than a desktop, collapsed plots, legends cut
off, category labels, annotations that vanish, clipped text."""

from __future__ import annotations

from collections import Counter

import pytest
from anchored import anchored
from test_mobile_profiles import example, gate_refuses, measure, problems

from ieeVisualizer import templates
from ieeVisualizer.render import (
    LegibilityError,
    RenderRequest,
    check_legible,
    get_profile,
    list_profiles,
    render,
)
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
DESKTOP = RenderRequest(format="png", width=1920, height=1152, units="px", dpi=192)


def words(texts, ticks=False) -> set:
    return {w for t in texts if ticks or not t["tick"] for w in t["text"].split()}


TEMPLATES = [e["name"] for e in templates.list_templates()]


@pytest.mark.parametrize("profile", [p.name for p in list_profiles()])
@pytest.mark.parametrize("name", TEMPLATES)
def test_every_template_is_legible_at_every_profile_and_shows_every_word(monkeypatch, name,
                                                                         profile):
    """Every template's example passes the send gate at every profile, and
    every text of a plain desktop render is in the profile render too with
    the same words in the same order, belonging to the same thing (its
    anchor: node, bar, legend entry, category tick, annotation position;
    tests/render/anchored.py) - and nothing else: nothing left out,
    shortened, moved off its data or attributable to another node."""
    spec = example(name)
    _, desk, _ = measure(monkeypatch, spec, DESKTOP, css=960)
    result, texts, _ = measure(monkeypatch, spec, get_profile(profile).figure_request("png"))
    check_legible(result)
    assert words(desk) - words(texts) == set()
    want, got = anchored(spec, DESKTOP), anchored(spec, get_profile(profile).figure_request("png"))
    assert (want - got, got - want) == (Counter(), Counter())


def _funnel_with_a_note() -> FigureSpec:
    """The funnel with a caller's own annotation at a data position."""
    spec = example("funnel")
    spec.annotations.append(TextAnnotation(text="Budget cap here", x=20000.0, y="Visited site"))
    return spec


@pytest.mark.parametrize("profile", [p.name for p in list_profiles()])
def test_a_callers_annotation_keeps_its_data_position(profile):
    """Only a template's row notes (whose x is a layout position) may move;
    a caller's annotation stays at its data anchor at every profile."""
    spec = _funnel_with_a_note()
    want, got = anchored(spec, DESKTOP), anchored(spec, get_profile(profile).figure_request("png"))
    assert (want - got, got - want) == (Counter(), Counter())
    built = build_figure(spec, get_profile(profile).figure_request("png"))
    (note,) = [a for a in built.annotation_artists if "Budget" in a.get_text()]
    assert note.xy[0] == 20000.0


def _random_network(rnd, trial, sizes=False):
    from ieeVisualizer.network import Edge, NetworkSpec, Node
    from ieeVisualizer.network.model import Layout, Quantity

    words = ["Chief", "executive", "Operations", "Finance", "Accounting", "Business", "sales",
             "Consumer", "Fleet", "Field", "teams", "Support", "Logistics", "Warehouse", "North"]
    n = rnd.randint(4, 22)
    nodes = [Node(id=f"n{i}", label=" ".join(rnd.choice(words) for _ in range(rnd.randint(1, 3))),
                  size=rnd.choice([1, 5, 50, 400, 1000]) if sizes else None)
             for i in range(n)]
    edges = [Edge(source=f"n{rnd.randrange(i)}", target=f"n{i}") for i in range(1, n)]
    layout = Layout(algorithm=rnd.choice(["force", "circular", "layered"]),
                    direction=rnd.choice(["TB", "LR"]), seed=trial)
    size = Quantity("Size", "units") if sizes else None
    return NetworkSpec(nodes=nodes, edges=edges, layout=layout, title="T", node_size=size)


def _network_cases():
    import random

    rnd = random.Random(1)
    cases = [(f"template {e['name']}", example(e["name"])) for e in templates.list_templates()
             if e["name"] in ("org_chart", "network", "dependency_map")]
    # half with node sizes: large markers are what rim-to-rim attribution is for
    return cases + [(f"random {k}", _random_network(rnd, k, sizes=k % 2 == 1))
                    for k in range(40)]


@pytest.mark.parametrize("request_name", ["default request", "no profile", "email-phone",
                                          "email-tablet", "desktop"])
def test_every_node_label_is_clearly_its_own_nodes_or_said_not_to_be(request_name):
    """A drawn node label is nearer its own node than any other (by a
    margin), or the render says it is not (``label_ambiguous``, gating) -
    for the templates and 40 seeded random networks (half with node sizes),
    with and without a profile. "Nearer" is a reader model
    (``anchored.owner``: nearest box point to the drawn marker's edge, read
    from the drawn markers, unclamped, 2.25 pt margin), not the renderer's
    rule (``_attributable``: box-to-rim gap clamped at 0, 3 pt margin, from
    its own bookkeeping)."""
    from anchored import MARGIN_PX, drawn_nodes, owner

    from ieeVisualizer.network.render import build_network

    req = {"default request": RenderRequest(), "no profile": DESKTOP}.get(request_name) \
        or get_profile(request_name).figure_request("png")
    silent = []
    for name, spec in _network_cases():
        built = build_network(spec, req)
        fig = built.figure
        renderer = fig.canvas.get_renderer()
        nodes = drawn_nodes(built)
        flagged = set(built.ambiguous_labels)
        for i, t in built.labels.items():
            who = owner(t.get_window_extent(renderer), nodes, MARGIN_PX * fig.dpi / 192.0)
            if who != i and i not in flagged:
                silent.append((name, spec.nodes[i].display_label, who))
        if flagged:
            assert "label_ambiguous" in {w.code for w in built.warnings}
    assert silent == []


def view_words(monkeypatch, make) -> tuple:
    """``(result, words)``: ``make()``'s result and the words of every text
    drawn while it ran (view labels and its figures), tick labels left out."""
    from matplotlib.figure import Figure
    from matplotlib.text import Text

    drawn, saved = [], []
    draw, save = Text.draw, Figure.savefig

    def spy(self, renderer):
        drawn.append(self)
        return draw(self, renderer)

    def save_spy(self, *a, **kw):
        saved.append(self)
        return save(self, *a, **kw)

    monkeypatch.setattr(Text, "draw", spy)
    monkeypatch.setattr(Figure, "savefig", save_spy)
    result = make()
    monkeypatch.undo()
    ticks = {id(lab) for fig in saved for ax in fig.axes for axis in (ax.xaxis, ax.yaxis)
             for tick in axis.get_major_ticks() + axis.get_minor_ticks()
             for lab in (tick.label1, tick.label2)}
    return result, {w for t in drawn if id(t) not in ticks and t.get_visible()
                    for w in t.get_text().split()}


@pytest.mark.parametrize("profile", [p.name for p in list_profiles()])
@pytest.mark.parametrize("view", ["weekly_briefing", "reporting_pipeline"])
def test_gallery_views_are_legible_at_every_profile_and_show_every_word(monkeypatch, view,
                                                                        profile):
    from ieeVisualizer.view import examples, render_email, render_view

    build = getattr(examples, view)
    _, desk = view_words(monkeypatch, lambda: render_view(build(), "png"))
    result, seen = view_words(monkeypatch, lambda: render_view(build(), "png", profile=profile))
    check_legible(result)
    assert desk - seen == set()
    if get_profile(profile).narrow:
        check_legible(render_email(build(), profile))


def test_value_labels_left_out_on_a_phone_fail_the_gate():
    """Vertical bars too narrow for their labels: a taller figure does not
    widen them, so the labels are left out and the gate refuses."""
    spec = _bars(24)
    spec.layers[0].style["value_labels"] = True
    result = render(spec, PHONE.figure_request("png"))
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
    # too many even for a figure grown to its height limit
    result = render(_bars(150, "y"), PHONE.figure_request("png"))
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


def test_a_legend_of_long_names_wraps_below_the_plot_and_the_figure_grows(monkeypatch):
    """12 long series names: wrapped (every word kept) into a legend below
    the plot, the figure grown so it fits; the gate passes."""
    spec = _series(12)
    result, texts, size = measure(monkeypatch, spec, PHONE.figure_request("png"))
    codes = {w.code for w in result.warnings}
    assert "figure_grown" in codes and size[0] == PHONE.pixel_width and size[1] > 960
    assert problems(texts, size, 12.0) == {"small": [], "clipped": [], "overlaps": []}
    want = {w for i in range(12) for w in f"Series number {i} with a long descriptive name".split()}
    assert want <= words(texts)
    check_legible(result)
    built = build_figure(spec, PHONE.figure_request("png"))
    assert built.axes.get_legend().get_window_extent().y1 <= built.axes.get_window_extent().y0


def test_a_legend_too_long_even_for_a_grown_figure_is_reported():
    result = render(_series(150), PHONE.figure_request("png"))
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


def test_funnel_rates_sit_under_their_own_rows_value_label_on_a_phone():
    """The rate column would cover the first value label on a phone: each
    rate moves under its own stage's value label, inside that stage's row,
    whole (wrapped at most), and the figure grows to hold them."""
    spec = example("funnel")
    built = build_figure(spec, PHONE.figure_request("png"))
    ctx = built.context
    rates = [a for a in built.annotation_artists if "of previous" in a.get_text()]
    assert len(rates) == 3
    labels = {lab.centre: lab for lab in ctx.value_labels}
    step = abs(built.axes.transData.transform((0, 1))[1]
               - built.axes.transData.transform((0, 0))[1])
    for a in rates:
        row = float(built.axes.yaxis.convert_units(a.xy[1]))
        lab = labels[row]
        box, lbox = a.get_window_extent(), lab.text.get_window_extent()
        centre = built.axes.transData.transform((0, row))[1]
        assert box.y1 <= lbox.y0 + 1 and abs(box.x0 - lbox.x0) < 2  # under its own label
        assert centre - step / 2 <= box.y0  # still inside its row
        assert not any(box.overlaps(o.text.get_window_extent()) for o in ctx.value_labels)
        assert " ".join(a.get_text().split()).endswith("of previous")
    assert "figure_grown" in {w.code for w in built.warnings}


def _wide_tree(direction: str = "TB", n: int = 12):
    from ieeVisualizer.network import Edge, NetworkSpec, Node
    from ieeVisualizer.network.model import Layout

    nodes = [Node(id="root", label="Head office")] + [
        Node(id=f"l{i}", label=f"Regional warehouse {i}") for i in range(n)]
    edges = [Edge(source="root", target=f"l{i}") for i in range(n)]
    return NetworkSpec(nodes=nodes, edges=edges, layout=Layout("layered", direction=direction))


def test_a_network_too_big_for_the_phone_is_refused_with_the_real_reason():
    from ieeVisualizer.network import render_network

    top_down = render_network(_wide_tree("TB"), PHONE.figure_request("png"))
    assert gate_refuses(top_down)
    (w,) = [w for w in top_down.warnings if w.code in ("labels_skipped", "labels_abbreviated",
                                                       "label_ambiguous")][:1]
    assert "a taller figure does not help" in w.message and "grown" not in w.message
    assert "too big for this screen" in w.message
    left_right = render_network(_wide_tree("LR", 60), PHONE.figure_request("png"))
    assert gate_refuses(left_right)
    msgs = [w.message for w in left_right.warnings
            if w.code in ("labels_skipped", "labels_abbreviated", "label_ambiguous")]
    assert msgs and all("grown to" in m and "too big for this screen" in m for m in msgs)


def _swap_value_labels(built):
    a, b = built.context.value_labels[0].text, built.context.value_labels[2].text
    a.xy, b.xy = b.xy, a.xy


def _swap_legend_swatches(built):
    legend = built.axes.get_legend()
    h = getattr(legend, "legend_handles", None) or legend.legendHandles
    ca, cb = h[0].get_facecolor(), h[1].get_facecolor()
    h[0].set_facecolor(cb)
    h[1].set_facecolor(ca)


def _swap_wrapped_cycle_styles(built):
    """Entries 0 and 10 share a colour (the cycle wrapped); swap their dash."""
    legend = built.axes.get_legend()
    h = getattr(legend, "legend_handles", None) or legend.legendHandles
    a, b = h[0].get_linestyle(), h[10].get_linestyle()
    h[0].set_linestyle(b)
    h[10].set_linestyle(a)


def _shift_annotation(built):
    note = next(a for a in built.annotation_artists if "Budget" in a.get_text())
    note.xyann = (note.xyann[0] - 300, note.xyann[1])


@pytest.mark.parametrize(("name", "mutate"), [
    ("bar_comparison", _swap_value_labels),     # two bars' labels drawn on each other's bar
    ("bar_comparison", _swap_legend_swatches),  # legend texts beside the other colour
    ("funnel+note", _shift_annotation),         # a note drawn away from its data anchor
    ("12 series", _swap_wrapped_cycle_styles),  # same colour, other dash, other series
])
def test_the_anchor_comparison_tells_a_wrong_drawing_apart(name, mutate):
    """tests/render/anchored.py measures anchors from the drawing: each of
    these drawing errors changes what it reads."""
    spec = {"funnel+note": _funnel_with_a_note, "12 series": lambda: _series(12, "S{}")}.get(
        name, lambda: example(name))()
    assert anchored(spec, DESKTOP, mutate) != anchored(spec, DESKTOP)


def test_legend_texts_are_paired_with_the_swatch_drawn_beside_them():
    """Reordering the handle list without moving anything drawn changes
    nothing: pairs come from the drawing, not from list order."""
    def reorder(built):
        legend = built.axes.get_legend()
        name = "legend_handles" if hasattr(legend, "legend_handles") else "legendHandles"
        setattr(legend, name, list(reversed(getattr(legend, name))))

    spec = _series(12, "S{}")
    assert anchored(spec, DESKTOP, reorder) == anchored(spec, DESKTOP)


def test_attribution_is_rim_to_rim_so_a_large_node_is_judged_like_a_small_one():
    from matplotlib.transforms import Bbox

    from ieeVisualizer.network.render import _attributable

    label = Bbox.from_extents(100, 0, 140, 10)
    pts = [(60.0, 5.0), (158.0, 5.0)]  # own node: radius 30, rim 10 px away; other: rim 16 px
    assert _attributable(0, label, pts, [30.0, 2.0], 1.0)
    assert not _attributable(1, label, pts, [30.0, 2.0], 1.0)


@pytest.mark.parametrize("request_name", ["default request", "no profile"])
def test_without_a_profile_no_label_main_drew_whole_is_shortened_or_dropped(monkeypatch,
                                                                           request_name):
    """Main's rule takes a label's first free place. Looking for a clearer
    place must not cost a label its whole text: a label main's rule draws
    whole is drawn whole (perhaps flagged label_ambiguous)."""
    import random

    import ieeVisualizer.network.render as net

    req = {"default request": RenderRequest(), "no profile": DESKTOP}[request_name]
    rnd = random.Random(3)
    specs = [_random_network(rnd, k, sizes=k % 2 == 0) for k in range(30)]
    worse = []
    for k, spec in enumerate(specs):
        built = net.build_network(spec, req)
        with monkeypatch.context() as m:
            m.setattr(net, "_attributable", lambda *a: True)  # main's rule
            mains = net.build_network(spec, req)
        whole = set(mains.labels) - set(mains.abbreviated_labels)
        lost = whole & (set(built.abbreviated_labels) | set(built.skipped_labels))
        if lost:
            worse.append((k, sorted(spec.nodes[i].display_label for i in lost)))
    assert worse == []


def test_a_callers_annotation_listed_first_is_not_taken_for_a_row_note():
    """Row notes are recognised by content, not by position in the list."""
    spec = example("funnel")
    spec.annotations.insert(0, TextAnnotation(text="Budget cap here", x=20000.0,
                                              y="Visited site"))
    built = build_figure(spec, PHONE.figure_request("png"))
    notes = {a.get_text().replace("\n", " "): a.xy for a in built.annotation_artists}
    assert notes["Budget cap here"][0] == 20000.0
    moved = [xy for text, xy in notes.items() if text.endswith("of previous")]
    assert len(moved) == 3 and all(xy[0] < 20000.0 * 0.7 for xy in moved)  # at their bars


def _funnel_edited(how: str) -> FigureSpec:
    spec = example("funnel")
    a = spec.annotations[0]  # the rate on "Started quote"
    if how == "moved":  # the rate's x changed after the template built it
        spec.annotations[0] = TextAnnotation(text=a.text, x=a.x * 0.9, y=a.y)
    elif how == "same place":  # a caller's own note at the rate column's place
        spec.annotations.append(TextAnnotation(text="Target 20 % next week", x=a.x, y=a.y))
    elif how == "duplicate":
        spec.annotations.append(TextAnnotation(text=a.text, x=a.x, y=a.y))
    return spec


@pytest.mark.parametrize("how", ["moved", "same place"])
def test_a_row_anchored_note_stays_in_its_row_or_is_refused(how):
    """A note anchored on a row that is not moved under the value label (it
    matches no recorded row note) must not wrap upwards into the row above:
    on a phone it is centred on its row, and one still reaching into another
    row is refused (annotation_outside_row)."""
    spec = _funnel_edited(how)
    result = render(spec, PHONE.figure_request("png"))
    built = build_figure(spec, PHONE.figure_request("png"))
    ax = built.axes
    for a in built.annotation_artists:
        y = float(ax.yaxis.convert_units(a.xy[1]))
        lo, hi = sorted(ax.transData.transform([(0, y - 0.5), (0, y + 0.5)])[:, 1])
        box = a.get_window_extent()
        inside = lo - 1 <= box.y0 and box.y1 <= hi + 1
        if not inside:
            assert gate_refuses(result) and "annotation_outside_row" in {
                w.code for w in result.warnings}, a.get_text()
    if how == "moved":
        (w,) = [w for w in result.warnings if w.code == "row_note_stale"]
        assert "18.5% of previous" in w.message and "Started quote" in w.message
        # the edited note fits its own row once placed after layout: it is sent
        check_legible(result)


def _gating(result) -> set:
    try:
        check_legible(result)
    except LegibilityError as e:
        return {i.code for i in e.issues}
    return set()


def test_a_duplicated_row_note_is_refused_by_name():
    result = render(_funnel_edited("duplicate"), PHONE.figure_request("png"))
    (w,) = [w for w in result.warnings if w.code == "row_note_duplicated"]
    assert "18.5% of previous" in w.message and "annotations 0, 3" in w.message
    assert "row_note_duplicated" in _gating(result)  # refused for this, not only overlap


def _tall_note() -> FigureSpec:
    """Six rows, no value labels, and a caller's note too long for its row:
    wrapped on a phone it is taller than the row in every placement."""
    spec = _bars(6, "y")
    spec.layers[0].orientation = "horizontal"
    spec.annotations.append(TextAnnotation(
        text=" ".join(["Kampanja tauolla kaksi päivää"] * 3), x=1.0, y="Region 02"))
    return spec


def test_a_note_too_tall_for_its_row_is_refused_for_that_alone():
    """annotation_outside_row is the only gating code here, so the gate's
    verdict rests on it."""
    result = render(_tall_note(), PHONE.figure_request("png"))
    assert _gating(result) == {"annotation_outside_row"}



def _row_drift(built):
    """A caller's note that grows upwards from its anchor (as wrapping did on
    a phone) into the row above: its text still starts within a few pt of
    the anchor, so "near the anchor" alone does not catch it."""
    note = next(a for a in built.annotation_artists if "Budget" in a.get_text())
    step = abs(built.axes.transData.transform((0, 1))[1]
               - built.axes.transData.transform((0, 0))[1]) * 72.0 / built.figure.dpi
    note.set_text("Budget\ncap\nhere")
    note.set_va("bottom")
    note.set_fontsize(step / 3)


def test_the_anchor_comparison_tells_a_note_drifting_into_another_row():
    spec = _funnel_with_a_note()
    assert anchored(spec, DESKTOP, _row_drift) != anchored(spec, DESKTOP)


def _scatter14() -> FigureSpec:
    from ieeVisualizer.spec import ScatterMark

    cols = {"t": list(range(10))}
    cols.update({f"s{i}": [i + k for k in range(10)] for i in range(14)})
    return FigureSpec(datasets={"d": DataSnapshot.from_columns(cols)},
                      layers=[ScatterMark(data="d", encoding={"x": "t", "y": f"s{i}"},
                                          name=f"P{i}") for i in range(14)])


def _swap_scatter_swatches(built):
    legend = built.axes.get_legend()
    h = getattr(legend, "legend_handles", None) or legend.legendHandles
    a, b = h[0].get_facecolor().copy(), h[1].get_facecolor().copy()
    h[0].set_facecolor(b)
    h[1].set_facecolor(a)


def _redash(built):
    """Two dash patterns that matplotlib both reports as '--'."""
    legend = built.axes.get_legend()
    h = getattr(legend, "legend_handles", None) or legend.legendHandles
    h[10].set_linestyle((0, (6, 2, 1, 2)))


@pytest.mark.parametrize(("make", "mutate"), [
    (_scatter14, _swap_scatter_swatches),  # scatter swatches: extents are infinite
    (lambda: _series(12, "S{}"), _redash),  # a dash pattern, not the name '--'
])
def test_the_anchor_comparison_reads_every_swatch(make, mutate):
    from anchored import legend_pairs

    spec = make()
    built = build_figure(spec, DESKTOP)
    assert all(h is not None for _, h in legend_pairs(built.axes.get_legend()))
    assert anchored(spec, DESKTOP, mutate) != anchored(spec, DESKTOP)


def test_a_collapsed_final_layout_is_reported_as_such():
    """Long category names that leave the plot a few px wide: the saved
    layout is the collapsed one, so layout_collapsed is reported (not only
    legibility_plot_collapsed)."""
    import copy

    t = templates.get("bar_comparison")
    e = copy.deepcopy(t.example())
    c = e["data"]["columns"]
    long = "Erittäin pitkä kategorianimi joka ei mahdu mihinkään kapeaan näkymään"
    c["channel"]["values"] = [f"{x} {long}" for x in ("Google Ads", "Meta", "Orgaaninen haku")]
    c["revenue"]["values"] = [1000.5, 8000.5, 2000.5]
    c["revenue_prev"]["values"] = [900.0, 5400.0, 9900.0]
    result = render(t.build_payload(e), get_profile("email-tablet").figure_request("png"))
    codes = {w.code for w in result.warnings}
    assert {"layout_collapsed", "legibility_plot_collapsed"} <= codes


def test_an_edited_row_note_running_off_the_plot_is_refused():
    """A rate note edited after the template built the spec (here
    translated) is drawn at its own place, the rate column; running past the
    plot's right edge, away from its row's bar, it is refused."""
    spec = example("funnel")
    a = spec.annotations[1]
    spec.annotations[1] = TextAnnotation(text=a.text.replace("of previous", "edellisestä"),
                                         x=a.x, y=a.y)
    result = render(spec, PHONE.figure_request("png"))
    assert "row_note_detached" in _gating(result)


def test_wrapping_keeps_a_number_and_its_unit_together():
    from ieeVisualizer.render.mpl.legibility import wrap

    for width in range(30, 120, 5):
        assert all(not line.startswith("%") for line in
                   wrap("Target 20 % next week, up 5 € from last", 12, width).split("\n"))
