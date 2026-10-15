"""Second round of hostile-input checks: every measured string is budgeted
and bounded, table columns are limited, slots never get negative widths,
free-form JSON depth is bounded, large numbers are written without invented
digits, grid detours never share a lane, and a delta that rounds to zero
reads as zero. Anything that could hang on a regression runs in a
subprocess with a timeout (``run_py``)."""

import dataclasses
import itertools
import json
import os
import random
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ieeVisualizer.render import RenderError
from ieeVisualizer.spec import (
    DataSnapshot,
    Provenance,
    SpecParseError,
    SpecValidationError,
    validate,
)
from ieeVisualizer.spec._common import copy_json, copy_tree
from ieeVisualizer.view import (
    Card,
    Comparison,
    Flow,
    FlowEdge,
    FlowLayout,
    FlowNode,
    Grid,
    Kpi,
    Stack,
    Table,
    Text,
    ViewSpec,
    apply_view_patch,
    layout_flow,
    render_view,
    validate_view,
    view_from_json,
    view_hash,
    view_to_dict,
)
from ieeVisualizer.view import limits as L
from ieeVisualizer.view.draw import Group, Label, Rect
from ieeVisualizer.view.examples import reporting_pipeline_flow, weekly_briefing
from ieeVisualizer.view.layout import Ctx, _kpi, _node_size, kpi_lines, layout_view
from ieeVisualizer.view.text import format_number, text_width

ROOT = Path(__file__).resolve().parents[2]


def run_py(code: str, timeout: float) -> str:
    """Run ``code`` in a fresh interpreter (this checkout first on the path)."""
    e = dict(os.environ, MPLBACKEND="Agg")
    e["PYTHONPATH"] = str(ROOT) + os.pathsep + e.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True,
                       text=True, timeout=timeout, env=e, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return r.stdout


def _errors(view):
    return [(i.path, i.code) for i in validate_view(view) if i.severity == "error"]


def _deep(n):
    d = cur = {}
    for _ in range(n - 1):
        cur["a"] = {}
        cur = cur["a"]
    return d  # n levels of objects, the outer one included


# --------------------------------------------------------------------------
# 1. table headers, column limits, every measured string
# --------------------------------------------------------------------------


def _unit_table(ncols, unit_len, tables=1):
    sets = {f"d{t}": DataSnapshot.from_columns({f"c{i}": [] for i in range(ncols)},
                                               units={f"c{i}": "u" * unit_len
                                                      for i in range(ncols)})
            for t in range(tables)}
    return ViewSpec(root=Stack([Table(data=f"d{t}", max_rows=0) for t in range(tables)]),
                    datasets=sets)


def test_table_header_units_count_and_columns_are_limited_without_rows():
    errs = _errors(_unit_table(200, 10_000))  # was [] and a 57 s SVG
    assert ("/root/children/0/data", "too_many_columns") in errs
    assert ("/root/children/0", "too_much_text") in errs
    assert _errors(_unit_table(L.MAX_TABLE_COLUMNS + 1, 1)) == [
        ("/root/children/0/data", "too_many_columns")]
    assert _errors(_unit_table(L.MAX_TABLE_COLUMNS, 1)) == []
    k = L.MAX_TOTAL_TABLE_COLUMNS // L.MAX_TABLE_COLUMNS
    assert _errors(_unit_table(L.MAX_TABLE_COLUMNS, 1, tables=k)) == []
    assert _errors(_unit_table(L.MAX_TABLE_COLUMNS, 1, tables=k + 1)) == [
        (f"/root/children/{k}", "too_many_columns")]
    # the header text budget: name + unit per column, with max_rows 0
    per_col = L.MAX_TEXT_CHARS + 2  # "cN" + unit
    fits = L.MAX_TOTAL_TEXT_CHARS // per_col
    assert _errors(_unit_table(fits, L.MAX_TEXT_CHARS)) == []
    assert _errors(_unit_table(fits + 1, L.MAX_TEXT_CHARS)) == [
        ("/root/children/0", "too_much_text")]


def test_view_title_and_kpi_units_count_in_the_text_budget():
    s = "t" * L.MAX_TEXT_CHARS
    n = L.MAX_TOTAL_TEXT_CHARS // L.MAX_TEXT_CHARS
    v = ViewSpec(title=s, root=Stack([Text(s) for _ in range(n)]))
    assert _errors(v) == [(f"/root/children/{n - 1}", "too_much_text")]
    unit = "u" * (L.MAX_TOTAL_TEXT_CHARS // 6 + 1)  # drawn three times per tile
    v = ViewSpec(root=Stack([Kpi("k", 1.0, unit=unit) for _ in range(2)]))
    assert _errors(v) == [("/root/children/1", "too_much_text")]
    assert _errors(ViewSpec(root=Stack([Kpi("k", 1.0, unit=unit)]))) == []


def test_largest_header_table_renders_quickly():
    out = run_py(f"""
        import time
        from ieeVisualizer.spec import DataSnapshot
        from ieeVisualizer.view import Stack, Table, ViewSpec, render_view, validate_view
        n = {L.MAX_TOTAL_TEXT_CHARS // (L.MAX_TEXT_CHARS + 2)}
        ds = DataSnapshot.from_columns({{f"c{{i}}": [] for i in range(n)}},
                                       units={{f"c{{i}}": "u" * 10000 for i in range(n)}})
        v = ViewSpec(root=Stack([Table(data="d", max_rows=0)]), datasets={{"d": ds}})
        assert not [i for i in validate_view(v) if i.severity == "error"]
        t = time.time(); render_view(v, "svg"); render_view(v, "html")
        print(round(time.time() - t, 2))
    """, timeout=120)
    assert float(out) < 5


AUDIT = Path(__file__).resolve().parent / "string_field_audit.py"


def test_every_string_field_is_budgeted_and_renders_in_bounded_time():
    """For every string field that reaches layout or rendering, a 10 000-char
    value (one unbroken word, and words) repeated as often as validation
    allows renders SVG, HTML and (for the word) PNG, or is refused with
    view_too_large, in bounded time (before: 57 s for table units, and
    unbounded with ``max_rows`` 0). Two parts run side by side."""
    env = dict(os.environ, MPLBACKEND="Agg")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    procs = [subprocess.Popen([sys.executable, str(AUDIT), f"{i}/2"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env, cwd=str(ROOT))
             for i in range(2)]
    rows, total = [], None
    for p in procs:
        out, err = p.communicate(timeout=900)
        assert p.returncode == 0, err
        head, *part = [line.split("\t") for line in out.strip().splitlines()]
        total = int(head[1])
        rows += part
    assert total >= 27 and len(rows) == 2 * total, rows
    for name, shape, count, *times in rows:
        assert int(count) >= 1, (name, shape)
        secs = [float(t.replace("refused ", "")) for t in times]
        # matplotlib draws ~100 us per glyph, so a PNG of the whole text
        # budget takes several seconds; SVG and HTML stay well under that
        assert max(secs[:2]) < 10 and (len(secs) == 2 or secs[2] < 30), (name, shape, times)


# --------------------------------------------------------------------------
# 2. slot widths never go negative
# --------------------------------------------------------------------------


def _widths(items):
    for it in items:
        if isinstance(it, Group):
            assert it.scale > 0
            yield from _widths(it.items)
        elif isinstance(it, Rect):
            yield it.w


def test_grid_with_more_columns_than_children_uses_the_children_count():
    ds = DataSnapshot.from_columns({"a": [1, 2]})
    kids = [Text("Hello world"), Kpi("Revenue", 1234.5, unit="EUR"), Table(data="d"),
            reporting_pipeline_flow()]
    v = ViewSpec(root=Stack([Grid(kids, columns=200)]), datasets={"d": ds})
    assert _errors(v) == []
    items, _, _ = layout_view(v)
    assert min(_widths(items)) >= 0
    svg = render_view(v, "svg").content.decode()
    assert 'width="-' not in svg and "scale(-" not in svg
    same = ViewSpec(root=Stack([Grid(kids, columns=4)]), datasets={"d": ds})
    assert svg == render_view(same, "svg").content.decode()
    html = render_view(v, "html").content.decode()
    assert "repeat(4,minmax(0,1fr))" in html


@pytest.mark.parametrize("root, where", [
    (Grid([Text("x") for _ in range(20)], columns=20), "/root/columns"),
    (Stack([Text("x") for _ in range(20)], direction="horizontal"), "/root"),
    (Stack([Grid([Text("x")] * 4, columns=4), Grid([Text("y")] * 11, columns=11)]),
     "/root/children/1/columns"),
])
def test_too_narrow_slots_are_refused(root, where):
    v = ViewSpec(root=root)
    assert _errors(v) == [(where, "too_narrow")]
    with pytest.raises(SpecValidationError):
        render_view(v, "svg")
    with pytest.raises(RenderError):
        layout_view(v)


def test_bad_grid_columns_are_reported_not_raised():
    for cols in ("3", None, 2.5, True, 0, -4):
        v = ViewSpec(root=Grid([Text("x")] * 3, columns=cols))
        assert _errors(v) == [("/root/columns", "invalid_value")], cols


def test_nested_cards_are_refused_before_they_go_negative():
    node = Text("x")
    for _ in range(30):
        node = Card([node], title="c")
    errs = _errors(ViewSpec(root=node))
    assert len(errs) == 1 and errs[0][1] == "too_narrow"


def test_slot_width_is_checked_at_the_rendered_width():
    v = ViewSpec(root=Grid([Text("x")] * 5, columns=5))
    assert _errors(v) == []
    render_view(v, "svg")
    with pytest.raises(SpecValidationError) as ei:
        render_view(v, "html", width=400)
    assert ei.value.issues[0].code == "too_narrow"


def test_no_negative_width_in_random_valid_views():
    r = random.Random(3)
    leaves = [lambda: Text("words " * r.randint(1, 30)), lambda: Kpi("k", r.random() * 1e6),
              lambda: reporting_pipeline_flow()]

    def node(depth):
        if depth > 3 or r.random() < 0.4:
            return r.choice(leaves)()
        kids = [node(depth + 1) for _ in range(r.randint(1, 6))]
        return r.choice([lambda: Grid(kids, columns=r.randint(1, 12)), lambda: Card(kids),
                         lambda: Stack(kids, direction=r.choice(["vertical", "horizontal"]))])()

    drawn = 0
    for _ in range(60):
        v = ViewSpec(root=node(0))
        if _errors(v):
            assert {c for _, c in _errors(v)} == {"too_narrow"}
            continue
        items, _, _ = layout_view(v)
        assert min(_widths(items)) >= 0
        drawn += 1
    assert drawn > 20


# --------------------------------------------------------------------------
# 3. free-form JSON depth
# --------------------------------------------------------------------------


def _with(path, value):
    d = view_to_dict(weekly_briefing())
    cur = d
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    return json.dumps(d)


@pytest.mark.parametrize("path", [
    ("metadata",),
    ("figures", "trend", "metadata"),
    ("figures", "trend", "layers", 0, "style"),
    ("datasets", "weekly", "provenance", "extra"),
])
def test_free_form_json_depth_is_limited_at_parse(path):
    pointer = "/" + "/".join(map(str, path))
    with pytest.raises(SpecParseError) as ei:
        view_from_json(_with(path, _deep(L.MAX_JSON_DEPTH + 1)))
    issue = ei.value.issues[0]
    assert issue.code == "too_deep" and issue.path.startswith(pointer + "/a")
    v = view_from_json(_with(path, _deep(L.MAX_JSON_DEPTH)))  # the deepest allowed
    assert _errors(v) == []
    assert view_hash(v).startswith("sha256:")
    apply_view_patch(v, [{"op": "replace", "path": "/title", "value": "x"}])


def test_deep_json_built_in_python_is_reported_never_raised():
    meta = _deep(5000)
    v = ViewSpec(root=Stack([Text("x")]), metadata=meta)
    assert [c for _, c in _errors(v)] == ["too_deep"]
    with pytest.raises(SpecParseError) as ei:
        view_hash(v)
    assert ei.value.issues[0].code == "too_deep"
    with pytest.raises(SpecValidationError):
        render_view(v, "svg")
    fig = dataclasses.replace(weekly_briefing().figures["trend"], metadata=_deep(5000))
    v = dataclasses.replace(weekly_briefing(), figures={"trend": fig})
    assert [c for _, c in _errors(v)] == ["too_deep"]  # raised RecursionError before
    assert [i.code for i in validate(fig)] == ["too_deep"]
    with pytest.raises(SpecValidationError):
        render_view(v, "html")
    ds = DataSnapshot.from_columns({"c": [1]}, provenance=Provenance(extra=_deep(5000)))
    v = ViewSpec(root=Stack([Table(data="d")]), datasets={"d": ds})
    assert [c for _, c in _errors(v)] == ["too_deep"]


def test_copies_are_iterative_and_bounded():
    loop: dict = {}
    loop["self"] = loop
    for copy in (lambda x: copy_json(x, "", "metadata"), copy_tree):
        with pytest.raises(SpecParseError):
            copy(loop)
    nested = {"a": [1, (2, {"b": (3,)})], "c": None}
    out = copy_tree(nested)
    assert out == nested and out is not nested and out["a"][1][1] is not nested["a"][1][1]
    assert copy_json({"a": [1, {"b": 2.5}]}, "", "m") == {"a": [1, {"b": 2.5}]}
    with pytest.raises(SpecParseError) as ei:
        copy_json({"a": [1, {"b": float("nan")}]}, "/m", "m")
    assert ei.value.issues[0].path == "/m/a/1/b"
    with pytest.raises(SpecParseError):
        apply_view_patch(weekly_briefing(), [{"op": "add", "path": "/metadata/x",
                                              "value": _deep(5000)}])


# --------------------------------------------------------------------------
# 4 and 6. numbers: no invented digits, no "inf", no "-0", tiles hold them
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value, decimals, expected", [
    (1e23, None, "1e+23"),
    (-1e23, None, "−1e+23"),
    (float(2 ** 70), None, "1.1805916207174113e+21"),
    (1e15, None, "1e+15"),
    (999_999_999_999_999.0, None, "999 999 999 999 999"),
    (1e308, 2, "1e+308"),
    (10 ** 30, None, "1e+30"),
    (10 ** 400, None, "1e+400"),
    (12345.678, None, "12 345.678"),
    (2.675, 2, "2.68"),  # the decimal form, not the binary 2.67499999...
    (-0.04, 1, "0.0"),
    (-0.0, None, "0"),
    (1e-05, None, "1e-05"),
])
def test_format_number_writes_only_digits_it_has(value, decimals, expected):
    assert format_number(value, decimals) == expected.replace(" ", " ")


@pytest.mark.parametrize("value, ref, decimals, expected", [
    (1e308, -1e308, None, "▲ +2e+308 vs prev (−1e+308)"),
    (-1e308, 1e308, 2, "▼ −2e+308 vs prev (1e+308)"),
    (1e23, 0, None, "▲ +1e+23 vs prev (0)"),
    (-0.04, 0.0, 1, "= 0.0 vs prev (0.0)"),
    (0.04, 0.0, 1, "= 0.0 vs prev (0.0)"),
    (-0.06, 0.0, 1, "▼ −0.1 vs prev (0.0)"),
    (5, 5, None, "= 0 vs prev (5)"),
])
def test_kpi_delta_is_exact_and_its_arrow_follows_the_rounded_delta(value, ref, decimals,
                                                                    expected):
    _, cmp = kpi_lines(Kpi("k", value, decimals=decimals, comparison=Comparison(ref, "prev")))
    assert cmp == expected
    assert "inf" not in cmp and "−0.0 " not in cmp


def _labels(items):
    for it in items:
        if isinstance(it, Label):
            yield it


@pytest.mark.parametrize("kpi", [
    Kpi("Revenue", 1e308, unit="EUR", comparison=Comparison(-1e308, "last")),
    Kpi("Revenue", -1.7976931348623157e308, unit="EUR per booking, all channels",
        comparison=Comparison(1.7976931348623157e308, "a very long comparison label")),
    Kpi("x" * 300, 123456789.125, unit="u" * 300, decimals=10, source="s" * 300,
        comparison=Comparison(-0.04, "y" * 300)),
])
@pytest.mark.parametrize("width", [80.0, 160.0, 300.0])
def test_kpi_text_stays_inside_its_tile(kpi, width):
    items, h = _kpi(kpi, "/root", 10.0, 20.0, width, Ctx(ViewSpec()))
    tile = items[0]
    assert isinstance(tile, Rect) and (tile.x, tile.y, tile.w, tile.h) == (10.0, 20.0, width, h)
    labels = list(_labels(items))
    assert labels
    for lb in labels:
        right = lb.x + text_width(lb.text, lb.size, lb.bold, lb.italic)
        assert tile.x <= lb.x and right <= tile.x + tile.w - 16 + 1e-6, (lb, right)
        assert tile.y < lb.y - lb.size * 0.7 and lb.y < tile.y + tile.h, lb


# --------------------------------------------------------------------------
# 5. grid detours: distinct lanes
# --------------------------------------------------------------------------


def _collinear_overlap(s, t, tol=0.5):
    (a, b), (c, d) = s, t
    for i in (0, 1):  # i = the shared coordinate (0: vertical, 1: horizontal)
        if a[i] == b[i] == c[i] == d[i]:
            j = 1 - i
            lo = max(min(a[j], b[j]), min(c[j], d[j]))
            hi = min(max(a[j], b[j]), max(c[j], d[j]))
            return hi - lo > tol
    return False


def test_grid_edges_without_a_shared_node_never_share_a_segment():
    pairs = 0
    for seed in range(300):
        r = random.Random(seed)
        n = r.randint(2, 20)
        nodes = [FlowNode(f"n{i}", "N" * r.randint(1, 12)) for i in range(n)]
        edges = [FlowEdge(f"n{r.randrange(n)}", f"n{r.randrange(n)}")
                 for _ in range(r.randint(1, 4 * n))]
        cols = r.choice([None, 1, 2, 3, 4, n])
        g = layout_flow(Flow(nodes=nodes, edges=edges, layout=FlowLayout("grid", columns=cols)),
                        _node_size)
        for (i, e1), (j, e2) in itertools.combinations(enumerate(edges), 2):
            if {e1.source, e1.target} & {e2.source, e2.target}:
                continue
            pairs += 1
            r1, r2 = g.routes[i], g.routes[j]
            assert not any(_collinear_overlap(s, t) for s in zip(r1, r1[1:])
                           for t in zip(r2, r2[1:])), (seed, cols, e1, e2, r1, r2)
    assert pairs > 20_000


def test_a_busy_grid_channel_is_widened():
    """Six detours along the one gap of a single column: each its own lane
    (the old layout stacked lanes 6+ on the cap of the 8th px offset)."""
    nodes = [FlowNode(f"n{i}", f"N{i}") for i in range(14)]
    edges = [FlowEdge(f"n{i}", f"n{i + 7}") for i in range(7)]
    g = layout_flow(Flow(nodes=nodes, edges=edges, layout=FlowLayout("grid", columns=1)),
                    _node_size)
    xs = [route[1][0] for route in g.routes]
    assert len(set(xs)) == 7
    assert all(x <= g.width for x in xs)


# --------------------------------------------------------------------------
# Fenwick crossing count, 2 to 5 layers, against the pairwise definition
# --------------------------------------------------------------------------


def test_crossing_count_matches_brute_force_on_several_layers():
    from ieeVisualizer.view.flow import _crossings

    r = random.Random(11)
    for _ in range(500):
        layers = [[f"{li}_{i}" for i in range(r.randint(1, 7))] for li in range(r.randint(2, 5))]
        where = {n: li for li, lay in enumerate(layers) for n in lay}
        links = []
        for _ in range(r.randint(0, 25)):
            li = r.randrange(len(layers) - 1)
            links.append((r.choice(layers[li]), r.choice(layers[li + 1])))
        for lay in layers:
            r.shuffle(lay)
        pos = {n: i for lay in layers for i, n in enumerate(lay)}
        brute = sum((pos[a] - pos[c]) * (pos[b] - pos[d]) < 0
                    for (a, b), (c, d) in itertools.combinations(links, 2)
                    if where[a] == where[c])
        assert _crossings(layers, links, where) == brute
