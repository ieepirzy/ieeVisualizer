"""Graphs and networks: a semantic graph (:class:`NetworkSpec`) kept apart from
its layout hint, validated with structured issues and size limits, drawn
node-link or as an adjacency matrix, with a text outline as its accessible
alternative. See ``docs/NETWORKS.md``.

Importing this package does not import numpy or matplotlib; the layout and
rendering functions import them when called.

    >>> from ieeVisualizer.network import NetworkSpec, Node, Edge, render_network
"""

from __future__ import annotations

from typing import Any

from .. import templates as _templates  # noqa: F401 - import order: see templates/__init__
from .model import (
    DIRECTIONS,
    LAYOUTS,
    MATRIX_ORDERS,
    SELF_LOOP_POLICIES,
    Edge,
    Layout,
    NetworkSpec,
    Node,
    Quantity,
    apply_network_patch,
    graph_hash,
    network_from_dict,
    network_from_json,
    network_hash,
    network_to_dict,
    network_to_json,
)
from .validate import (
    MAX_EDGES,
    MAX_FORCE_NODES,
    MAX_LABEL_CHARS,
    MAX_MATRIX_NODES,
    MAX_NODES,
    MAX_TOTAL_LABEL_CHARS,
    validate_network,
)

_LAZY = {"render_network": "render", "build_network": "render", "network_outline": "render",
         "BuiltNetwork": "render", "compute_layout": "layout", "LayoutResult": "layout",
         "export_network": "export"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DIRECTIONS", "LAYOUTS", "MATRIX_ORDERS", "MAX_EDGES", "MAX_FORCE_NODES", "MAX_LABEL_CHARS",
    "MAX_MATRIX_NODES", "MAX_NODES", "MAX_TOTAL_LABEL_CHARS", "SELF_LOOP_POLICIES", "Edge",
    "Layout", "NetworkSpec", "Node", "Quantity", "apply_network_patch", "graph_hash",
    "network_from_dict", "network_from_json", "network_hash", "network_to_dict",
    "network_to_json", "validate_network", *_LAZY,
]
