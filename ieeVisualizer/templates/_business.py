"""Helpers shared by the business templates (``bar_comparison``, ``funnel``)."""

from __future__ import annotations

from typing import Any

from ..spec import Column, DataSnapshot
from ..themes import NEUTRAL, NumberLocale, ThemeError, default_registry
from .base import Issues, Parameter, column_path, param_path

THEME_PARAM = Parameter(
    "theme", "string", "Theme name (or name@version) recorded as the spec's theme; text the "
    "template writes itself (e.g. rates) uses that theme's number locale. Default: none "
    "(renderer default theme, neutral number format).")


def check_theme(p: dict[str, Any], issues: Issues) -> None:
    ref = p.get("theme")
    if ref is None:
        return
    try:
        default_registry.resolve(ref)
    except ThemeError as e:
        issues.error(param_path("theme"), "invalid_parameter", e.issues[0].message)


def locale_for(ref: str | None) -> NumberLocale:
    if ref is None:
        return NEUTRAL
    return default_registry.resolve(ref).locale or NEUTRAL


def check_key_column(data: DataSnapshot, name: str, what: str, issues: Issues) -> None:
    """No nulls and no duplicates in a category/stage column."""
    values = data.columns[name].values
    nulls = sum(v is None for v in values)
    if nulls:
        issues.error(column_path(name), "null_in_key_column",
                     f"{what} column {name!r} has {nulls} null value(s); those rows cannot be "
                     "placed. Fill or drop them upstream.")
        return
    seen, dups = set(), []
    for v in values:
        if v in seen and v not in dups:
            dups.append(v)
        seen.add(v)
    if dups:
        issues.error(column_path(name), "invalid_column_values",
                     f"{what} column {name!r} repeats {dups[:5]}; each {what} needs exactly one "
                     "row. Aggregate upstream.")


def copy_rows(data: DataSnapshot, names: list[str], rows: list[int]) -> DataSnapshot:
    import copy

    cols = {}
    for name in names:
        c = data.columns[name]
        cols[name] = Column(values=[c.values[i] for i in rows], dtype=c.dtype, unit=c.unit,
                            description=c.description, denominator=c.denominator,
                            aggregation=c.aggregation)
    return DataSnapshot(columns=cols, provenance=copy.deepcopy(data.provenance))
