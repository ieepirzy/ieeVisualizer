"""Drawing a NetworkSpec: determinism, geometry, encodings, matrix, labels."""

import hashlib
import os
import subprocess
import sys

import numpy as np
import pytest

from ieeVisualizer.artifacts import MemoryWriter
from ieeVisualizer.network import (
    Edge,
    Layout,
    NetworkSpec,
    Node,
    Quantity,
    build_network,
    compute_layout,
    export_network,
    network_outline,
    render_network,
)
from ieeVisualizer.network.matrix import adjacency
from ieeVisualizer.network.render import LW_MIN, MISSING_COLOR
from ieeVisualizer.render import RenderRequest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def weighted(n_edges=None, **kw) -> NetworkSpec:
    nodes = [Node(f"n{i}", group="ab"[i % 2]) for i in range(6)]
    edges = [Edge("n0", "n1", 1.0), Edge("n1", "n2", 2.0), Edge("n2", "n3", 5.0),
             Edge("n3", "n4", 10.0), Edge("n4", "n5", 0.0), Edge("n5", "n0", None)]
    args = dict(nodes=nodes, edges=edges[:n_edges], edge_weight=Quantity("Flow", "kg/s"))
    args.update(kw)
    return NetworkSpec(**args)


_DIGEST = """
import hashlib, sys
sys.path.insert(0, {root!r})
from ieeVisualizer.network import Edge, Layout, NetworkSpec, Node, compute_layout, render_network
from ieeVisualizer.render import RenderRequest
nodes = [Node(f"n{{i}}", group=f"g{{i % 3}}") for i in range(30)]
edges = [Edge(f"n{{i}}", f"n{{(i * 7 + 3) % 30}}") for i in range(30) if (i * 7 + 3) % 30 != i]
spec = NetworkSpec(nodes=nodes, edges=edges, directed=True, layout=Layout("force", seed=5))
xy = compute_layout(spec).xy
svg = render_network(spec, RenderRequest(format="svg")).content
print(hashlib.sha256(xy.tobytes()).hexdigest(), hashlib.sha256(svg).hexdigest())
"""


def test_force_layout_and_svg_are_identical_across_processes_and_hash_seeds():
    code = _DIGEST.format(root=ROOT)
    outs = set()
    for seed in ("0", "12345", "random"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=env, check=False)
        assert r.returncode == 0, r.stderr
        outs.add(r.stdout.strip())
    assert len(outs) == 1
    ns: dict = {}
    exec(code.replace("print(", "result = ("), ns)  # and in this process
    assert " ".join(ns["result"]) in outs


def test_seed_changes_the_layout_and_pinned_nodes_stay_put():
    spec = weighted(layout=Layout("force", seed=1))
    other = weighted(layout=Layout("force", seed=2))
    assert not np.array_equal(compute_layout(spec).xy, compute_layout(other).xy)
    spec.nodes[0].position = [3.0, -2.0]
    assert compute_layout(spec).xy[0].tolist() == [3.0, -2.0]


def _px(built, xy):
    return built.axes.transData.transform(np.asarray(xy, float))


@pytest.mark.parametrize("algo", ["force", "layered", "circular", "grid"])
def test_edges_start_and_end_at_their_nodes(algo):
    spec = weighted(layout=Layout(algo), directed=algo in ("layered", "grid"))
    built = build_network(spec)
    ppt = built.figure.dpi / 72
    radius = [np.sqrt(a) / 2 * ppt for a in built.node_areas]
    idx = {n.id: i for i, n in enumerate(spec.nodes)}
    for k, e in enumerate(spec.edges):
        a, b = idx[e.source], idx[e.target]
        pa, pb = _px(built, built.node_xy[[a, b]])
        path = _px(built, built.edge_paths[k])
        assert np.hypot(*(path[0] - pa)) == pytest.approx(radius[a] + ppt, abs=0.5)
        end = _px(built, built.edge_heads[k])[0] if k in built.edge_heads else path[-1]
        assert np.hypot(*(end - pb)) == pytest.approx(radius[b] + ppt, abs=0.5)
        if algo != "layered":  # straight: start and end lie on the centre line
            u = (pb - pa) / np.hypot(*(pb - pa))
            for p in (path[0], end):
                assert abs(u[0] * (p - pa)[1] - u[1] * (p - pa)[0]) < 0.5
    assert bool(built.edge_heads) == (algo in ("layered", "grid"))
    segs = built.edge_collection.get_segments()
    assert len(segs) == len(spec.edges)
    np.testing.assert_allclose(segs[2], built.edge_paths[2])


def test_weight_to_width_is_monotone_and_the_legend_states_the_unit():
    spec = weighted(n_edges=5)  # weights 1, 2, 5, 10, 0
    built = build_network(spec)
    by_weight = sorted((spec.edges[k].weight, w) for k, w in built.edge_widths.items())
    widths = [w for _, w in by_weight]
    assert widths == sorted(widths) and len(set(widths)) == len(widths)
    assert built.edge_collection.get_linewidths().tolist() == [
        built.edge_widths[k] for k in range(5)]
    (leg,) = built.legends[1:]  # [0] is the group legend
    assert leg.get_title().get_text() == "Flow (kg/s)"
    texts = [t.get_text() for t in leg.get_texts()]
    assert texts and all(t.endswith(" kg/s") for t in texts)
    lws = [h.get_linewidth() for h in leg.legend_handles]
    assert lws == sorted(lws)  # reference lines grow with their values


def test_missing_weight_is_not_drawn_as_zero_and_is_flagged():
    spec = weighted()  # edge 4 weighs 0, edge 5 is not recorded
    built = build_network(spec)
    coll = built.edge_collection
    lw = coll.get_linewidths()
    colours = coll.get_colors()
    styles = coll.get_linestyles()
    assert built.edge_widths[4] == LW_MIN  # a real 0 is the thinnest weighted line
    assert built.edge_widths[5] != built.edge_widths[4]
    assert tuple(colours[5]) != tuple(colours[4])
    from matplotlib.colors import to_rgba

    assert tuple(colours[5]) == to_rgba(MISSING_COLOR)
    # no kind: both solid; the colour (a hue no weight maps to) marks the gap
    assert styles[5] == styles[4] and styles[4][1] is None
    assert lw[5] == built.edge_widths[5]
    texts = [t.get_text() for t in built.legends[1].get_texts()]
    assert "not recorded (1)" in texts
    assert "missing_weight" in [w.code for w in render_network(spec).warnings]
    assert "Flow not recorded" in network_outline(spec)
    sized = weighted(node_size=Quantity("Mass", "kg"))
    for i, n in enumerate(sized.nodes):
        n.size = None if i == 2 else float(i)
    faces = build_network(sized).node_artist.get_facecolors()
    assert faces[2][3] == 0.0  # hollow: no data is not a small disc


def test_matrix_cells_and_orderings():
    spec = weighted(layout=Layout("matrix", order="input"))
    w, missing, present = adjacency(spec, list(range(6)))
    assert w[0, 1] == w[1, 0] == 1.0  # undirected: symmetric
    assert w[4, 5] == 0.0 and np.isnan(w[5, 0]) and missing[5, 0] and missing[0, 5]
    assert np.isnan(w[0, 2]) and not present[0, 2]  # no edge: blank, not 0
    directed = weighted(directed=True)
    w, _, _ = adjacency(directed, list(range(6)))
    assert w[0, 1] == 1.0 and np.isnan(w[1, 0])
    grouped = weighted(layout=Layout("matrix", order="group"))
    order = compute_layout(grouped).order
    assert [grouped.nodes[i].group for i in order] == ["a"] * 3 + ["b"] * 3
    assert order == [0, 2, 4, 1, 3, 5]  # declaration order inside a group
    # seriation recovers a shuffled path: neighbours end up adjacent
    perm = [5, 2, 7, 0, 3, 8, 1, 6, 4, 9]
    path = NetworkSpec(nodes=[Node(f"p{i}") for i in perm],
                       edges=[Edge(f"p{i}", f"p{i + 1}") for i in range(9)],
                       layout=Layout("matrix", order="seriation"))
    seq = [path.nodes[i].id for i in compute_layout(path).order]
    assert seq in ([f"p{i}" for i in range(10)], [f"p{i}" for i in range(9, -1, -1)])
    built = build_network(path)
    assert built.matrix.shape == (10, 10)
    assert np.isfinite(built.matrix).sum() == 0  # unweighted: presence only


def test_overlapping_labels_are_abbreviated_or_skipped_with_a_warning():
    nodes = [Node(f"n{i}", label=f"A rather long label number {i}") for i in range(36)]
    spec = NetworkSpec(nodes=nodes, layout=Layout("grid"))
    built = build_network(spec, RenderRequest(width=3, height=2.4))
    codes = {w.code for w in built.warnings}
    assert {"labels_abbreviated", "labels_skipped"} & codes
    assert built.skipped_labels or built.abbreviated_labels
    renderer = built.figure.canvas.get_renderer()
    boxes = [t.get_window_extent(renderer) for t in built.labels.values()]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not a.overlaps(b)
    for i in built.abbreviated_labels:
        assert built.labels[i].get_text().endswith("…")
    outline = network_outline(spec)
    assert all(n.label in outline for n in nodes)
    roomy = build_network(spec, RenderRequest(width=30, height=24))
    assert not roomy.skipped_labels and not roomy.abbreviated_labels


def test_outline_lists_degrees_in_reading_order():
    spec = NetworkSpec(nodes=[Node("c", "Child"), Node("r", "Root")],
                       edges=[Edge("r", "c", directed=True)], layout=Layout("layered"))
    text = network_outline(spec)
    assert text.splitlines()[:4] == [
        "Network: 2 nodes, 1 directed edges.", "Nodes:",
        "1. Root (in-degree 0, out-degree 1)", "2. Child (in-degree 1, out-degree 0)"]
    assert text.endswith("- Root -> Child")
    svg = render_network(spec, RenderRequest(format="svg")).content.decode()
    assert "1. Root (in-degree 0, out-degree 1)" in svg


def test_formats_and_export_manifest():
    spec = weighted(title="Pipes")
    for fmt, magic in (("png", b"\x89PNG"), ("svg", b"<?xml"), ("pdf", b"%PDF")):
        assert render_network(spec, RenderRequest(format=fmt)).content.startswith(magic)
    writer = MemoryWriter()
    art = export_network(spec, ["png", "svg"], writer, name="pipes")
    assert art.artifact_id.startswith("pipes-")
    assert art.title == "Pipes" and [v.format for v in art.variants] == ["png", "svg"]
    assert sorted(name for aid, name in writer.files if aid == art.artifact_id) == [
        "figure.png", "figure.svg", "manifest.json", "spec.json"]
    first = render_network(spec, RenderRequest(format="svg")).content
    assert hashlib.sha256(first).digest() == hashlib.sha256(
        render_network(spec, RenderRequest(format="svg")).content).digest()
