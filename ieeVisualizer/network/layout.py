"""Deterministic layouts for a validated :class:`NetworkSpec` (numpy only).

Every layout is a pure function of the spec: no hashing-order dependence, no
global random state (the force layout uses ``numpy.random.default_rng(seed)``),
ties broken by declaration order. The same spec gives the same coordinates
bit for bit, in this process or another one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import NetworkSpec
from .validate import back_edges, components


@dataclass
class LayoutResult:
    """``xy[i]`` is the position of ``spec.nodes[i]``. ``order`` is the
    reading order (node indices) used by the text outline and, for
    ``matrix``, the row/column order. ``reversed_edges`` (layered) are edge
    indices drawn against the layer direction."""

    xy: np.ndarray
    order: list[int]
    layers: list[int] = field(default_factory=list)
    reversed_edges: list[int] = field(default_factory=list)


def _index(spec: NetworkSpec) -> dict[str, int]:
    return {n.id: i for i, n in enumerate(spec.nodes)}


def _pairs(spec: NetworkSpec) -> list[tuple[int, int, int]]:
    """(edge index, source index, target index) of the non-loop edges."""
    idx = _index(spec)
    return [(k, idx[e.source], idx[e.target]) for k, e in enumerate(spec.edges)
            if e.source != e.target]


def group_order(spec: NetworkSpec) -> list[int]:
    """Nodes grouped by group (groups in order of first appearance, ungrouped
    last), declaration order within a group."""
    groups = list(dict.fromkeys(n.group for n in spec.nodes if n.group is not None))
    rank = {g: r for r, g in enumerate(groups)}
    return sorted(range(len(spec.nodes)),
                  key=lambda i: (rank.get(spec.nodes[i].group, len(groups)), i))


def seriation_order(spec: NetworkSpec) -> list[int]:
    """Reverse Cuthill-McKee on the undirected graph: a bandwidth-reducing
    seriation that puts connected nodes next to each other. Components in
    order of their first node; each starts at its lowest-degree node;
    neighbours are visited by (degree, declaration order)."""
    n = len(spec.nodes)
    adj: list[set] = [set() for _ in range(n)]
    for _, a, b in _pairs(spec):
        adj[a].add(b)
        adj[b].add(a)
    deg = [len(a) for a in adj]
    ids = [nd.id for nd in spec.nodes]
    idx = _index(spec)
    out: list[int] = []
    pairs = [(spec.nodes[a].id, spec.nodes[b].id) for _, a, b in _pairs(spec)]
    for comp in components(ids, pairs):
        members = [idx[c] for c in comp]
        start = min(members, key=lambda i: (deg[i], i))
        seen, queue, k = {start}, [start], 0
        while k < len(queue):
            v = queue[k]
            k += 1
            for w in sorted(adj[v] - seen, key=lambda i: (deg[i], i)):
                seen.add(w)
                queue.append(w)
        out.extend(reversed(queue))
    return out


def _force(spec: NetworkSpec) -> np.ndarray:
    """Fruchterman-Reingold with a weak pull to the centre (keeps components
    together), linear cooling, pinned nodes held at their ``position``.
    Edge weights do not affect the layout: distances are not a measurement."""
    n = len(spec.nodes)
    rng = np.random.default_rng(spec.layout.seed)
    pos = rng.uniform(-1.0, 1.0, size=(n, 2))
    pinned = np.array([nd.position is not None for nd in spec.nodes])
    if pinned.any():
        pos[pinned] = np.array([nd.position for nd in spec.nodes if nd.position is not None],
                               dtype=float)
    if n == 1:
        return pos
    pairs = _pairs(spec)
    src = np.array([a for _, a, _ in pairs], dtype=int)
    dst = np.array([b for _, _, b in pairs], dtype=int)
    k = 2.0 / np.sqrt(n)
    iters = spec.layout.iterations
    t0 = 0.2
    for it in range(iters):
        delta = pos[:, None, :] - pos[None, :, :]
        dist2 = np.maximum((delta ** 2).sum(axis=2), 1e-12)
        disp = ((k * k / dist2)[:, :, None] * delta).sum(axis=1)
        if len(src):
            d = pos[src] - pos[dst]
            f = d * (np.sqrt((d ** 2).sum(axis=1)) / k)[:, None]
            np.subtract.at(disp, src, f)
            np.add.at(disp, dst, f)
        disp -= 0.05 * pos * np.sqrt(n)  # gravity
        length = np.maximum(np.sqrt((disp ** 2).sum(axis=1)), 1e-12)
        t = t0 * (1.0 - it / iters) + 1e-4
        step = disp / length[:, None] * np.minimum(length, t)[:, None]
        step[pinned] = 0.0
        pos = pos + step
    return pos


def _circular(spec: NetworkSpec) -> tuple[np.ndarray, list[int]]:
    order = group_order(spec)
    n = len(order)
    xy = np.zeros((n, 2))
    for r, i in enumerate(order):
        a = np.pi / 2 - 2 * np.pi * r / n  # clockwise from the top
        xy[i] = (np.cos(a), np.sin(a))
    return xy, order


def _grid(spec: NetworkSpec) -> tuple[np.ndarray, list[int]]:
    order = group_order(spec)
    cols = int(np.ceil(np.sqrt(len(order))))
    xy = np.zeros((len(order), 2))
    for r, i in enumerate(order):
        xy[i] = (r % cols, -(r // cols))
    return xy, order


def _bipartite(spec: NetworkSpec) -> tuple[np.ndarray, list[int]]:
    groups = list(dict.fromkeys(n.group for n in spec.nodes))
    left = [i for i, n in enumerate(spec.nodes) if n.group == groups[0]]
    right = [i for i, n in enumerate(spec.nodes) if n.group != groups[0]]
    lrank = {i: r for r, i in enumerate(left)}
    nbrs: dict[int, list[int]] = {i: [] for i in right}
    for _, a, b in _pairs(spec):
        if a in lrank and b in nbrs:
            nbrs[b].append(lrank[a])
        elif b in lrank and a in nbrs:
            nbrs[a].append(lrank[b])
    right.sort(key=lambda i: (float(np.mean(nbrs[i])) if nbrs[i] else float("inf"), i))
    xy = np.zeros((len(spec.nodes), 2))
    for col, side in enumerate((left, right)):
        for r, i in enumerate(side):
            xy[i] = (col, -(r - (len(side) - 1) / 2))
    return xy, left + right


def _layered(spec: NetworkSpec) -> LayoutResult:
    """Longest-path layers after reversing DFS back edges. A forest (every
    node at most one parent) gets a tidy tree: leaves side by side, each
    parent centred over its children. Otherwise four barycentre sweeps."""
    n = len(spec.nodes)
    pairs = _pairs(spec)
    ids = [nd.id for nd in spec.nodes]
    rev_local = set(back_edges(ids, [(ids[a], ids[b]) for _, a, b in pairs]))
    dag = [(b, a) if r in rev_local else (a, b) for r, (_, a, b) in enumerate(pairs)]
    reversed_edges = [pairs[r][0] for r in sorted(rev_local)]
    children: list[list[int]] = [[] for _ in range(n)]
    parents: list[list[int]] = [[] for _ in range(n)]
    for a, b in dag:
        children[a].append(b)
        parents[b].append(a)
    layer = [0] * n
    indeg = [len(p) for p in parents]
    queue = [i for i in range(n) if indeg[i] == 0]
    k = 0
    while k < len(queue):  # Kahn's order: longest path from the sources
        v = queue[k]
        k += 1
        for w in children[v]:
            layer[w] = max(layer[w], layer[v] + 1)
            indeg[w] -= 1
            if indeg[w] == 0:
                queue.append(w)
    pos = [0.0] * n
    if all(len(p) <= 1 for p in parents):
        # post-order with an explicit stack (a chain is up to MAX_NODES deep, so
        # recursion would depend on the caller's stack depth): leaves side by
        # side in DFS order, each parent centred over its first and last child
        nxt = 0.0
        for root in (i for i in range(n) if not parents[i]):
            stack = [(root, 0)]
            while stack:
                v, j = stack[-1]
                if not children[v]:
                    pos[v] = nxt
                    nxt += 1.0
                    stack.pop()
                elif j < len(children[v]):
                    stack[-1] = (v, j + 1)
                    stack.append((children[v][j], 0))
                else:
                    pos[v] = (pos[children[v][0]] + pos[children[v][-1]]) / 2
                    stack.pop()
    else:
        rows: dict[int, list[int]] = {}
        for i in range(n):
            rows.setdefault(layer[i], []).append(i)
        rank = {i: r for row in rows.values() for r, i in enumerate(row)}
        depth = max(rows) + 1
        for sweep in range(4):
            down = sweep % 2 == 0
            for li in (range(1, depth) if down else range(depth - 2, -1, -1)):
                ref = parents if down else children
                row = rows[li]
                row.sort(key=lambda i: (float(np.mean([rank[j] for j in ref[i]]))
                                        if ref[i] else rank[i], rank[i]))
                for r, i in enumerate(row):
                    rank[i] = r
        for row in rows.values():
            for i in row:
                pos[i] = rank[i] - (len(row) - 1) / 2
    xy = np.zeros((n, 2))
    for i in range(n):
        xy[i] = (pos[i], -layer[i]) if spec.layout.direction == "TB" else (layer[i], -pos[i])
    order = sorted(range(n), key=lambda i: (layer[i], pos[i], i))
    return LayoutResult(xy, order, layers=layer, reversed_edges=reversed_edges)


def compute_layout(spec: NetworkSpec) -> LayoutResult:
    """Positions for every node of a spec that passed :func:`validate_network`.
    Raises :class:`~ieeVisualizer.render.base.RenderError`
    (``layout_not_finite``) rather than return a NaN or infinite coordinate."""
    algo = spec.layout.algorithm
    if algo == "force":
        with np.errstate(all="ignore"):  # overflow is reported below, not warned
            res = LayoutResult(_force(spec), list(range(len(spec.nodes))))
    elif algo == "layered":
        res = _layered(spec)
    elif algo == "matrix":
        order = {"group": group_order, "seriation": seriation_order,
                 "input": lambda s: list(range(len(s.nodes)))}[spec.layout.order](spec)
        res = LayoutResult(np.zeros((len(spec.nodes), 2)), order)
    else:
        fn = {"circular": _circular, "grid": _grid, "bipartite": _bipartite}[algo]
        xy, order = fn(spec)
        res = LayoutResult(xy, order)
    bad = ~np.isfinite(res.xy).all(axis=1)
    if bad.any():
        from ..render.base import RenderError
        from ..spec._common import ValidationIssue

        first = int(np.nonzero(bad)[0][0])
        raise RenderError([ValidationIssue(
            f"/nodes/{first}", "layout_not_finite",
            f"the {algo} layout produced non-finite coordinates for {int(bad.sum())} node(s) "
            f"(first: {spec.nodes[first].id!r}); nothing is drawn. Check pinned positions "
            "(validate_network bounds them).")])
    return res
