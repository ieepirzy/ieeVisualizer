"""Matplotlib adapter. Importing this package does not import matplotlib;
it is imported when a figure is first built."""

from .adapter import (
    ADAPTER_NAME,
    ADAPTER_VERSION,
    CAPABILITIES,
    BuiltFigure,
    MatplotlibAdapter,
    build_figure,
    render_context,
)

__all__ = [
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "CAPABILITIES",
    "BuiltFigure",
    "MatplotlibAdapter",
    "build_figure",
    "render_context",
]
