"""ViewSpec data model: a tree of layout containers holding components.

Containers: ``stack`` (vertical/horizontal), ``grid`` (``columns``) and
``card`` (titled group, children stacked vertically). Components: ``figure``
(an embedded FigureSpec or a key into ``ViewSpec.figures``), ``text``,
``kpi``, ``table``, ``caveat``/``footnote`` and ``flow`` (see
:mod:`.flow`). Parsing is strict in the same way as the FigureSpec parser:
unknown keys, wrong JSON types and unknown ``type`` values are refused with a
JSON pointer; semantic checks live in :func:`.validate.validate_view`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from ..spec import FigureSpec, SpecParseError, ValidationIssue
from ..spec._common import (
    canon_float,
    check_keys,
    copy_json,
    expect_dict,
    expect_int,
    expect_list,
    expect_number,
    expect_str,
    fail,
    ptr_join,
    suggest,
)
from ..spec.data import Dataset, dataset_from_dict, dataset_to_dict
from ..spec.figure import figure_from_dict, figure_to_dict
from .limits import MAX_DEPTH, scan_strings

VIEW_SCHEMA_VERSION = "1.0"
SUPPORTED_VIEW_SCHEMA_VERSIONS = ("1.0",)

DIRECTIONS = ("vertical", "horizontal")
TEXT_STYLES = ("body", "heading", "lead", "small")
NODE_COMPONENTS = ("card", "process", "data", "external")
EDGE_KINDS = ("dataflow", "dependency", "causal")
FLOW_ALGORITHMS = ("layered", "grid")
FLOW_DIRECTIONS = ("LR", "TB")


@dataclass
class Stack:
    children: list[Node] = field(default_factory=list)
    direction: str = "vertical"
    type: str = field(default="stack", init=False)


@dataclass
class Grid:
    children: list[Node] = field(default_factory=list)
    columns: int = 2
    type: str = field(default="grid", init=False)


@dataclass
class Card:
    children: list[Node] = field(default_factory=list)
    title: str | None = None
    type: str = field(default="card", init=False)


@dataclass
class FigureComponent:
    """Exactly one of ``figure`` (key into ``ViewSpec.figures``) or ``spec``.
    ``aspect`` is height / width of the slot the figure is rendered into."""

    figure: str | None = None
    spec: FigureSpec | None = None
    aspect: float = 0.6
    type: str = field(default="figure", init=False)

    def __post_init__(self) -> None:
        self.aspect = canon_float(self.aspect, "/aspect", "aspect")


@dataclass
class Text:
    """Plain text with a small markdown subset (``**bold**``, ``*italic*``,
    `` `code` ``, ``- `` bullets, blank-line paragraphs). Never raw HTML."""

    text: str = ""
    style: str = "body"
    type: str = field(default="text", init=False)


@dataclass
class Comparison:
    value: float | None
    label: str

    def __post_init__(self) -> None:
        self.value = canon_float(self.value, "/value", "value")


@dataclass
class Kpi:
    """A headline number. ``value=None`` renders "no data", never 0.
    ``dataset`` names a ``ViewSpec.datasets`` entry the figure came from
    (its provenance goes into the manifest); ``source`` is shown as text."""

    label: str = ""
    value: float | None = None
    unit: str | None = None
    decimals: int | None = None
    comparison: Comparison | None = None
    source: str | None = None
    dataset: str | None = None
    type: str = field(default="kpi", init=False)

    def __post_init__(self) -> None:
        # 55 and 55.0 must hash equally
        self.value = canon_float(self.value, "/value", "value")


@dataclass
class Table:
    """Columns of the snapshot ``ViewSpec.datasets[data]``; nulls show "—"."""

    data: str = ""
    columns: list[str] | None = None
    title: str | None = None
    max_rows: int | None = None
    type: str = field(default="table", init=False)


@dataclass
class Note:
    """``caveat`` (uncertainty / limitation) or ``footnote`` (provenance)."""

    text: str = ""
    type: str = "caveat"


@dataclass
class FlowNode:
    id: str
    label: str
    component: str = "card"
    group: str | None = None


@dataclass
class FlowEdge:
    """``source`` -> ``target`` (JSON ``from``/``to``). A ``causal`` edge is a
    claim and must carry a label saying what is claimed."""

    source: str
    target: str
    label: str | None = None
    kind: str = "dataflow"


@dataclass
class FlowLayout:
    """Layout hint only; the graph's meaning never depends on it."""

    algorithm: str = "layered"
    direction: str = "LR"
    columns: int | None = None


@dataclass
class Flow:
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    layout: FlowLayout = field(default_factory=FlowLayout)
    title: str | None = None
    description: str | None = None
    type: str = field(default="flow", init=False)


Node = Union[Stack, Grid, Card, FigureComponent, Text, Kpi, Table, Note, Flow]
CONTAINERS = (Stack, Grid, Card)


@dataclass
class ViewSpec:
    root: Node = field(default_factory=Stack)
    title: str | None = None
    figures: dict[str, FigureSpec] = field(default_factory=dict)
    datasets: dict[str, Dataset] = field(default_factory=dict)
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = VIEW_SCHEMA_VERSION


# --------------------------------------------------------------------------
# to_dict. Optional fields that are None are omitted; defaults are written.
# --------------------------------------------------------------------------


def _opt(out: dict[str, Any], **kw: Any) -> dict[str, Any]:
    out.update({k: v for k, v in kw.items() if v is not None})
    return out


def node_to_dict(n: Node) -> dict[str, Any]:
    if isinstance(n, Stack):
        return {"type": "stack", "direction": n.direction,
                "children": [node_to_dict(c) for c in n.children]}
    if isinstance(n, Grid):
        return {"type": "grid", "columns": n.columns,
                "children": [node_to_dict(c) for c in n.children]}
    if isinstance(n, Card):
        return _opt({"type": "card", "children": [node_to_dict(c) for c in n.children]},
                    title=n.title)
    if isinstance(n, FigureComponent):
        return _opt({"type": "figure", "aspect": n.aspect}, figure=n.figure,
                    spec=figure_to_dict(n.spec) if n.spec is not None else None)
    if isinstance(n, Text):
        return {"type": "text", "text": n.text, "style": n.style}
    if isinstance(n, Kpi):
        cmp = None if n.comparison is None else {"value": n.comparison.value,
                                                 "label": n.comparison.label}
        return _opt({"type": "kpi", "label": n.label, "value": n.value}, unit=n.unit,
                    decimals=n.decimals, comparison=cmp, source=n.source, dataset=n.dataset)
    if isinstance(n, Table):
        return _opt({"type": "table", "data": n.data}, title=n.title, max_rows=n.max_rows,
                    columns=list(n.columns) if n.columns is not None else None)
    if isinstance(n, Note):
        return {"type": n.type, "text": n.text}
    if isinstance(n, Flow):
        lay = _opt({"algorithm": n.layout.algorithm, "direction": n.layout.direction},
                   columns=n.layout.columns)
        return _opt({
            "type": "flow",
            "nodes": [_opt({"id": x.id, "label": x.label, "component": x.component},
                           group=x.group) for x in n.nodes],
            "edges": [_opt({"from": e.source, "to": e.target, "kind": e.kind}, label=e.label)
                      for e in n.edges],
            "layout": lay,
        }, title=n.title, description=n.description)
    raise TypeError(f"not a view node: {type(n).__name__}")


def view_to_dict_raw(v: ViewSpec) -> dict[str, Any]:
    return _opt({
        "schema_version": v.schema_version,
        "root": node_to_dict(v.root),
        "figures": {k: figure_to_dict(f) for k, f in v.figures.items()},
        "datasets": {k: dataset_to_dict(d) for k, d in v.datasets.items()},
        "metadata": dict(v.metadata),
    }, id=v.id, title=v.title)


# --------------------------------------------------------------------------
# from_dict
# --------------------------------------------------------------------------

NODE_TYPES = ("stack", "grid", "card", "figure", "text", "kpi", "table", "caveat", "footnote",
              "flow")


def too_deep_message() -> str:
    return (f"containers are nested more than {MAX_DEPTH} deep here; flatten the layout "
            "(a grid or a horizontal stack instead of nested stacks).")


def _children(d: dict[str, Any], path: str, depth: int) -> list[Node]:
    items = expect_list(d.get("children", []), ptr_join(path, "children"), "children")
    if items and depth >= MAX_DEPTH:
        raise fail(ptr_join(path, "children"), "too_deep", too_deep_message())
    return [node_from_dict(c, ptr_join(path, "children", i), depth + 1)
            for i, c in enumerate(items)]


def _s(d: dict[str, Any], path: str, key: str, optional: bool = True,
       default: str | None = None) -> str | None:
    if key not in d:
        return default
    return expect_str(d[key], ptr_join(path, key), key, optional=optional)


def figure_spec_at(d: Any, path: str) -> FigureSpec:
    """Parse an embedded FigureSpec, re-pathing parse errors under ``path``."""
    try:
        return figure_from_dict(d)
    except SpecParseError as e:
        raise SpecParseError([ValidationIssue(path + i.path, i.code, i.message, i.severity)
                              for i in e.issues]) from None


def node_from_dict(d: Any, path: str, depth: int = 1
                   ) -> Node:  # noqa: C901 - one branch per node type
    """``depth`` is the container depth of ``d`` (the root is 1)."""
    d = expect_dict(d, path, "a view node")
    t = d.get("type")
    if t not in NODE_TYPES:
        raise fail(ptr_join(path, "type"), "unknown_node_type",
                   f"unknown view node type {t!r}.{suggest(str(t), NODE_TYPES)} "
                   f"Supported: {', '.join(NODE_TYPES)}.")
    if t == "stack":
        check_keys(d, path, "a stack", ("type", "direction", "children"))
        return Stack(children=_children(d, path, depth), direction=_s(d, path, "direction", False,
                                                               "vertical"))
    if t == "grid":
        check_keys(d, path, "a grid", ("type", "columns", "children"))
        cols = expect_int(d.get("columns", 2), ptr_join(path, "columns"), "columns")
        return Grid(children=_children(d, path, depth), columns=cols)
    if t == "card":
        check_keys(d, path, "a card", ("type", "title", "children"))
        return Card(children=_children(d, path, depth), title=_s(d, path, "title"))
    if t == "figure":
        check_keys(d, path, "a figure component", ("type", "figure", "spec", "aspect"))
        spec = (figure_spec_at(d["spec"], ptr_join(path, "spec"))
                if d.get("spec") is not None else None)
        return FigureComponent(
            figure=_s(d, path, "figure"), spec=spec,
            aspect=expect_number(d.get("aspect", 0.6), ptr_join(path, "aspect"), "aspect"))
    if t == "text":
        check_keys(d, path, "a text component", ("type", "text", "style"), ("text",))
        return Text(text=_s(d, path, "text", False), style=_s(d, path, "style", False, "body"))
    if t == "kpi":
        check_keys(d, path, "a kpi", ("type", "label", "value", "unit", "decimals",
                                      "comparison", "source", "dataset"), ("label", "value"))
        cmp = None
        if d.get("comparison") is not None:
            cp = ptr_join(path, "comparison")
            cd = expect_dict(d["comparison"], cp, "comparison")
            check_keys(cd, cp, "comparison", ("value", "label"), ("value", "label"))
            cmp = Comparison(value=expect_number(cd["value"], ptr_join(cp, "value"), "value",
                                                 True),
                             label=expect_str(cd["label"], ptr_join(cp, "label"), "label"))
        return Kpi(label=_s(d, path, "label", False),
                   value=expect_number(d["value"], ptr_join(path, "value"), "value", True),
                   unit=_s(d, path, "unit"),
                   decimals=expect_int(d.get("decimals"), ptr_join(path, "decimals"),
                                       "decimals", True),
                   comparison=cmp, source=_s(d, path, "source"), dataset=_s(d, path, "dataset"))
    if t == "table":
        check_keys(d, path, "a table", ("type", "data", "columns", "title", "max_rows"),
                   ("data",))
        cols = None
        if d.get("columns") is not None:
            cp = ptr_join(path, "columns")
            cols = [expect_str(c, ptr_join(cp, i), "a column name")
                    for i, c in enumerate(expect_list(d["columns"], cp, "columns"))]
        return Table(data=_s(d, path, "data", False), columns=cols, title=_s(d, path, "title"),
                     max_rows=expect_int(d.get("max_rows"), ptr_join(path, "max_rows"),
                                         "max_rows", True))
    if t in ("caveat", "footnote"):
        check_keys(d, path, f"a {t}", ("type", "text"), ("text",))
        return Note(text=_s(d, path, "text", False), type=t)
    return _flow_from_dict(d, path)


def _flow_from_dict(d: dict[str, Any], path: str) -> Flow:
    check_keys(d, path, "a flow", ("type", "nodes", "edges", "layout", "title", "description"),
               ("nodes",))
    nodes = []
    for i, nd in enumerate(expect_list(d["nodes"], ptr_join(path, "nodes"), "nodes")):
        np_ = ptr_join(path, "nodes", i)
        nd = expect_dict(nd, np_, "a flow node")
        check_keys(nd, np_, "a flow node", ("id", "label", "component", "group"),
                   ("id", "label"))
        nodes.append(FlowNode(id=_s(nd, np_, "id", False), label=_s(nd, np_, "label", False),
                              component=_s(nd, np_, "component", False, "card"),
                              group=_s(nd, np_, "group")))
    edges = []
    for i, ed in enumerate(expect_list(d.get("edges", []), ptr_join(path, "edges"), "edges")):
        ep = ptr_join(path, "edges", i)
        ed = expect_dict(ed, ep, "a flow edge")
        check_keys(ed, ep, "a flow edge", ("from", "to", "label", "kind"), ("from", "to"))
        edges.append(FlowEdge(source=_s(ed, ep, "from", False), target=_s(ed, ep, "to", False),
                              label=_s(ed, ep, "label"),
                              kind=_s(ed, ep, "kind", False, "dataflow")))
    lp = ptr_join(path, "layout")
    raw = d.get("layout", {})
    if isinstance(raw, str):  # shorthand "layered" -> {"algorithm": "layered"}
        raw = {"algorithm": raw}
    ld = expect_dict(raw, lp, "layout")
    check_keys(ld, lp, "a flow layout", ("algorithm", "direction", "columns"))
    lay = FlowLayout(algorithm=_s(ld, lp, "algorithm", False, "layered"),
                     direction=_s(ld, lp, "direction", False, "LR"),
                     columns=expect_int(ld.get("columns"), ptr_join(lp, "columns"), "columns",
                                        True))
    return Flow(nodes=nodes, edges=edges, layout=lay, title=_s(d, path, "title"),
                description=_s(d, path, "description"))


def view_from_dict(d: Any) -> ViewSpec:
    try:
        return _view_from_dict(d)
    except RecursionError:  # backstop: e.g. metadata nested thousands deep
        raise fail("", "too_deep", "the document is nested too deeply to read.") from None


def _view_from_dict(d: Any) -> ViewSpec:
    d = expect_dict(d, "", "a ViewSpec")
    if "schema_version" not in d:
        raise fail("/schema_version", "missing_schema_version",
                   f"a serialized ViewSpec must carry schema_version; add "
                   f"\"schema_version\": \"{VIEW_SCHEMA_VERSION}\".")
    ver = d["schema_version"]
    if ver not in SUPPORTED_VIEW_SCHEMA_VERSIONS:
        raise fail("/schema_version", "unsupported_schema_version",
                   f"ViewSpec schema_version {ver!r} is not supported (supports "
                   f"{', '.join(SUPPORTED_VIEW_SCHEMA_VERSIONS)}); it is not reinterpreted.")
    check_keys(d, "", "a ViewSpec",
               ("schema_version", "id", "title", "root", "figures", "datasets", "metadata"),
               ("root",))
    for p, code, msg in scan_strings(d):  # before anything stores or measures a string
        raise fail(p, code, msg)
    figs = expect_dict(d.get("figures", {}), "/figures", "figures")
    dsets = expect_dict(d.get("datasets", {}), "/datasets", "datasets")
    return ViewSpec(
        schema_version=ver,
        id=_s(d, "", "id"),
        title=_s(d, "", "title"),
        root=node_from_dict(d["root"], "/root"),
        figures={k: figure_spec_at(f, ptr_join("/figures", k)) for k, f in figs.items()},
        datasets={k: dataset_from_dict(ds, ptr_join("/datasets", k)) for k, ds in dsets.items()},
        metadata=copy_json(expect_dict(d.get("metadata", {}), "/metadata", "metadata"),
                           "/metadata", "metadata"),
    )


def walk(node: Node, path: str = "/root"):
    """Yield ``(path, node)`` for every node, depth first, in document order.
    Iterative, so an over-deep tree is reported by validation, not by Python."""
    stack = [(path, node)]
    while stack:
        p, n = stack.pop()
        yield p, n
        if isinstance(n, CONTAINERS):
            stack.extend(reversed([(ptr_join(p, "children", i), c)
                                   for i, c in enumerate(n.children)]))


def too_deep_at(node: Node, limit: int) -> str | None:
    """Pointer of the first node (document order) nested deeper than
    ``limit`` (the root is at depth 1), or None. Iterative and stops there."""
    stack = [("/root", node, 1)]
    while stack:
        p, n, d = stack.pop()
        if d > limit:
            return p
        if isinstance(n, CONTAINERS):
            stack.extend(reversed([(ptr_join(p, "children", i), c, d + 1)
                                   for i, c in enumerate(n.children)]))
    return None
