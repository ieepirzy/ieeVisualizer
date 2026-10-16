# ruff: noqa: N999 - the package name predates this module
"""ViewSpec: composition of figures, text, KPI tiles, tables, notes and flow
diagrams into one view, rendered to SVG, static HTML or PNG.

Importing this package does not import matplotlib (rendering does). See
``docs/VIEWS.md``.
"""

from .email import EmailRender, render_email
from .export import VIEW_MANIFEST_SCHEMA_VERSION, ViewArtifact, export_view
from .flow import Box, FlowGeometry, back_edges, flow_outline, layout_flow, reading_order
from .model import (
    DIRECTIONS,
    EDGE_KINDS,
    FLOW_ALGORITHMS,
    NODE_COMPONENTS,
    NODE_TYPES,
    TEXT_STYLES,
    VIEW_SCHEMA_VERSION,
    Card,
    Comparison,
    FigureComponent,
    Flow,
    FlowEdge,
    FlowLayout,
    FlowNode,
    Grid,
    Kpi,
    Note,
    Stack,
    Table,
    Text,
    ViewSpec,
    view_from_dict,
)
from .render import VIEW_FORMATS, render_view
from .validate import (
    apply_view_patch,
    validate_view,
    view_from_json,
    view_hash,
    view_to_dict,
    view_to_json,
)

__all__ = [
    "DIRECTIONS",
    "EDGE_KINDS",
    "FLOW_ALGORITHMS",
    "NODE_COMPONENTS",
    "NODE_TYPES",
    "TEXT_STYLES",
    "VIEW_FORMATS",
    "VIEW_MANIFEST_SCHEMA_VERSION",
    "VIEW_SCHEMA_VERSION",
    "Box",
    "Card",
    "Comparison",
    "EmailRender",
    "FigureComponent",
    "Flow",
    "FlowEdge",
    "FlowGeometry",
    "FlowLayout",
    "FlowNode",
    "Grid",
    "Kpi",
    "Note",
    "Stack",
    "Table",
    "Text",
    "ViewArtifact",
    "ViewSpec",
    "apply_view_patch",
    "back_edges",
    "export_view",
    "flow_outline",
    "render_email",
    "layout_flow",
    "reading_order",
    "render_view",
    "validate_view",
    "view_from_dict",
    "view_from_json",
    "view_hash",
    "view_to_dict",
    "view_to_json",
]
