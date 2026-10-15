# ruff: noqa: N999 - the package name predates this module
"""Versioned component templates that build :class:`~ieeVisualizer.spec.FigureSpec`
objects from data + parameters, and the registry agents browse them through.

Importing this package never imports matplotlib, numpy or sympy; the
``vector_field_2d`` template imports numpy/sympy lazily when it samples
expressions. See ``docs/TEMPLATES.md``.

    >>> from ieeVisualizer import templates
    >>> [t["name"] for t in templates.list_templates()]
    ['bar_comparison', 'funnel', 'timeseries', 'vector_field_2d']
    >>> spec = templates.get("vector_field_2d").build(u="-y", v="x", grid=5)
"""

from __future__ import annotations

from typing import Any

from ..spec import FigureSpec
from .bar_comparison import BarComparisonTemplate
from .base import (
    PARAM_TYPES,
    ColumnRequirement,
    ComponentTemplate,
    Parameter,
    TemplateInputError,
    TemplateRegistryError,
)
from .funnel import FunnelTemplate
from .registry import TemplateRegistry, parse_semver
from .timeseries import TimeseriesTemplate
from .vector_field import VectorField2DTemplate


def _builtin_registry() -> TemplateRegistry:
    reg = TemplateRegistry()
    reg.register(TimeseriesTemplate())
    reg.register(VectorField2DTemplate())
    reg.register(BarComparisonTemplate())
    reg.register(FunnelTemplate())
    return reg


#: The registry pre-loaded with the built-in templates. Register your own
#: templates here, or build a separate :class:`TemplateRegistry`.
default_registry = _builtin_registry()


def get(name: str, version: str | None = None) -> ComponentTemplate:
    """``default_registry.get``: the template, latest release by default."""
    return default_registry.get(name, version)


def list_templates() -> list[dict[str, Any]]:
    """``default_registry.list()``: one JSON summary per template."""
    return default_registry.list()


def describe(name: str, version: str | None = None) -> dict[str, Any]:
    """``default_registry.describe``: JSON catalogue entry with example payload."""
    return default_registry.describe(name, version)


def catalog() -> dict[str, Any]:
    """``default_registry.catalog()``: every template's catalogue entry."""
    return default_registry.catalog()


def build(name: str, data: Any = None, params: dict[str, Any] | None = None,
          version: str | None = None) -> FigureSpec:
    """``default_registry.build``: build a FigureSpec from a named template."""
    return default_registry.build(name, data, params, version)


__all__ = [
    "PARAM_TYPES",
    "BarComparisonTemplate",
    "ColumnRequirement",
    "ComponentTemplate",
    "FunnelTemplate",
    "Parameter",
    "TemplateInputError",
    "TemplateRegistry",
    "TemplateRegistryError",
    "TimeseriesTemplate",
    "VectorField2DTemplate",
    "build",
    "catalog",
    "default_registry",
    "describe",
    "get",
    "list_templates",
    "parse_semver",
]
