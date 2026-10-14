# ruff: noqa: N999 - the package name predates this module
"""Render FigureSpecs through capability-selected backend adapters.

``render(spec, RenderRequest(format="svg"))`` validates the spec, resolves any
``DatasetRef`` through ``resolver=``, picks an adapter whose
:class:`Capabilities` cover what the spec needs and returns a
:class:`RenderResult` (bytes, media type, fidelity warnings, adapter
name/version). See ``docs/RENDERING.md``.

Importing this package does not import matplotlib; the matplotlib adapter
loads it when it first draws.
"""

from .base import (
    MEDIA_TYPES,
    Capabilities,
    CapabilityError,
    RendererAdapter,
    RenderError,
    RenderRequest,
    RenderResult,
    RenderWarning,
    Resolver,
    UnresolvedDatasetError,
)
from .mpl import MatplotlibAdapter, build_figure
from .prepare import PreparedSpec, prepare
from .registry import (
    AdapterRegistry,
    Requirements,
    default_registry,
    render,
    requirements_for,
    select_adapter,
)

__all__ = [
    "MEDIA_TYPES",
    "AdapterRegistry",
    "Capabilities",
    "CapabilityError",
    "MatplotlibAdapter",
    "PreparedSpec",
    "RenderError",
    "RenderRequest",
    "RenderResult",
    "RenderWarning",
    "RendererAdapter",
    "Requirements",
    "Resolver",
    "UnresolvedDatasetError",
    "build_figure",
    "default_registry",
    "prepare",
    "render",
    "requirements_for",
    "select_adapter",
]
