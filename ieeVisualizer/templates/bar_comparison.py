"""Built-in ``bar_comparison`` template: categories x one or more measures as
grouped bars, optionally next to the previous period's value.

* One bar layer per measure (``value_columns``), side by side in each
  category slot: the renderer dodges bar layers that share categories, in
  layer order, so the template sets no ``bar_width``/``bar_offset`` (those
  are explicit overrides for hand-written specs). ``previous_column`` (one
  measure only) adds a bar for the previous period to the left of (below)
  the current one - the first layer, so also first in the legend - drawn
  with role ``reference`` (lighter, the theme's neutral colour) and named in
  the legend, so it cannot be mistaken for the current value. It is
  observed data; the role only changes how it is drawn.
* Rows keep their order unless ``sort`` says otherwise (by the first
  measure; a null sorts last). Horizontal bars list the first category at
  the top; ``metadata["template"]["category_order"]`` is the reading order.
* A null value stays null: the renderer labels its slot "no data". Negative
  values are drawn below a zero line. Nothing is aggregated, filled or
  converted; all drawn columns must share one unit.
* ``value_labels`` asks the renderer to print each bar's value, in the
  render theme's number format (``1 234,5``; ``1 234,50 €`` for EUR).
"""

from __future__ import annotations

from typing import Any

from ..spec import Axis, BarMark, DataSnapshot, FigureSpec, ReferenceLine
from ..spec.data import dataset_to_dict
from ._business import THEME_PARAM, check_key_column, check_theme, copy_rows
from .base import ColumnRequirement, ComponentTemplate, Issues, Parameter, column_path, param_path

AUTO_HORIZONTAL_LABEL = 12  # characters
AUTO_HORIZONTAL_COUNT = 12  # categories


class BarComparisonTemplate(ComponentTemplate):
    name = "bar_comparison"
    version = "1.0.0"
    title = "Bar comparison"
    description = (
        "Grouped bars of one or more measures per category, optionally with the previous "
        "period's value drawn distinctly beside the current one; value labels in the theme's "
        "number format."
    )
    parameters = (
        Parameter("category_column", "column", "Column naming each category (one row per "
                  "category).", default="category"),
        Parameter("value_columns", "column_list", "Numeric measure column(s), one bar each per "
                  "category.", required=True),
        Parameter("previous_column", "column", "Previous-period values of the (single) measure, "
                  "drawn as a lighter reference bar beside it."),
        Parameter("previous_label", "string", "Legend label of the previous-period bars.",
                  default="previous period"),
        Parameter("sort", "string", "Order of categories: as given, or by the first measure.",
                  default="none", allowed=("none", "ascending", "descending")),
        Parameter("orientation", "string", "Bar direction; 'auto' is horizontal when a label is "
                  f"longer than {AUTO_HORIZONTAL_LABEL} characters or there are more than "
                  f"{AUTO_HORIZONTAL_COUNT} categories.", default="auto",
                  allowed=("auto", "vertical", "horizontal")),
        Parameter("value_labels", "boolean", "Print each bar's value at its end.", default=True),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption (source, period, caveats)."),
        Parameter("value_label", "string", "Value-axis label; default: the measure's "
                  "description or name when there is one measure."),
        Parameter("category_label", "string", "Category-axis label; default: the category "
                  "column's description or name."),
        THEME_PARAM,
    )
    columns = (
        ColumnRequirement("category", "category_column", ("str", "int"), "category key",
                          unit="not_applicable", drawn=False),
        ColumnRequirement("value", "value_columns", ("float", "int"),
                          "bar length; null = 'no data', never zero"),
        ColumnRequirement("previous", "previous_column", ("float", "int"),
                          "previous-period value of the measure, same unit"),
    )
    notes = (
        "One bar layer per measure, dodged side by side by the renderer; previous_column adds "
        "a role 'reference' layer named by previous_label, first (left of / below the "
        "current bar).",
        "Nulls stay null (the renderer writes 'no data'); negative values are allowed.",
        "Units are never converted; every drawn column must declare the same unit.",
    )

    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        assert data is not None
        check_theme(p, issues)
        values, prev = p["value_columns"], p["previous_column"]
        used = [p["category_column"]] + list(values) + ([prev] if prev else [])
        if len(set(used)) != len(used):
            issues.error(param_path("value_columns"), "conflicting_parameters",
                         f"a column is used twice among {used}; each column plays one role.")
        if prev is not None and len(values) != 1:
            issues.error(param_path("previous_column"), "conflicting_parameters",
                         "a previous-period comparison needs exactly one measure; got "
                         f"{values}. Draw one figure per measure.")
        if issues.errors:
            return
        check_key_column(data, p["category_column"], "category", issues)
        drawn = list(values) + ([prev] if prev else [])
        units = {c: data.columns[c].unit for c in drawn}
        declared = sorted({u for u in units.values() if u is not None})
        if len(declared) > 1 or (declared and None in units.values()):
            issues.error(column_path(drawn[0]), "mixed_units",
                         f"the bars share one value axis but their columns' units are {units}; "
                         "declare one unit for all (nothing is converted here).")
        elif not declared:
            issues.warn(column_path(drawn[0]), "missing_unit",
                        f"no unit is known for {drawn}; the value axis will have none.")

    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        assert data is not None
        cat, values, prev = p["category_column"], p["value_columns"], p["previous_column"]
        cats = data.columns[cat].values
        order = list(range(len(cats)))
        if p["sort"] != "none":
            first = data.columns[values[0]].values
            present = [i for i in order if first[i] is not None]
            present.sort(key=lambda i: first[i], reverse=p["sort"] == "descending")
            order = present + [i for i in order if first[i] is None]
        horizontal = p["orientation"] == "horizontal" or (
            p["orientation"] == "auto" and (len(cats) > AUTO_HORIZONTAL_COUNT or max(
                len(str(c)) for c in cats) > AUTO_HORIZONTAL_LABEL))
        reading = [cats[i] for i in order]
        drawn_rows = list(reversed(order)) if horizontal else order  # first category on top
        names = [cat] + list(values) + ([prev] if prev else [])
        snap = copy_rows(data, names, drawn_rows)

        cat_ch, val_ch = ("y", "x") if horizontal else ("x", "y")
        slots = ([prev] if prev else []) + list(values)  # layer order = slot order
        spec = FigureSpec(title=p["title"], caption=p["caption"], theme=p["theme"],
                          datasets={"values": snap})
        layers = []
        for col in slots:
            style: dict[str, Any] = {"value_labels": p["value_labels"]}
            is_prev = col == prev
            label = _name(data, values[0]) + ", " + p["previous_label"] if is_prev \
                else _name(data, col)
            layers.append(BarMark(
                data="values", encoding={cat_ch: cat, val_ch: col},
                orientation="horizontal" if horizontal else "vertical",
                role="reference" if is_prev else "observed", name=label, style=style))
        spec.layers = layers
        vcol = data.columns[values[0]]
        value_label = p["value_label"] or (_name(data, values[0]) if len(values) == 1 else None)
        spec.axes = {
            cat_ch: Axis(label=p["category_label"] or data.columns[cat].description or cat,
                         scale="category"),
            val_ch: Axis(label=value_label, unit=vcol.unit),
        }
        if any(v is not None and v < 0 for c in slots for v in data.columns[c].values):
            spec.annotations.append(ReferenceLine(axis=val_ch, value=0.0, style={
                "linestyle": "solid", "linewidth": 0.8}))
        spec.metadata["template"] = {
            "input_hash": data.content_hash(),
            "measures": list(values),
            "previous": {"column": prev, "label": p["previous_label"], "role": "reference"}
            if prev else None,
            "orientation": "horizontal" if horizontal else "vertical",
            "category_order": reading,
        }
        return spec

    def example(self) -> dict[str, Any]:
        snap = DataSnapshot.from_columns(
            {"channel": ["Google Ads", "Meta", "Organic search", "Referral", "Direct"],
             "revenue": [18450.5, 9120.0, 12890.75, None, 3310.0],
             "revenue_prev": [16200.0, 10480.25, 11020.0, 2150.0, 3015.5]},
            units={"revenue": "EUR", "revenue_prev": "EUR"})
        snap.columns["revenue"].description = "Revenue"
        snap.columns["revenue"].aggregation = "sum over 2026-10-01..2026-10-14"
        snap.columns["revenue_prev"].aggregation = "sum over 2026-09-17..2026-09-30"
        snap.columns["channel"].description = "Acquisition channel"
        snap.provenance.source = "example"
        snap.provenance.notes = ("Illustrative numbers, not a measurement. Referral revenue for "
                                 "the current period was not collected.")
        return {
            "data": dataset_to_dict(snap),
            "params": {
                "category_column": "channel",
                "value_columns": ["revenue"],
                "previous_column": "revenue_prev",
                "previous_label": "previous 14 days",
                "sort": "descending",
                "title": "Revenue by channel, last 14 days",
                "caption": "Illustrative data. Referral: no data collected this period.",
            },
        }


def _name(data: DataSnapshot, col: str) -> str:
    return data.columns[col].description or col
