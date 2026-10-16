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
    msg = _parse(build_message(rendered, text_fallback="Numbers: 412 orders\r\n",
                               allow_custom_text=True, **HEADERS))
    assert _content(msg.get_body(("plain",))) == "Numbers: 412 orders\n"
    with pytest.raises(MessageError):
        build_message(rendered, text_fallback="  ", allow_custom_text=True, **HEADERS)


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


@pytest.mark.parametrize("unit, value_text", [("%", "94.2%"), ("percent", "94.2 percent")])
def test_a_percentage_delta_is_in_percentage_points(unit, value_text):
    """94.2 % vs 92.7 % is 1.5 percentage points; "+1.5%" reads as a relative
    change (it would be +1.6 % relative), a different and wrong claim."""
    from ieeVisualizer.view import Comparison, render_view

    kpi = Kpi("On time", 94.2, unit=unit, decimals=1, comparison=Comparison(92.7, "week 40"))
    expected = f"▲ +1.5 pp vs week 40 (92.7{'%' if unit == '%' else ' percent'})"
    assert kpi_lines(kpi) == (value_text, expected)
    view = ViewSpec(root=Stack([kpi]))
    email_render = render_email(view)
    assert expected in email_render.text
    assert "+1.5 pp" in email_render.html and "+1.5%" not in email_render.html
    assert "+1.5 pp" in render_view(view, "svg").content.decode("utf-8")
    down = Kpi("Share", 40.0, unit="%", comparison=Comparison(42.5, "plan"))
    assert kpi_lines(down)[1] == "▼ −2.5 pp vs plan (42.5%)"
    other = Kpi("Revenue", 10.0, unit="EUR", comparison=Comparison(8.0, "plan"))
    assert kpi_lines(other)[1] == "▲ +2 EUR vs plan (8 EUR)"  # other units unchanged


def test_band_bounds_have_distinct_names_in_the_alt_text(rendered):
    text = " ".join(re.split("[ \n]+", rendered.text))
    assert "Orders range (low to high), lower bound: 4 values from 329 to 380" in text
    assert "Orders range (low to high), upper bound: 4 values from 413 to 430" in text
    assert "Orders range (low to high):" not in text


# --------------------------------------------------------------------------
# Review of #26: header structure, the seal, dates, pp units, MIME details
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("sender", "=?utf-8?q?CEO_=3Cceo=40x=2Eexample=3E?= <reports@b.example>"),
    ("recipients", ["=?utf-8?q?x=3E=2C_evil=40attacker=2Eexample_=3C?= <a@b.example>"]),
    ("recipients", [Address("=?utf-8?q?ceo=40x=2Eexample?=", addr_spec="a@b.example")]),
    ("subject", "=?utf-8?b?QmNjOiB4QHkuZXhhbXBsZQ0KWC1Ib3N0aWxlOiAx?="),
    ("subject", "Report =?utf-8?q?=0D=0ABcc:_evil@x.example?="),
])
def test_encoded_words_are_refused_not_decoded_into_headers(rendered, field, value):
    with pytest.raises(MessageError) as info:
        build_message(rendered, **dict(HEADERS, **{field: value}))
    assert "encoded_word" in {i.code for i in info.value.issues}


@pytest.mark.parametrize("field, value, code", [
    ("subject", "Weekly ‮briefing", "format_character"),
    ("subject", "Weekly​briefing", "format_character"),
    ("sender", "Reports‮ <reports@example.com>", "format_character"),
    ("sender", "ceo@example.com <reports@example.com>", "address_in_display_name"),
    ("recipients", [Address("ceo@example.com", addr_spec="a@example.com")],
     "address_in_display_name"),
])
def test_invisible_and_address_shaped_names_are_refused(rendered, field, value, code):
    with pytest.raises(MessageError) as info:
        build_message(rendered, **dict(HEADERS, **{field: value}))
    assert code in {i.code for i in info.value.issues}


def test_address_headers_parse_to_exactly_the_addresses_given(rendered):
    from email.utils import getaddresses

    given = [("Smith, Jane", "jane@example.com"), ('Ann "The Boss" Lee', "ann@example.org"),
             ("Äiti Mäkelä", "aiti@example.fi"), ("", "plain@example.net"),
             ("Ops; Night Shift", "ops@example.com")]
    recipients = [Address(n, addr_spec=a) for n, a in given]
    raw = build_message(rendered, subject="S", sender="Raportti Öy <r@example.com>",
                        recipients=recipients).as_bytes()
    for pol in (policy.default, policy.compat32):
        back = email.message_from_bytes(raw, policy=pol)
        to = getaddresses([str(h) for h in back.get_all("To")])
        if pol is policy.compat32:  # compat32 leaves encoded words encoded
            to = [(str(email.header.make_header(email.header.decode_header(n))), a)
                  for n, a in to]
        assert to == given, pol
        assert [a for _, a in getaddresses([str(back["From"])])] == ["r@example.com"]
    assert len(email.message_from_bytes(raw, policy=policy.default)["To"].addresses) == 5


def test_a_render_changed_after_the_gate_is_refused():
    view = ViewSpec(root=Stack([Kpi("Revenue", 48230, unit="EUR", decimals=0)]))
    tablet = render_email(view, "email-tablet")
    tablet.warnings.clear()  # the verdict was for the render as made
    with pytest.raises(MessageError) as info:
        build_message(tablet, **HEADERS)
    assert {(i.path, i.code) for i in info.value.issues} == {("/warnings", "render_modified")}
    edited = render_email(view)
    assert "48\u202f230" in edited.html
    edited.html = edited.html.replace("48\u202f230", "84\u202f230")
    with pytest.raises(MessageError) as info:
        build_message(edited, **HEADERS)
    assert {(i.path, i.code) for i in info.value.issues} == {("/html", "render_modified")}
    swapped = render_email(weekly_briefing())
    name = next(iter(swapped.images))
    swapped.images[name] = b"\x89PNG\r\n\x1a\nother"
    with pytest.raises(MessageError) as info:
        build_message(swapped, **HEADERS)
    assert ("/images", "render_modified") in {(i.path, i.code) for i in info.value.issues}
    from ieeVisualizer.view import EmailRender

    forged = EmailRender(html=edited.html, text="412 orders", profile="email-phone")
    with pytest.raises(MessageError) as info:
        build_message(forged, **HEADERS)
    assert [i.code for i in info.value.issues] == ["not_rendered"]
    good = render_email(view)
    with pytest.raises(MessageError) as info:
        build_message(good, text_fallback="anything at all", **HEADERS)
    assert [i.code for i in info.value.issues] == ["custom_text_not_allowed"]
    with pytest.raises(MessageError) as info:  # allow_ungated does not allow custom text
        build_message(good, text_fallback="anything at all", allow_ungated=True, **HEADERS)
    assert [i.code for i in info.value.issues] == ["custom_text_not_allowed"]
    with pytest.raises(MessageError) as info:  # ... and custom text keeps the seal check
        build_message(tablet, text_fallback="anything", allow_custom_text=True, **HEADERS)
    assert ("/warnings", "render_modified") in {(i.path, i.code) for i in info.value.issues}
    with pytest.raises(EmailGateError):  # ... and the gate
        build_message(render_email(view, "email-tablet"), text_fallback="anything",
                      allow_custom_text=True, **HEADERS)
    build_message(forged, allow_ungated=True, **HEADERS)  # the caller's explicit choice


@pytest.mark.parametrize("field, value, code", [
    ("date", "yesterday", "invalid_date"),
    ("date", "Fri, 16 Oct 2026 09:00:00 -0000", "invalid_date"),  # no time zone
    ("message_id", "x@example.com", "invalid_message_id"),
    ("message_id", "<x y@example.com>", "invalid_message_id"),
])
def test_date_and_message_id_are_validated(rendered, field, value, code):
    with pytest.raises(MessageError) as info:
        build_message(rendered, **dict(HEADERS, **{field: value}))
    assert code in {i.code for i in info.value.issues}


def test_default_date_is_gmt_and_given_values_are_kept(rendered):
    from email.utils import parsedate_to_datetime

    when = parsedate_to_datetime(build_message(rendered, **HEADERS)["Date"])
    assert when.utcoffset() is not None and when.utcoffset().total_seconds() == 0  # not -0000
    msg = build_message(rendered, date="Fri, 16 Oct 2026 09:00:00 +0300",
                        message_id="<briefing-41@example.com>", **HEADERS)
    assert msg["Message-ID"] == "<briefing-41@example.com>"
    assert msg["Date"] == "Fri, 16 Oct 2026 09:00:00 +0300"


def test_related_part_declares_its_root_type(rendered):
    related = _parse(build_message(rendered, **HEADERS)).get_payload()[1]
    assert related.get_content_type() == "multipart/related"
    assert related.get_param("type") == "text/html"


def test_body_lines_starting_with_from_are_encoded():
    e = render_email(ViewSpec(root=Stack([Text("Summary\n\nFrom the workshops: 412 orders")])))
    assert "\nFrom the workshops" in e.text
    raw = build_message(e, **HEADERS).as_bytes()
    lines = raw.split(b"\r\n")
    assert not [ln for ln in lines if ln.startswith(b"From ")]
    assert any(ln.startswith(b"=46rom the workshops") for ln in lines)
    back = email.message_from_bytes(raw, policy=policy.default)
    assert "From the workshops: 412 orders" in back.get_body(("plain",)).get_content()


@pytest.mark.parametrize("unit, delta", [
    ("% YoY", "▲ +2.0 pp YoY"), ("％", "▲ +2.0 pp"), ("pct", "▲ +2.0 pp"),
    ("%-points", "▲ +2.0 %-points"), ("pp", "▲ +2.0 pp"), ("Percent", "▲ +2.0 pp"),
    ("% of revenue", "▲ +2.0 pp of revenue"), ("EUR", "▲ +2.0 EUR"), ("‰", "▲ +2.0 ‰"),
])
def test_percentage_units_all_give_point_deltas(unit, delta):
    from ieeVisualizer.view import Comparison

    line = kpi_lines(Kpi("x", 12.0, unit=unit, decimals=1, comparison=Comparison(10.0, "prev")))
    assert line[1].startswith(delta + " vs prev")


def test_percent_kpi_with_fraction_values_warns_without_gating():
    from ieeVisualizer.render import GATING_CODES
    from ieeVisualizer.view import Comparison, render_view

    kpi = Kpi("On time", 0.942, unit="%", decimals=3, comparison=Comparison(0.927, "week 40"))
    for result in (render_email(ViewSpec(root=Stack([kpi]))),
                   render_view(ViewSpec(root=Stack([kpi])), "svg")):
        codes = [w.code for w in result.warnings]
        assert codes.count("kpi_percent_looks_like_fraction") == 1
    assert "kpi_percent_looks_like_fraction" not in GATING_CODES
    fine = render_email(ViewSpec(root=Stack([Kpi("On time", 94.2, unit="%")])))
    assert not fine.warnings


def test_forecast_connector_point_is_not_counted_as_a_forecast(rendered):
    text = " ".join(re.split("[ \n]+", rendered.text))
    assert "Orders (forecast): 4 values from 371 to 405 count" in text


def test_legend_label_never_doubles_parentheses():
    from ieeVisualizer.render.mpl.style import legend_label

    assert legend_label("Orders range (low to high)", None, "forecast") == \
        "Orders range (low to high, forecast)"
    assert legend_label("Orders", None, "forecast") == "Orders (forecast)"
    assert legend_label("Orders (forecast)", None, "forecast") == "Orders (forecast)"


# --------------------------------------------------------------------------
# Round 2: header encoding on every Python, addresses, QP lines, pp rules
# --------------------------------------------------------------------------

FINNISH = ("Viikkoraportti Pääkaupunkiseutu Länsi-Suomi Itä-Suomi myynti kasvoi 12 % – "
           "johtoryhmälle tilaukset hyväksytty, työmäärä: €48 230 Ääkkönen Hämäläinen").split()
CJK = "週次報告 売上 東京 大阪 增长 보고서 매출".split()


def _phrases(words, seed, n):
    import random

    rnd = random.Random(seed)
    return [" ".join(rnd.choice(words) for _ in range(rnd.randint(2, 18))) for _ in range(n)]


_EW = re.compile(r"=\?([^?]+)\?([bBqQ])\?([^?]*)\?=")


def rfc2047(value: str) -> str:
    """A strict RFC 2047 decoder (the oracle): unfold, decode every encoded
    word, and drop whitespace only *between* two adjacent encoded words
    (section 6.2). Cross-checked against Node's mailparser and Python 3.13."""
    import base64
    import quopri

    value = value.replace("\r\n", "").replace("\n", "").strip()  # the FWS after "Name:"
    out, pos = [], 0
    for m in _EW.finditer(value):
        between = value[pos:m.start()]
        if not (pos and not between.strip()):
            out.append(between)
        charset, enc, data = m.groups()
        raw = (base64.b64decode(data) if enc in "bB"
               else quopri.decodestring(data.replace("_", " ").encode(), header=True))
        out.append(raw.decode(charset))
        pos = m.end()
    out.append(value[pos:])
    return "".join(out)


def only_extra_spaces(got: str, want: str) -> bool:
    """Is ``got`` ``want`` with spaces added (never one missing, nothing else)?"""
    i = 0
    for ch in got:
        if i < len(want) and ch == want[i]:
            i += 1
        elif ch != " ":
            return False
    return i == len(want)


def test_the_oracle_decodes_like_rfc_2047():
    assert rfc2047("=?utf-8?b?w6Q=?= =?utf-8?b?w7Y=?=") == "äö"  # 6.2: dropped
    assert rfc2047("a =?utf-8?b?w6Q=?=  b") == "a ä  b"  # kept next to plain text
    assert rfc2047("=?utf-8?q?a_b?=\r\n =?utf-8?q?=C3=A4?=") == "a bä"
    assert only_extra_spaces("a  b", "a b") and not only_extra_spaces("ab", "a b")


@pytest.mark.parametrize("words", [FINNISH, CJK, FINNISH + CJK + ["Report", "a@b"]],
                         ids=["fi", "cjk", "mixed"])
def test_long_non_ascii_headers_decode_to_exactly_the_text_given(rendered, words):
    """Folding never loses the space between two words (Python 3.9's policy
    put it between encoded words, where decoders drop it). The oracle is a
    strict RFC 2047 decoder on every Python. Python's own default-policy
    parser before 3.13 keeps whitespace between adjacent encoded words
    (against RFC 2047 6.2): there the only difference allowed is an extra
    space, never a missing one."""
    import sys
    from email.utils import getaddresses

    subjects = _phrases(words, 5, 80)
    names = [n.replace("@", "") for n in _phrases(words, 6, 80)]
    for subject, name in zip(subjects, names):
        msg = build_message(rendered, subject=subject, sender=Address(name, "r", "example.com"),
                            recipients=[Address(name, "a", "example.com"), "b@example.com"])
        raw = msg.as_bytes()
        head = raw.split(b"\r\n\r\n", 1)[0]
        assert max(map(len, head.split(b"\r\n"))) <= 76
        assert all(b < 128 for b in head)
        old = email.message_from_bytes(raw, policy=policy.compat32)
        assert rfc2047(old["Subject"]) == subject
        to = getaddresses([old["To"].replace("\r\n", "")])
        assert [(rfc2047(n), a) for n, a in to] == [(name, "a@example.com"),
                                                    ("", "b@example.com")]
        frm = getaddresses([old["From"].replace("\r\n", "")])
        assert [(rfc2047(n), a) for n, a in frm] == [(name, "r@example.com")]
        back = email.message_from_bytes(raw, policy=policy.default)
        got = [str(back["Subject"]), back["To"].addresses[0].display_name,
               back["From"].addresses[0].display_name]
        assert [a.addr_spec for a in back["To"].addresses] == ["a@example.com",
                                                               "b@example.com"]
        for g, want in zip(got, [subject, name, name]):
            if sys.version_info >= (3, 13):
                assert g == want
            else:
                assert only_extra_spaces(g, want), (g, want)


def test_ascii_names_needing_quotes_round_trip(rendered):
    given = [("J. R. Smith", "j@example.com"), ('Ann "Boss" Lee', "ann@example.org"),
             ("Back\\slash", "b@example.org"), ("x" * 70, "long@example.org"),
             ("Ops; Night, Shift", "ops@example.com")]
    raw = build_message(rendered, subject="S" * 120, sender="r@example.com",
                        recipients=[Address(n, addr_spec=a) for n, a in given]).as_bytes()
    back = email.message_from_bytes(raw, policy=policy.default)
    assert [(a.display_name, a.addr_spec) for a in back["To"].addresses] == given
    assert str(back["Subject"]) == "S" * 120


@pytest.mark.parametrize("recipient", [
    "=?utf-8?q?ceo=40x.example?=@b.example",
    "=?utf-8?b?Y2VvQHguZXhhbXBsZQ==?=@attacker.example",
    "a.=?utf-8?q?x=3E?=@b.example",
    "Board <=?utf-8?q?x?=@b.example>",
    "x" * 65 + "@example.com",
    "a@" + "b" * 64 + ".example",
    "a@" + ".".join(["abcdefghi"] * 26) + ".example",
])
def test_bad_local_parts_and_lengths_are_invalid_addresses(rendered, recipient):
    with pytest.raises(MessageError) as info:
        build_message(rendered, **dict(HEADERS, recipients=[recipient]))
    assert [i.code for i in info.value.issues] == ["invalid_address"]


def test_internationalised_domains_are_written_in_punycode(rendered):
    msg = build_message(rendered, **dict(HEADERS, recipients=["Kalle <kalle@pääkaupunki.fi>"]))
    back = email.message_from_bytes(msg.as_bytes(), policy=policy.default)
    assert back["To"].addresses[0].addr_spec == "kalle@" + "pääkaupunki.fi".encode("idna").decode()
    assert back["To"].addresses[0].addr_spec.startswith("kalle@xn--")


@pytest.mark.parametrize("name, code", [
    ("ceo＠example.com", "address_in_display_name"),
    ("ceo﹫example.com", "address_in_display_name"),
    ("＜ceo＞", "address_in_display_name"),
    ("ㅤㅤ", "blank_display_name"),
    ("ﾠ", "blank_display_name"),
    (" 　", "blank_display_name"),
])
def test_lookalike_and_blank_display_names_are_refused(rendered, name, code):
    with pytest.raises(MessageError) as info:
        build_message(rendered, **dict(HEADERS, recipients=[Address(name, "a", "example.com")]))
    assert code in {i.code for i in info.value.issues}


def test_from_lines_stay_within_76_characters():
    line = "From " + "x" * 66 + " more"  # 76 characters: QP wraps it, =46rom lengthens it
    assert len(line) == 76
    e = render_email(ViewSpec(root=Stack([Text("Intro\n\n" + line),
                                          Text("From " + "é" * 60)])))
    raw = build_message(e, **HEADERS).as_bytes()
    lines = raw.split(b"\r\n")
    assert max(map(len, lines)) <= 76
    assert not [ln for ln in lines if ln.startswith(b"From ")]
    text = _content(email.message_from_bytes(raw, policy=policy.default).get_body(("plain",)))
    assert line in text.splitlines() and "From " + "é" * 60 in text


@pytest.mark.parametrize("unit, delta", [
    ("% points", "▲ +2.0 % points"), ("%-pts", "▲ +2.0 %-pts"), ("%-point", "▲ +2.0 %-point"),
    ("%-yks.", "▲ +2.0 %-yks."), ("%-pisteet", "▲ +2.0 %-pisteet"),
    ("%-ile", "▲ +2.0 %-ile"), ("percentile", "▲ +2.0 percentile"), ("%/yr", "▲ +2.0 pp/yr"),
])
def test_points_and_percentiles_keep_their_unit(unit, delta):
    from ieeVisualizer.view import Comparison

    line = kpi_lines(Kpi("x", 12.0, unit=unit, decimals=1, comparison=Comparison(10.0, "prev")))
    assert line[1].startswith(delta + " vs prev")


@pytest.mark.parametrize("kpi", [
    Kpi("Share", 0.5, unit="%"),  # a single value is an ordinary share
    Kpi("Gap", 0.4, unit="pp", comparison=None),
    Kpi("Gap", 0.4, unit="%-points", comparison=Kpi("", 0).comparison),
])
def test_fraction_warning_needs_a_percentage_value_and_comparison(kpi):
    from ieeVisualizer.view import Comparison

    assert not render_email(ViewSpec(root=Stack([kpi]))).warnings
    pts = Kpi("Gap", 0.4, unit="%-points", comparison=Comparison(0.3, "prev"))
    assert not render_email(ViewSpec(root=Stack([pts]))).warnings


@pytest.mark.parametrize("text", ["Revenue 48 230 EUR", "Myynti   kasvoi",
                                  "a b 　c", "Report – 12 % up"])
def test_unicode_spaces_are_encoded_not_written_raw(rendered, text):
    from email.utils import getaddresses

    raw = build_message(rendered, subject=text, sender=Address(text, "r", "example.com"),
                        recipients=["c@example.com"]).as_bytes()
    head = raw.split(b"\r\n\r\n", 1)[0]
    assert all(b < 128 for b in head)
    old = email.message_from_bytes(raw, policy=policy.compat32)
    assert rfc2047(old["Subject"]) == text
    assert rfc2047(getaddresses([old["From"].replace("\r\n", "")])[0][0]) == text


def test_ascii_words_stay_plain_so_buggy_readers_lose_nothing(rendered):
    """Runs of non-ASCII words that fit one encoded word each, separated by
    ASCII words: no two encoded words are adjacent, so even a reader that
    keeps the whitespace between adjacent encoded words (Python < 3.13)
    decodes the text exactly."""
    subject = ("Viikkoraportti myynti Pääkaupunkiseutu kasvoi 12 % Länsi-Suomi tilaukset "
               "Itä-Suomi hyväksytty Ääkkönen ja Hämäläinen")
    name = "Ääkkönen Raportti Hämäläinen"
    msg = build_message(rendered, subject=subject, sender=Address(name, "r", "example.com"),
                        recipients=["c@example.com"])
    raw = msg.as_bytes()
    head = raw.split(b"\r\n\r\n", 1)[0].decode()
    assert not re.search(r"\?=\s+=\?", head)  # no adjacent encoded words
    assert "myynti" in head and "Raportti" in head  # ASCII words written as themselves
    back = email.message_from_bytes(raw, policy=policy.default)
    assert str(back["Subject"]) == subject
    assert back["From"].addresses[0].display_name == name
