"""NetworkSpec: a semantic graph (nodes, edges, measured quantities) plus a
separate layout hint, with strict parsing, canonical JSON and a hash.

Plain dataclasses following the ``ieeVisualizer.spec`` conventions, so they
can move to pydantic with the rest of the models. Importing this module never
imports numpy or matplotlib.

The graph is the claim; the layout is only a hint. Nothing in ``nodes`` or
``edges`` depends on the layout, so the same graph can be drawn node-link,
as an adjacency matrix or read as a text outline.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..spec._common import (
    SpecParseError,
    SpecValidationError,
    canon_float,
    canonical_json,
    check_keys,
    expect_bool,
    expect_dict,
    expect_int,
    expect_list,
    expect_number,
    expect_str,
    fail,
    non_finite_error,
    ptr_join,
    sha256_of,
)
from ..spec.data import Provenance

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = ("1.0",)
KIND = "network"
LAYOUTS = ("force", "circular", "layered", "grid", "bipartite", "matrix")
MATRIX_ORDERS = ("group", "seriation", "input")
DIRECTIONS = ("TB", "LR")
SELF_LOOP_POLICIES = ("forbid", "allow")
_PROV_FIELDS = ("source", "query", "retrieved_at", "source_version", "timezone", "run_id",
                "notes")
#: Free-form JSON (``nodes[].attributes``, ``metadata``, ``provenance.extra``)
#: may nest at most this deep ...
MAX_JSON_DEPTH = 64
#: ... and takes at most this many bytes of canonical JSON, all of it together.
MAX_FREE_JSON_BYTES = 2_000_000

# C0 controls other than tab, newline and carriage return, and the two
# noncharacters U+FFFE/U+FFFF: none of them may appear in XML 1.0, so a string
# holding one would make the SVG (and the outline embedded in it) malformed.
_BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def text_problem(s: str) -> str | None:
    """Why ``s`` cannot be stored, hashed and drawn, or ``None``: a lone
    surrogate (not encodable as UTF-8) or a character XML 1.0 forbids."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as e:
        return (f"contains a lone surrogate (U+{ord(s[e.start]):04X} at character "
                f"{e.start}), which is not valid Unicode text; fix the encoding upstream.")
    m = _BAD_CHARS.search(s)
    if m:
        return (f"contains the control character U+{ord(m.group()):04X} at character "
                f"{m.start()}, which SVG/XML cannot hold; remove it upstream.")
    return None


def _str(value: Any, path: str, what: str, optional: bool = False) -> str | None:
    """:func:`expect_str` that also refuses text :func:`text_problem` rejects."""
    s = expect_str(value, path, what, optional)
    if s is not None:
        bad = text_problem(s)
        if bad:
            raise fail(path, "invalid_text", f"{what} {bad}")
    return s


def _leaf_bytes(v: Any, path: str) -> int:
    try:
        return len(canonical_json(v).encode("utf-8"))
    except ValueError:  # an int beyond Python's int -> str digit limit
        raise fail(path, "number_out_of_range", "a number with that many digits cannot be "
                   "written as JSON that readers can hold; rescale it upstream.") from None


def free_json(value: Any, path: str, what: str,
              budget: int = MAX_FREE_JSON_BYTES) -> tuple[Any, int]:
    """Validated deep copy of free-form JSON and its size in bytes of canonical
    JSON (exactly ``len(canonical_json(value).encode())``). Iterative, so a
    deeply nested input cannot exhaust the interpreter stack. Raises
    :class:`SpecParseError`: ``too_deep`` beyond :data:`MAX_JSON_DEPTH`
    levels, ``too_large`` beyond ``budget`` bytes, ``invalid_text``,
    ``invalid_type``, ``non_finite_number``; the pointer names the value."""
    root: list[Any] = [None]
    stack: list[tuple[Any, Any, Any, str, int]] = [(value, root, 0, path, 1)]
    size = 0
    while stack:
        v, parent, key, p, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise fail(p, "too_deep", f"{what} nests more than {MAX_JSON_DEPTH} levels deep; "
                       "flatten it or keep it outside the spec.")
        if isinstance(v, dict):
            out: Any = {}
            size += 2 + max(len(v) - 1, 0)
            children = []
            for k, item in v.items():
                if not isinstance(k, str):
                    raise fail(p, "invalid_type", f"{what} keys must be strings, got {k!r}.")
                kp = ptr_join(p, k)
                bad = text_problem(k)
                if bad:
                    raise fail(kp, "invalid_text", f"{what} key {bad}")
                size += _leaf_bytes(k, kp) + 1
                out[k] = None  # keeps the input's key order
                children.append((item, out, k, kp, depth + 1))
            stack.extend(reversed(children))
        elif isinstance(v, list):
            out = [None] * len(v)
            size += 2 + max(len(v) - 1, 0)
            stack.extend((item, out, i, ptr_join(p, i), depth + 1)
                         for i, item in reversed(list(enumerate(v))))
        elif v is None or isinstance(v, (str, bool, int, float)):
            if isinstance(v, str):
                bad = text_problem(v)
                if bad:
                    raise fail(p, "invalid_text", f"{what} {bad}")
            elif isinstance(v, float) and not math.isfinite(v):
                raise non_finite_error(p, what, v)
            out = v
            size += _leaf_bytes(v, p)
        else:
            raise fail(p, "invalid_type",
                       f"{what} must hold only JSON values, got {type(v).__name__}.")
        if size > budget:
            raise fail(p, "too_large",
                       f"free-form JSON (node attributes, metadata and provenance.extra "
                       f"together) exceeds {MAX_FREE_JSON_BYTES:,} bytes of canonical JSON; "
                       "keep large payloads outside the spec and reference them.")
        parent[key] = out
    return root[0], size


@dataclass
class Quantity:
    """What a number on nodes or edges means: ``title`` and ``unit`` (``"1"``
    for a dimensionless value). ``signed=True`` allows negative edge weights
    (e.g. correlations); node sizes are never signed."""

    title: str
    unit: str
    signed: bool = False


@dataclass
class Node:
    """``size`` is a value of the spec's ``node_size`` quantity (``None`` =
    not recorded, never 0). ``position`` pins the node in a force layout."""

    id: str
    label: str | None = None
    group: str | None = None
    size: float | None = None
    position: list[float] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.size = canon_float(self.size)
        if isinstance(self.position, (list, tuple)):
            self.position = [canon_float(v) for v in self.position]

    @property
    def display_label(self) -> str:
        return self.label if self.label else self.id


@dataclass
class Edge:
    """``weight`` is a value of the spec's ``edge_weight`` quantity (``None``
    = not recorded: drawn neutral and flagged, never as 0). ``directed``
    overrides the spec-wide default for this edge."""

    source: str
    target: str
    weight: float | None = None
    directed: bool | None = None
    kind: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        self.weight = canon_float(self.weight)


@dataclass
class Layout:
    """Layout hint, kept apart from the graph. ``seed``/``iterations`` are
    used by ``force``, ``direction`` by ``layered``, ``order`` by ``matrix``."""

    algorithm: str = "force"
    seed: int = 0
    iterations: int = 200
    direction: str = "TB"
    order: str = "group"


@dataclass
class NetworkSpec:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    directed: bool = False
    layout: Layout = field(default_factory=Layout)
    node_size: Quantity | None = None
    edge_weight: Quantity | None = None
    self_loops: str = "forbid"
    id: str | None = None
    title: str | None = None
    caption: str | None = None
    theme: str | None = None
    provenance: Provenance = field(default_factory=Provenance)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def is_directed(self, edge: Edge) -> bool:
        return self.directed if edge.directed is None else edge.directed

    def to_dict(self) -> dict[str, Any]:
        return network_to_dict(self)

    def to_json(self, indent: int | None = None) -> str:
        return network_to_json(self, indent)


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _canon(v: Any) -> Any:
    try:
        return canon_float(v)
    except ValueError:
        return v  # written as is; validate_network reports it


def _quantity_to_dict(q: Quantity | None) -> dict[str, Any] | None:
    if q is None:
        return None
    return {"title": q.title, "unit": q.unit, "signed": q.signed}


def network_to_dict(spec: NetworkSpec) -> dict[str, Any]:
    """Plain-JSON form. Optional ``None`` fields are omitted, defaults are
    written, numbers are canonical floats. Deep-copied."""
    nodes = []
    for n in spec.nodes:
        d = _drop_none({"id": n.id, "label": n.label, "group": n.group, "size": _canon(n.size),
                        "position": None if n.position is None
                        else [_canon(v) for v in n.position]})
        if n.attributes:
            d["attributes"] = copy.deepcopy(n.attributes)
        nodes.append(d)
    edges = [_drop_none({"source": e.source, "target": e.target, "weight": _canon(e.weight),
                         "directed": e.directed, "kind": e.kind, "label": e.label})
             for e in spec.edges]
    lay = spec.layout
    out: dict[str, Any] = {
        "schema_version": spec.schema_version,
        "kind": KIND,
        "nodes": nodes,
        "edges": edges,
        "directed": spec.directed,
        "self_loops": spec.self_loops,
        "layout": {"algorithm": lay.algorithm, "seed": lay.seed, "iterations": lay.iterations,
                   "direction": lay.direction, "order": lay.order},
    }
    out.update(_drop_none({"id": spec.id, "title": spec.title, "caption": spec.caption,
                           "theme": spec.theme, "node_size": _quantity_to_dict(spec.node_size),
                           "edge_weight": _quantity_to_dict(spec.edge_weight)}))
    prov = _drop_none({k: getattr(spec.provenance, k) for k in _PROV_FIELDS})
    if spec.provenance.extra:
        prov["extra"] = spec.provenance.extra
    if prov:
        out["provenance"] = prov
    if spec.metadata:
        out["metadata"] = spec.metadata
    return copy.deepcopy(out)


def network_to_json(spec: NetworkSpec, indent: int | None = None) -> str:
    """Canonical JSON (``indent=None``: sorted keys, compact; the hashing form)."""
    return canonical_json(network_to_dict(spec), indent=indent)


def network_hash(spec: NetworkSpec) -> str:
    """``sha256:<hex>`` of the canonical JSON (graph, layout hint and metadata)."""
    return sha256_of(network_to_dict(spec))


def graph_hash(spec: NetworkSpec) -> str:
    """Hash of the semantic graph only (nodes, edges, quantities): the same
    graph under another layout or title, or with other pinned
    ``nodes[].position`` (a layout hint), has the same ``graph_hash``."""
    d = network_to_dict(spec)
    for n in d["nodes"]:
        n.pop("position", None)
    return sha256_of({k: d.get(k) for k in ("nodes", "edges", "directed", "node_size",
                                             "edge_weight")})


def _quantity(d: Any, path: str, what: str) -> Quantity | None:
    if d is None:
        return None
    d = expect_dict(d, path, what)
    check_keys(d, path, what, ("title", "unit", "signed"), ("title", "unit"))
    return Quantity(title=_str(d["title"], ptr_join(path, "title"), "title"),
                    unit=_str(d["unit"], ptr_join(path, "unit"), "unit"),
                    signed=expect_bool(d.get("signed", False), ptr_join(path, "signed"),
                                       "signed"))


def _free(d: Any, path: str, what: str, used: list[int]) -> dict[str, Any]:
    """Parse one free-form JSON object, charging it to the spec's byte budget."""
    out, size = free_json(expect_dict(d, path, what), path, what,
                          MAX_FREE_JSON_BYTES - used[0])
    used[0] += size
    return out


def _node(d: Any, path: str, used: list[int]) -> Node:
    d = expect_dict(d, path, "a node")
    check_keys(d, path, "a node", ("id", "label", "group", "size", "position", "attributes"),
               ("id",))
    pos = d.get("position")
    if pos is not None:
        pp = ptr_join(path, "position")
        pos = expect_list(pos, pp, "position")
        if len(pos) != 2:
            raise fail(pp, "invalid_value", f"position must be [x, y], got {len(pos)} values.")
        pos = [expect_number(v, ptr_join(pp, i), "a coordinate") for i, v in enumerate(pos)]
    return Node(
        id=_str(d["id"], ptr_join(path, "id"), "node id"),
        label=_str(d.get("label"), ptr_join(path, "label"), "label", optional=True),
        group=_str(d.get("group"), ptr_join(path, "group"), "group", optional=True),
        size=expect_number(d.get("size"), ptr_join(path, "size"), "size", optional=True),
        position=pos,
        attributes=_free(d.get("attributes", {}), ptr_join(path, "attributes"), "attributes",
                         used))


def _edge(d: Any, path: str) -> Edge:
    d = expect_dict(d, path, "an edge")
    check_keys(d, path, "an edge", ("source", "target", "weight", "directed", "kind", "label"),
               ("source", "target"))
    directed = d.get("directed")
    return Edge(
        source=_str(d["source"], ptr_join(path, "source"), "source"),
        target=_str(d["target"], ptr_join(path, "target"), "target"),
        weight=expect_number(d.get("weight"), ptr_join(path, "weight"), "weight", optional=True),
        directed=None if directed is None else expect_bool(directed, ptr_join(path, "directed"),
                                                           "directed"),
        kind=_str(d.get("kind"), ptr_join(path, "kind"), "kind", optional=True),
        label=_str(d.get("label"), ptr_join(path, "label"), "label", optional=True))


def _layout(d: Any, path: str) -> Layout:
    if isinstance(d, str):  # shorthand: "force"
        d = {"algorithm": d}
    d = expect_dict(d, path, "layout")
    check_keys(d, path, "layout", ("algorithm", "seed", "iterations", "direction", "order"))
    lay = Layout()
    for key in ("algorithm", "direction", "order"):
        if key in d:
            setattr(lay, key, _str(d[key], ptr_join(path, key), key))
    for key in ("seed", "iterations"):
        if key in d:
            setattr(lay, key, expect_int(d[key], ptr_join(path, key), key))
    return lay


def network_from_dict(d: Any) -> NetworkSpec:
    """Parse the dict form. Raises :class:`SpecParseError` on the first
    structural problem; semantic checks are :func:`validate_network`'s."""
    d = expect_dict(d, "", "a network spec")
    check_keys(d, "", "a network spec",
               ("schema_version", "kind", "nodes", "edges", "directed", "self_loops", "layout",
                "id", "title", "caption", "theme", "node_size", "edge_weight", "provenance",
                "metadata"), ("schema_version", "nodes"))
    version = d["schema_version"]
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise fail("/schema_version", "unsupported_schema_version",
                   f"network schema_version {version!r} is not readable by this library "
                   f"(supported: {', '.join(SUPPORTED_SCHEMA_VERSIONS)}).")
    if d.get("kind", KIND) != KIND:
        raise fail("/kind", "invalid_value", f"kind must be {KIND!r}, got {d.get('kind')!r}.")
    used = [0]  # bytes of free-form JSON so far (MAX_FREE_JSON_BYTES in total)
    nodes = [_node(n, ptr_join("/nodes", i), used)
             for i, n in enumerate(expect_list(d["nodes"], "/nodes", "nodes"))]
    edges = [_edge(e, ptr_join("/edges", i))
             for i, e in enumerate(expect_list(d.get("edges", []), "/edges", "edges"))]
    prov = Provenance()
    if "provenance" in d:
        pd = expect_dict(d["provenance"], "/provenance", "provenance")
        check_keys(pd, "/provenance", "provenance", _PROV_FIELDS + ("extra",))
        for k in _PROV_FIELDS:
            setattr(prov, k, _str(pd.get(k), f"/provenance/{k}", k, optional=True))
        prov.extra = _free(pd.get("extra", {}), "/provenance/extra", "provenance.extra", used)
    opt = {k: _str(d.get(k), f"/{k}", k, optional=True)
           for k in ("id", "title", "caption", "theme")}
    return NetworkSpec(
        nodes=nodes, edges=edges,
        directed=expect_bool(d.get("directed", False), "/directed", "directed"),
        self_loops=_str(d.get("self_loops", "forbid"), "/self_loops", "self_loops"),
        layout=_layout(d.get("layout", {}), "/layout"),
        node_size=_quantity(d.get("node_size"), "/node_size", "node_size"),
        edge_weight=_quantity(d.get("edge_weight"), "/edge_weight", "edge_weight"),
        provenance=prov,
        metadata=_free(d.get("metadata", {}), "/metadata", "metadata", used),
        schema_version=version, **opt)


def network_from_json(s: str) -> NetworkSpec:
    """Parse JSON text; ``NaN``/``Infinity`` constants are refused."""
    import json

    def refuse(name: str) -> Any:
        raise fail("", "non_finite_number", f"the JSON text contains {name}, which is not a "
                   "JSON number.")
    try:
        d = json.loads(s, parse_constant=refuse)
    except json.JSONDecodeError as e:
        raise fail("", "invalid_json", f"not valid JSON: {e}") from None
    return network_from_dict(d)


def apply_network_patch(spec: NetworkSpec, ops: Sequence[dict[str, Any]], *,
                        check: bool = True) -> NetworkSpec:
    """RFC-6902 ``add``/``replace``/``remove``/``test`` on the dict form, with
    the same semantics (and the same implementation) as
    :func:`ieeVisualizer.spec.apply_patch`. Atomic; ``spec`` is not mutated.
    With ``check``, a result with validation errors raises
    :class:`SpecValidationError`."""
    from ..spec.patch import _apply_one, _err
    from .validate import validate_network

    if isinstance(ops, dict) or not isinstance(ops, (list, tuple)):
        raise _err("/ops", "invalid_op", "ops must be a list of operation objects.")
    doc = network_to_dict(spec)
    for n, op in enumerate(ops):
        doc = _apply_one(doc, op, f"/ops/{n}")
    new = network_from_dict(doc)
    if check:
        errors = [i for i in validate_network(new) if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
    return new


__all__ = ["MAX_FREE_JSON_BYTES", "MAX_JSON_DEPTH", "Edge", "Layout", "NetworkSpec", "Node",
           "Quantity", "SpecParseError", "free_json", "text_problem",
           "apply_network_patch", "graph_hash", "network_from_dict", "network_from_json",
           "network_hash", "network_to_dict", "network_to_json"]
