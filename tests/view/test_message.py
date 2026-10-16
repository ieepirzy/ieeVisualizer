"""build_message / write_eml: the MIME message for an EmailRender, the send
gate applied at assembly, header safety, and the weekly briefing example."""

from __future__ import annotations

import email
import importlib.util
import re
from email import policy
from email.headerregistry import Address
from pathlib import Path

import pytest

from ieeVisualizer.render import LegibilityError
from ieeVisualizer.view import (
    EmailGateError,
    Flow,
    FlowEdge,
    FlowNode,
    MessageError,
    Stack,
    Table,
    Text,
    ViewSpec,
    build_message,
    render_email,
    write_eml,
)
from ieeVisualizer.view.examples import weekly_briefing
from ieeVisualizer.view.layout import kpi_lines, table_cells
from ieeVisualizer.view.model import Card, Grid, Kpi

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weekly_briefing.py"
HEADERS = dict(subject="Weekly briefing", sender="Reports <reports@example.com>",
               recipients=["Team <team@example.com>"])


def _example():
    spec = importlib.util.spec_from_file_location("weekly_briefing_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def briefing():
    return _example().briefing()


@pytest.fixture(scope="module")
def rendered(briefing):
    return render_email(briefing, "email-phone")


def _walk(node):
    yield node
    for c in getattr(node, "children", None) or []:
        yield from _walk(c)


def _parse(msg) -> email.message.EmailMessage:
    return email.message_from_bytes(msg.as_bytes(), policy=policy.default)


def _content(part) -> str:
    """A text part's content with the wire's CRLF line ends read as ``\n``."""
    return part.get_content().replace("\r\n", "\n")


def test_mime_structure_and_content_ids_match_the_html(rendered):
    msg = _parse(build_message(rendered, **HEADERS))
    assert msg.get_content_type() == "multipart/alternative"
    plain, related = msg.get_payload()
    assert plain.get_content_type() == "text/plain"
    assert related.get_content_type() == "multipart/related"
    html_part, *images = related.get_payload()
    assert html_part.get_content_type() == "text/html"
    html = _content(html_part)
    assert html == rendered.html
    refs = re.findall(r'src="cid:([^"]*)"', html)
    assert refs and len(refs) == len(set(refs)) == len(rendered.images)
    by_cid = {}
    for part in images:
        assert part.get_content_type() == "image/png"
        assert part.get_content_disposition() == "inline"
        cid = part["Content-ID"]
        assert cid.startswith("<") and cid.endswith(">")
        assert cid[1:-1] not in by_cid  # exactly one part per Content-ID
        by_cid[cid[1:-1]] = part
        assert part.get_filename() == cid[1:-1].split("@")[0]
        assert part["Content-Transfer-Encoding"] == "base64"
    assert set(by_cid) == set(refs)  # every reference has its part and vice versa
    for name, part in by_cid.items():
        content = part.get_content()
        assert content[:8] == b"\x89PNG\r\n\x1a\n"
        assert content == rendered.images[name]
    for part in (plain, html_part):
        assert part.get_content_charset() == "utf-8"
        assert part["Content-Transfer-Encoding"] == "quoted-printable"


def test_every_line_is_within_the_rfc_limits(rendered):
    raw = build_message(rendered, **HEADERS).as_bytes()
    lines = raw.split(b"\r\n")
    assert b"\n" not in b"".join(lines)  # CRLF only
    assert max(map(len, lines)) <= 76
    assert all(len(line) <= 76 for line in rendered.text.split("\n")
               if " " in line.strip())  # only an unbreakable word may be longer


def test_plain_text_has_every_kpi_line_and_table_cell(briefing, rendered):
    text = _content(_parse(build_message(rendered, **HEADERS)).get_body(("plain",)))
    flat = " ".join(re.split("[ \r\n]+", text))  # wrapping moves breaks, never characters
    nodes = list(_walk(briefing.root))
    kpis = [n for n in nodes if isinstance(n, Kpi)]
    tables = [n for n in nodes if isinstance(n, Table)]
    assert len(kpis) == 4 and tables
    for k in kpis:
        value, comparison = kpi_lines(k)
        assert f"{k.label}: {value}" in flat
        assert k.unit and value.endswith(k.unit)  # the value carries its unit
        assert comparison and comparison in flat  # ... and its comparison basis
        assert k.comparison.label in comparison
    for t in tables:
        names, heads, rows, total = table_cells(t, briefing.datasets[t.data])
        assert len(rows) == total
        for row in rows:
            for head, cell in zip(heads, row):
                assert f"{head}: {cell}" in flat
    assert "Figure: Service orders per week" in flat  # the figure's text alternative
    assert briefing.title in text.splitlines()[0]


def test_plain_text_of_the_library_example_and_a_flow():
    view = weekly_briefing()
    flow = Flow(title="Pipeline", nodes=[FlowNode("a", "Collect"), FlowNode("b", "Report")],
                edges=[FlowEdge("a", "b", "rows")])
    view.root.children.append(Card([flow], title="How it is made"))
    text = render_email(view).text
    assert "Quotes (week 42): 118 quotes" in text
    assert "week 41: no data" in text  # a null comparison is said, not zeroed
    assert "Marketing cost per booking: no data" in text
    assert "quotes (count): —" in text  # a null cell stays a dash
    assert "HOW IT IS MADE" in text and "1. Collect: feeds Report (rows)." in text
    assert "<" not in text.replace("<0", "")  # no markup leaks into plain text
    assert "**" not in text


def test_ungated_render_is_refused_by_default():
    view = ViewSpec(title="Tablet layout", root=Stack([Text("Hello"), Grid([
        Kpi("Revenue", 1234.5, unit="EUR")])]))
    ungated = render_email(view, "email-tablet")  # email_not_phone_safe
    with pytest.raises(EmailGateError) as info:
        build_message(ungated, **HEADERS)
    assert isinstance(info.value, LegibilityError)
    assert info.value.codes == ["email_not_phone_safe"]
    assert info.value.issues[0].code == "email_not_phone_safe"
    msg = build_message(ungated, allow_ungated=True, **HEADERS)  # an explicit choice
    assert msg.get_content_type() == "multipart/alternative"


@pytest.mark.parametrize("field, value, code", [
    ("subject", "Briefing\r\nBcc: victim@example.net", "header_injection"),
    ("subject", "Briefing\nX-Spam: no", "header_injection"),
    ("subject", "Briefing more", "header_injection"),
    ("subject", "   ", "empty_header"),
    ("subject", "x" * 300, "header_too_long"),
    ("sender", "reports@example.com\r\nBcc: victim@example.net", "header_injection"),
    ("sender", "Reports\n <reports@example.com>", "header_injection"),
    ("sender", Address("Evil\x85Bcc: v@example.net", "reports", "example.com"),
     "header_injection"),
    ("sender", "not an address", "invalid_address"),
    ("sender", "reports@localhost", "invalid_address"),
    ("recipients", ["a@example.com\nBcc: victim@example.net"], "header_injection"),
    ("recipients", ["a@example.com, b@example.com"], "invalid_address"),
    ("recipients", ["a@example.com,b@example.com"], "invalid_address"),
    ("recipients", ["<a@example.com> <b@example.com>"], "invalid_address"),
    ("recipients", [], "no_recipients"),
    ("recipients", [42], "invalid_type"),
    ("date", "Fri, 16 Oct 2026 09:00:00 +0300\r\nBcc: v@example.net", "header_injection"),
    ("message_id", "<x@example.com>\nBcc: v@example.net", "header_injection"),
])
def test_header_injection_and_bad_addresses_are_refused(rendered, field, value, code):
    args = dict(HEADERS, **{field: value})
    with pytest.raises(MessageError) as info:
        build_message(rendered, **args)
    assert code in {i.code for i in info.value.issues}
    path = {i.path for i in info.value.issues if i.code == code}
    assert any(p.startswith(f"/{field}") for p in path)


def test_unicode_subject_and_names_round_trip(rendered):
    msg = build_message(rendered, subject="Viikkokatsaus – viikko 41 ✓",
                        sender="Raportointi Öy <reports@example.com>",
                        recipients=["Äiti Mäkelä <a@example.com>", "b@example.com",
                                    Address("Lars Ørsted", "lars", "example.org")])
    raw = msg.as_bytes()
    assert all(b < 128 for b in raw)  # 7-bit on the wire: encoded words + QP + base64
    back = email.message_from_bytes(raw, policy=policy.default)
    assert back["Subject"] == "Viikkokatsaus – viikko 41 ✓"
    assert [a.display_name for a in back["To"].addresses] == ["Äiti Mäkelä", "",
                                                              "Lars Ørsted"]
    assert back["From"].addresses[0].addr_spec == "reports@example.com"


def test_images_not_referenced_by_cid_are_refused(briefing):
    by_file = render_email(briefing, image_src=lambda name: name)
    with pytest.raises(MessageError) as info:
        build_message(by_file, **HEADERS)
    assert {i.code for i in info.value.issues} == {"cid_mismatch"}


def test_text_fallback_replaces_the_generated_text(rendered):
    msg = _parse(build_message(rendered, text_fallback="Numbers: 412 orders\r\n", **HEADERS))
    assert _content(msg.get_body(("plain",))) == "Numbers: 412 orders\n"
    with pytest.raises(MessageError):
        build_message(rendered, text_fallback="  ", **HEADERS)


def test_example_script_passes_the_gate_and_writes_a_parseable_eml(tmp_path, capsys):
    assert _example().main(tmp_path) is True
    assert "gate: PASS" in capsys.readouterr().out
    eml = tmp_path / "briefing.eml"
    msg = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert not msg.defects and all(not p.defects for p in msg.walk())
    assert msg["Subject"].startswith("Fernhill Bicycle Works")
    pngs = sorted(tmp_path.glob("*.png"))
    assert len(pngs) == 2
    html = (tmp_path / "briefing.html").read_text(encoding="utf-8")
    for p in pngs:  # the preview references the files written next to it
        assert f'src="{p.name}"' in html
    parts = {p["Content-ID"][1:-1].split("@")[0]: p.get_content()
             for p in msg.walk() if p.get_content_type() == "image/png"}
    assert parts == {p.name: p.read_bytes() for p in pngs}
    assert "412 orders" in msg.get_body(("plain",)).get_content()


def test_write_eml_returns_the_path(rendered, tmp_path):
    path = write_eml(build_message(rendered, **HEADERS), tmp_path / "x.eml")
    assert path.read_bytes().startswith(b"Subject: Weekly briefing\r\n")
