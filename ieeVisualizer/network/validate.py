"""Semantic validation of a :class:`NetworkSpec`, with size limits checked first.

``validate_network(spec)`` never raises for a bad spec; it returns
:class:`~ieeVisualizer.spec.ValidationIssue` objects (JSON pointer, code,
message naming the fix, severity). Size limits are checked before anything
else and in O(nodes + edges + text), so an oversized graph is refused in
milliseconds, before any layout (force layout is O(nodes^2) per iteration).
Pure Python: no numpy, no matplotlib.
"""

from __future__ import annotations

import math
from typing import Any

from ..spec._common import ValidationIssue, json_problem, ptr, suggest
from .model import (
    DIRECTIONS,
    KIND,
    LAYOUTS,
    MATRIX_ORDERS,
    SELF_LOOP_POLICIES,
    SUPPORTED_SCHEMA_VERSIONS,
    Edge,
    Layout,
    NetworkSpec,
    Node,
    Quantity,
)

#: Limits (see docs/NETWORKS.md for the timings they were chosen from).
MAX_NODES = 1000
MAX_EDGES = 5000
MAX_FORCE_NODES = 500
MAX_FORCE_ITERATIONS = 1000
#: Force layout work budget: nodes^2 x iterations (500 nodes x 200 iterations).
MAX_FORCE_WORK = 50_000_000
MAX_MATRIX_NODES = 1000
MAX_LABEL_CHARS = 200
MAX_TOTAL_LABEL_CHARS = 50_000
#: At most this many issues of one code are listed individually.
MAX_ISSUES_PER_CODE = 20


class _Issues:
    def __init__(self) -> None:
        self.items: list[ValidationIssue] = []
        self.counts: dict[str, int] = {}

    def add(self, path: str, code: str, message: str, severity: str = "error") -> None:
        n = self.counts.get(code, 0)
        self.counts[code] = n + 1
        if n < MAX_ISSUES_PER_CODE:
            self.items.append(ValidationIssue(path, code, message, severity))


def _check_limits(spec: NetworkSpec, out: _Issues) -> bool:
    """True when every limit holds. Each check is O(size of the input)."""
    ok = True
    n, m = len(spec.nodes), len(spec.edges)
    algo = spec.layout.algorithm if isinstance(spec.layout, Layout) else None
    if n > MAX_NODES:
        out.add("/nodes", "too_many_nodes",
                f"{n} nodes exceed the limit of {MAX_NODES}; aggregate or filter upstream.")
        ok = False
    elif algo == "force" and n > MAX_FORCE_NODES:
        out.add("/layout/algorithm", "too_many_nodes",
                f"{n} nodes exceed the force-layout limit of {MAX_FORCE_NODES} (the layout is "
                "quadratic in nodes); use layout 'matrix', 'circular' or 'grid'.")
        ok = False
    elif algo == "matrix" and n > MAX_MATRIX_NODES:
        out.add("/nodes", "too_many_nodes",
                f"{n} nodes exceed the matrix limit of {MAX_MATRIX_NODES}.")
        ok = False
    if m > MAX_EDGES:
        out.add("/edges", "too_many_edges",
                f"{m} edges exceed the limit of {MAX_EDGES}; aggregate or filter upstream.")
        ok = False
    if isinstance(spec.layout, Layout) and isinstance(spec.layout.iterations, int) and (
            spec.layout.iterations > MAX_FORCE_ITERATIONS or spec.layout.iterations < 1):
        out.add("/layout/iterations", "invalid_value",
                f"iterations must be 1..{MAX_FORCE_ITERATIONS}, got {spec.layout.iterations}.")
        ok = False
    elif ok and algo == "force" and isinstance(spec.layout.iterations, int) and (
            n * n * spec.layout.iterations > MAX_FORCE_WORK):
        out.add("/layout/iterations", "too_much_layout_work",
                f"{n} nodes x {spec.layout.iterations} iterations exceed the force-layout "
                f"budget (nodes^2 x iterations <= {MAX_FORCE_WORK:,}); use at most "
                f"{MAX_FORCE_WORK // max(n * n, 1)} iterations.")
        ok = False
    if not ok:
        return False
    total = 0
    texts = [(ptr("nodes", i, f), getattr(nd, f, None))
             for i, nd in enumerate(spec.nodes) for f in ("id", "label", "group")]
    texts += [(ptr("edges", i, f), getattr(e, f, None))
              for i, e in enumerate(spec.edges) for f in ("kind", "label")]
    for path, s in texts:
        if isinstance(s, str):
            total += len(s)
            if len(s) > MAX_LABEL_CHARS:
                out.add(path, "label_too_long",
                        f"{len(s)} characters exceed the limit of {MAX_LABEL_CHARS} per label; "
                        "shorten it (the full text can go in the node's attributes).")
                ok = False
    if total > MAX_TOTAL_LABEL_CHARS:
        out.add("", "too_much_text",
                f"{total} characters of ids and labels exceed the limit of "
                f"{MAX_TOTAL_LABEL_CHARS}.")
        ok = False
    return ok


def components(ids: list[str], pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Weakly connected components, each in input order, ordered by their
    first node. Union-find; deterministic (no hashing of ordering)."""
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def back_edges(ids: list[str], pairs: list[tuple[str, str]]) -> list[int]:
    """Indices of edges that close a cycle in a DFS taken in declaration order
    (iterative; reversing them makes the graph acyclic)."""
    out_edges: dict[str, list[tuple[str, int]]] = {i: [] for i in ids}
    for k, (a, b) in enumerate(pairs):
        out_edges[a].append((b, k))
    state = {i: 0 for i in ids}  # 0 new, 1 on stack, 2 done
    found: list[int] = []
    for root in ids:
        if state[root]:
            continue
        stack = [(root, 0)]
        state[root] = 1
        while stack:
            v, j = stack[-1]
            if j < len(out_edges[v]):
                stack[-1] = (v, j + 1)
                w, k = out_edges[v][j]
                if state[w] == 1:
                    found.append(k)
                elif state[w] == 0:
                    state[w] = 1
                    stack.append((w, 0))
            else:
                state[v] = 2
                stack.pop()
    return sorted(found)


def _num_ok(v: Any) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)
                         and math.isfinite(v))


def _quantity(q: Any, path: str, out: _Issues, node: bool) -> None:
    if q is None:
        return
    if not isinstance(q, Quantity):
        out.add(path, "invalid_type", "must be a Quantity(title, unit).")
        return
    for f in ("title", "unit"):
        if not isinstance(getattr(q, f), str) or not getattr(q, f).strip():
            out.add(f"{path}/{f}", "missing_unit" if f == "unit" else "invalid_value",
                    f"{f} must be a non-empty string" + (
                        "; use '1' for a dimensionless value." if f == "unit" else "."))
    if node and q.signed:
        out.add(f"{path}/signed", "invalid_value", "node sizes cannot be signed (an area "
                "cannot be negative); encode a signed value another way.")


def validate_network(spec: Any) -> list[ValidationIssue]:
    out = _Issues()
    if not isinstance(spec, NetworkSpec):
        return [ValidationIssue("", "invalid_type", "not a NetworkSpec.")]
    if spec.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        out.add("/schema_version", "unsupported_schema_version",
                f"schema_version {spec.schema_version!r} is not supported.")
    if not isinstance(spec.nodes, list) or not isinstance(spec.edges, list) or not all(
            isinstance(n, Node) for n in spec.nodes) or not all(
            isinstance(e, Edge) for e in spec.edges) or not isinstance(spec.layout, Layout):
        return [ValidationIssue("", "invalid_type",
                                "nodes must be Node objects, edges Edge objects and layout a "
                                "Layout.")]
    if not _check_limits(spec, out):
        return out.items
    lay = spec.layout
    for key, allowed in (("algorithm", LAYOUTS), ("direction", DIRECTIONS),
                         ("order", MATRIX_ORDERS)):
        if getattr(lay, key) not in allowed:
            out.add(f"/layout/{key}", "invalid_value",
                    f"{key} {getattr(lay, key)!r} is not one of {', '.join(allowed)}."
                    f"{suggest(str(getattr(lay, key)), allowed)}")
    if not isinstance(lay.seed, int) or isinstance(lay.seed, bool) or lay.seed < 0:
        out.add("/layout/seed", "invalid_value", "seed must be a non-negative integer.")
    if spec.self_loops not in SELF_LOOP_POLICIES:
        out.add("/self_loops", "invalid_value",
                f"self_loops must be one of {', '.join(SELF_LOOP_POLICIES)}.")
    if not isinstance(spec.directed, bool):
        out.add("/directed", "invalid_type", "directed must be true or false.")
    _quantity(spec.node_size, "/node_size", out, node=True)
    _quantity(spec.edge_weight, "/edge_weight", out, node=False)
    for key in ("metadata",):
        bad = json_problem(getattr(spec, key), f"/{key}")
        if bad:
            out.add(*bad)
    if not spec.nodes:
        out.add("/nodes", "empty_network", "the network has no nodes; there is nothing to "
                "draw. Supply nodes rather than drawing an empty figure.")
        return out.items

    ids: list[str] = []
    seen: dict[str, int] = {}
    for i, n in enumerate(spec.nodes):
        p = ptr("nodes", i)
        if not isinstance(n.id, str) or not n.id:
            out.add(f"{p}/id", "invalid_value", "node id must be a non-empty string.")
            continue
        if n.id in seen:
            out.add(f"{p}/id", "duplicate_node_id",
                    f"node id {n.id!r} is already used by /nodes/{seen[n.id]}; ids must be "
                    "unique. Rename one or merge the two nodes upstream.")
            continue
        seen[n.id] = i
        ids.append(n.id)
        if not _num_ok(n.size):
            out.add(f"{p}/size", "invalid_value", "size must be a finite number or null.")
        elif n.size is not None:
            if spec.node_size is None:
                out.add(f"{p}/size", "undeclared_quantity",
                        "a node has a size but node_size does not say what it measures; set "
                        "node_size {title, unit}.")
            elif n.size < 0:
                out.add(f"{p}/size", "negative_size",
                        f"size {n.size} is negative; a node area cannot encode it.")
        elif spec.node_size is not None:
            out.add(f"{p}/size", "missing_value",
                    f"node {n.id!r} has no {spec.node_size.title}; it is drawn hollow at a "
                    "neutral size, not as 0.", "warning")
        if n.position is not None and (len(n.position) != 2
                                       or not all(_num_ok(v) and v is not None
                                                  for v in n.position)):
            out.add(f"{p}/position", "invalid_value", "position must be [x, y] numbers.")
        bad = json_problem(n.attributes, f"{p}/attributes")
        if bad:
            out.add(*bad)

    pairs: list[tuple[str, str]] = []
    pair_index: list[int] = []
    seen_pairs: dict[tuple[str, str], int] = {}
    for k, e in enumerate(spec.edges):
        p = ptr("edges", k)
        okref = True
        for end in ("source", "target"):
            v = getattr(e, end)
            if v not in seen:
                out.add(f"{p}/{end}", "unknown_node",
                        f"{end} {v!r} is not a node id.{suggest(str(v), seen)}")
                okref = False
        if not _num_ok(e.weight):
            out.add(f"{p}/weight", "invalid_value", "weight must be a finite number or null.")
        elif e.weight is not None:
            if spec.edge_weight is None:
                out.add(f"{p}/weight", "undeclared_quantity",
                        "an edge has a weight but edge_weight does not say what it measures; "
                        "set edge_weight {title, unit} ('1' for dimensionless).")
            elif e.weight < 0 and not spec.edge_weight.signed:
                out.add(f"{p}/weight", "negative_weight",
                        f"weight {e.weight} is negative but {spec.edge_weight.title!r} is not "
                        "signed; fix the value or set edge_weight.signed=true if negative "
                        "values are meaningful.")
        elif spec.edge_weight is not None:
            out.add(f"{p}/weight", "missing_weight",
                    f"edge {e.source!r} -> {e.target!r} has no recorded "
                    f"{spec.edge_weight.title}; it is drawn dotted in a neutral colour, "
                    "not as 0.", "warning")
        if not okref:
            continue
        if e.source == e.target:
            if spec.self_loops == "forbid":
                out.add(p, "self_loop", f"edge from {e.source!r} to itself; remove it or set "
                        "self_loops='allow' if loops are meaningful.")
            else:
                out.add(p, "self_loop", f"self-loop on {e.source!r}.", "info")
        directed = spec.is_directed(e)
        key = (e.source, e.target) if directed else tuple(sorted((e.source, e.target)))
        key = key + (str(directed), e.kind or "")
        if key in seen_pairs:
            out.add(p, "parallel_edges",
                    f"edge duplicates /edges/{seen_pairs[key]} (same ends, direction and "
                    "kind); merge them upstream and state how weights were combined.")
        else:
            seen_pairs[key] = k
        pairs.append((e.source, e.target))
        pair_index.append(k)

    if lay.algorithm == "bipartite":
        groups = list(dict.fromkeys(n.group for n in spec.nodes))
        if len(groups) != 2 or None in groups:
            out.add("/layout/algorithm", "not_bipartite",
                    f"a bipartite layout needs every node in exactly two groups; found "
                    f"{len(groups)} group value(s) {groups[:5]}.")
        else:
            gof = {n.id: n.group for n in spec.nodes}
            for (a, b), k in zip(pairs, pair_index):
                if gof[a] == gof[b]:
                    out.add(ptr("edges", k), "not_bipartite",
                            f"edge {a!r} -> {b!r} joins two nodes of group {gof[a]!r}; a "
                            "bipartite layout needs every edge to cross the groups.")
    if lay.algorithm == "layered":
        rev = back_edges(ids, [pq for pq in pairs if pq[0] != pq[1]])
        if rev:
            loopless = [k for (a, b), k in zip(pairs, pair_index) if a != b]
            names = ", ".join(f"/edges/{loopless[r]}" for r in rev[:5])
            out.add("/layout/algorithm", "cycle_in_layered_layout",
                    f"the graph has cycles; {len(rev)} edge(s) ({names}) are drawn against "
                    "the layer direction.", "info")
    positioned = [n for n in spec.nodes if n.position is not None]
    if positioned and lay.algorithm != "force":
        out.add("/layout/algorithm", "position_ignored",
                f"{len(positioned)} node position(s) are used only by the force layout; "
                f"layout {lay.algorithm!r} ignores them.", "warning")
    comps = components(ids, pairs)
    if len(comps) > 1:
        sizes = sorted((len(c) for c in comps), reverse=True)
        out.add("/edges", "disconnected",
                f"the graph has {len(comps)} connected components (sizes "
                f"{', '.join(map(str, sizes[:10]))}{', ...' if len(sizes) > 10 else ''}).",
                "info")
    for code, n in out.counts.items():
        if n > MAX_ISSUES_PER_CODE:
            out.items.append(ValidationIssue("", code, f"{n - MAX_ISSUES_PER_CODE} more "
                                             f"{code} issue(s) not listed.", "info"))
    return out.items


__all__ = ["KIND", "MAX_EDGES", "MAX_FORCE_NODES", "MAX_FORCE_WORK", "MAX_LABEL_CHARS",
           "MAX_MATRIX_NODES", "MAX_NODES", "MAX_TOTAL_LABEL_CHARS", "back_edges", "components",
           "validate_network"]
