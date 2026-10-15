"""ViewSpec parsing, validation, round trip, hash, patch and flow layout."""

import pytest

from ieeVisualizer.spec import SpecParseError, SpecValidationError
from ieeVisualizer.view import (
    FigureComponent,
    Flow,
    FlowEdge,
    FlowLayout,
    FlowNode,
    Kpi,
    Stack,
    Table,
    ViewSpec,
    apply_view_patch,
    flow_outline,
    layout_flow,
    reading_order,
    validate_view,
    view_from_dict,
    view_from_json,
    view_hash,
    view_to_dict,
    view_to_json,
)
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing

SECTION_5_2 = {  # the illustrative shape from CONVERSATIONAL_UI_AND_REPORTING.md 5.2
    "type": "flow",
    "nodes": [{"id": "agent", "component": "card", "label": "Reporting agent"},
              {"id": "evidence", "component": "card", "label": "Authorized evidence"},
              {"id": "viz", "component": "card", "label": "ieeVisualizer"},
              {"id": "email", "component": "card", "label": "Email report"},
              {"id": "web", "component": "card", "label": "Interactive report"}],
    "edges": [{"from": "agent", "to": "evidence"}, {"from": "evidence", "to": "viz"},
              {"from": "viz", "to": "email"}, {"from": "viz", "to": "web"}],
    "layout": "layered",
}


def _view(root, **kw):
    return {"schema_version": "1.0", "root": root, **kw}


def _codes(view):
    return [(i.path, i.code, i.severity) for i in validate_view(view)]


def _flow(nodes, edges, **layout):
    return Flow(nodes=[FlowNode(n, n.upper()) for n in nodes],
                edges=[FlowEdge(a, b) for a, b in edges], layout=FlowLayout(**layout))


@pytest.mark.parametrize("build", [reporting_pipeline, weekly_briefing])
def test_round_trip_is_byte_identical_and_hash_stable(build):
    view = build()
    assert validate_view(view) == []
    text = view_to_json(view)
    again = view_from_json(text)
    assert view_to_json(again) == text
    assert view_hash(again) == view_hash(view) == view_hash(build())


def test_equal_numbers_hash_equally():
    a = ViewSpec(root=Kpi("x", 55))
    b = ViewSpec(root=Kpi("x", 55.0))
    assert view_hash(a) == view_hash(b)
    assert view_hash(a) != view_hash(ViewSpec(root=Kpi("x", None)))


def test_section_5_2_example_parses_with_layout_shorthand():
    view = view_from_dict(_view(SECTION_5_2))
    assert view.root.layout.algorithm == "layered"
    assert view_to_dict(view)["root"]["layout"] == {"algorithm": "layered", "direction": "LR"}
    assert validate_view(view) == []
    assert reading_order(view.root) == ["agent", "evidence", "viz", "email", "web"]


@pytest.mark.parametrize("doc, path, code", [
    (_view({"type": "stack", "colour": "red"}), "/root/colour", "unknown_field"),
    (_view({"type": "sidebar"}), "/root/type", "unknown_node_type"),
    (_view({"type": "kpi", "label": "x"}), "/root/value", "missing_field"),
    (_view({"type": "kpi", "label": "x", "value": "12"}), "/root/value", "invalid_type"),
    (_view({"type": "flow", "nodes": [{"id": "a"}]}), "/root/nodes/0/label", "missing_field"),
    ({"root": {"type": "stack"}}, "/schema_version", "missing_schema_version"),
    (_view({"type": "stack"}, figures={"f": {"schema_version": "1.0", "layers": 3}}),
     "/figures/f/layers", "invalid_type"),
])
def test_parse_errors_carry_pointer_and_code(doc, path, code):
    with pytest.raises(SpecParseError) as e:
        view_from_dict(doc)
    assert (e.value.issues[0].path, e.value.issues[0].code) == (path, code)


def test_validation_reports_flow_problems_with_paths():
    f = Flow(nodes=[FlowNode("a", "A"), FlowNode("a", "A again"), FlowNode("b", "B")],
             edges=[FlowEdge("a", "zz"), FlowEdge("a", "b", kind="causal"),
                    FlowEdge("b", "b", kind="sideways")])
    got = _codes(ViewSpec(root=f))
    assert ("/root/nodes/1/id", "duplicate_node_id", "error") in got
    assert ("/root/edges/0/to", "unknown_node", "error") in got
    assert ("/root/edges/1/label", "causal_edge_unlabelled", "error") in got
    assert ("/root/edges/2/kind", "invalid_value", "error") in got
    assert ("/root/edges/2", "self_loop", "info") in got


def test_cycle_is_info_for_layered_and_silent_for_grid():
    cyc = _flow("abc", [("a", "b"), ("b", "c"), ("c", "a")])
    issues = validate_view(ViewSpec(root=cyc))
    assert [(i.path, i.code, i.severity) for i in issues] == [
        ("/root/layout", "cycle_in_layered_layout", "info")]
    assert "/edges/2" in issues[0].message
    assert validate_view(ViewSpec(root=_flow("abc", [("a", "b"), ("b", "a")],
                                              algorithm="grid"))) == []


def test_validation_of_figures_tables_and_kpis(tmp_path):
    view = weekly_briefing()
    view.root.children.append(FigureComponent(figure="nope"))
    view.root.children.append(FigureComponent(figure="trend", spec=view.figures["trend"]))
    view.root.children.append(Table("weekly", columns=["week", "bokings"]))
    view.root.children.append(Table("missing"))
    view.root.children.append(Kpi("x", 1, dataset="missing"))
    view.figures["spare"] = view.figures["trend"]
    view.figures["trend"].layers[0].data = "gone"
    got = _codes(view)
    n = len(weekly_briefing().root.children)
    assert (f"/root/children/{n}/figure", "unknown_figure", "error") in got
    assert (f"/root/children/{n + 1}", "figure_source_conflict", "error") in got
    assert (f"/root/children/{n + 2}/columns/1", "unknown_column", "error") in got
    assert (f"/root/children/{n + 3}/data", "unknown_dataset", "error") in got
    assert (f"/root/children/{n + 4}/dataset", "unknown_dataset", "error") in got
    assert ("/figures/trend/layers/0/data", "unknown_dataset", "error") in got
    assert ("/figures/spare", "unused_figure", "info") in got


def test_patch_by_pointer_returns_new_view_and_revalidates():
    view = weekly_briefing()
    path = "/root/children/1/children/2/value"
    new = apply_view_patch(view, [{"op": "test", "path": path, "value": None},
                                  {"op": "replace", "path": path, "value": 4.2}])
    assert new.root.children[1].children[2].value == 4.2
    assert view.root.children[1].children[2].value is None
    with pytest.raises(SpecValidationError) as e:
        apply_view_patch(view, [{"op": "replace", "path": "/root/children/2/figure",
                                 "value": "nope"}])
    assert e.value.issues[0].code == "unknown_figure"


def test_outline_reading_order_and_causal_wording():
    f = reporting_pipeline().root.children[0]
    f.edges.append(FlowEdge("email", "agent", "readers ask more questions", kind="causal"))
    lines = flow_outline(f)
    assert lines[0] == "1. Reporting agent: feeds Authorized evidence (queries)."
    assert lines[2].startswith("3. ieeVisualizer: feeds Email report (PNG / HTML); feeds "
                               "Interactive report")
    email = next(ln for ln in lines if ln.split(". ", 1)[1].startswith("Email report"))
    assert email.endswith("is claimed to affect Reporting agent (causal claim: readers ask "
                          "more questions).")


def _size(n):
    return (100.0, 40.0)


def test_layered_layout_is_deterministic_and_respects_layers():
    f = _flow("abcde", [("a", "b"), ("b", "c"), ("a", "c"), ("c", "d"), ("a", "e")])
    g1, g2 = layout_flow(f, _size), layout_flow(f, _size)
    assert g1 == g2
    xs = {k: b.x for k, b in g1.boxes.items()}
    assert xs["a"] < xs["b"] < xs["c"] < xs["d"]  # longest path: c after b, not beside it
    assert xs["e"] == xs["b"]
    # a -> c skips a layer: routed through a dummy slot, so it has a bend point
    assert len(g1.routes[2]) == 3
    late = layout_flow(_flow("xyzw", [("x", "y"), ("y", "z"), ("w", "z")]), _size)
    assert late.boxes["z"].x > late.boxes["y"].x > late.boxes["x"].x == late.boxes["w"].x
    tb = layout_flow(_flow("abcde", [(a, b) for a, b in zip("abcd", "bcde")], direction="TB"),
                     _size)
    ys = [tb.boxes[k].y for k in "abcde"]
    assert ys == sorted(ys) and len(set(ys)) == 5


def test_cyclic_graph_lays_out_with_reversed_edge_drawn_true_direction():
    f = _flow("abc", [("a", "b"), ("b", "c"), ("c", "a")])
    g = layout_flow(f, _size)
    assert g.reversed_edges == [2]
    start, end = g.routes[2][0], g.routes[2][-1]
    assert g.boxes["c"].x <= start[0] <= g.boxes["c"].x + 100  # leaves c ...
    assert g.boxes["a"].x <= end[0] <= g.boxes["a"].x + 100  # ... and arrives at a


def test_groups_become_lanes_and_nodes_stay_in_their_lane():
    f = Flow(nodes=[FlowNode("a", "A", group="one"), FlowNode("b", "B", group="two"),
                    FlowNode("c", "C", group="one"), FlowNode("d", "D")],
             edges=[FlowEdge("a", "b"), FlowEdge("b", "c"), FlowEdge("a", "d")])
    g = layout_flow(f, _size)
    lanes = dict(g.lanes)
    assert list(lanes) == ["one", "two", "Other"]
    for node, lane in (("a", "one"), ("c", "one"), ("b", "two"), ("d", "Other")):
        band, box = lanes[lane], g.boxes[node]
        assert band.y <= box.y and box.y + box.h <= band.y + band.h, node


def test_stack_default_and_unknown_direction():
    assert _codes(ViewSpec(root=Stack(direction="diagonal"))) == [
        ("/root/children", "empty_container", "warning"),
        ("/root/direction", "invalid_value", "error")]
