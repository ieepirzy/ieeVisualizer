"""Network templates (``org_chart``, ``dependency_map``, ``network``): data +
parameters -> a validated :class:`NetworkSpec`.

They follow the ComponentTemplate contract (declared parameters and columns,
``check()`` never raises, issue paths into the ``{"data", "params"}``
payload, provenance stamped in ``metadata["template"]``) but build a
NetworkSpec, validated with :func:`validate_network`. Pure Python: building
needs neither numpy nor matplotlib.
"""

from __future__ import annotations

import copy
from typing import Any

from ..spec import DataSnapshot, SpecValidationError
from ..templates.base import (
    ColumnRequirement,
    ComponentTemplate,
    Issues,
    Parameter,
    TemplateInputError,
    coerce_data,
    column_path,
    param_path,
)
from .model import LAYOUTS, MATRIX_ORDERS, Edge, Layout, NetworkSpec, Node, Quantity
from .validate import back_edges, validate_network

_KEY = ("str", "int")
_NUM = ("float", "int")


def _p(name: str, type_: str, desc: str, default: Any = None, allowed=None) -> Parameter:
    return Parameter(name, type_, desc, default=default,
                     allowed=tuple(allowed) if allowed else None)


_TITLE = _p("title", "string", "Figure title.")
_CAPTION = _p("caption", "string", "Caption (sources, caveats).")
_WEIGHT = (
    _p("weight_column", "column", "Numeric edge weight column (null = not recorded)."),
    _p("weight_unit", "string", "Weight unit when the column declares none ('1' if "
       "dimensionless); an error if it contradicts the column's unit."),
    _p("weight_title", "string", "What the weight measures (default: column description or "
       "name)."),
)
_SIZE = (
    _p("size_column", "column", "Numeric node size column (area is proportional to it)."),
    _p("size_unit", "string", "Size unit when the column declares none."),
    _p("size_title", "string", "What the size measures (default: column description or name)."),
)


def _quantity(data: DataSnapshot | None, params: dict[str, Any], prefix: str,
              issues: Issues, where: str) -> Quantity | None:
    """Quantity for ``<prefix>_column`` from the column's unit/description and
    the ``<prefix>_unit``/``<prefix>_title`` parameters."""
    name = params.get(f"{prefix}_column")
    if name is None or data is None or name not in data.columns:
        return None
    col = data.columns[name]
    unit, override = col.unit, params.get(f"{prefix}_unit")
    if unit and override and override != unit:
        issues.error(param_path(f"{prefix}_unit"), "unit_override_conflict",
                     f"{prefix}_unit {override!r} contradicts column {name!r}'s unit {unit!r}; "
                     "nothing is converted. Drop the override or fix the column.")
    unit = unit or override
    if not unit:
        issues.error(_cp(where, name), "missing_unit",
                     f"column {name!r} declares no unit; set {prefix}_unit (use '1' for a "
                     "dimensionless value). A weight or size without a unit is not "
                     "interpretable.")
        return None
    return Quantity(title=params.get(f"{prefix}_title") or col.description or name, unit=unit)


def _cp(where: str, name: str) -> str:
    return (where or "/data") + column_path(name)[len("/data"):]


def _values(data: DataSnapshot, name: str | None, n: int) -> list[Any]:
    return list(data.columns[name].values) if name else [None] * n


def _keys(data: DataSnapshot, name: str, issues: Issues, where: str) -> list[str]:
    vals = data.columns[name].values
    for r, v in enumerate(vals):
        if v is None:
            issues.error(_cp(where, name), "null_in_key_column",
                         f"row {r} of {name!r} is null; every row needs an id.")
            return []
    return [str(v) for v in vals]


class _NetworkTemplate(ComponentTemplate):
    """ComponentTemplate whose product is a NetworkSpec."""

    def build(self, data: Any = None, **params: Any) -> NetworkSpec:  # type: ignore[override]
        issues = Issues()
        snapshot, resolved = self._run_checks(data, params, issues)
        if issues.errors:
            raise TemplateInputError(issues.items)
        spec = self._build(snapshot, resolved, issues)
        if issues.errors:
            raise TemplateInputError(issues.items)
        found = validate_network(spec)
        errors = [i for i in found if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
        meta = {"name": self.name, "version": self.version, "params": copy.deepcopy(resolved)}
        warns = issues.warnings + [i for i in found if i.severity == "warning"]
        if warns:
            meta["warnings"] = [i.to_dict() for i in warns]
        spec.metadata["template"] = meta
        return spec

    def check(self, data: Any = None, **params: Any):
        issues = Issues()
        snapshot, resolved = self._run_checks(data, params, issues)
        if not issues.errors:
            spec = self._build(snapshot, resolved, issues)
            if not issues.errors:
                issues.items += [i for i in validate_network(spec) if i.severity != "info"]
        return issues.items

    def _edges_spec(self, data: DataSnapshot, params: dict[str, Any], issues: Issues,
                    where: str = "") -> tuple[list[Edge], list[str], Quantity | None]:
        n = data.num_rows or 0
        src = _keys(data, params["source_column"], issues, where)
        dst = _keys(data, params["target_column"], issues, where)
        weight = _quantity(data, params, "weight", issues, where)
        ws = _values(data, params.get("weight_column"), n)
        kinds = _values(data, params.get("kind_column"), n)
        edges = [Edge(s, t, weight=None if w is None else float(w),
                      kind=None if k is None else str(k))
                 for s, t, w, k in zip(src, dst, ws, kinds)]
        return edges, list(dict.fromkeys(x for pair in zip(src, dst) for x in pair)), weight


class OrgChartTemplate(_NetworkTemplate):
    name = "org_chart"
    version = "1.0.0"
    title = "Organisation chart"
    description = ("A hierarchy (one row per unit or person, with its parent) drawn as a "
                   "top-down tree; groups coloured, optional size such as headcount.")
    parameters = (
        _p("id_column", "column", "Unique id of each unit/person.", "id"),
        _p("parent_column", "column", "Id of the parent (null for a root).", "parent"),
        _p("label_column", "column", "Display label (default: the id)."),
        _p("group_column", "column", "Group, e.g. department (coloured)."),
        *_SIZE,
        _p("direction", "string", "TB (top-down) or LR (left to right).", "TB", ("TB", "LR")),
        _TITLE, _CAPTION,
    )
    columns = (
        ColumnRequirement("id", "id_column", _KEY, "unique node id", unit="not_applicable"),
        ColumnRequirement("parent", "parent_column", _KEY, "parent id, null for a root",
                          unit="not_applicable"),
        ColumnRequirement("label", "label_column", ("str",), "label", unit="not_applicable"),
        ColumnRequirement("group", "group_column", ("str",), "group", unit="not_applicable"),
        ColumnRequirement("size", "size_column", _NUM, "node size (e.g. headcount)"),
    )
    notes = ("Edges run parent -> child and are undirected in the drawing (no arrows); the "
             "layout is the deterministic layered tree.",
             "A parent id that is not in the id column, or a cycle, is an error: the data "
             "is not a hierarchy.")

    def _build(self, data, params, issues):
        n = data.num_rows or 0
        ids = _keys(data, params["id_column"], issues, "")
        parents = data.columns[params["parent_column"]].values
        if len(set(ids)) != len(ids):
            dup = next(i for i in ids if ids.count(i) > 1)
            issues.error(column_path(params["id_column"]), "duplicate_key",
                         f"id {dup!r} appears more than once; ids must be unique.")
        known = set(ids)
        edges = []
        for r, p in enumerate(parents):
            if p is None:
                continue
            if str(p) not in known:
                issues.error(column_path(params["parent_column"]), "unknown_parent",
                             f"row {r}: parent {p!r} is not an id; every parent must be a row.")
                continue
            edges.append(Edge(str(p), ids[r]))
        if not issues.errors and back_edges(ids, [(e.source, e.target) for e in edges]):
            issues.error(column_path(params["parent_column"]), "not_a_hierarchy",
                         "the parent links form a cycle, so this is not a hierarchy.")
        size = _quantity(data, params, "size", issues, "")
        labels = _values(data, params.get("label_column"), n)
        groups = _values(data, params.get("group_column"), n)
        sizes = _values(data, params.get("size_column"), n)
        nodes = [Node(i, label=lb, group=g, size=None if s is None else float(s))
                 for i, lb, g, s in zip(ids, labels, groups, sizes)]
        return NetworkSpec(nodes=nodes, edges=edges, node_size=size, title=params["title"],
                           caption=params["caption"], provenance=data.provenance,
                           layout=Layout("layered", direction=params["direction"]))

    def example(self):
        rows = [("ceo", None, "Chief executive", "Management", 2),
                ("ops", "ceo", "Operations", "Operations", 4),
                ("sales", "ceo", "Sales", "Sales", 6),
                ("fin", "ceo", "Finance", "Management", 3),
                ("field", "ops", "Field teams", "Operations", 38),
                ("fleet", "ops", "Fleet", "Operations", 7),
                ("b2b", "sales", "Business sales", "Sales", 5),
                ("b2c", "sales", "Consumer sales", "Sales", None),
                ("acc", "fin", "Accounting", "Management", 2)]
        cols = list(zip(*rows))
        return {"data": {"type": "snapshot", "columns": {
            "id": {"values": list(cols[0])}, "parent": {"values": list(cols[1])},
            "name": {"values": list(cols[2])}, "dept": {"values": list(cols[3])},
            "headcount": {"values": list(cols[4]), "unit": "people", "dtype": "int",
                          "description": "Headcount"}},
            "provenance": {"source": "examples", "notes": "Illustrative, not real data."}},
            "params": {"id_column": "id", "parent_column": "parent", "label_column": "name",
                       "group_column": "dept", "size_column": "headcount",
                       "title": "Organisation (illustrative)"}}


_EDGE_PARAMS = (
    _p("source_column", "column", "Edge source id column.", "source"),
    _p("target_column", "column", "Edge target id column.", "target"),
    *_WEIGHT,
    _p("kind_column", "column", "Edge kind (drawn with distinct dash patterns)."),
)
_EDGE_COLUMNS = (
    ColumnRequirement("source", "source_column", _KEY, "edge source", unit="not_applicable"),
    ColumnRequirement("target", "target_column", _KEY, "edge target", unit="not_applicable"),
    ColumnRequirement("weight", "weight_column", _NUM, "edge weight", drawn=False),
    ColumnRequirement("kind", "kind_column", ("str",), "edge kind", unit="not_applicable"),
)


class DependencyMapTemplate(_NetworkTemplate):
    name = "dependency_map"
    version = "1.0.0"
    title = "Dependency map"
    description = ("Directed dependencies (one row per edge: source depends on target) as a "
                   "layered or force layout, optional weight with unit and edge kinds.")
    parameters = _EDGE_PARAMS + (
        _p("layout", "string", "layered or force.", "layered", ("layered", "force")),
        _p("direction", "string", "TB or LR (layered).", "LR", ("TB", "LR")),
        _p("seed", "integer", "Force layout seed.", 0),
        _TITLE, _CAPTION,
    )
    columns = _EDGE_COLUMNS
    notes = ("Arrows point from the dependent (source) to what it depends on (target).",
             "A null weight is drawn in its own 'not recorded' colour and flagged, never as 0.")

    def _build(self, data, params, issues):
        edges, ids, weight = self._edges_spec(data, params, issues)
        return NetworkSpec(nodes=[Node(i) for i in ids], edges=edges, directed=True,
                           edge_weight=weight, title=params["title"], caption=params["caption"],
                           provenance=data.provenance,
                           layout=Layout(params["layout"], seed=params["seed"],
                                         direction=params["direction"]))

    def example(self):
        rows = [("web", "api", 420.0, "sync"), ("mobile", "api", 180.0, "sync"),
                ("api", "auth", 600.0, "sync"), ("api", "pricing", 95.0, "sync"),
                ("api", "db", 900.0, "sync"), ("pricing", "db", 60.0, "sync"),
                ("api", "queue", None, "async"), ("worker", "queue", 40.0, "async"),
                ("worker", "db", 75.0, "sync"), ("worker", "email", 12.0, "async")]
        cols = list(zip(*rows))
        return {"data": {"type": "snapshot", "columns": {
            "source": {"values": list(cols[0])}, "target": {"values": list(cols[1])},
            "calls": {"values": list(cols[2]), "unit": "req/min",
                      "description": "Call rate"},
            "mode": {"values": list(cols[3])}},
            "provenance": {"source": "examples", "notes": "Illustrative, not measured."}},
            "params": {"weight_column": "calls", "kind_column": "mode",
                       "title": "Service dependencies (illustrative)"}}


class NetworkTemplate(_NetworkTemplate):
    name = "network"
    version = "1.0.0"
    title = "Network"
    description = ("Generic graph from an edge table (and optional node table): force, "
                   "circular, grid, bipartite, layered or adjacency-matrix view.")
    parameters = _EDGE_PARAMS + (
        _p("weight_signed", "boolean", "Negative weights are meaningful (e.g. correlations); "
           "otherwise a negative weight is an error.", False),
        _p("directed", "boolean", "Edges are directed.", False),
        _p("id_column", "column", "Node id column of the node table.", "id"),
        _p("label_column", "column", "Node label column."),
        _p("group_column", "column", "Node group/community column (coloured)."),
        *_SIZE,
        _p("layout", "string", "Layout or view.", "force", LAYOUTS),
        _p("order", "string", "Matrix ordering.", "group", MATRIX_ORDERS),
        _p("seed", "integer", "Force layout seed.", 0),
        _p("self_loops", "string", "forbid or allow.", "forbid", ("forbid", "allow")),
        _TITLE, _CAPTION,
    )
    columns = _EDGE_COLUMNS + (
        ColumnRequirement("node id", "id_column", _KEY, "node table id (nodes only)",
                          unit="not_applicable"),
        ColumnRequirement("node size", "size_column", _NUM, "node size (nodes only)"),
    )
    notes = ("data is {'edges': <table>, 'nodes': <table, optional>}; columns of the "
             "node table are read from 'nodes'. Without a node table, nodes are the edge "
             "endpoints in order of appearance.",
             "Edge weights never move nodes: distances in a force layout are not a "
             "measurement.")

    def _run_checks(self, data, params, issues):
        resolved = self._check_params(params, issues)
        if not isinstance(data, dict) or "edges" not in data or set(data) - {"edges", "nodes"}:
            issues.error("/data", "invalid_data",
                         "data must be {'edges': <table>, 'nodes': <optional table>}.")
            return None, resolved
        parts = {}
        for part in ("edges", "nodes"):
            if data.get(part) is None:
                continue
            sub = Issues()
            parts[part] = coerce_data(data[part], sub)
            for i in sub.items:
                issues.error(i.path.replace("/data", f"/data/{part}", 1), i.code, i.message)
        need = {"edges": ("source_column", "target_column", "weight_column", "kind_column"),
                "nodes": ("id_column", "label_column", "group_column", "size_column")}
        for part, names in need.items():
            snap = parts.get(part)
            for pname in names:
                col = resolved.get(pname)
                if col is None or (part == "nodes" and snap is None):
                    continue
                if snap is None or col not in snap.columns:
                    issues.error(param_path(pname), "missing_column",
                                 f"column {col!r} is not in the {part} table.")
        return parts, resolved

    def _build(self, parts, params, issues):
        edges, ids, weight = self._edges_spec(parts["edges"], params, issues, "/data/edges")
        if weight is not None:
            weight.signed = params["weight_signed"]
        nodes_t, size = parts.get("nodes"), None
        if nodes_t is not None:
            n = nodes_t.num_rows or 0
            nid = _keys(nodes_t, params["id_column"], issues, "/data/nodes")
            size = _quantity(nodes_t, params, "size", issues, "/data/nodes")
            nodes = [Node(i, label=lb, group=None if g is None else str(g),
                          size=None if s is None else float(s))
                     for i, lb, g, s in zip(nid, _values(nodes_t, params["label_column"], n),
                                            _values(nodes_t, params["group_column"], n),
                                            _values(nodes_t, params["size_column"], n))]
        else:
            nodes = [Node(i) for i in ids]
        return NetworkSpec(nodes=nodes, edges=edges, directed=params["directed"],
                           edge_weight=weight, node_size=size, self_loops=params["self_loops"],
                           title=params["title"], caption=params["caption"],
                           provenance=parts["edges"].provenance,
                           layout=Layout(params["layout"], seed=params["seed"],
                                         order=params["order"]))

    def example(self):
        groups = {"A": ["a1", "a2", "a3", "a4"], "B": ["b1", "b2", "b3", "b4"],
                  "C": ["c1", "c2", "c3"]}
        nodes = [(i, g) for g, ids in groups.items() for i in ids]
        pairs = [("a1", "a2", 5), ("a1", "a3", 3), ("a2", "a3", 4), ("a3", "a4", 2),
                 ("a2", "a4", 1), ("b1", "b2", 6), ("b2", "b3", 2), ("b1", "b3", 3),
                 ("b3", "b4", 4), ("b2", "b4", None), ("c1", "c2", 2), ("c2", "c3", 3),
                 ("c1", "c3", 1), ("a4", "b1", 1), ("b4", "c1", 1)]
        e = list(zip(*pairs))
        return {"data": {
            "edges": {"type": "snapshot", "columns": {
                "source": {"values": list(e[0])}, "target": {"values": list(e[1])},
                "papers": {"values": list(e[2]), "unit": "papers", "dtype": "int",
                           "description": "Joint papers"}},
                "provenance": {"source": "examples", "notes": "Illustrative, not real data."}},
            "nodes": {"type": "snapshot", "columns": {
                "id": {"values": [i for i, _ in nodes]},
                "lab": {"values": [f"Lab {g}" for _, g in nodes]}}}},
            "params": {"weight_column": "papers", "group_column": "lab",
                       "title": "Co-authorship (illustrative)"}}


TEMPLATES = (OrgChartTemplate, DependencyMapTemplate, NetworkTemplate)
