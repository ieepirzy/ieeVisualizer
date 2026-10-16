"""Hostile and oversized ViewSpecs: size limits, nesting, illegal text, slow
paths, KPI/number formatting, grid edge routing and cross-process
determinism. Anything that could hang on a regression runs in a subprocess
with a timeout."""

import dataclasses
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET  # noqa: N817
from pathlib import Path

import pytest

import ieeVisualizer.view.render as view_render
from ieeVisualizer.render import RenderError
from ieeVisualizer.spec import DataSnapshot, SpecParseError, SpecValidationError
from ieeVisualizer.view import (
    Card,
    Comparison,
    FigureComponent,
    Flow,
    FlowEdge,
    FlowLayout,
    FlowNode,
    Kpi,
    Stack,
    Table,
    Text,
    ViewSpec,
    flow_outline,
    layout_flow,
    render_view,
    validate_view,
    view_from_dict,
    view_from_json,
)
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing
from ieeVisualizer.view.layout import _node_size, kpi_lines, table_cells
from ieeVisualizer.view.text import format_number, text_width

ROOT = Path(__file__).resolve().parents[2]


def run_py(code: str, timeout: float, **env: str) -> str:
    """Run ``code`` in a fresh interpreter (this checkout first on the path)."""
    e = dict(os.environ, MPLBACKEND="Agg", **env)
    # tests/ for cost.py: the slow-path budgets are in its reference seconds
    e["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests"), e.get("PYTHONPATH", "")])
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True,
                       text=True, timeout=timeout, env=e, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return r.stdout


def _errors(view):
    return [(i.path, i.code) for i in validate_view(view) if i.severity == "error"]


def _codes(view):
    return {i.code for i in validate_view(view)}


def _chain(n, **layout):
    return Flow(nodes=[FlowNode(f"n{i}", f"N{i}") for i in range(n)],
                edges=[FlowEdge(f"n{i}", f"n{i + 1}") for i in range(n - 1)],
                layout=FlowLayout(**layout))


# --------------------------------------------------------------------------
# H1: truncating a long table cell is not quadratic
# --------------------------------------------------------------------------


def test_long_table_cell_truncates_quickly():
    out = run_py("""
        from cost import measure
        from ieeVisualizer.spec import DataSnapshot
        from ieeVisualizer.view import Stack, Table, ViewSpec, render_view
        ds = DataSnapshot.from_columns({"a": ["x" * 10000], "b": ["y" * 10000]})
        v = ViewSpec(root=Stack([Table(data="d")]), datasets={"d": ds})
        with measure() as c:
            svg = render_view(v, "svg").content.decode()
        print(c.seconds, svg.count("\\u2026"))
    """, timeout=60)
    seconds, ellipses = out.split()
    assert float(seconds) < 15 and int(ellipses) == 2


def test_fit_keeps_the_longest_prefix_that_fits():
    from ieeVisualizer.view.layout import _fit

    s = "The quick brown fox jumps over the lazy dog " * 6
    for w in (0.0, 5.0, 60.0, 200.0, 333.3):
        got = _fit(s, 13, w)
        keep = len(s)  # the previous linear definition, as the reference
        while keep and text_width(s[:keep] + "…", 13) > w:
            keep -= 1
        assert got == s[:keep] + "…"
    assert _fit("short", 13, 500) == "short"


# --------------------------------------------------------------------------
# H2: size limits before layout; linear layering; one layout per flow
# --------------------------------------------------------------------------


def test_oversized_flow_is_refused_before_layout():
    out = run_py("""
        from cost import measure
        from ieeVisualizer.view import Flow, FlowEdge, FlowNode, Stack, ViewSpec, validate_view
        n = 10000
        f = Flow(nodes=[FlowNode(f"n{i}", f"N{i}") for i in range(n)],
                 edges=[FlowEdge(f"n{i}", f"n{i+1}") for i in range(n - 1)])
        with measure() as c:
            issues = validate_view(ViewSpec(root=Stack([f])))
        print(c.seconds, [(i.path, i.code) for i in issues])
    """, timeout=60)
    seconds, codes = out.split(" ", 1)
    assert float(seconds) < 5
    assert eval(codes) == [("/root/children/0/nodes", "too_many_nodes"),  # noqa: S307
                           ("/root/children/0/edges", "too_many_edges")]


def test_flow_budgets_are_per_view_and_dummy_slots_are_counted():
    from ieeVisualizer.view import limits as L

    half = _chain(L.MAX_FLOW_NODES // 2 + 1)
    assert _errors(ViewSpec(root=Stack([half]))) == []
    two = ViewSpec(root=Stack([half, _chain(L.MAX_FLOW_NODES // 2 + 1)]))
    assert _errors(two) == [("/root/children/1/nodes", "too_many_nodes")]
    # chain of 100 plus an edge from n0 to every node: spans add up to ~4900 slots
    fan = _chain(100)
    fan.edges += [FlowEdge("n0", f"n{i}") for i in range(2, 100)]
    assert _errors(ViewSpec(root=Stack([fan]))) == [("/root/children/0/edges",
                                                     "too_many_dummy_nodes")]
    fan.layout = FlowLayout("grid")  # a grid has no layer slots
    assert _errors(ViewSpec(root=Stack([fan]))) == []


def test_layering_is_linear():
    out = run_py("""
        from cost import measure
        from ieeVisualizer.view import Flow, FlowEdge, FlowNode
        from ieeVisualizer.view.flow import _layers, back_edges
        n = 30000
        f = Flow(nodes=[FlowNode(f"n{i}", "x") for i in range(n)],
                 edges=[FlowEdge(f"n{i}", f"n{i+1}") for i in range(n - 1)])
        with measure() as c:
            layer, _ = _layers(f, set(back_edges(f)))
        print(c.seconds, layer["n29999"])
    """, timeout=60)
    seconds, last = out.split()
    assert float(seconds) < 5 and last == "29999"


def test_largest_allowed_flow_lays_out_in_seconds():
    out = run_py("""
        import random
        from cost import measure
        from ieeVisualizer.view import Flow, FlowEdge, FlowNode, Stack, ViewSpec, render_view
        from ieeVisualizer.view import validate_view
        from ieeVisualizer.view import limits as L
        r = random.Random(1); n, e = L.MAX_FLOW_NODES, L.MAX_FLOW_EDGES
        nodes = [FlowNode(f"n{i}", f"Node {i}", group=r.choice([None, "A", "B"]))
                 for i in range(n)]
        edges = []
        while len(edges) < e:
            a = r.randrange(n - 20)
            edges.append(FlowEdge(f"n{a}", f"n{min(n - 1, a + r.randint(20, 60))}"))
        v = ViewSpec(root=Stack([Flow(nodes=nodes, edges=edges)]))
        assert not [i for i in validate_view(v) if i.severity == "error"]
        with measure() as c:
            render_view(v, "svg"); render_view(v, "html")
        print(c.seconds)
    """, timeout=120)
    assert float(out) < 20


def test_crossing_count_matches_pairwise_definition():
    from ieeVisualizer.view.flow import _crossings

    r = random.Random(7)
    for _ in range(200):
        top = [f"a{i}" for i in range(r.randint(1, 8))]
        bot = [f"b{i}" for i in range(r.randint(1, 8))]
        r.shuffle(top)
        r.shuffle(bot)
        links = [(r.choice(top), r.choice(bot)) for _ in range(r.randint(0, 15))]
        where = {**{n: 0 for n in top}, **{n: 1 for n in bot}}
        pos = {n: i for lay in (top, bot) for i, n in enumerate(lay)}
        segs = [(pos[u], pos[v]) for u, v in links]
        brute = sum((a - c) * (b - d) < 0 for i, (a, b) in enumerate(segs)
                    for c, d in segs[i + 1:])
        assert _crossings([top, bot], links, where) == brute


def test_each_flow_is_laid_out_once_per_render(monkeypatch):
    import ieeVisualizer.view.flow as flow_mod

    calls = []
    real = flow_mod.layout_flow

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(flow_mod, "layout_flow", counting)
    for fmt in ("svg", "html"):
        calls.clear()
        render_view(reporting_pipeline(), fmt)
        assert len(calls) == 1, fmt
    f = reporting_pipeline().root.children[0]
    assert flow_outline(f) == flow_outline(f, real(f, _node_size).order)


def _table_view(rows, cols=2, **kw):
    ds = DataSnapshot.from_columns({f"c{j}": list(range(rows)) for j in range(cols)})
    return ViewSpec(root=Stack([Table(data="d", **kw)]), datasets={"d": ds})


def test_long_table_is_capped_and_says_so():
    from ieeVisualizer.view import limits as L

    v = _table_view(5000)
    issues = validate_view(v)
    assert [(i.code, i.severity) for i in issues] == [("table_truncated", "warning")]
    html = render_view(v, "html").content.decode()
    assert html.count("<tr>") == L.MAX_TABLE_ROWS + 1  # + header row
    assert f"Showing {L.MAX_TABLE_ROWS} of 5000 rows" in html
    svg = render_view(v, "svg").content.decode()
    assert f"Showing {L.MAX_TABLE_ROWS} of 5000 rows" in svg
    assert _errors(_table_view(5000, max_rows=L.MAX_TABLE_ROWS + 1)) == [
        ("/root/children/0/max_rows", "too_many_rows")]
    assert _errors(_table_view(L.MAX_TABLE_ROWS, cols=20)) == [
        ("/root/children/0", "too_many_cells")]


def test_text_and_node_budgets():
    from ieeVisualizer.view import limits as L

    long = ViewSpec(root=Stack([Text("x" * (L.MAX_TEXT_CHARS + 1))]))
    assert _errors(long) == [("/root/children/0/text", "text_too_long")]
    k = L.MAX_TOTAL_TEXT_CHARS // L.MAX_TEXT_CHARS + 1
    many = ViewSpec(root=Stack([Text("y" * L.MAX_TEXT_CHARS) for _ in range(k)]))
    assert _errors(many) == [(f"/root/children/{k - 1}", "too_much_text")]
    nodes = ViewSpec(root=Stack([Text("t") for _ in range(L.MAX_VIEW_NODES)]))
    assert _errors(nodes) == [(f"/root/children/{L.MAX_VIEW_NODES - 1}", "too_many_nodes")]
    spec = weekly_briefing().figures["trend"]
    figs = ViewSpec(root=Stack([FigureComponent(spec=spec)
                                for _ in range(L.MAX_FIGURES + 1)]))
    assert _errors(figs) == [(f"/root/children/{L.MAX_FIGURES}", "too_many_figures")]


def test_limits_are_documented_with_their_values():
    from ieeVisualizer.view import limits as L

    doc = (ROOT / "docs" / "VIEWS.md").read_text(encoding="utf-8")
    names = [n for n in dir(L) if n.startswith("MAX_")]
    assert len(names) >= 11
    for n in names:
        assert re.search(rf"`{n}`\s*\|\s*{getattr(L, n):,}".replace(",", "[,_ ]?"), doc), n


def test_png_height_is_bounded():
    v = ViewSpec(root=Stack([Text(" ".join(["word"] * 1900)) for _ in range(5)]))
    assert _errors(v) == []
    with pytest.raises(RenderError) as ei:
        render_view(v, "png", scale=4)
    assert ei.value.issues[0].code == "view_too_large"


# --------------------------------------------------------------------------
# M1: nesting depth
# --------------------------------------------------------------------------


def _nested(depth, kind="stack"):
    d = {"type": "text", "text": "x"}
    for _ in range(depth - 1):
        d = {"type": kind, "children": [d]}
    return {"schema_version": "1.0", "root": d}


def test_depth_limit_at_parse():
    from ieeVisualizer.view import limits as L

    assert validate_view(view_from_dict(_nested(L.MAX_DEPTH))) == []
    for kind in ("stack", "card", "grid"):
        with pytest.raises(SpecParseError) as ei:
            view_from_dict(_nested(500, kind))
        (issue,) = ei.value.issues
        assert issue.code == "too_deep"
        assert issue.path == "/root" + "/children/0" * (L.MAX_DEPTH - 1) + "/children"
    with pytest.raises(SpecParseError) as ei:
        view_from_json('{"schema_version": "1.0", "root": {"type": "text", "text": "x"}, '
                       '"metadata": {"a": ' + "[" * 100000 + "]" * 100000 + "}}")
    assert ei.value.issues[0].code == "too_deep"


def test_depth_limit_at_validate_and_render():
    from ieeVisualizer.view import limits as L

    for depth in (L.MAX_DEPTH + 1, 400, 5000):
        node = Text("x")
        for _ in range(depth - 1):
            node = Card([node])
        v = ViewSpec(root=node)
        (issue,) = validate_view(v)
        assert issue.code == "too_deep"
        assert issue.path == "/root" + "/children/0" * L.MAX_DEPTH
        with pytest.raises(SpecValidationError):
            render_view(v, "svg")


# --------------------------------------------------------------------------
# M2: XML-illegal code points
# --------------------------------------------------------------------------


def test_control_characters_and_surrogates_are_rejected():
    v = ViewSpec(title="a\x0bb", root=Stack([Text("t\x01x"), Text("ok\ttab\nline\r")]))
    assert _errors(v) == [("/root/children/0/text", "invalid_text"), ("/title", "invalid_text")]
    with pytest.raises(SpecValidationError):
        render_view(v, "svg")
    assert _errors(ViewSpec(title="a\ud800b", root=Text("x"))) == [("/title", "invalid_text")]
    with pytest.raises(SpecParseError) as ei:
        view_from_json('{"schema_version": "1.0", "title": "a\\ud800", '
                       '"root": {"type": "text", "text": "x"}}')
    assert [(i.path, i.code) for i in ei.value.issues] == [("/title", "invalid_text")]
    ds = DataSnapshot.from_columns({"a": ["fine", "bad\x02"]})
    tv = ViewSpec(root=Table(data="d"), datasets={"d": ds})
    assert _errors(tv) == [("/datasets/d/columns/a/values/1", "invalid_text")]


def test_embedded_figure_strings_are_checked():
    v = weekly_briefing()
    v.figures["trend"].title = "a\x01b"
    assert _errors(v) == [("/figures/trend/title", "invalid_text")]
    for fmt in ("svg", "html", "png"):
        with pytest.raises(SpecValidationError):
            render_view(v, fmt)
    v = weekly_briefing()
    spec = v.figures["trend"]
    spec.caption = "c\ud800"
    v.root.children.append(FigureComponent(spec=spec))
    paths = [p for p, c in _errors(v) if c == "invalid_text"]
    assert paths == [f"/root/children/{len(v.root.children) - 1}/spec/caption",
                     "/figures/trend/caption"]


def test_unparsable_figure_svg_is_a_render_error(monkeypatch):
    real = view_render.render

    def broken(spec, req, resolver=None):
        res = real(spec, req, resolver=resolver)
        if req.format == "svg":
            res = dataclasses.replace(res, content=res.content.replace(b"</svg>",
                                                                       b"\x01</svg>"))
        return res

    monkeypatch.setattr(view_render, "render", broken)
    with pytest.raises(RenderError) as ei:
        render_view(weekly_briefing(), "svg")
    assert [(i.path, i.code) for i in ei.value.issues] == [("/figures/trend",
                                                            "figure_render_failed")]


# --------------------------------------------------------------------------
# M3 / M4 / Low: numbers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value, ref, decimals, expected", [
    (0.3, 0.1, None, "▲ +0.2 vs prev (0.1)"),
    (1.1, 1.0, None, "▲ +0.1 vs prev (1)"),
    (0.123, 0.1, None, "▲ +0.023 vs prev (0.1)"),
    (0.1, 0.3, None, "▼ −0.2 vs prev (0.3)"),
    (100, 55, None, "▲ +45 vs prev (55)"),
    (0.3, 0.1, 2, "▲ +0.20 vs prev (0.10)"),
    (2.5, 2.5, None, "= 0 vs prev (2.5)"),
])
def test_kpi_delta_has_no_float_noise(value, ref, decimals, expected):
    _, cmp = kpi_lines(Kpi(label="k", value=value, decimals=decimals,
                           comparison=Comparison(ref, "prev")))
    assert cmp == expected


@pytest.mark.parametrize("d, ok", [(-1, False), (11, False), (10**6, False), (0, True),
                                   (10, True)])
def test_kpi_decimals_range(d, ok):
    v = ViewSpec(root=Stack([Kpi(label="x", value=1.5, decimals=d)]))
    if ok:
        assert _errors(v) == []
        render_view(v, "svg")
    else:
        assert _errors(v) == [("/root/children/0/decimals", "invalid_value")]
        with pytest.raises(SpecValidationError):
            render_view(v, "html")


def test_format_number_uses_one_minus_glyph_for_sign_and_exponent():
    assert format_number(1e-05) == "1e\u221205"
    assert format_number(-1e-05) == "\u22121e\u221205"
    assert format_number(-1234) == "\u22121\u202f234"
    assert format_number(-0.5, 2) == "\u22120.50"
    assert "-" not in format_number(-1.5e-300) + format_number(-1e23)


# --------------------------------------------------------------------------
# M5: unequal column lengths
# --------------------------------------------------------------------------


def test_unequal_columns_are_reported_and_never_drawn_as_empty():
    ds = DataSnapshot.from_columns({"week": ["w1", "w2", "w3"], "bookings": [5, None]})
    v = ViewSpec(root=Stack([Table(data="d")]), datasets={"d": ds})
    assert _errors(v) == [("/datasets/d/columns/bookings", "column_length_mismatch")]
    for fmt in ("svg", "html"):
        with pytest.raises(SpecValidationError):
            render_view(v, fmt)
    with pytest.raises(RenderError) as ei:  # the layout's own backstop
        table_cells(Table(data="d"), ds, "/root/children/0")
    assert ei.value.issues[0].code == "column_length_mismatch"
    parsed = view_from_dict({"schema_version": "1.0", "root": {"type": "table", "data": "d"},
                             "datasets": {"d": {"type": "snapshot", "columns": {
                                 "a": {"values": [1, 2, 3]}, "b": {"values": [1]}}}}})
    assert _errors(parsed) == [("/datasets/d/columns/b", "column_length_mismatch")]


# --------------------------------------------------------------------------
# M6: grid edges never pass through a node they do not connect
# --------------------------------------------------------------------------


def _crosses(p, q, b, shrink=0.5):
    """Does segment p-q enter the interior of box b (shrunk by ``shrink``)?"""
    lo, hi = 0.0, 1.0
    for d, a, mn, mx in ((q[0] - p[0], p[0], b.x + shrink, b.x + b.w - shrink),
                         (q[1] - p[1], p[1], b.y + shrink, b.y + b.h - shrink)):
        if d == 0:
            if not mn < a < mx:
                return False
            continue
        t0, t1 = sorted(((mn - a) / d, (mx - a) / d))
        lo, hi = max(lo, t0), min(hi, t1)
        if lo >= hi:
            return False
    return True


def _on_border(b, p, tol=0.6):
    x, y = p
    inside = b.x - tol <= x <= b.x + b.w + tol and b.y - tol <= y <= b.y + b.h + tol
    return inside and min(abs(x - b.x), abs(x - b.x - b.w), abs(y - b.y),
                          abs(y - b.y - b.h)) <= tol


def test_grid_edge_routes_around_the_box_between():
    f = Flow(nodes=[FlowNode(x, x) for x in "ABC"], edges=[FlowEdge("A", "C")],
             layout=FlowLayout("grid", columns=3))
    g = layout_flow(f, _node_size)
    route = g.routes[0]
    assert not any(_crosses(p, q, g.boxes["B"]) for p, q in zip(route, route[1:]))
    assert _on_border(g.boxes["A"], route[0]) and _on_border(g.boxes["C"], route[-1])
    assert all(0 <= x <= g.width and 0 <= y <= g.height for x, y in route)


def test_grid_routes_never_cross_a_non_endpoint_box():
    checked = 0
    for seed in range(400):
        r = random.Random(seed)
        n = r.randint(2, 16)
        nodes = [FlowNode(f"n{i}", "N" * r.randint(1, 12)) for i in range(n)]
        edges = [FlowEdge(f"n{r.randrange(n)}", f"n{r.randrange(n)}",
                          label=r.choice([None, "a label"])) for _ in range(r.randint(1, 3 * n))]
        cols = r.choice([None, 1, 2, 3, 4, 5])
        f = Flow(nodes=nodes, edges=edges, layout=FlowLayout("grid", columns=cols))
        g = layout_flow(f, _node_size)
        for e, route in zip(edges, g.routes):
            if e.source == e.target:
                continue
            assert _on_border(g.boxes[e.source], route[0])
            assert _on_border(g.boxes[e.target], route[-1])
            assert all(-0.01 <= x <= g.width + 0.01 and -0.01 <= y <= g.height + 0.01
                       for x, y in route), (seed, e)
            for nid, b in g.boxes.items():
                if nid in (e.source, e.target):
                    continue
                for p, q in zip(route, route[1:]):
                    assert not _crosses(p, q, b), (seed, e, nid)
                    checked += 1
    assert checked > 10000


# --------------------------------------------------------------------------
# Nit: a nested figure's stylesheet does not leak into the page
# --------------------------------------------------------------------------


def test_nested_figure_css_does_not_leak():
    html = render_view(weekly_briefing(), "html").content.decode()
    (page_css,) = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)
    assert "stroke-linejoin" not in page_css
    svg = render_view(weekly_briefing(), "svg").content
    assert b"<style" not in svg
    root = ET.fromstring(svg)
    fig = next(el for el in root.iter("{http://www.w3.org/2000/svg}svg")
               if "data-figure" in el.attrib)
    wrapper = fig.find("{http://www.w3.org/2000/svg}g")
    assert wrapper.get("style") == "stroke-linejoin: round;stroke-linecap: butt"


# --------------------------------------------------------------------------
# Determinism across interpreter processes (hash randomisation)
# --------------------------------------------------------------------------

_DIGESTS = """
    import hashlib, json
    from ieeVisualizer.view import (Flow, FlowEdge, FlowLayout, FlowNode, Stack, ViewSpec,
                                    render_view)
    from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing
    nodes = [FlowNode(x, x.upper(), group=g) for x, g in
             [("a", "L1"), ("b", "L2"), ("c", "L1"), ("d", None), ("e", "L2")]]
    edges = [FlowEdge("a", "b"), FlowEdge("b", "c"), FlowEdge("c", "a", "because", "causal"),
             FlowEdge("d", "d", "self"), FlowEdge("a", "e"), FlowEdge("e", "c")]
    views = [weekly_briefing(), reporting_pipeline(),
             ViewSpec(root=Stack([Flow(nodes=nodes, edges=edges)])),
             ViewSpec(root=Stack([Flow(nodes=nodes, edges=edges + [FlowEdge("a", "d")],
                                       layout=FlowLayout("grid", columns=3))]))]
    print(json.dumps([hashlib.sha256(render_view(v, f).content).hexdigest()
                      for v in views for f in ("svg", "html", "png")]))
"""


def test_outputs_are_identical_across_processes_and_hash_seeds():
    runs = [json.loads(run_py(_DIGESTS, timeout=300, PYTHONHASHSEED=seed))
            for seed in ("0", "4242")]
    assert runs[0] == runs[1] and len(runs[0]) == 12
    here = hashlib.sha256(render_view(reporting_pipeline(), "svg").content).hexdigest()
    assert here == runs[0][3]
