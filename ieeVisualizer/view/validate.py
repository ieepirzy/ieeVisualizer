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
column_length_mismatch     columns of a view dataset differ in length
invalid_text               a string holds a control character / lone surrogate
text_too_long              one string longer than ``MAX_TEXT_CHARS``
too_much_text              the view's text adds up to more than allowed
too_deep                   containers nested deeper than ``MAX_DEPTH``, or
                           free-form JSON deeper than ``MAX_JSON_DEPTH``
too_many_nodes             too many view nodes, or flow nodes in the view
too_many_edges             too many flow edges in the view
too_many_dummy_nodes       layered flows would need too many long-edge slots
too_many_figures           more figures than ``MAX_FIGURES``
too_many_rows              a table's ``max_rows`` above ``MAX_TABLE_ROWS``
too_many_cells             the tables draw more cells than allowed
too_many_columns           a table, or the view's tables, draw too many columns
too_narrow                 a grid / horizontal stack / card leaves a child a
                           slot narrower than ``MIN_SLOT_WIDTH`` at 960 px
table_truncated            (warning) a table longer than ``MAX_TABLE_ROWS``
                           shows its first rows and says so
========================== ===================================================

The size limits (:mod:`.limits`) are checked here, before any layout, so an
oversized view is refused quickly with a pointer instead of rendering for
minutes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from ..spec import (
    DatasetRef,
    FigureSpec,
    SpecValidationError,
    ValidationIssue,
)
from ..spec import validate as validate_figure
from ..spec._common import (
    beyond_float64,
    canonical_json,
    copy_tree,
    fail,
    is_number,
    json_problem,
    out_of_range_error,
    ptr_join,
    sha256_of,
)
from ..spec.figure import figure_to_dict
from ..spec.patch import _apply_one, _err
from . import limits as L
from .flow import back_edges, dummy_count
from .model import (
    CONTAINERS,
    DIRECTIONS,
    EDGE_KINDS,
    FLOW_ALGORITHMS,
    FLOW_DIRECTIONS,
    NODE_COMPONENTS,
    SUPPORTED_VIEW_SCHEMA_VERSIONS,
    TEXT_STYLES,
    Card,
    FigureComponent,
    Flow,
    FlowNode,
    Grid,
    Kpi,
    Note,
    Stack,
    Table,
    Text,
    ViewSpec,
    too_deep_at,
    too_deep_message,
    view_from_dict,
    view_to_dict_raw,
    walk,
)

MAX_TEXT_ISSUES = 20  # invalid_text / text_too_long issues reported per view
DEFAULT_WIDTH = 960.0  # render_view's default width; slot widths are validated at it


class _Budget:
    """View-wide counters for the :mod:`.limits` budgets; each limit is
    reported once, at the node where it was exceeded."""

    def __init__(self, out: list):
        self.out = out
        self.used: dict[str, int] = {}

    def take(self, what: str, n: int, limit: int, path: str, code: str, msg: str) -> bool:
        before = self.used.get(what, 0)
        self.used[what] = before + n
        if before <= limit < before + n:
            self.out.append(ValidationIssue(path, code, msg.format(n=before + n, limit=limit)))
        return before + n <= limit


def _node_text(n: Any) -> int:
    """Characters of a node's own strings that the layout measures (a table's
    headers and cells are counted by :func:`_table_size`)."""
    if isinstance(n, Flow):
        k = len(n.title or "") + len(n.description or "")
        k += sum(len(x.label) + len(x.group or "") for x in n.nodes if isinstance(x, FlowNode))
        return k + sum(len(e.label or "") + (len("claim: ") if e.kind == "causal" else 0)
                       for e in n.edges)
    if isinstance(n, Kpi):  # the unit is drawn after the value, the delta and the reference
        return (len(n.label or "") + 3 * len(n.unit or "") + len(n.source or "")
                + len((n.comparison.label or "") if n.comparison else ""))
    if isinstance(n, (Text, Note)):
        return len(n.text or "")
    if isinstance(n, (Card, Table)):
        return len(n.title or "")
    return 0


def _figure_text(spec: Any) -> int:
    """Characters of a FigureSpec's own strings that its renderer measures and
    draws (title, caption, axis labels and units, legend title, layer names,
    encoding titles and fields, annotation labels). Dataset values,
    ``metadata`` and ``style`` are not drawn as text and are not counted."""
    try:
        doc = figure_to_dict(spec)
    except Exception:  # noqa: BLE001 - a malformed figure is reported by its validation
        return 0
    total = 0
    todo: list[Any] = [v for k, v in doc.items()
                       if k not in ("datasets", "metadata", "schema_version")]
    while todo:
        v = todo.pop()
        if isinstance(v, str):
            total += len(v)
        elif isinstance(v, dict):
            todo.extend(x for k, x in v.items() if k != "style")
        elif isinstance(v, (list, tuple)):
            todo.extend(v)
    return total


def too_narrow_at(view: ViewSpec, width: float) -> ValidationIssue | None:
    """The first container (document order) that would give a child a slot
    narrower than ``MIN_SLOT_WIDTH`` px when the view is drawn ``width`` px
    wide, as a ``too_narrow`` issue; None if every slot is wide enough. Mirrors
    the box model of :mod:`.layout` (and of the HTML grid). Iterative; call it
    only on a tree within ``MAX_DEPTH``."""
    from .layout import GAP, PAD, slot_columns

    todo = [("/root", view.root, width - 2 * PAD)]
    while todo:
        path, n, w = todo.pop()
        if not isinstance(n, CONTAINERS) or not n.children:
            continue
        if isinstance(n, Card):
            cols, cw, where = 1, w - 32, path
            what = "a card's frame"
        else:
            cols = slot_columns(n)
            cw = (w - GAP * (cols - 1)) / cols
            where = ptr_join(path, "columns") if isinstance(n, Grid) else path
            what = (f"{cols} columns" if isinstance(n, Grid)
                    else f"{cols} side-by-side children")
        if cw < L.MIN_SLOT_WIDTH:
            return ValidationIssue(where, "too_narrow", (
                f"{what} leave each child {max(cw, 0):.0f} px of the {w:.0f} px available at "
                f"a view width of {width:g} px; the minimum is {L.MIN_SLOT_WIDTH} px. Use "
                "fewer columns, or a vertical stack / several rows instead."))
        todo.extend(reversed([(ptr_join(path, "children", i), c, cw)
                              for i, c in enumerate(n.children)]))
    return None


def _strings(out: list, view: ViewSpec) -> None:
    try:
        doc = view_to_dict_raw(view)
    except Exception:  # noqa: BLE001 - a malformed object model is reported elsewhere
        return
    found = 0
    for path, code, msg in L.scan_strings(doc):
        found += 1
        if found <= MAX_TEXT_ISSUES:
            out.append(ValidationIssue(path, code, msg))
    if found > MAX_TEXT_ISSUES:
        out.append(ValidationIssue("", "invalid_text",
                                   f"{found - MAX_TEXT_ISSUES} more string(s) with the same "
                                   "problems are not listed."))


def _choice(out: list, path: str, value: Any, allowed: Sequence[str], what: str) -> None:
    if value not in allowed:
        out.append(ValidationIssue(path, "invalid_value",
                                   f"{what} must be one of {', '.join(allowed)}, got {value!r}."))


def _flow_size(f: Flow, path: str, budget: _Budget, unique: bool) -> None:
    ok = budget.take("flow_nodes", len(f.nodes), L.MAX_FLOW_NODES, ptr_join(path, "nodes"),
                     "too_many_nodes", "the view's flows have {n} nodes in total; the limit is "
                     "{limit}. Split the diagram or summarise groups of nodes as one node.")
    ok &= budget.take("flow_edges", len(f.edges), L.MAX_FLOW_EDGES, ptr_join(path, "edges"),
                      "too_many_edges", "the view's flows have {n} edges in total; the limit "
                      "is {limit}. Split the diagram or merge parallel edges.")
    if ok and unique and f.layout.algorithm == "layered":
        budget.take("dummies", dummy_count(f), L.MAX_FLOW_DUMMY_NODES, ptr_join(path, "edges"),
                    "too_many_dummy_nodes", "edges spanning several layers would need {n} "
                    "routing slots in total; the limit is {limit}. Use layout 'grid', or "
                    "shorten long edges (they skip many layers).")


def _flow(out: list, f: Flow, path: str, budget: _Budget) -> None:
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
    _flow_size(f, path, budget, len(seen) == len(f.nodes))
    if (f.layout.algorithm == "layered" and len(seen) == len(f.nodes)
            and len(f.nodes) <= L.MAX_FLOW_NODES and len(f.edges) <= L.MAX_FLOW_EDGES):
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
    where = too_deep_at(view.root, L.MAX_DEPTH)
    if where is not None:  # nothing below may recurse into such a tree
        add(ValidationIssue(where, "too_deep", too_deep_message()))
        return out
    _strings(out, view)
    if not isinstance(view.metadata, dict):
        add(ValidationIssue("/metadata", "invalid_type", "metadata must be a JSON object."))
    else:
        problem = json_problem(view.metadata, "/metadata")
        if problem:
            add(ValidationIssue(*problem))
    budget = _Budget(out)
    budget.take("text", len(view.title or ""), L.MAX_TOTAL_TEXT_CHARS, "/title",
                "too_much_text", "the view's text adds up to {n} characters; the limit is "
                "{limit}.")
    narrow = too_narrow_at(view, DEFAULT_WIDTH)
    if narrow is not None:
        add(narrow)
    used: set[str] = set()
    for path, n in walk(view.root):
        budget.take("nodes", 1, L.MAX_VIEW_NODES, path, "too_many_nodes",
                    "the view has more than {limit} nodes; split it into several views.")
        budget.take("text", _node_text(n), L.MAX_TOTAL_TEXT_CHARS, path, "too_much_text",
                    "the view's text adds up to {n} characters; the limit is {limit}.")
        if isinstance(n, CONTAINERS) and not n.children:
            add(ValidationIssue(ptr_join(path, "children"), "empty_container",
                                f"this {n.type} has no children.", "warning"))
        if isinstance(n, Stack):
            _choice(out, ptr_join(path, "direction"), n.direction, DIRECTIONS, "direction")
        elif isinstance(n, Grid) and (not isinstance(n.columns, int) or isinstance(n.columns, bool)
                                     or n.columns < 1):
            add(ValidationIssue(ptr_join(path, "columns"), "invalid_value",
                                f"columns must be an integer >= 1, got {n.columns!r}."))
        elif isinstance(n, FigureComponent):
            budget.take("figures", 1, L.MAX_FIGURES, path, "too_many_figures",
                        "the view places more than {limit} figures; split it into several "
                        "views.")
            spec = n.spec if n.spec is not None else view.figures.get(n.figure)
            if spec is not None:
                budget.take("text", L.FIGURE_TEXT_WEIGHT * _figure_text(spec),
                            L.MAX_TOTAL_TEXT_CHARS, path, "too_much_text",
                            "the view's text adds up to {n} characters (an embedded figure's "
                            "strings count twice); the limit is {limit}.")
            if beyond_float64(n.aspect):
                add(out_of_range_error(ptr_join(path, "aspect"), "aspect", n.aspect).issues[0])
            elif not (is_number(n.aspect) and math.isfinite(n.aspect) and 0.1 <= n.aspect <= 4):
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
                if beyond_float64(v):
                    add(out_of_range_error(path + "/" + key, "value", v).issues[0])
                elif v is not None and not (is_number(v) and math.isfinite(v)):
                    add(ValidationIssue(path + "/" + key, "invalid_type",
                                        f"{key} must be a finite number or null (= no data), "
                                        f"got {v!r}."))
            d = n.decimals
            if d is not None and not (isinstance(d, int) and not isinstance(d, bool)
                                      and 0 <= d <= 10):
                add(ValidationIssue(ptr_join(path, "decimals"), "invalid_value",
                                    f"decimals must be an integer from 0 to 10, got {d!r}."))
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
            elif n.max_rows is not None and n.max_rows > L.MAX_TABLE_ROWS:
                add(ValidationIssue(ptr_join(path, "max_rows"), "too_many_rows",
                                    f"max_rows {n.max_rows} is above the limit of "
                                    f"{L.MAX_TABLE_ROWS} drawn rows; a table that long belongs "
                                    "in a data export, not a view."))
            if ds is not None and not isinstance(ds, DatasetRef):
                _table_size(n, ds, path, budget)
        elif isinstance(n, Flow):
            _flow(out, n, path, budget)
    out.extend(i for i in validate_figure(FigureSpec(datasets=dict(view.datasets)))
               if i.code == "column_length_mismatch" or "/provenance/" in i.path)
    for key, fig in view.figures.items():
        fp = ptr_join("/figures", key)
        out.extend(ValidationIssue(fp + i.path, i.code, i.message, i.severity)
                   for i in validate_figure(fig))
        if key not in used:
            add(ValidationIssue(fp, "unused_figure", f"figure {key!r} is not placed in the view.",
                                "info"))
    return out


def _table_size(n: Table, ds: Any, path: str, budget: _Budget) -> None:
    """Columns, cells and text a table draws. Headers (name + unit) are laid
    out even with ``max_rows`` 0, so columns and header text are counted on
    their own, not through the cell count."""
    total = ds.num_rows
    names = [c for c in (n.columns if n.columns is not None else list(ds.columns))
             if c in ds.columns]
    if len(names) > L.MAX_TABLE_COLUMNS:
        budget.out.append(ValidationIssue(
            ptr_join(path, "columns") if n.columns is not None else ptr_join(path, "data"),
            "too_many_columns", f"the table draws {len(names)} columns; the limit is "
            f"{L.MAX_TABLE_COLUMNS}. Choose the columns to show with 'columns'."))
    budget.take("columns", len(names), L.MAX_TOTAL_TABLE_COLUMNS, path, "too_many_columns",
                "the view's tables draw {n} columns in total; the limit is {limit}. Show "
                "fewer columns or split the view.")
    heads = sum(len(c) + len(ds.columns[c].unit or "") for c in names)
    budget.take("text", heads, L.MAX_TOTAL_TEXT_CHARS, path, "too_much_text",
                "the view's text adds up to {n} characters; the limit is {limit}.")
    if total is None:  # column_length_mismatch is reported for the dataset
        return
    if n.max_rows is None and total > L.MAX_TABLE_ROWS:
        budget.out.append(ValidationIssue(
            path, "table_truncated", f"the table has {total} rows; the first "
            f"{L.MAX_TABLE_ROWS} are drawn, followed by 'Showing {L.MAX_TABLE_ROWS} of "
            f"{total} rows'. Set max_rows to choose fewer.", "warning"))
    shown = min(total, L.MAX_TABLE_ROWS if n.max_rows is None
                 else max(0, min(n.max_rows, L.MAX_TABLE_ROWS)))
    if budget.take("cells", shown * len(names), L.MAX_TABLE_CELLS, path, "too_many_cells",
                   "the view's tables draw {n} cells in total; the limit is {limit}. Show "
                   "fewer rows (max_rows) or columns."):
        chars = sum(len(v) for c in names for v in ds.columns[c].values[:shown]
                    if isinstance(v, str))
        budget.take("text", chars, L.MAX_TOTAL_TEXT_CHARS, path,
                    "too_much_text", "the view's text adds up to {n} characters; the limit "
                    "is {limit}.")


def errors(view: ViewSpec) -> list[ValidationIssue]:
    return [i for i in validate_view(view) if i.severity == "error"]


# --------------------------------------------------------------------------
# JSON round trip, hash, patch
# --------------------------------------------------------------------------


def view_to_dict(view: ViewSpec) -> dict[str, Any]:
    """A fresh dict (no aliasing of ``view``). Iterative and depth-bounded: a
    view built in Python with containers nested past ``MAX_DEPTH`` or a tree
    of metadata thousands deep raises SpecParseError ``too_deep``, never
    RecursionError."""
    where = too_deep_at(view.root, L.MAX_DEPTH)
    if where is not None:
        raise fail(where, "too_deep", too_deep_message())
    return copy_tree(view_to_dict_raw(view), "", "the view")


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
    except RecursionError:
        raise fail("", "too_deep", "the JSON text is nested too deeply to read.") from None
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
