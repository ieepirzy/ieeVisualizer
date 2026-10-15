"""Flow diagrams: deterministic layout and a text alternative.

The semantic graph (:class:`~.model.Flow` nodes and edges) is separate from
its geometry. :func:`layout_flow` turns graph + layout hint + node sizes into
a :class:`FlowGeometry` in pure Python:

``layered`` (Sugiyama-style, no dependency)
    1. cycles are broken by reversing DFS back edges (declaration order), so
       a cyclic graph still lays out; the reversed edges are reported;
    2. longest-path layering: a node sits one layer after its furthest
       predecessor;
    3. edges spanning several layers get invisible dummy slots so they route
       around boxes instead of through them;
    4. nodes are ordered within layers by barycentre sweeps (four down/up
       passes, keeping the order with the fewest crossings); ``group`` makes
       swimlanes - a node never leaves its lane;
    5. boxes share one size; layers are spaced to fit the widest edge label.

``grid``
    declaration order, row-major, ``columns`` (default: ceil(sqrt(n))).

Every step breaks ties by declaration order, so the same flow always yields
the same coordinates. :func:`reading_order` / :func:`flow_outline` give the
accessible list form of the same graph.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from .model import Flow, FlowNode

LANE_PAD = 14.0
SLOT_GAP = 26.0
MIN_LAYER_GAP = 56.0
MARGIN = 24.0
SWEEPS = 4


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class FlowGeometry:
    width: float
    height: float
    boxes: dict[str, Box]
    #: One polyline per edge (index-aligned with ``flow.edges``), from the
    #: ``from`` box border to the ``to`` box border; ``None`` if a ref is bad.
    routes: list[list[tuple[float, float]] | None]
    label_at: list[tuple[float, float] | None]
    lanes: list[tuple[str, Box]] = field(default_factory=list)
    reversed_edges: list[int] = field(default_factory=list)
    order: list[str] = field(default_factory=list)


def back_edges(flow: Flow) -> list[int]:
    """Indexes of edges closing a cycle (DFS in declaration order), self-loops included."""
    ids = [n.id for n in flow.nodes]
    known = set(ids)
    out_edges: dict[str, list[int]] = {i: [] for i in ids}
    for k, e in enumerate(flow.edges):
        if e.source in known and e.target in known:
            out_edges[e.source].append(k)
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    back: list[int] = []
    for root in ids:
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(out_edges[root]))]
        while stack:
            node, it = stack[-1]
            k = next(it, None)
            if k is None:
                state[node] = 2
                stack.pop()
                continue
            t = flow.edges[k].target
            if state.get(t) == 1:
                back.append(k)
            elif t not in state:
                state[t] = 1
                stack.append((t, iter(out_edges[t])))
    return sorted(back)


def _layers(flow: Flow, rev: set[int]) -> tuple[dict[str, int], list[tuple[str, str, int]]]:
    ids = [n.id for n in flow.nodes]
    known = set(ids)
    dag = []  # (u, v, edge index) with u before v
    for k, e in enumerate(flow.edges):
        if e.source in known and e.target in known and e.source != e.target:
            dag.append((e.target, e.source, k) if k in rev else (e.source, e.target, k))
    indeg = {i: 0 for i in ids}
    for _, v, _ in dag:
        indeg[v] += 1
    layer = {i: 0 for i in ids}
    ready = [i for i in ids if indeg[i] == 0]
    pos = {i: n for n, i in enumerate(ids)}
    while ready:
        u = ready.pop(0)
        for a, v, _ in dag:
            if a == u:
                layer[v] = max(layer[v], layer[u] + 1)
                indeg[v] -= 1
                if indeg[v] == 0:
                    ready.append(v)
                    ready.sort(key=pos.__getitem__)
    return layer, dag


def _crossings(layers: list[list[str]], links: list[tuple[str, str]],
               where: dict[str, int]) -> int:
    pos = {n: i for lay in layers for i, n in enumerate(lay)}
    by_layer: dict[int, list[tuple[int, int]]] = {}
    for u, v in links:
        by_layer.setdefault(where[u], []).append((pos[u], pos[v]))
    total = 0
    for segs in by_layer.values():
        for i, (a, b) in enumerate(segs):
            for c, d in segs[i + 1:]:
                total += (a - c) * (b - d) < 0
    return total


def _order(layers: list[list[str]], links: list[tuple[str, str]], lane: dict[str, int],
           where: dict[str, int]) -> list[list[str]]:
    up: dict[str, list[str]] = {}
    down: dict[str, list[str]] = {}
    for u, v in links:
        down.setdefault(u, []).append(v)
        up.setdefault(v, []).append(u)
    best = [list(x) for x in layers]
    best_c = _crossings(best, links, where)
    cur = [list(x) for x in layers]
    for sweep in range(SWEEPS * 2):
        downward = sweep % 2 == 0
        rng = range(1, len(cur)) if downward else range(len(cur) - 2, -1, -1)
        nbrs = up if downward else down
        for li in rng:
            ref = {n: i for i, n in enumerate(cur[li - 1 if downward else li + 1])}
            here = {n: i for i, n in enumerate(cur[li])}

            def key(n: str, ref=ref, here=here, nbrs=nbrs) -> tuple:
                ps = [ref[m] for m in nbrs.get(n, []) if m in ref]
                bary = sum(ps) / len(ps) if ps else float(here[n])
                return (lane[n], bary, here[n])

            cur[li] = sorted(cur[li], key=key)
        c = _crossings(cur, links, where)
        if c < best_c:
            best, best_c = [list(x) for x in cur], c
    return best


def layout_flow(flow: Flow, node_size: Callable[[FlowNode], tuple[float, float]],
                label_size: Callable[[str], tuple[float, float]] | None = None,
                lane_label_size: Callable[[str], tuple[float, float]] | None = None,
                ) -> FlowGeometry:
    """Geometry for ``flow``. ``node_size`` gives each node's (w, h); all
    boxes get the largest. ``label_size`` measures edge labels (spacing)."""
    w = max((node_size(n)[0] for n in flow.nodes), default=120.0)
    h = max((node_size(n)[1] for n in flow.nodes), default=40.0)
    lab_w = max((label_size(e.label)[0] for e in flow.edges if e.label and label_size),
                default=0.0)
    if flow.layout.algorithm == "grid":
        return _grid(flow, w, h, max(MIN_LAYER_GAP, lab_w + 24))
    return _layered(flow, w, h, lab_w, lane_label_size)


def _clip(b: Box, tx: float, ty: float) -> tuple[float, float]:
    """Point where the segment from b's centre toward (tx, ty) leaves b."""
    dx, dy = tx - b.cx, ty - b.cy
    if dx == 0 and dy == 0:
        return b.cx, b.cy
    s = min(b.w / 2 / abs(dx) if dx else math.inf, b.h / 2 / abs(dy) if dy else math.inf)
    return b.cx + dx * s, b.cy + dy * s


def _self_loop(b: Box, lr: bool) -> list[tuple[float, float]]:
    if lr:
        return [(b.cx + b.w / 4, b.y), (b.cx + b.w / 4, b.y - 16), (b.cx - b.w / 4, b.y - 16),
                (b.cx - b.w / 4, b.y)]
    return [(b.x + b.w, b.cy - b.h / 4), (b.x + b.w + 16, b.cy - b.h / 4),
            (b.x + b.w + 16, b.cy + b.h / 4), (b.x + b.w, b.cy + b.h / 4)]


def _mid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Midpoint of the middle segment (of the last one for an elbow route)."""
    i = len(points) - 2 if len(points) == 4 else (len(points) - 1) // 2
    (x0, y0), (x1, y1) = points[i], points[i + 1]
    return (x0 + x1) / 2, (y0 + y1) / 2


def _grid(flow: Flow, w: float, h: float, gap: float) -> FlowGeometry:
    n = len(flow.nodes)
    cols = flow.layout.columns or max(1, math.ceil(math.sqrt(n)))
    boxes = {nd.id: Box(MARGIN + (i % cols) * (w + gap), MARGIN + (i // cols) * (h + gap), w, h)
             for i, nd in enumerate(flow.nodes)}
    routes: list = []
    for e in flow.edges:
        a, b = boxes.get(e.source), boxes.get(e.target)
        if a is None or b is None:
            routes.append(None)
        elif a is b:
            routes.append(_self_loop(a, True))
        else:
            routes.append([_clip(a, b.cx, b.cy), _clip(b, a.cx, a.cy)])
    rows = math.ceil(n / cols) if n else 0
    return FlowGeometry(
        width=2 * MARGIN + min(cols, n) * w + max(min(cols, n) - 1, 0) * gap,
        height=2 * MARGIN + rows * h + max(rows - 1, 0) * gap, boxes=boxes, routes=routes,
        label_at=[_mid(r) if r else None for r in routes], order=[nd.id for nd in flow.nodes])


def _layered(flow: Flow, w: float, h: float, lab_w: float,
             lane_label_size: Callable[[str], tuple[float, float]] | None) -> FlowGeometry:
    lr = flow.layout.direction != "TB"
    rev = set(back_edges(flow))
    layer, dag = _layers(flow, rev)
    groups: list[str | None] = []
    for nd in flow.nodes:
        if nd.group not in groups:
            groups.append(nd.group)
    if None in groups:  # ungrouped nodes form the last lane
        groups.remove(None)
        groups.append(None)
    lane = {nd.id: groups.index(nd.group) for nd in flow.nodes}
    nlayers = max(layer.values(), default=-1) + 1
    layers: list[list[str]] = [[] for _ in range(nlayers)]
    for nd in flow.nodes:
        layers[layer[nd.id]].append(nd.id)
    links: list[tuple[str, str]] = []
    chains: dict[int, list[str]] = {}  # edge index -> [u, dummies..., v] in DAG direction
    for u, v, k in dag:
        chain = [u]
        for li in range(layer[u] + 1, layer[v]):
            d = f"~{k}~{li}"
            layer[d], lane[d] = li, lane[u]
            layers[li].append(d)
            chain.append(d)
        chain.append(v)
        chains[k] = chain
        links.extend(zip(chain, chain[1:]))
    for li in range(nlayers):  # initial order: lane, then declaration/insertion order
        first = {n: i for i, n in enumerate(layers[li])}
        layers[li] = sorted(layers[li], key=lambda n, first=first: (lane[n], first[n]))
    layers = _order(layers, links, lane, layer)

    # cross-axis capacity of each lane = most of its members in any one layer
    cap = [max((sum(1 for n in lay if lane[n] == g) for lay in layers), default=0) or 1
           for g in range(len(groups))]
    named = any(g is not None for g in groups)
    main_ext, cross_ext = (w, h) if lr else (h, w)
    gap = max(MIN_LAYER_GAP, (lab_w + 32) if lr else 0.0, 56.0)
    head = 0.0
    if named and lane_label_size:  # room for lane titles before the first layer
        head = max(lane_label_size(g or "Other")[0 if lr else 1] for g in groups) + 16
    lane_start, c = [], MARGIN
    for g in range(len(groups)):
        lane_start.append(c)
        c += cap[g] * (cross_ext + SLOT_GAP) - SLOT_GAP + (2 * LANE_PAD if named else 0)
        c += SLOT_GAP
    cross_total = c - SLOT_GAP + MARGIN
    main_total = 2 * MARGIN + head + nlayers * main_ext + max(nlayers - 1, 0) * gap

    centre: dict[str, tuple[float, float]] = {}
    for li, lay in enumerate(layers):
        for g in range(len(groups)):
            members = [n for n in lay if lane[n] == g]
            off = (cap[g] - len(members)) / 2
            for j, n in enumerate(members):
                m = MARGIN + head + li * (main_ext + gap) + main_ext / 2
                cc = (lane_start[g] + (LANE_PAD if named else 0)
                      + (off + j) * (cross_ext + SLOT_GAP) + cross_ext / 2)
                centre[n] = (m, cc) if lr else (cc, m)
    boxes = {nd.id: Box(centre[nd.id][0] - w / 2, centre[nd.id][1] - h / 2, w, h)
             for nd in flow.nodes}

    routes: list = []
    for k, e in enumerate(flow.edges):
        if e.source not in boxes or e.target not in boxes:
            routes.append(None)
            continue
        if e.source == e.target:
            routes.append(_self_loop(boxes[e.source], lr))
            continue
        chain = chains[k]
        a, b = boxes[chain[0]], boxes[chain[-1]]
        start = (a.x + a.w, a.cy) if lr else (a.cx, a.y + a.h)
        end = (b.x, b.cy) if lr else (b.cx, b.y)
        pts = [start] + [centre[d] for d in chain[1:-1]] + [end]
        if len(pts) == 2 and (start[1] != end[1] if lr else start[0] != end[0]):
            # elbow just after the source, so the long run (and its label) sits
            # level with the target instead of on a diagonal
            if lr:
                pts = [start, (start[0] + 18, start[1]), (start[0] + 18, end[1]), end]
            else:
                pts = [start, (start[0], start[1] + 18), (end[0], start[1] + 18), end]
        if k in rev:  # drawn in its true direction, nudged off any forward twin
            pts = [(x, y + 8) if lr else (x + 8, y) for x, y in reversed(pts)]
        routes.append(pts)
    lanes = []
    if named:
        for g, name in enumerate(groups):
            ext = cap[g] * (cross_ext + SLOT_GAP) - SLOT_GAP + 2 * LANE_PAD
            band = (Box(MARGIN / 2, lane_start[g], main_total - MARGIN, ext) if lr
                    else Box(lane_start[g], MARGIN / 2, ext, main_total - MARGIN))
            lanes.append((name or "Other", band))
    order = [n for lay in layers for n in lay if not n.startswith("~")]
    width, height = (main_total, cross_total) if lr else (cross_total, main_total)
    return FlowGeometry(width=width, height=height, boxes=boxes, routes=routes,
                        label_at=[_mid(r) if r else None for r in routes], lanes=lanes,
                        reversed_edges=sorted(rev), order=order)


# --------------------------------------------------------------------------
# Text alternative
# --------------------------------------------------------------------------

_VERBS = {"dataflow": "feeds", "dependency": "is needed by", "causal": "is claimed to affect"}


def reading_order(flow: Flow) -> list[str]:
    """Node ids in reading order: layer by layer (then lane, then position)
    for ``layered``; declaration order for ``grid``."""
    if flow.layout.algorithm == "grid":
        return [n.id for n in flow.nodes]
    return layout_flow(flow, lambda n: (1.0, 1.0)).order


def flow_outline(flow: Flow) -> list[str]:
    """One sentence per node, in reading order, naming every outgoing edge.
    A causal edge is always worded as a claim."""
    by_id = {n.id: n for n in flow.nodes}
    lines = []
    for i, nid in enumerate(reading_order(flow), 1):
        n = by_id[nid]
        name = n.label + (f" [{n.group}]" if n.group else "")
        outs = []
        for e in flow.edges:
            if e.source == nid and e.target in by_id:
                s = f"{_VERBS.get(e.kind, e.kind)} {by_id[e.target].label}"
                if e.kind == "causal":
                    s += f" (causal claim: {e.label})" if e.label else " (unlabelled causal claim)"
                elif e.label:
                    s += f" ({e.label})"
                outs.append(s)
        lines.append(f"{i}. {name}: " + ("; ".join(outs) if outs else "no outgoing links") + ".")
    return lines
