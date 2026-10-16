"""Hostile and edge-case input for NetworkSpec: text that XML cannot hold,
lone surrogates, extreme pinned positions, deep stacks, parallel and
reciprocal edges, oversized or deeply nested free-form JSON."""

import json
import sys
import xml.dom.minidom

import numpy as np
import pytest

from ieeVisualizer.network import (
    Edge,
    Layout,
    NetworkSpec,
    Node,
    Quantity,
    build_network,
    compute_layout,
    graph_hash,
    network_from_dict,
    network_from_json,
    network_hash,
    network_outline,
    render_network,
    validate_network,
)
from ieeVisualizer.network.matrix import adjacency
from ieeVisualizer.network.render import KIND_STYLES, MISSING_COLOR
from ieeVisualizer.render import RenderRequest
from ieeVisualizer.render.base import RenderError
from ieeVisualizer.spec import SpecParseError, SpecValidationError
from ieeVisualizer.spec._common import canonical_json

# Names added with these checks are imported inside the tests that use them,
# so that against the code before them each test fails on its own behaviour.
MAX_ABS_POSITION = 1e6  # docs/NETWORKS.md; asserted equal in the test below
MAX_JSON_DEPTH = 64


def doc(**kw):
    d = {"schema_version": "1.0", "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
         "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}]}
    d.update(kw)
    return d


def errors(spec):
    return [(i.path, i.code) for i in validate_network(spec) if i.severity == "error"]


def parse_error(d):
    with pytest.raises(SpecParseError) as ei:
        network_from_dict(d) if isinstance(d, dict) else network_from_json(d)
    return ei.value.issues[0].path, ei.value.issues[0].code


# ---- 1. control characters ------------------------------------------------

BAD_CHARS = ["\x00", "\x01", "\x08", "\x0b", "\x0c", "\x1f", "￾", "￿"]


@pytest.mark.parametrize("ch", BAD_CHARS)
@pytest.mark.parametrize("make, path", [
    (lambda c: doc(nodes=[{"id": f"a{c}"}]), "/nodes/0/id"),
    (lambda c: doc(nodes=[{"id": "a", "label": c}, {"id": "b"}, {"id": "c"}]),
     "/nodes/0/label"),
    (lambda c: doc(nodes=[{"id": "a", "group": c}, {"id": "b"}, {"id": "c"}]),
     "/nodes/0/group"),
    (lambda c: doc(edges=[{"source": "a", "target": "b", "kind": c}]), "/edges/0/kind"),
    (lambda c: doc(title=f"x{c}q"), "/title"),
    (lambda c: doc(caption=c), "/caption"),
    (lambda c: doc(edge_weight={"title": "W", "unit": c}), "/edge_weight/unit"),
    (lambda c: doc(provenance={"source": c}), "/provenance/source"),
    (lambda c: doc(nodes=[{"id": "a", "attributes": {"k": [1, c]}}]),
     "/nodes/0/attributes/k/1"),
    (lambda c: doc(metadata={c: 1}), "/metadata/" + "{c}"),
])
def test_parse_refuses_characters_xml_cannot_hold(ch, make, path):
    assert parse_error(make(ch)) == (path.replace("{c}", ch), "invalid_text")


def test_validation_refuses_control_characters_and_render_never_writes_them():
    spec = NetworkSpec(nodes=[Node("a\x01b", label="x\x00"), Node("b")],
                       edges=[Edge("a\x01b", "b", label="\x0b")], title="t\x1f",
                       caption="c￾", metadata={"k": "\x02"})
    found = errors(spec)
    for path in ("/nodes/0/id", "/nodes/0/label", "/edges/0/source", "/edges/0/label",
                 "/title", "/caption", "/metadata/k"):
        assert (path, "invalid_text") in found
    with pytest.raises(SpecValidationError):
        render_network(spec, RenderRequest(format="svg"))


def test_tab_newline_and_carriage_return_stay_allowed_and_the_svg_is_well_formed():
    d = doc(title="a\tb", caption="line 1\nline 2\r",
            nodes=[{"id": "a", "label": "x\ty"}, {"id": "b"}, {"id": "c"}])
    spec = network_from_dict(d)
    assert errors(spec) == []
    svg = render_network(spec, RenderRequest(format="svg")).content
    xml.dom.minidom.parseString(svg)


# ---- 2. lone surrogates ---------------------------------------------------

def test_lone_surrogates_are_refused_by_parse_and_validation_not_crash_later():
    assert parse_error('{"schema_version":"1.0","nodes":[{"id":"\\ud800"}]}') == (
        "/nodes/0/id", "invalid_text")
    assert parse_error('{"schema_version":"1.0","nodes":[{"id":"a","attributes":'
                       '{"\\udc00":1}}]}') == ("/nodes/0/attributes/\udc00", "invalid_text")
    spec = NetworkSpec(nodes=[Node("\ud800"), Node("b", attributes={"k": ["\udfff"]})],
                       edges=[Edge("\ud800", "b", kind="\ud83d")],
                       edge_weight=Quantity("W\ud800", "1"), metadata={"\udc00": 1})
    found = errors(spec)
    for path in ("/nodes/0/id", "/nodes/1/attributes/k/0", "/edges/0/source", "/edges/0/kind",
                 "/edge_weight/title", "/metadata/\udc00"):
        assert (path, "invalid_text") in found, (path, found)
    for fmt in ("png", "svg"):
        with pytest.raises(SpecValidationError):  # not a TypeError from the drawing
            render_network(spec, RenderRequest(format=fmt))


# ---- 3. extreme pinned positions -------------------------------------------

def test_pinned_positions_are_bounded_and_layout_output_is_checked_finite():
    for pos in ([1e200, 0.0], [1e300, 1e300], [-1e300, 5.0], [MAX_ABS_POSITION * 1.01, 0]):
        spec = network_from_dict(doc(nodes=[{"id": "a", "position": pos}, {"id": "b"},
                                            {"id": "c"}]))
        assert errors(spec) == [("/nodes/0/position", "position_out_of_range")]
        with pytest.raises(SpecValidationError):
            render_network(spec, RenderRequest(format="png"))
    # compute_layout does not validate; it still refuses to return NaN
    spec = network_from_dict(doc(nodes=[{"id": "a", "position": [1e300, 1e300]},
                                        {"id": "b", "position": [-1e300, -1e300]},
                                        {"id": "c"}]))
    with pytest.raises(RenderError) as ei:
        compute_layout(spec)
    assert ei.value.issues[0].code == "layout_not_finite"
    # at the bound, with many pins, the layout stays finite
    m = MAX_ABS_POSITION
    nodes = [{"id": f"n{i}", "position": [m * (-1) ** i, -m * (-1) ** (i // 2)]}
             if i % 3 else {"id": f"n{i}"} for i in range(60)]
    edges = [{"source": f"n{i}", "target": f"n{(i * 7 + 1) % 60}"} for i in range(60)
             if (i * 7 + 1) % 60 != i]
    spec = network_from_dict(doc(nodes=nodes, edges=edges))
    assert errors(spec) == []
    assert np.isfinite(compute_layout(spec).xy).all()
    built = build_network(spec)
    assert np.isfinite(built.node_xy).all()
    from ieeVisualizer.network import validate

    assert validate.MAX_ABS_POSITION == MAX_ABS_POSITION


# ---- 4. layered tree from a deep call stack --------------------------------

def _deep(k, fn):
    return _deep(k - 1, fn) if k else fn()


def test_tidy_tree_is_iterative_and_works_from_a_deep_stack(monkeypatch):
    n = 1000
    chain = NetworkSpec(nodes=[Node(f"n{i}") for i in range(n)],
                        edges=[Edge(f"n{i}", f"n{i + 1}") for i in range(n - 1)],
                        directed=True, layout=Layout("layered"))
    assert errors(chain) == []
    limit = sys.getrecursionlimit()

    def refuse(_):
        raise AssertionError("the layout must not change the recursion limit")

    monkeypatch.setattr(sys, "setrecursionlimit", refuse)
    lay = _deep(500, lambda: compute_layout(chain))
    assert sys.getrecursionlimit() == limit
    assert lay.xy[:, 0].tolist() == [0.0] * n and lay.layers == list(range(n))
    # a small tree: leaves side by side in DFS order, parents centred over them
    tree = NetworkSpec(nodes=[Node(x) for x in "rabcde"],
                       edges=[Edge("r", "a"), Edge("r", "b"), Edge("a", "c"), Edge("a", "d"),
                              Edge("b", "e")], directed=True, layout=Layout("layered"))
    x = compute_layout(tree).xy[:, 0].tolist()
    assert x == [1.25, 0.5, 2.0, 0.0, 1.0, 2.0]


# ---- 5. parallel and reciprocal edges --------------------------------------

def _offset(built, spec, k):
    """Signed distance (px) of edge k's path from the straight line between
    its nodes, measured in one orientation (first declared node -> other) and
    taken where the path is farthest from that line."""
    idx = {n.id: i for i, n in enumerate(spec.nodes)}
    e = spec.edges[k]
    a, b = sorted((idx[e.source], idx[e.target]))
    pa, pb = built.axes.transData.transform(built.node_xy[[a, b]])
    u = (pb - pa) / np.hypot(*(pb - pa))
    p = built.axes.transData.transform(built.edge_paths[k]) - pa
    d = u[0] * p[:, 1] - u[1] * p[:, 0]
    return float(d[np.argmax(np.abs(d))])


@pytest.mark.parametrize("edges, directed", [
    ([Edge("a", "b", kind="road"), Edge("a", "b", kind="rail")], False),
    ([Edge("a", "b"), Edge("b", "a")], True),
    ([Edge("a", "b", directed=True), Edge("a", "b")], False),
    ([Edge("a", "b", kind="x"), Edge("b", "a", kind="y"), Edge("a", "b", kind="z")], True),
])
def test_edges_between_the_same_nodes_are_drawn_apart(edges, directed):
    spec = NetworkSpec(nodes=[Node("a"), Node("b"), Node("c")],
                       edges=edges + [Edge("b", "c")], directed=directed,
                       layout=Layout("circular"))
    assert errors(spec) == []
    built = build_network(spec)
    offs = [_offset(built, spec, k) for k in range(len(edges))]
    for i in range(len(offs)):
        for j in range(i + 1, len(offs)):
            assert abs(offs[i] - offs[j]) > 8.0, offs  # px apart, not on one line
    assert sum(offs) == pytest.approx(0.0, abs=0.5)  # symmetric about the line
    straight = built.axes.transData.transform(built.edge_paths[len(edges)])
    assert len(straight) == 2  # a single edge between two nodes stays straight


def test_further_self_loops_on_one_node_are_larger_rings():
    spec = NetworkSpec(nodes=[Node("a"), Node("b")], self_loops="allow",
                       edges=[Edge("a", "a", kind="x"), Edge("a", "a", kind="y"),
                              Edge("a", "b")])
    built = build_network(spec)
    h0, h1 = (np.ptp(built.axes.transData.transform(built.edge_paths[k])[:, 1])
              for k in (0, 1))
    assert h1 > h0 * 1.5


def test_matrix_cells_holding_several_edges_are_marked_and_warned():
    spec = NetworkSpec(nodes=[Node("a"), Node("b"), Node("c")],
                       edges=[Edge("a", "b", 3.0, kind="road"), Edge("a", "b", 50.0, kind="rail"),
                              Edge("b", "c", 1.0, directed=True), Edge("b", "c", 2.0),
                              Edge("c", "c", 4.0)],
                       edge_weight=Quantity("Flow", "t"), self_loops="allow",
                       layout=Layout("matrix", order="input"))
    order = [0, 1, 2]
    w, missing, present = adjacency(spec, order)
    assert np.isnan(w[0, 1]) and np.isnan(w[1, 2])  # no one weight stands for 2 edges
    assert w[2, 1] == 2.0 and w[2, 2] == 4.0
    built = build_network(spec)
    warn = [x for x in built.warnings if x.code == "edges_combined_in_cell"]
    assert len(warn) == 1 and "3 matrix cell(s)" in warn[0].message
    legend = [t.get_text() for t in built.axes.get_legend().get_texts()]
    assert "several edges, count shown (3 cells)" in legend
    assert sorted(t.get_text() for t in built.axes.texts) == ["2", "2", "2"]
    outline = network_outline(spec)
    assert "Flow 3 t" in outline and "Flow 50 t" in outline
    from ieeVisualizer.network.matrix import cell_counts

    assert cell_counts(spec, order).tolist() == [[0, 2, 0], [2, 0, 2], [0, 1, 1]]


# ---- 6. graph_hash ignores the layout pin ------------------------------------

def test_graph_hash_ignores_pinned_positions_but_network_hash_does_not():
    plain = network_from_dict(doc())
    pinned = network_from_dict(doc(nodes=[{"id": "a", "position": [0.5, 0.0]}, {"id": "b"},
                                          {"id": "c"}]))
    assert graph_hash(pinned) == graph_hash(plain)
    assert network_hash(pinned) != network_hash(plain)
    assert pinned.nodes[0].position == [0.5, 0.0]  # hashing does not strip the spec
    other = network_from_dict(doc(nodes=[{"id": "a", "label": "A"}, {"id": "b"},
                                         {"id": "c"}]))
    assert graph_hash(other) != graph_hash(plain)


# ---- 7. a missing weight keeps its kind's dash pattern ----------------------

def test_missing_weight_keeps_the_kind_dash_and_uses_a_colour_the_scale_never_uses():
    from matplotlib.colors import to_rgba

    spec = NetworkSpec(nodes=[Node(x) for x in "abcd"],
                       edges=[Edge("a", "b", 2.0, kind="rail"), Edge("b", "c", None, kind="rail"),
                              Edge("c", "d", None, kind="road"), Edge("d", "a", 1.0)],
                       edge_weight=Quantity("Flow", "t"), layout=Layout("circular"))
    built = build_network(spec)
    coll = built.edge_collection
    styles, colours = coll.get_linestyles(), coll.get_colors()
    assert styles[1] == styles[0]  # rail stays rail when its weight is missing
    assert styles[2] != styles[1] and styles[2][1] is not None  # road is dashed too
    assert tuple(colours[1]) == tuple(colours[2]) == to_rgba(MISSING_COLOR)
    r, g, b, _ = to_rgba(MISSING_COLOR)
    assert max(r, g, b) - min(r, g, b) > 0.3  # a hue: no shade of the grey scale
    weight_leg = built.legends[0]
    rows = dict(zip([t.get_text() for t in weight_leg.get_texts()],
                    _handles(weight_leg)))
    assert to_rgba(rows["not recorded (2)"].get_color()) == to_rgba(MISSING_COLOR)
    kinds = [t.get_text() for t in built.legends[1].get_texts()]
    assert kinds == ["rail", "road"]
    assert styles[0][1] is None and KIND_STYLES[0] == "-"
    signed = NetworkSpec(nodes=spec.nodes, edges=spec.edges,
                         edge_weight=Quantity("Corr", "1", signed=True),
                         layout=Layout("circular"))
    from ieeVisualizer.network.render import MISSING_COLOR_SIGNED

    sc = build_network(signed).edge_collection.get_colors()
    assert tuple(sc[1]) == to_rgba(MISSING_COLOR_SIGNED)
    assert "Flow not recorded" in network_outline(spec)


# ---- 8. matrix labels -------------------------------------------------------

def test_matrix_label_abbreviation_is_warned():
    spec = network_from_dict(doc(nodes=[{"id": "a", "label": "Accounts receivable recon"},
                                        {"id": "b"}, {"id": "c"}], layout="matrix"))
    built = build_network(spec)
    ticks = [t.get_text() for t in built.axes.get_yticklabels()]
    assert "Accounts receivab…" in ticks
    warn = [w for w in built.warnings if w.code == "labels_abbreviated"]
    assert len(warn) == 1 and "'a'" in warn[0].message
    short = build_network(network_from_dict(doc(layout="matrix")))
    assert "labels_abbreviated" not in [w.code for w in short.warnings]


# ---- 9. free-form JSON: depth and byte budget -------------------------------

def _nest(depth):
    d = cur = {}
    for _ in range(depth - 1):
        cur["k"] = {}
        cur = cur["k"]
    return d


def test_deep_free_form_json_is_refused_without_recursion():
    deep = _nest(3000)
    path, code = parse_error(doc(nodes=[{"id": "a", "attributes": deep}]))
    assert code == "too_deep" and path == "/nodes/0/attributes" + "/k" * MAX_JSON_DEPTH
    ok = _nest(MAX_JSON_DEPTH)
    assert network_from_dict(doc(metadata=ok)).metadata == ok
    assert parse_error(doc(metadata=_nest(MAX_JSON_DEPTH + 1)))[1] == "too_deep"
    lists = []
    for _ in range(3000):
        lists = [lists]
    assert parse_error(doc(provenance={"extra": {"x": lists}}))[1] == "too_deep"
    spec = NetworkSpec(nodes=[Node("a", attributes=deep)])
    assert errors(spec) == [("/nodes/0/attributes" + "/k" * MAX_JSON_DEPTH, "too_deep")]


def test_free_form_json_has_a_total_byte_budget():
    blob = "x" * 100_000
    nodes = [{"id": f"n{i}", "attributes": {"blob": blob}} for i in range(25)]
    path, code = parse_error(doc(nodes=nodes, edges=[]))
    assert code == "too_large" and path.startswith("/nodes/19/attributes")
    small = [{"id": f"n{i}", "attributes": {"blob": blob}} for i in range(15)]
    spec = network_from_dict(doc(nodes=small, edges=[], metadata={"m": blob},
                                 provenance={"extra": {"e": blob}}))
    assert errors(spec) == []
    spec.metadata = {"m": "y" * 600_000}
    assert ("/metadata/m", "too_large") in errors(spec)


def test_free_json_counts_exact_canonical_bytes_and_copies():
    from ieeVisualizer.network.model import MAX_FREE_JSON_BYTES, free_json

    value = {"z": [1, 2.5, -0.0, True, None, "é\"\\\n", {}, []], "a": {"ü": {"x": 1e300}},
             "": "\U0001f600", "big": 10 ** 30}
    out, size = free_json(value, "/x", "x")
    assert out == value and out is not value and out["a"] is not value["a"]
    assert size == len(canonical_json(value).encode("utf-8"))
    assert list(out) == list(value)
    with pytest.raises(SpecParseError):
        free_json({"a": "b" * 10}, "/x", "x", budget=10)
    assert MAX_FREE_JSON_BYTES == 2_000_000
    from ieeVisualizer.network import model

    assert model.MAX_JSON_DEPTH == MAX_JSON_DEPTH


# ---- nit: labels in a top-down tree do not sit on the outgoing edges --------

def test_top_down_tree_labels_sit_beside_their_node_clear_of_edges():
    spec = NetworkSpec(nodes=[Node("r", "Root"), Node("a", "Alpha"), Node("b", "Beta"),
                              Node("c", "Gamma")],
                       edges=[Edge("r", "a"), Edge("r", "b"), Edge("a", "c")],
                       directed=True, layout=Layout("layered", direction="TB"))
    built = build_network(spec, RenderRequest(width=8, height=6))
    assert not built.skipped_labels and not built.abbreviated_labels
    renderer = built.figure.canvas.get_renderer()
    to_px = built.axes.transData.transform
    boxes = {i: t.get_window_extent(renderer) for i, t in built.labels.items()}
    for k, path in built.edge_paths.items():
        pts = to_px(path)
        fine = np.concatenate([np.linspace(pts[j], pts[j + 1], 50)
                               for j in range(len(pts) - 1)])
        for i, bb in boxes.items():
            assert not any(bb.contains(x, y) for x, y in fine), (spec.nodes[i].id, k)
    for i, bb in boxes.items():
        assert bb.x0 > to_px(built.node_xy[[i]])[0][0]  # to the right of its node


def test_round_trip_of_valid_unicode_text_is_unchanged():
    d = doc(title="Ünïcødé \U0001f600 ‮RTL", nodes=[{"id": "ä"}, {"id": "b"},
                                                         {"id": "c"}],
            edges=[{"source": "ä", "target": "b"}])
    spec = network_from_dict(d)
    assert errors(spec) == []
    assert network_from_json(json.dumps(d)).title == d["title"]
    assert network_hash(spec) == network_hash(network_from_dict(d))


def _handles(legend):
    """``Legend.legend_handles`` (matplotlib >= 3.7; ``legendHandles`` before)."""
    return getattr(legend, "legend_handles", None) or legend.legendHandles
