# ruff: noqa: N999 - the package name predates this module
"""Themes: corporate visual identity as checked, versioned data.

    >>> from ieeVisualizer import themes
    >>> t = themes.get("muutto365-report-light")
    >>> t.record()["version"]
    '1.0.0'
    >>> themes.format_quantity(1234.5, "EUR", t.locale)
    '1\\xa0234,50\\xa0€'

A :class:`Theme` is plain data (JSON round-trip, SHA-256 identity). The
registry refuses a theme that fails its structural or accessibility checks
(:func:`check_theme`). ``FigureSpec.theme`` / ``RenderRequest.theme`` name a
theme as ``"name"`` or ``"name@version"``; renderers resolve it with
:func:`resolve_for_render`, which falls back to ``default`` with a warning
for an unknown name. Importing this package does not import matplotlib.
See ``docs/THEMES.md``.
"""

from __future__ import annotations

from typing import Any

from .accessibility import check_theme, report
from .builtin import BUILTIN
from .color import contrast_ratio, delta_e76, delta_e2000, simulate_cvd
from .locale import (
    FI_FI,
    NEUTRAL,
    NumberLocale,
    format_currency,
    format_number,
    format_percent,
    format_quantity,
)
from .registry import ThemeRegistry
from .theme import FigureTarget, RoleStyle, Theme, ThemeError, Typography


def _builtin_registry() -> ThemeRegistry:
    reg = ThemeRegistry()
    for t in BUILTIN:
        reg.register(t)
    return reg


#: Registry pre-loaded with the built-in themes; register your own here.
default_registry = _builtin_registry()


def get(ref: str) -> Theme:
    """``default_registry.resolve(ref)``; ``ref`` is ``name`` or ``name@version``."""
    return default_registry.resolve(ref)


def register(theme: Theme) -> Theme:
    return default_registry.register(theme)


def list_themes() -> list[dict[str, Any]]:
    return default_registry.list()


def describe(ref: str) -> dict[str, Any]:
    return default_registry.describe(ref)


def resolve_for_render(ref: str | None) -> tuple[Theme, str | None]:
    """``(theme, problem)``: the theme ``ref`` names, or ``default`` plus a
    sentence saying why (``ref`` unknown). ``None`` means ``default``."""
    if ref is None:
        return default_registry.resolve("default"), None
    try:
        return default_registry.resolve(ref), None
    except ThemeError as e:
        return default_registry.resolve("default"), e.issues[0].message


__all__ = [
    "FI_FI",
    "NEUTRAL",
    "FigureTarget",
    "NumberLocale",
    "RoleStyle",
    "Theme",
    "ThemeError",
    "ThemeRegistry",
    "Typography",
    "check_theme",
    "contrast_ratio",
    "default_registry",
    "delta_e2000",
    "delta_e76",
    "describe",
    "format_currency",
    "format_number",
    "format_percent",
    "format_quantity",
    "get",
    "list_themes",
    "register",
    "report",
    "resolve_for_render",
    "simulate_cvd",
]
