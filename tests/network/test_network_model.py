"""NetworkSpec model, validation, limits and templates (no drawing)."""

import json

import pytest
from cost import measure

from ieeVisualizer import templates
from ieeVisualizer.network import (
    MAX_FORCE_NODES,
    MAX_NODES,
    Edge,
    Layout,
    NetworkSpec,
    Node,
    Quantity,
    apply_network_patch,
    graph_hash,
    network_from_dict,
    network_from_json,
    network_hash,
    network_to_json,
    validate_network,
)
from ieeVisualizer.spec import PatchError, SpecParseError, SpecValidationError
from ieeVisualizer.templates import TemplateInputError


def small(**kw) -> NetworkSpec:
    args = dict(nodes=[Node("a", "Alice", "x", 2.0), Node("b", group="x", size=1),
                       Node("c", group="y", size=None)],
                edges=[Edge("a", "b", 3), Edge("b", "c", None, kind="mail")],
                node_size=Quantity("Headcount", "people"),
                edge_weight=Quantity("Messages", "msg/week"))
    args.update(kw)
    return NetworkSpec(**args)


def codes(spec, severity=None):
    return [(i.path, i.code) for i in validate_network(spec)
            if severity is None or i.severity == severity]


def test_round_trip_is_canonical_and_hash_ignores_layout_for_graph_hash():
    spec = small()
    text = network_to_json(spec)
    again = network_from_json(text)
    assert network_to_json(again) == text
    assert json.loads(text)["edges"][0]["weight"] == 3.0  # 3 and 3.0 hash alike
    assert network_hash(again) == network_hash(spec)
    other = small(layout=Layout("matrix"), title="t")
    assert network_hash(other) != network_hash(spec)
    assert graph_hash(other) == graph_hash(spec)
    assert network_from_dict({"schema_version": "1.0", "nodes": [{"id": "a"}],
                              "layout": "circular"}).layout.algorithm == "circular"


@pytest.mark.parametrize("doc, path, code", [
    ({"schema_version": "2.0", "nodes": []}, "/schema_version", "unsupported_schema_version"),
    ({"schema_version": "1.0", "nodes": [{"id": "a", "colour": 1}]}, "/nodes/0/colour",
     "unknown_field"),
    ({"schema_version": "1.0", "nodes": [{"id": "a", "position": [1]}]}, "/nodes/0/position",
     "invalid_value"),
    ({"schema_version": "1.0", "nodes": [], "edges": [{"source": "a"}]}, "/edges/0/target",
     "missing_field"),
])
def test_parse_errors_point_at_the_problem(doc, path, code):
    with pytest.raises(SpecParseError) as exc:
        network_from_dict(doc)
    assert (exc.value.issues[0].path, exc.value.issues[0].code) == (path, code)


def test_patch_is_atomic_and_validated():
    spec = small()
    new = apply_network_patch(spec, [{"op": "add", "path": "/edges/1/weight", "value": 4}])
    assert new.edges[1].weight == 4.0 and spec.edges[1].weight is None
    with pytest.raises(SpecValidationError) as exc:
        apply_network_patch(spec, [{"op": "replace", "path": "/edges/0/target", "value": "zz"}])
    assert exc.value.issues[0].code == "unknown_node"
    with pytest.raises(PatchError):
        apply_network_patch(spec, [{"op": "remove", "path": "/edges/9"}])


def test_valid_spec_has_only_the_missing_value_warnings():
    assert codes(small()) == [("/nodes/2/size", "missing_value"),
                              ("/edges/1/weight", "missing_weight")]


def test_validation_codes():
    spec = small(nodes=[Node("a"), Node("a"), Node("b")],
                 edges=[Edge("a", "bb"), Edge("a", "a"), Edge("a", "b", -1.0),
                        Edge("b", "a", 2.0)],
                 node_size=None)
    found = codes(spec, "error")
    assert ("/nodes/1/id", "duplicate_node_id") in found
    assert ("/edges/0/target", "unknown_node") in found
    assert ("/edges/1", "self_loop") in found
    assert ("/edges/2/weight", "negative_weight") in found
    assert ("/edges/3", "parallel_edges") in found  # undirected b-a duplicates a-b
    issue = next(i for i in validate_network(spec) if i.code == "unknown_node")
    assert "Did you mean 'b'?" in issue.message
    signed = small(edges=[Edge("a", "b", -1.0)],
                   edge_weight=Quantity("Correlation", "1", signed=True), self_loops="allow")
    assert not codes(signed, "error")
    no_decl = small(edge_weight=None)
    assert ("/edges/0/weight", "undeclared_quantity") in codes(no_decl, "error")
    directed = small(edges=[Edge("a", "b"), Edge("b", "a")], directed=True, edge_weight=None)
    assert not codes(directed, "error")  # a->b and b->a are different edges


def test_information_about_structure():
    spec = small(edges=[Edge("a", "b", 1.0)], layout=Layout("layered"))
    assert ("/edges", "disconnected") in codes(spec, "info")
    cyc = small(edges=[Edge("a", "b", 1.0), Edge("b", "c", 1.0), Edge("c", "a", 1.0)],
                layout=Layout("layered"), directed=True)
    assert ("/layout/algorithm", "cycle_in_layered_layout") in codes(cyc, "info")
    bip = small(layout=Layout("bipartite"))
    assert ("/edges/0", "not_bipartite") in codes(bip, "error")  # a and b share group x


def test_limits_refuse_before_any_other_work(monkeypatch):
    import ieeVisualizer.network.layout as layout_mod

    monkeypatch.setattr(layout_mod, "compute_layout", lambda s: pytest.fail("laid out"))
    huge = NetworkSpec(nodes=[Node(f"n{i}") for i in range(100_000)],
                       edges=[Edge("n0", "n1")] * 200_000)
    with measure() as cost:
        found = codes(huge)
    assert cost.seconds < 0.5, cost
    assert found == [("/nodes", "too_many_nodes"), ("/edges", "too_many_edges")]
    force = NetworkSpec(nodes=[Node(f"n{i}") for i in range(MAX_FORCE_NODES + 1)])
    assert codes(force) == [("/layout/algorithm", "too_many_nodes")]
    slow = NetworkSpec(nodes=[Node(f"n{i}") for i in range(400)],
                       layout=Layout("force", iterations=1000))
    assert codes(slow) == [("/layout/iterations", "too_much_layout_work")]
    assert not codes(NetworkSpec(nodes=[Node(f"n{i}") for i in range(MAX_NODES)],
                                 layout=Layout("circular")), "error")
    long = NetworkSpec(nodes=[Node("a", label="x" * 201)])
    assert codes(long) == [("/nodes/0/label", "label_too_long")]
    from ieeVisualizer.network import render_network

    with pytest.raises(SpecValidationError):
        render_network(huge)


def test_network_templates_are_registered_and_examples_build():
    names = [t["name"] for t in templates.list_templates()]
    assert {"org_chart", "dependency_map", "network"} <= set(names)
    for name in ("org_chart", "dependency_map", "network"):
        entry = json.loads(json.dumps(templates.describe(name)))
        spec = templates.get(name).build_payload(entry["example"])
        assert isinstance(spec, NetworkSpec)
        assert not codes(spec, "error")
        assert spec.metadata["template"]["name"] == name
    org = templates.get("org_chart").build_payload(templates.get("org_chart").example())
    assert org.layout.algorithm == "layered" and org.node_size.unit == "people"
    assert org.metadata["template"]["warnings"][0]["code"] == "missing_value"
    dep = templates.get("dependency_map").build_payload(
        templates.get("dependency_map").example())
    assert dep.directed and dep.edge_weight.unit == "req/min"


def test_org_chart_refuses_data_that_is_not_a_hierarchy():
    t = templates.get("org_chart")
    bad_parent = {"id": ["a", "b"], "parent": [None, "zz"]}
    assert [(i.path, i.code) for i in t.check(bad_parent)] == [
        ("/data/columns/parent", "unknown_parent")]
    cycle = {"id": ["a", "b", "c"], "parent": ["c", "a", "b"]}
    with pytest.raises(TemplateInputError) as exc:
        t.build(cycle)
    assert exc.value.issues[0].code == "not_a_hierarchy"


def test_weight_without_unit_is_refused_and_override_conflict_reported():
    t = templates.get("dependency_map")
    data = {"source": ["a"], "target": ["b"], "w": [1.0]}
    assert [(i.path, i.code) for i in t.check(data, weight_column="w")] == [
        ("/data/columns/w", "missing_unit")]
    spec = t.build(data, weight_column="w", weight_unit="ms")
    assert spec.edge_weight.unit == "ms"
    from ieeVisualizer.spec import DataSnapshot

    snap = DataSnapshot.from_columns(data, units={"w": "s"})
    assert ("/params/weight_unit", "unit_override_conflict") in [
        (i.path, i.code) for i in t.check(snap, weight_column="w", weight_unit="ms")]


def test_generic_network_template_checks_node_references():
    t = templates.get("network")
    data = {"edges": {"source": ["a"], "target": ["q"]}, "nodes": {"id": ["a", "b"]}}
    assert ("/edges/0/target", "unknown_node") in [(i.path, i.code) for i in t.check(data)]
    neg = {"edges": {"type": "snapshot", "columns": {
        "source": {"values": ["a"]}, "target": {"values": ["b"]},
        "r": {"values": [-0.5], "unit": "1"}}}}
    assert ("/edges/0/weight", "negative_weight") in [
        (i.path, i.code) for i in t.check(neg, weight_column="r")]
    assert t.build(neg, weight_column="r", weight_signed=True).edge_weight.signed
