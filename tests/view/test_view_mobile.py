"""Views at phone and tablet profiles, and the email-safe HTML."""

from __future__ import annotations

import re
import struct

import pytest

from ieeVisualizer.render import get_profile
from ieeVisualizer.spec import DataSnapshot
from ieeVisualizer.view import (
    Grid,
    Kpi,
    Stack,
    Table,
    ViewSpec,
    render_email,
    render_view,
)
from ieeVisualizer.view.draw import Label, Rect
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing
from ieeVisualizer.view.layout import layout_view


def png_width(content: bytes) -> int:
    return struct.unpack(">I", content[16:20])[0]


def test_phone_view_png_is_legible_and_profile_sized():
    result = render_view(weekly_briefing(), "png", profile="email-phone")
    assert png_width(result.content) == get_profile("email-phone").pixel_width
    codes = {w.code for w in result.warnings}
    assert not {c for c in codes if c.startswith("legibility_")}
    assert "table_cells_truncated" not in codes  # one card per row instead


def _labels(items) -> list:
    from ieeVisualizer.view.draw import Group

    out = []
    for it in items:
        if isinstance(it, Group):
            out += _labels(it.items)
        elif isinstance(it, Label):
            out.append(it)
    return out


def test_a_table_too_wide_for_a_phone_png_is_one_card_per_row_with_every_cell_whole():
    from ieeVisualizer.view.layout import table_cells

    view = weekly_briefing()
    items, _, _ = layout_view(view, 320, get_profile("email-phone"))
    texts = [lab.text for lab in _labels(items)]
    assert not [t for t in texts if t.endswith("…")]
    table = next(n for n in _walk(view.root) if isinstance(n, Table))
    _, heads, rows, _ = table_cells(table, view.datasets[table.data])
    for row in rows:
        assert set(row) <= set(texts)  # every cell whole, on a line of its own
    assert set(heads) <= set(texts)
    assert any(t.startswith("One card per row") for t in texts)
    plain, _, _ = layout_view(view, 320)  # no profile: cut as before
    assert any(lab.text.endswith("…") for lab in _labels(plain))


def _walk(n):
    yield n
    for c in getattr(n, "children", None) or ():
        yield from _walk(c)


def _node_lines(items, out=None, node=None) -> dict:
    """``{node id: [lines drawn in its box]}``."""
    from ieeVisualizer.view.draw import Group

    out = {} if out is None else out
    for it in items:
        if isinstance(it, Group):
            _node_lines(it.items, out, it.attrs.get("data-node", node))
        elif isinstance(it, Label) and node is not None:
            out.setdefault(node, []).append(it.text)
    return out


def test_flow_on_a_phone_wraps_node_labels_between_words_at_the_minimum():
    result = render_view(reporting_pipeline(), "png", profile="email-phone")
    codes = {w.code for w in result.warnings}
    assert "flow_text_too_small" not in codes and "legibility_text_too_small" not in codes
    items, _, _ = layout_view(reporting_pipeline(), 320, get_profile("email-phone"))
    flow = next(n for n in _walk(reporting_pipeline().root) if hasattr(n, "nodes"))
    drawn = _node_lines(items)
    for node in flow.nodes:
        lines = drawn[node.id]
        assert " ".join(lines).split() == node.label.split()  # whole words, in order
    assert any(len(lines) > 1 for lines in drawn.values())  # some did wrap
    tablet = render_view(reporting_pipeline(), "png", profile="email-tablet")
    assert not [w for w in tablet.warnings if w.code.startswith(("legibility", "flow_"))]


def test_narrow_profile_stacks_kpi_tiles():
    view = ViewSpec(root=Grid(columns=3, children=[
        Kpi(label=f"KPI {i}", value=1234567.0 * i, unit="EUR") for i in range(1, 4)]))
    items, _, _ = layout_view(view, 320, get_profile("email-phone"))
    tiles = [i for i in items if isinstance(i, Rect) and i.fill == "#f6f8fa"]
    assert len(tiles) == 3 and len({t.x for t in tiles}) == 1  # one under another
    labels = [i for i in items if isinstance(i, Label)]
    assert min(lab.size for lab in labels) >= 12  # no value shrinks below the minimum
    wide, _, _ = layout_view(view, 960)
    assert len({t.x for t in wide if isinstance(t, Rect) and t.fill == "#f6f8fa"}) == 3


def test_profile_and_explicit_width_are_exclusive():
    with pytest.raises(ValueError, match="profile sets width"):
        render_view(weekly_briefing(), "png", profile="email-phone", width=400)


# --------------------------------------------------------------------------
# email HTML
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def briefing_email():
    return render_email(weekly_briefing())


def test_email_html_needs_no_style_block_script_or_svg(briefing_email):
    html = briefing_email.html
    for banned in ("<style", "<script", "@media", "<svg", "class=", "data:image", "<link"):
        assert banned not in html
    assert '<meta name="color-scheme" content="light">' in html
    assert "-webkit-text-size-adjust:100%" in html


def test_email_images_fit_the_column_and_are_attached(briefing_email):
    imgs = re.findall(r"<img [^>]*>", briefing_email.html)
    assert len(imgs) == len(briefing_email.images) == 1
    for tag in imgs:
        width = int(re.search(r'width="(\d+)"', tag).group(1))
        assert f"max-width:{width}px" in tag and "width:100%" in tag and "height:auto" in tag
        # Content-ID form (local@domain), unique per view, same for the same view
        assert re.search(r'src="cid:figure-0\.[0-9a-f]{12}\.png@ieevisualizer"', tag)
        # blocked images still say what the figure shows, numbers included
        assert re.search(r'alt="Bookings and quotes per week\. bookings: 6 values from 41 to 55',
                         tag)
    (png,) = briefing_email.images.values()
    prof = get_profile("email-phone")  # laid out for the 296 px content of a 320 px screen
    assert png_width(png) == round((prof.css_width - 24) * prof.scale)
    assert not [w for w in briefing_email.warnings if w.code.startswith("legibility_")]


def test_email_table_too_wide_for_a_phone_becomes_cards_with_every_cell():
    cols = {f"column {c}": [f"value {c}{r} with some words" for r in range(3)] for c in "ABCD"}
    view = ViewSpec(datasets={"t": DataSnapshot.from_columns(cols)},
                    root=Stack(children=[Table(data="t", title="Wide")]))
    html = " ".join(render_email(view).html.split())  # long lines break at spaces
    assert "One card per row: the 4 columns do not fit" in html
    for values in cols.values():
        for v in values:
            assert v in html  # whole, never cut with an ellipsis
    narrow = ViewSpec(datasets={"t": DataSnapshot.from_columns({"n": [1, 2], "m": [None, 4]})},
                      root=Stack(children=[Table(data="t")]))
    html = render_email(narrow).html
    assert "One card per row" not in html and "<th" in html and "—" in html


def test_email_flow_is_an_image_with_its_text_version():
    email = render_email(reporting_pipeline())
    assert [n.split(".")[0] for n in email.images] == ["flow-0"]
    assert "Text version" in email.html and "<ol" in email.html
    assert "flow_text_too_small" not in {w.code for w in email.warnings}  # boxes narrowed


def test_email_reports_gmail_clipping():
    view = ViewSpec(datasets={"t": DataSnapshot.from_columns(
        {f"c{c}": [f"row {i} column {c} " + "x" * 20 for i in range(200)] for c in range(5)})},
        root=Stack(children=[Table(data="t")]))  # 1000 cells, the view's limit
    email = render_email(view)
    assert len(email.html.encode()) > 102 * 1024
    assert "email_clipped_by_gmail" in {w.code for w in email.warnings}


def test_phone_png_table_cards_keep_long_tokens_whole_and_gate_them():
    from ieeVisualizer.render import LegibilityError, check_legible

    url = "https://example.com/a/very/long/unbroken/path/segment/that/cannot/wrap?id=123456"
    view = ViewSpec(datasets={"t": DataSnapshot.from_columns({
        "Customer reference": [url, "Helsinki A"], "Revenue total for the period": [1.0, 3.0],
        "Created at": ["2026-01-07", "2026-01-08"]})},
        root=Stack(children=[Table(data="t")]))
    items, _, _ = layout_view(view, 320, get_profile("email-phone"))
    assert url in [lab.text for lab in _labels(items)]  # whole, on one line
    with pytest.raises(LegibilityError, match="value_too_wide_for_screen"):
        check_legible(render_view(view, "png", profile="email-phone"))
