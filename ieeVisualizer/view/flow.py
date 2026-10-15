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
    declaration order, row-major, ``columns`` (default: ceil(sqrt(n))). An
    edge is straight when the line clears every other box; otherwise it
    detours orthogonally through the empty gaps between rows and columns, so
    it never crosses a node it does not connect.

Layering, crossing counts and lane placement are O((V + D) log(V + D)) per
sweep for V nodes and D dummy slots; :mod:`.limits` bounds V, E and D before
layout runs.

Every step breaks ties by declaration order, so the same flow always yields
the same coordinates. :func:`reading_order` / :func:`flow_outline` give the
accessible list form of the same graph.
"""

from __future__ import annotations

import math
from collections import deque
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
    """Longest-path layering in O(V + E) (Kahn's topological order)."""
    ids = [n.id for n in flow.nodes]
    known = set(ids)
    dag = []  # (u, v, edge index) with u before v
    succ: dict[str, list[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for k, e in enumerate(flow.edges):
        if e.source in known and e.target in known and e.source != e.target:
            u, v = (e.target, e.source) if k in rev else (e.source, e.target)
            dag.append((u, v, k))
            succ[u].append(v)
            indeg[v] += 1
    layer = {i: 0 for i in ids}
    ready = deque(i for i in ids if indeg[i] == 0)
    while ready:
        u = ready.popleft()
        for v in succ[u]:
            layer[v] = max(layer[v], layer[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                ready.append(v)
    return layer, dag


def dummy_count(flow: Flow) -> int:
    """Invisible slots a layered layout of ``flow`` inserts: the sum of
    ``span - 1`` over its edges. O(V + E); used by validation before layout."""
    layer, dag = _layers(flow, set(back_edges(flow)))
    return sum(layer[v] - layer[u] - 1 for u, v, _ in dag)


def _crossings(layers: list[list[str]], links: list[tuple[str, str]],
               where: dict[str, int]) -> int:
    """Edge crossings between adjacent layers: per layer, the number of
    strictly inverted segment pairs, counted with a Fenwick tree (E log E)."""
    pos = {n: i for lay in layers for i, n in enumerate(lay)}
    by_layer: dict[int, list[tuple[int, int]]] = {}
    for u, v in links:
        by_layer.setdefault(where[u], []).append((pos[u], pos[v]))
    total = 0
    for segs in by_layer.values():
        segs.sort()
        size = max(b for _, b in segs) + 1
        tree = [0] * (size + 1)
        for seen, (_, b) in enumerate(segs):
            i, at_most = b + 1, 0  # earlier segments ending at or before b
            while i > 0:
                at_most += tree[i]
                i -= i & -i
            total += seen - at_most
            i = b + 1
            while i <= size:
                tree[i] += 1
                i += i & -i
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


def _hits(p: tuple[float, float], q: tuple[float, float], b: Box, pad: float) -> bool:
    """Does segment p-q pass through ``b`` grown by ``pad`` (Liang-Barsky)?"""
    lo, hi = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for d, a, mn, mx in ((dx, p[0], b.x - pad, b.x + b.w + pad),
                         (dy, p[1], b.y - pad, b.y + b.h + pad)):
        if d == 0:
            if not mn < a < mx:
                return False
            continue
        t0, t1 = (mn - a) / d, (mx - a) / d
        if t0 > t1:
            t0, t1 = t1, t0
        lo, hi = max(lo, t0), min(hi, t1)
        if lo >= hi:
            return False
    return True


def _longest_mid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Midpoint of the longest segment (the channel run of a detour)."""
    segs = list(zip(points, points[1:]))
    (x0, y0), (x1, y1) = max(segs, key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
    return (x0 + x1) / 2, (y0 + y1) / 2


def _grid(flow: Flow, w: float, h: float, gap: float) -> FlowGeometry:
    """Row-major cells. An edge is a straight line when that line clears every
    other box (by 4 px); otherwise it detours orthogonally through the gaps
    between rows and columns, which never contain a box, so an edge never
    reads as passing through a node it does not touch."""
    n = len(flow.nodes)
    cols = flow.layout.columns or max(1, math.ceil(math.sqrt(n)))
    ncols = min(cols, n)
    rows = math.ceil(n / cols) if n else 0
    boxes: dict[str, Box] = {}
    cell: dict[str, tuple[int, int]] = {}
    for i, nd in enumerate(flow.nodes):
        r, c = divmod(i, cols)
        boxes[nd.id] = Box(MARGIN + c * (w + gap), MARGIN + r * (h + gap), w, h)
        cell[nd.id] = (r, c)
    all_boxes = list(boxes.items())
    used: dict[tuple[str, int], int] = {}  # channel -> runs already in it
    extra = [0.0, 0.0]  # canvas growth right / below for outer channels

    def lane_offset(key: tuple[str, int]) -> float:
        k = used.get(key, 0)
        used[key] = k + 1
        step = (k + 1) // 2 * 8.0 * (1 if k % 2 else -1)
        lim = gap / 2 - 8
        return max(-lim, min(lim, step))

    routes: list = []
    labels: list = []
    for e in flow.edges:
        a, b = boxes.get(e.source), boxes.get(e.target)
        if a is None or b is None:
            routes.append(None)
            labels.append(None)
            continue
        if a is b:
            pts = _self_loop(a, True)
            routes.append(pts)
            labels.append(_mid(pts))
            continue
        straight = [_clip(a, b.cx, b.cy), _clip(b, a.cx, a.cy)]
        if not any(_hits(straight[0], straight[1], bx, 4.0) for k, bx in all_boxes
                   if k not in (e.source, e.target)):
            routes.append(straight)
            labels.append(_mid(straight))
            continue
        (r1, c1), (r2, c2) = cell[e.source], cell[e.target]
        # stubs sit a quarter off the box centre lines, where straight edges
        # between neighbours run, so a detour never overlaps one of them
        q, s = w / 4, h / 4
        if r1 == r2:  # run along the row gap below (above for the last row)
            below = r1 < rows - 1 or r1 == 0
            if below and r1 == rows - 1:
                extra[1] = gap / 2
            y = (a.y + h + gap / 2 if below else a.y - gap / 2)
            y += lane_offset(("row", 2 * r1 + (1 if below else -1)))
            ya, yb = (a.y + h, b.y + h) if below else (a.y, b.y)
            pts = [(a.cx + q, ya), (a.cx + q, y), (b.cx - q, y), (b.cx - q, yb)]
        elif c1 == c2:  # run along the column gap right (left for the last column)
            right = c1 < ncols - 1 or c1 == 0
            if right and c1 == ncols - 1:
                extra[0] = gap / 2
            x = (a.x + w + gap / 2 if right else a.x - gap / 2)
            x += lane_offset(("col", 2 * c1 + (1 if right else -1)))
            xa, xb = (a.x + w, b.x + w) if right else (a.x, b.x)
            pts = [(xa, a.cy + s), (x, a.cy + s), (x, b.cy - s), (xb, b.cy - s)]
        else:  # leave through the row gap toward b, enter b from the column gap beside it
            down, east = r2 > r1, c2 > c1
            y = (a.y + h + gap / 2 if down else a.y - gap / 2)
            y += lane_offset(("row", 2 * r1 + (1 if down else -1)))
            x = (b.x - gap / 2 if east else b.x + w + gap / 2)
            x += lane_offset(("col", 2 * c2 + (-1 if east else 1)))
            pts = [(a.cx + q, a.y + h if down else a.y), (a.cx + q, y), (x, y), (x, b.cy - s),
                   (b.x if east else b.x + w, b.cy - s)]
        routes.append(pts)
        labels.append(_longest_mid(pts))
    return FlowGeometry(
        width=2 * MARGIN + ncols * w + max(ncols - 1, 0) * gap + extra[0],
        height=2 * MARGIN + rows * h + max(rows - 1, 0) * gap + extra[1], boxes=boxes,
        routes=routes, label_at=labels, order=[nd.id for nd in flow.nodes])


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
    lane_of = {g: i for i, g in enumerate(groups)}
    lane = {nd.id: lane_of[nd.group] for nd in flow.nodes}
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
    members: list[dict[int, list[str]]] = []  # per layer: lane -> members in order
    cap = [1] * len(groups)
    for lay in layers:
        by_lane: dict[int, list[str]] = {}
        for n in lay:
            by_lane.setdefault(lane[n], []).append(n)
        members.append(by_lane)
        for g, ms in by_lane.items():
            cap[g] = max(cap[g], len(ms))
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
    for li, by_lane in enumerate(members):
        for g, ms in by_lane.items():
            off = (cap[g] - len(ms)) / 2
            for j, n in enumerate(ms):
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


def flow_outline(flow: Flow, order: list[str] | None = None) -> list[str]:
    """One sentence per node, in reading order, naming every outgoing edge.
    A causal edge is always worded as a claim. ``order`` is a reading order
    already computed (``FlowGeometry.order``), so a renderer that has laid the
    flow out does not lay it out again."""
    by_id = {n.id: n for n in flow.nodes}
    outs: dict[str, list[str]] = {}
    for e in flow.edges:
        if e.source in by_id and e.target in by_id:
            s = f"{_VERBS.get(e.kind, e.kind)} {by_id[e.target].label}"
            if e.kind == "causal":
                s += f" (causal claim: {e.label})" if e.label else " (unlabelled causal claim)"
            elif e.label:
                s += f" ({e.label})"
            outs.setdefault(e.source, []).append(s)
    lines = []
    for i, nid in enumerate(reading_order(flow) if order is None else order, 1):
        n = by_id[nid]
        name = n.label + (f" [{n.group}]" if n.group else "")
        mine = outs.get(nid)
        lines.append(f"{i}. {name}: " + ("; ".join(mine) if mine else "no outgoing links") + ".")
    return lines
