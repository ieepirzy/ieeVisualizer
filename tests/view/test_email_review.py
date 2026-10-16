"""Email output: the send gate, size limits, narrow slots, exact numbers,
line lengths, dark figures, the tablet profile and the text alternatives."""

from __future__ import annotations

import re

import pytest

from ieeVisualizer import themes
from ieeVisualizer.render import LegibilityError, RenderError, check_legible
from ieeVisualizer.spec import DataSnapshot, SpecValidationError
from ieeVisualizer.view import (
    Card,
    FigureComponent,
    Flow,
    FlowLayout,
    FlowNode,
    Grid,
    Kpi,
    Stack,
    Table,
    Text,
    ViewSpec,
    render_email,
    render_view,
)
from ieeVisualizer.view.examples import weekly_briefing
from ieeVisualizer.view.model import FlowEdge


def _hub_flow() -> ViewSpec:
    """Six boxes side by side in one layer: too wide for a phone even with
    the narrowest boxes."""
    nodes = [FlowNode("hub", "Source")] + [FlowNode(f"t{i}", f"Destination {i}")
                                           for i in range(6)]
    edges = [FlowEdge("hub", f"t{i}") for i in range(6)]
    return ViewSpec(root=Stack([Flow(nodes=nodes, edges=edges,
                                     layout=FlowLayout("layered", "TB"))]))


def test_a_flow_too_small_to_read_is_reported_once_and_fails_the_gate():
    for result in (render_email(_hub_flow()),
                   render_view(_hub_flow(), "png", profile="email-phone")):
        codes = [w.code for w in result.warnings]
        assert codes.count("flow_text_too_small") == 1
        assert "legibility_text_too_small" not in codes  # the same problem, said once
        with pytest.raises(LegibilityError, match="flow_text_too_small"):
            check_legible(result)


def test_a_table_too_wide_for_the_phone_png_is_cards_and_passes_the_gate():
    result = render_view(weekly_briefing(), "png", profile="email-phone")
    check_legible(result)  # no cell cut (tests/view/test_view_mobile.py checks the cards)


def test_the_briefing_email_passes_the_gate():
    check_legible(render_email(weekly_briefing()))


def test_a_huge_flow_is_refused_before_it_is_rasterised():
    n = 400
    view = ViewSpec(root=Stack(children=[Flow(
        nodes=[FlowNode(f"n{i}", f"Step {i} word word word") for i in range(n)],
        edges=[FlowEdge(f"n{i}", f"n{i + 1}") for i in range(n - 1)],
        layout=FlowLayout(direction="TB"))]))
    with pytest.raises(RenderError) as exc:
        render_email(view)
    assert exc.value.issues[0].code == "view_too_large"
    assert exc.value.issues[0].path == "/root/children/0"


@pytest.mark.parametrize("depth", [10, 14, 20])
def test_nesting_too_deep_for_a_phone_is_refused_with_a_pointer(depth):
    wb = weekly_briefing()
    node = Stack(children=[Kpi(label="Revenue", value=1234567.0, unit="EUR"),
                           FigureComponent(figure=next(iter(wb.figures)))])
    for _ in range(depth):
        node = Card(title="c", children=[node])
    view = ViewSpec(figures=wb.figures, datasets=wb.datasets, root=node)
    with pytest.raises(SpecValidationError) as exc:
        render_email(view)
    (issue,) = exc.value.issues
    assert issue.code == "too_narrow" and issue.path.startswith("/root/children/0/children/0")


def test_a_kpi_never_changes_notation_silently():
    view = ViewSpec(root=Grid(columns=2, children=[
        Card(children=[Kpi(label="Exact", value=123456789012345.67, decimals=10, unit="EUR")]),
        Kpi(label="Short", value=12.5, unit="EUR")]))
    email = render_email(view)
    (w,) = [w for w in email.warnings if w.code == "kpi_value_compacted"]
    assert "345.6700000000 EUR" in w.message and "e+14" in w.message  # exact and shown
    with pytest.raises(LegibilityError):
        check_legible(email)


def test_no_line_is_longer_than_smtp_allows():
    long = " ".join(["lorem"] * 1600)
    view = ViewSpec(root=Stack(children=[Text(text=long)]))
    for email in (render_email(weekly_briefing()), render_email(view)):
        assert max(len(line) for line in email.html.split("\n")) <= 998
        assert " ".join(email.html.split()).count("lorem") in (0, 1600)


def test_a_dark_figure_in_a_light_email_is_reported():
    dark = [t["name"] for t in themes.list_themes()
            if (themes.get(t["name"]).background or "#ffffff").lower() < "#4"]
    assert dark, "no dark built-in theme"
    wb = weekly_briefing()
    for spec in wb.figures.values():
        spec.theme = dark[0]
    assert "email_dark_figure" in {w.code for w in render_email(wb).warnings}
    assert "email_dark_figure" not in {w.code for w in render_email(weekly_briefing()).warnings}


def test_the_tablet_profile_says_it_is_not_phone_safe():
    email = render_email(weekly_briefing(), "email-tablet")
    assert "email_not_phone_safe" in {w.code for w in email.warnings}
    with pytest.raises(LegibilityError):
        check_legible(email)


@pytest.mark.parametrize("lang", ["en\r\nX-Injected: 1", "en us", "", 7])
def test_lang_must_be_a_language_tag(lang):
    view = ViewSpec(root=Stack(children=[Text(text="hi")]), metadata={"lang": lang})
    with pytest.raises(SpecValidationError, match="/metadata/lang"):
        render_email(view)
    ok = ViewSpec(root=Stack(children=[Text(text="hi")]), metadata={"lang": "fi-FI"})
    assert '<html lang="fi-FI">' in render_email(ok).html


def test_image_names_follow_the_picture_not_only_the_view():
    """Content-ID names carry the image's own hash: the same view with other
    data behind a reference gets other names."""
    from ieeVisualizer.spec import DatasetRef, FigureSpec, LineMark

    def view_with(values):
        spec = FigureSpec(datasets={"d": DatasetRef(kind="table", id="sales")},
                          layers=[LineMark(data="d", encoding={"x": "t", "y": "v"})])
        snap = DataSnapshot.from_columns({"t": [0, 1, 2], "v": values})
        view = ViewSpec(figures={"f": spec}, root=Stack(children=[FigureComponent(figure="f")]))
        return render_email(view, resolver=lambda ref: snap)

    a, b, c = view_with([1, 2, 3]), view_with([1, 2, 3]), view_with([5, 1, 4])
    assert list(a.images) == list(b.images)
    (name,) = a.images
    assert re.fullmatch(r"figure-0\.[0-9a-f]{12}\.png@ieevisualizer", name)
    assert set(a.images) != set(c.images)
    # the alt text summarises the resolved data, never positions
    assert re.search(r'alt="Figure\. v: 3 values from 1 to 5"', c.html)


def test_alt_text_never_reports_positions_as_values():
    from ieeVisualizer import templates
    from ieeVisualizer.view.email import figure_summary

    t = templates.get("vector_field_2d")
    text = figure_summary(t.build_payload(t.example()))
    assert text.startswith("vectors: vector magnitude, ") and " x:" not in text


def test_lines_are_limited_in_bytes_not_characters():
    view = ViewSpec(root=Stack(children=[Text(text=" ".join(["ääkkönen"] * 600))]))
    html = render_email(view).html
    assert max(len(line.encode("utf-8")) for line in html.split("\n")) <= 998


@pytest.mark.filterwarnings("ignore:Glyph .* missing from font")  # the case under test
def test_missing_glyphs_fail_the_gate(monkeypatch):
    no_cjk_fallback(monkeypatch)
    from ieeVisualizer.render import get_profile, render
    from ieeVisualizer.spec import FigureSpec, LineMark

    data = DataSnapshot.from_columns({"x": [0, 1, 2], "y": [1, 2, 3]})
    fig = FigureSpec(title="東京 売上", datasets={"d": data},
                     layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})])
    result = render(fig, get_profile("email-phone").figure_request("png"))
    (w,) = [w for w in result.warnings if w.code == "glyph_missing"]
    assert "東京 売上" in w.message
    flow = ViewSpec(root=Stack(children=[Flow(nodes=[FlowNode("a", "東京"), FlowNode("b", "大阪")],
                                              edges=[FlowEdge("a", "b")])]))
    for out in (result, render_email(flow), render_view(flow, "png", profile="email-phone")):
        with pytest.raises(LegibilityError, match="glyph_missing"):
            check_legible(out)


def no_cjk_fallback(monkeypatch) -> None:
    """These cases use CJK as text the fonts lack: without the CJK fallback a
    host may have installed (tests/render/test_font_fallback.py covers it)."""
    monkeypatch.setattr("ieeVisualizer.render.mpl.style.fallback_families", lambda: ())


def _gated(result) -> bool:
    try:
        check_legible(result)
    except LegibilityError:
        return True
    return False


@pytest.mark.filterwarnings("ignore:Glyph .* missing from font")
@pytest.mark.parametrize(("title", "theme", "refused"), [
    ("Price ($) 東京", None, True),           # a "$" that is not mathtext
    (r"$\mathrm{東京}$ sales", None, True),  # real mathtext
    ("Status ✓ ⚠ ok", "fallback-list-test", False),  # drawn by the fallback font
    ("Hyvä päivä: äöå ÄÖÅ", None, False),
])
def test_missing_glyphs_are_taken_from_the_real_draw(monkeypatch, title, theme, refused):
    no_cjk_fallback(monkeypatch)
    import dataclasses

    from ieeVisualizer.render import get_profile, render
    from ieeVisualizer.spec import FigureSpec, LineMark
    from ieeVisualizer.themes.builtin import REPORT_LIGHT

    if theme and theme not in {t["name"] for t in themes.list_themes()}:
        themes.register(dataclasses.replace(
            REPORT_LIGHT, name=theme, typography=dataclasses.replace(
                REPORT_LIGHT.typography, family=("Liberation Sans", "DejaVu Sans"))))
    data = DataSnapshot.from_columns({"x": [0, 1, 2], "y": [1, 2, 3]})
    fig = FigureSpec(title=title, theme=theme, datasets={"d": data},
                     layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})])
    result = render(fig, get_profile("email-phone").figure_request("png"))
    assert _gated(result) is refused
    assert ("glyph_missing" in {w.code for w in result.warnings}) is refused


@pytest.mark.filterwarnings("ignore:Glyph .* missing from font")
def test_italic_text_without_italic_glyphs_is_refused_upright_is_not():
    italic = ViewSpec(root=Stack(children=[Text(text="*مرحبا بالعالم*")]))
    upright = ViewSpec(root=Stack(children=[Text(text="مرحبا بالعالم")]))
    assert _gated(render_view(italic, "png", profile="email-phone"))
    assert not _gated(render_view(upright, "png", profile="email-phone"))


def test_alt_text_in_3d_uses_vector_magnitude_and_no_position():
    from ieeVisualizer.spec import FigureSpec, LineMark, QuiverMark
    from ieeVisualizer.view.email import figure_summary

    d = DataSnapshot.from_columns({"x": [0.0, 1.0], "y": [0.0, 1.0], "z": [0.0, 1.0],
                                   "u": [3.0, 0.0], "v": [4.0, 0.0], "w": [12.0, 1.0]})
    quiver = FigureSpec(coordinate_system="cartesian3d", datasets={"d": d},
                        layers=[QuiverMark(data="d", encoding={k: k for k in "xyzuvw"})])
    assert figure_summary(quiver) == "vectors: vector magnitude, 2 values from 1 to 13"
    line = FigureSpec(coordinate_system="cartesian3d", datasets={"d": d},
                      layers=[LineMark(data="d", encoding={"x": "x", "y": "y", "z": "z"})])
    assert figure_summary(line) == ""  # x, y, z are positions: no value to claim


@pytest.mark.parametrize("depth", [1, 2, 3, 6])
def test_whole_values_too_wide_for_their_card_are_refused(depth):
    import datetime as dt

    tz = dt.timezone(dt.timedelta(hours=3))
    data = DataSnapshot.from_columns({
        "when": [dt.datetime(2026, 10, 1, 12, 30, 15, 123456, tzinfo=tz)] * 2,
        "x": [-0.00207883514778638, 1.5], "label": ["a b", "c"], "n": [1, 2]})
    node = Table(data="t")
    for _ in range(depth):
        node = Card(title="c", children=[node])
    email = render_email(ViewSpec(datasets={"t": data}, root=Stack(children=[node])))
    if "One card per row" in email.html:
        assert "this table gets on a phone screen" in " ".join(email.html.split())
    assert "value_too_wide_for_screen" in {w.code for w in email.warnings}
    assert _gated(email)


def _updated_at(depth: int, grid: bool) -> ViewSpec:
    import datetime as dt

    tz = dt.timezone(dt.timedelta(hours=3))
    cols = {"Updated at": [dt.datetime(2026, 10, 5, 8, 30, tzinfo=tz),
                           dt.datetime(2026, 10, 12, 9, 15, tzinfo=tz)],
            "Revenue €": [48250.5, 51230.75], "Bookings": [41, 44],
            "Crew": ["Helsinki A", "Espoo B"]}
    node = Table(data="t", title="Weekly")
    for _ in range(depth):
        node = Card(title="Section", children=[node])
    root = (Grid(columns=2, children=[node, Kpi(label="Bookings", value=85)]) if grid
            else Stack(children=[node]))
    return ViewSpec(datasets={"t": DataSnapshot.from_columns(cols)}, root=root)


def test_room_is_the_true_content_width_of_the_narrowest_screen():
    """320 px screen - 2 x 12 px gutters - 2 cards x 26 px - the table card's
    30 px = 214 px for a value two cards deep."""
    import datetime as dt

    when = dt.datetime(2026, 10, 5, 8, 30, 0, 123456, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    email = render_email(ViewSpec(
        datasets={"t": DataSnapshot.from_columns({"at": [when] * 2, "n": [1, 2]})},
        root=Card(children=[Card(children=[Table(data="t")])])))
    (w,) = [w for w in email.warnings if w.code == "value_too_wide_for_screen"]
    assert "the 214 px they get on a 320 px screen" in w.message


def test_a_tz_aware_updated_at_column_in_a_grid_card_is_not_refused():
    """Measured margin (6 %), not 20 %: this fits every tested font at 320 px."""
    check_legible(render_email(_updated_at(1, True)))
