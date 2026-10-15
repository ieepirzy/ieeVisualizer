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
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing
from ieeVisualizer.view.model import FlowEdge


def test_a_flow_too_small_to_read_fails_the_gate_in_email_as_in_the_view_png():
    email = render_email(reporting_pipeline())
    assert "legibility_text_too_small" in {w.code for w in email.warnings}
    with pytest.raises(LegibilityError):
        check_legible(email)
    with pytest.raises(LegibilityError):
        check_legible(render_view(reporting_pipeline(), "png", profile="email-phone"))


def test_a_cut_table_in_the_phone_png_fails_the_gate():
    with pytest.raises(LegibilityError, match="table_cells_truncated"):
        check_legible(render_view(weekly_briefing(), "png", profile="email-phone"))


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


def test_a_short_table_with_long_headers_stays_a_table():
    view = ViewSpec(datasets={"t": DataSnapshot.from_columns(
        {"bookings this week": [41, 44], "quotes this week": [96, 101],
         "conversion this week": [0.43, 0.44]})},
        root=Stack(children=[Table(data="t")]))
    html = render_email(view).html
    assert "One card per row" not in html and "<th" in html


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


def test_image_names_are_content_ids_unique_per_view_and_stable():
    a, b = render_email(weekly_briefing()), render_email(weekly_briefing())
    other = render_email(reporting_pipeline())
    assert list(a.images) == list(b.images)
    (name,) = a.images
    assert re.fullmatch(r"figure-0\.[0-9a-f]{12}\.png@ieevisualizer", name)
    assert not set(a.images) & set(other.images)
