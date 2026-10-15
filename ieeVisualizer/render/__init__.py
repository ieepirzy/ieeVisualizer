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
from .profiles import (
    LEGIBILITY_CODES,
    LegibilityError,
    RenderProfile,
    check_legible,
    get_profile,
    list_profiles,
)
from .registry import (
    AdapterRegistry,
    Requirements,
    default_registry,
    render,
    requirements_for,
    select_adapter,
)

__all__ = [
    "LEGIBILITY_CODES",
    "MEDIA_TYPES",
    "AdapterRegistry",
    "Capabilities",
    "CapabilityError",
    "LegibilityError",
    "MatplotlibAdapter",
    "PreparedSpec",
    "RenderError",
    "RenderProfile",
    "RenderRequest",
    "RenderResult",
    "RenderWarning",
    "RendererAdapter",
    "Requirements",
    "Resolver",
    "UnresolvedDatasetError",
    "build_figure",
    "check_legible",
    "default_registry",
    "get_profile",
    "list_profiles",
    "prepare",
    "render",
    "requirements_for",
    "select_adapter",
]
