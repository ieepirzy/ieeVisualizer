"""Semantic validation of a ViewSpec, plus JSON round trip, hash and patch.

:func:`validate_view` returns :class:`~ieeVisualizer.spec.ValidationIssue`
objects whose ``path`` is a JSON pointer into :func:`view_to_dict` output, so
an agent can pass it straight to :func:`apply_view_patch`. Embedded and
referenced FigureSpecs are validated with :func:`ieeVisualizer.spec.validate`
and their issues re-pathed under the figure's pointer.

========================== ===================================================
code                       meaning
========================== ===================================================
unsupported_schema_version view schema_version not readable by this library
invalid_value              a field outside its allowed set (direction, style,
                           component, edge kind, layout, columns, aspect ...)
empty_container            (warning) a stack/grid/card with no children
figure_source_missing      a figure component names neither figure nor spec
figure_source_conflict     a figure component names both
unknown_figure             ``figure`` is not a key of ``figures``
unused_figure              (info) a ``figures`` entry nothing references
unknown_dataset            a table/kpi names a dataset the view lacks
table_needs_snapshot       a table is bound to a DatasetRef (not resolved here)
unknown_column             a table column the snapshot lacks
empty_flow                 a flow without nodes
duplicate_node_id          two flow nodes share an id
unknown_node               an edge ``from``/``to`` names no node
causal_edge_unlabelled     a causal edge without a label stating the claim
cycle_in_layered_layout    (info) cycle; the named edges are drawn reversed
self_loop                  (info) an edge from a node to itself
========================== ===================================================
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Sequence
from typing import Any

from ..spec import (
    DatasetRef,
    SpecValidationError,
    ValidationIssue,
)
from ..spec import validate as validate_figure
from ..spec._common import canonical_json, fail, is_number, ptr_join, sha256_of
from ..spec.patch import _apply_one, _err
from .flow import back_edges
from .model import (
    CONTAINERS,
    DIRECTIONS,
    EDGE_KINDS,
    FLOW_ALGORITHMS,
    FLOW_DIRECTIONS,
    NODE_COMPONENTS,
    SUPPORTED_VIEW_SCHEMA_VERSIONS,
    TEXT_STYLES,
    FigureComponent,
    Flow,
    Grid,
    Kpi,
    Stack,
    Table,
    Text,
    ViewSpec,
    view_from_dict,
    view_to_dict_raw,
    walk,
)


def _choice(out: list, path: str, value: Any, allowed: Sequence[str], what: str) -> None:
    if value not in allowed:
        out.append(ValidationIssue(path, "invalid_value",
                                   f"{what} must be one of {', '.join(allowed)}, got {value!r}."))


def _flow(out: list, f: Flow, path: str) -> None:
    add = out.append
    if not f.nodes:
        add(ValidationIssue(ptr_join(path, "nodes"), "empty_flow",
                            "a flow needs at least one node."))
    seen: dict[str, int] = {}
    for i, n in enumerate(f.nodes):
        p = ptr_join(path, "nodes", i)
        if n.id in seen:
            add(ValidationIssue(ptr_join(p, "id"), "duplicate_node_id",
                                f"node id {n.id!r} is already used by /nodes/{seen[n.id]}; "
                                "ids must be unique."))
        seen.setdefault(n.id, i)
        _choice(out, ptr_join(p, "component"), n.component, NODE_COMPONENTS, "component")
    for i, e in enumerate(f.edges):
        p = ptr_join(path, "edges", i)
        for key, ref in (("from", e.source), ("to", e.target)):
            if ref not in seen:
                add(ValidationIssue(ptr_join(p, key), "unknown_node",
                                    f"edge {key} {ref!r} names no node; known ids: "
                                    f"{', '.join(sorted(seen)) or 'none'}."))
        _choice(out, ptr_join(p, "kind"), e.kind, EDGE_KINDS, "edge kind")
        if e.kind == "causal" and not (e.label or "").strip():
            add(ValidationIssue(ptr_join(p, "label"), "causal_edge_unlabelled",
                                "a causal edge is a claim; give it a label stating what is "
                                "claimed (and on what evidence), or use kind 'dependency'."))
        if e.source == e.target and e.source in seen:
            add(ValidationIssue(p, "self_loop", f"edge from {e.source!r} to itself.", "info"))
    lp = ptr_join(path, "layout")
    _choice(out, ptr_join(lp, "algorithm"), f.layout.algorithm, FLOW_ALGORITHMS, "algorithm")
    _choice(out, ptr_join(lp, "direction"), f.layout.direction, FLOW_DIRECTIONS, "direction")
    if f.layout.columns is not None and f.layout.columns < 1:
        add(ValidationIssue(ptr_join(lp, "columns"), "invalid_value", "columns must be >= 1."))
    if f.layout.algorithm == "layered" and len(seen) == len(f.nodes):
        cyc = [k for k in back_edges(f) if f.edges[k].source != f.edges[k].target]
        if cyc:
            add(ValidationIssue(lp, "cycle_in_layered_layout",
                                "the graph has a cycle; a layered layout draws edge(s) "
                                f"{', '.join(f'/edges/{k}' for k in cyc)} against the layer "
                                "direction.", "info"))


def validate_view(view: ViewSpec) -> list[ValidationIssue]:  # noqa: C901 - one check per node type
    """Structured issues in document order (never raises for a bad view)."""
    out: list[ValidationIssue] = []
    add = out.append
    if view.schema_version not in SUPPORTED_VIEW_SCHEMA_VERSIONS:
        add(ValidationIssue("/schema_version", "unsupported_schema_version",
                            f"schema_version {view.schema_version!r} is not supported."))
    used: set[str] = set()
    for path, n in walk(view.root):
        if isinstance(n, CONTAINERS) and not n.children:
            add(ValidationIssue(ptr_join(path, "children"), "empty_container",
                                f"this {n.type} has no children.", "warning"))
        if isinstance(n, Stack):
            _choice(out, ptr_join(path, "direction"), n.direction, DIRECTIONS, "direction")
        elif isinstance(n, Grid) and (not isinstance(n.columns, int) or n.columns < 1):
            add(ValidationIssue(ptr_join(path, "columns"), "invalid_value",
                                f"columns must be an integer >= 1, got {n.columns!r}."))
        elif isinstance(n, FigureComponent):
            if not (is_number(n.aspect) and math.isfinite(n.aspect) and 0.1 <= n.aspect <= 4):
                add(ValidationIssue(ptr_join(path, "aspect"), "invalid_value",
                                    "aspect (height/width) must be in [0.1, 4], got "
                                    f"{n.aspect!r}."))
            if n.figure is None and n.spec is None:
                add(ValidationIssue(path, "figure_source_missing",
                                    "give either 'figure' (a key of /figures) or 'spec'."))
            elif n.figure is not None and n.spec is not None:
                add(ValidationIssue(path, "figure_source_conflict",
                                    "give either 'figure' or 'spec', not both."))
            elif n.spec is not None:
                out.extend(ValidationIssue(ptr_join(path, "spec") + i.path, i.code, i.message,
                                           i.severity) for i in validate_figure(n.spec))
            elif n.figure not in view.figures:
                add(ValidationIssue(ptr_join(path, "figure"), "unknown_figure",
                                    f"no figure {n.figure!r} in /figures; have "
                                    f"{', '.join(sorted(view.figures)) or 'none'}."))
            else:
                used.add(n.figure)
        elif isinstance(n, Text):
            _choice(out, ptr_join(path, "style"), n.style, TEXT_STYLES, "style")
        elif isinstance(n, Kpi):
            for key, v in (("value", n.value),
                           ("comparison/value", n.comparison and n.comparison.value)):
                if v is not None and not (is_number(v) and math.isfinite(v)):
                    add(ValidationIssue(path + "/" + key, "invalid_type",
                                        f"{key} must be a finite number or null (= no data), "
                                        f"got {v!r}."))
            if n.dataset is not None and n.dataset not in view.datasets:
                add(ValidationIssue(ptr_join(path, "dataset"), "unknown_dataset",
                                    f"no dataset {n.dataset!r} in /datasets."))
        elif isinstance(n, Table):
            ds = view.datasets.get(n.data)
            if ds is None:
                add(ValidationIssue(ptr_join(path, "data"), "unknown_dataset",
                                    f"no dataset {n.data!r} in /datasets; have "
                                    f"{', '.join(sorted(view.datasets)) or 'none'}."))
            elif isinstance(ds, DatasetRef):
                add(ValidationIssue(ptr_join(path, "data"), "table_needs_snapshot",
                                    f"dataset {n.data!r} is a reference; tables draw inline "
                                    "snapshots only. Resolve it into a snapshot first."))
            else:
                for i, c in enumerate(n.columns or []):
                    if c not in ds.columns:
                        add(ValidationIssue(ptr_join(path, "columns", i), "unknown_column",
                                            f"dataset {n.data!r} has no column {c!r}."))
            if n.max_rows is not None and n.max_rows < 0:
                add(ValidationIssue(ptr_join(path, "max_rows"), "invalid_value",
                                    "max_rows must be >= 0."))
        elif isinstance(n, Flow):
            _flow(out, n, path)
    for key, fig in view.figures.items():
        fp = ptr_join("/figures", key)
        out.extend(ValidationIssue(fp + i.path, i.code, i.message, i.severity)
                   for i in validate_figure(fig))
        if key not in used:
            add(ValidationIssue(fp, "unused_figure", f"figure {key!r} is not placed in the view.",
                                "info"))
    return out


def errors(view: ViewSpec) -> list[ValidationIssue]:
    return [i for i in validate_view(view) if i.severity == "error"]


# --------------------------------------------------------------------------
# JSON round trip, hash, patch
# --------------------------------------------------------------------------


def view_to_dict(view: ViewSpec) -> dict[str, Any]:
    return copy.deepcopy(view_to_dict_raw(view))


def view_to_json(view: ViewSpec, indent: int | None = None) -> str:
    """Canonical JSON (sorted keys, compact when ``indent`` is None)."""
    return canonical_json(view_to_dict(view), indent=indent)


def view_from_json(s: str) -> ViewSpec:
    def refuse(name: str) -> Any:
        raise fail("", "non_finite_number", f"the JSON text contains {name}, not a JSON number.")

    try:
        d = json.loads(s, parse_constant=refuse)
    except json.JSONDecodeError as e:
        raise fail("", "invalid_json", f"not valid JSON: {e}") from None
    return view_from_dict(d)


def view_hash(view: ViewSpec) -> str:
    """``sha256:<hex>`` of the canonical JSON (figures and datasets included)."""
    return sha256_of(view_to_dict(view))


def apply_view_patch(view: ViewSpec, ops: Sequence[dict[str, Any]], *, check: bool = True
                     ) -> ViewSpec:
    """RFC-6902-style ``add``/``replace``/``remove``/``test`` on the dict form,
    with the same semantics as :func:`ieeVisualizer.spec.apply_patch`. Returns
    a new ViewSpec; raises PatchError / SpecParseError / SpecValidationError."""
    if isinstance(ops, dict) or not isinstance(ops, (list, tuple)):
        raise _err("/ops", "invalid_op", "ops must be a list of operation objects.")
    doc = view_to_dict(view)
    for n, op in enumerate(ops):
        doc = _apply_one(doc, op, f"/ops/{n}")
    new = view_from_dict(doc)
    if check and errors(new):
        raise SpecValidationError(errors(new))
    return new
