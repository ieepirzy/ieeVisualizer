"""render_view / export_view: geometry, escaping, null handling, HTML safety,
real figure embedding, determinism and the manifest."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET  # noqa: N817

import pytest

import ieeVisualizer.view.render as view_render
from ieeVisualizer.artifacts import MemoryWriter
from ieeVisualizer.spec import DataSnapshot, SpecValidationError
from ieeVisualizer.view import (
    Comparison,
    FigureComponent,
    Flow,
    FlowEdge,
    FlowNode,
    Grid,
    Kpi,
    Note,
    Stack,
    Table,
    Text,
    ViewSpec,
    export_view,
    render_view,
)
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing

SVG = "{http://www.w3.org/2000/svg}"
EVIL = "<script>alert('x')</script> & \"q\""


def _svg(view, **kw):
    return ET.fromstring(render_view(view, "svg", **kw).content)


def _texts(root):
    return ["".join(t.itertext()) for t in root.iter(SVG + "text")]


def _boxes(root):
    out = {}
    for g in root.iter(SVG + "g"):
        if "data-node" in g.attrib:
            r = g.find(SVG + "rect")
            out[g.get("data-node")] = tuple(float(r.get(k)) for k in ("x", "y", "width",
                                                                       "height"))
    return out


def _edges(root):
    out = []
    for g in root.iter(SVG + "g"):
        if "data-edge" in g.attrib:
            line = [tuple(map(float, p.split(","))) for p in
                    g.find(SVG + "polyline").get("points").split()]
            tip = tuple(map(float, g.find(SVG + "polygon").get("points").split()[0].split(",")))
            out.append((g.get("data-from"), g.get("data-to"), line[0], tip))
    return out


def test_pipeline_edges_start_and_end_on_the_right_boxes():
    root = _svg(reporting_pipeline())
    boxes, edges = _boxes(root), _edges(root)
    assert set(boxes) == {"agent", "evidence", "viz", "email", "web"}
    assert [(a, b) for a, b, *_ in edges] == [("agent", "evidence"), ("evidence", "viz"),
                                             ("viz", "email"), ("viz", "web")]
    for a, b, start, tip in edges:
        (ax, ay, aw, ah), (bx, by, _, bh) = boxes[a], boxes[b]
        assert start == pytest.approx((ax + aw, ay + ah / 2), abs=0.02)  # source right side
        assert tip == pytest.approx((bx, by + bh / 2), abs=0.02)  # arrow tip on target left
    xs = [boxes[k][0] for k in ("agent", "evidence", "viz")]
    assert xs == sorted(xs) and boxes["email"][0] == boxes["web"][0] > xs[-1]
    assert boxes["email"][1] != boxes["web"][1]


def test_outputs_are_deterministic():
    for fmt in ("svg", "html", "png"):
        a = render_view(weekly_briefing(), fmt).content
        b = render_view(weekly_briefing(), fmt).content
        assert a == b, fmt


def _evil_view():
    return ViewSpec(
        title=EVIL,
        datasets={"d": DataSnapshot.from_columns({"name": [EVIL, None], "n": [1.5, None]})},
        root=Stack([
            Text(f"**{EVIL}** and *{EVIL}*"),
            Kpi(EVIL, 1, unit="&", comparison=Comparison(None, EVIL), source=EVIL),
            Table("d", title=EVIL),
            Note(EVIL), Note(EVIL, type="footnote"),
            Flow(title=EVIL, nodes=[FlowNode("a", EVIL), FlowNode("b", "b")],
                 edges=[FlowEdge("a", "b", EVIL), FlowEdge("b", "a", EVIL, kind="causal")]),
        ]))


def test_user_text_is_escaped_in_svg_and_html():
    view = _evil_view()
    svg = render_view(view, "svg").content.decode()
    ET.fromstring(svg)  # well-formed: nothing broke out of an element or attribute
    html = render_view(view, "html").content.decode()
    for doc in (svg, html):
        assert "<script" not in doc
        assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt; &amp; &quot;q&quot;" in doc
    assert EVIL in _texts(ET.fromstring(svg))  # and it reads back as the literal text


def test_null_kpi_reads_no_data_never_zero():
    view = ViewSpec(root=Grid([Kpi("Cost", None, unit="EUR"),
                               Kpi("Bookings", 7, comparison=Comparison(None, "last week"))],
                              columns=2))
    texts = _texts(_svg(view))
    assert "no data" in texts and "last week: no data" in texts
    assert not any(re.fullmatch(r"0( EUR)?", t) for t in texts)
    html = render_view(view, "html").content.decode()
    assert '<div class="iv-kpi-value iv-nodata">no data</div>' in html
    assert "0 EUR" not in html


def test_table_nulls_render_as_dash():
    view = ViewSpec(datasets={"d": DataSnapshot.from_columns({"k": ["a", "b"],
                                                              "v": [None, 0.0]})},
                    root=Table("d"))
    texts = _texts(_svg(view))
    assert texts[-4:] == ["a", "—", "b", "0"]  # null is a dash; a real 0 stays 0
    html = render_view(view, "html").content.decode()
    assert '<td class="iv-num iv-null">—</td>' in html


def test_html_is_self_contained_and_static():
    html = render_view(weekly_briefing(), "html").content.decode()
    html += render_view(reporting_pipeline(), "html").content.decode()
    for bad in ("<script", " src=", "href=\"http", "<link", "@import", "url(http", "<iframe"):
        assert bad not in html.lower(), bad
    assert not re.search(r"<[^>]*\son\w+\s*=", html)  # no event-handler attributes
    assert "<details><summary>Text version</summary>" in html
    assert "<li>Reporting agent: feeds Authorized evidence (queries).</li>" in html


def test_figures_go_through_the_real_renderer_with_prefixed_ids(monkeypatch):
    calls = []
    real = view_render.render

    def spy(spec, request, **kw):
        calls.append((spec.title, request.format))
        return real(spec, request, **kw)

    monkeypatch.setattr(view_render, "render", spy)
    view = weekly_briefing()
    view.root.children.append(FigureComponent(figure="trend", aspect=0.3))
    svg = render_view(view, "svg").content.decode()
    assert calls == [("Bookings and quotes per week", "svg")] * 2
    root = ET.fromstring(svg)
    ids = [el.get("id") for el in root.iter() if el.get("id")]
    assert len(ids) == len(set(ids)) > 0
    assert {i.split("-", 1)[0] for i in ids} == {"v0", "v1"}
    refs = re.findall(r'(?:href="#|url\(#)([^")]+)', svg)
    assert refs and set(refs) <= set(ids)
    nested = [s for s in root.iter(SVG + "svg") if s.get("data-figure")]
    assert [s.get("data-figure") for s in nested] == ["/figures/trend"] * 2
    assert "metadata" not in svg


def test_png_is_composed_at_scale():
    import struct

    png = render_view(reporting_pipeline(), "png", width=800, scale=1.5).content
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">I", png[16:20])[0] == 1200


def test_invalid_view_and_format_are_refused():
    with pytest.raises(SpecValidationError) as e:
        render_view(ViewSpec(root=FigureComponent(figure="none")), "svg")
    assert e.value.issues[0].code == "unknown_figure"
    with pytest.raises(ValueError, match="pdf"):
        render_view(reporting_pipeline(), "pdf")


def test_export_view_manifest_lists_files_figures_and_provenance():
    w = MemoryWriter()
    art = export_view(weekly_briefing(), ["html", "svg"], w, name="brief",
                      clock=lambda: __import__("datetime").datetime(2026, 10, 15))
    files = {name: content for (aid, name), content in w.files.items()
             if aid == art.artifact_id}
    assert set(files) == {"view.html", "view.svg", "view.json", "figure-0.spec.json",
                          "manifest.json"}
    m = json.loads(files["manifest.json"])
    for v in m["variants"]:
        assert "sha256:" + hashlib.sha256(files[v["file"]["name"]]).hexdigest() == \
            v["file"]["sha256"]
    assert m["figures"][0]["pointer"] == "/figures/trend"
    assert m["datasets"]["weekly"]["provenance"]["notes"].startswith("Synthetic")
    assert [k["value"] for k in m["kpis"]] == [55.0, 118.0, None]
    assert {w["code"] for v in m["variants"] for w in v["warnings"]} == {"null_values_omitted"}
    again = export_view(weekly_briefing(), ["html", "svg"], MemoryWriter(), name="brief")
    assert again.artifact_id == art.artifact_id
