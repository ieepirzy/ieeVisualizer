"""Built-in ``estimate_comparison`` template: point estimates with their
intervals across groups (e.g. A/B test arms, model variants, regions).

One ``errorbar`` layer: ``estimate_column`` per ``group_column`` (a category
axis, groups in row order) with caller-supplied ``lower_column`` /
``upper_column``. The interval must say what it is: ``interval_kind`` and
its ``interval_level`` (or, for standard-error/-deviation bars,
``interval_multiplier``), checked like the spec checks them and shown in
the legend ("95 % confidence interval"). Nothing is computed: no interval,
no difference, no significance. If the claim is about a difference, pass
the difference and its interval as the estimate (with ``reference=0``),
not two overlapping intervals.

A null estimate draws the interval alone; a null bound leaves that group's
interval out (a gap, reported by the renderer). A lower bound above its
upper bound, a repeated or null group, or bounds in another unit than the
estimate is an error.
"""

from __future__ import annotations

import copy
from typing import Any

from ..spec import (
    INTERVAL_KINDS,
    Axis,
    Column,
    DataSnapshot,
    ErrorbarMark,
    FigureSpec,
    ReferenceLine,
)
from ..spec.data import dataset_to_dict
from ..spec.intervals import metadata_problems
from .base import ColumnRequirement, ComponentTemplate, Issues, Parameter, column_path, param_path


class EstimateComparisonTemplate(ComponentTemplate):
    name = "estimate_comparison"
    version = "1.0.0"
    title = "Estimates with intervals"
    description = (
        "Point estimates with caller-supplied intervals across groups (e.g. A/B test arms), "
        "drawn as error bars on a category axis; the interval's kind and level are required "
        "and shown in the legend. Nothing is computed."
    )
    parameters = (
        Parameter("group_column", "column", "Column naming each group (one row per group).",
                  default="group"),
        Parameter("estimate_column", "column", "Point estimate per group.", required=True),
        Parameter("lower_column", "column", "Lower bound of each estimate's interval.",
                  required=True),
        Parameter("upper_column", "column", "Upper bound of each estimate's interval.",
                  required=True),
        Parameter("interval_kind", "string", "What the interval is.", required=True,
                  allowed=INTERVAL_KINDS),
        Parameter("interval_level", "number", "Coverage as a fraction (0.95) for confidence, "
                  "credible, prediction and quantile intervals; omit for the others."),
        Parameter("interval_multiplier", "number", "How many standard errors/deviations each "
                  "side spans, for standard-error/-deviation bars (e.g. 1)."),
        Parameter("interval_quantiles", "range", "For a non-central quantile interval: "
                  "[lower, upper] quantiles, e.g. [0.05, 0.75] (instead of a level)."),
        Parameter("reference", "number", "Draw a horizontal reference line at this value "
                  "(e.g. 0 when the estimates are differences)."),
        Parameter("reference_label", "string", "Label of the reference line.",
                  default="Reference"),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption: say n, the method and what is "
                  "estimated (e.g. 'Wilson 95 % CI, n = 1 200 visitors per arm')."),
        Parameter("x_label", "string", "Group axis label; default: the column's description, "
                  "else its name."),
        Parameter("y_label", "string", "Value axis label; default: the estimate column's "
                  "description, else its name."),
    )
    columns = (
        ColumnRequirement("group", "group_column", ("str", "int", "bool"), "group name",
                          unit="not_applicable"),
        ColumnRequirement("estimate", "estimate_column", ("float", "int"),
                          "point estimate; its unit is the value axis' unit"),
        ColumnRequirement("lower bound", "lower_column", ("float", "int"),
                          "caller-supplied lower bound, same unit as the estimate"),
        ColumnRequirement("upper bound", "upper_column", ("float", "int"),
                          "caller-supplied upper bound, same unit as the estimate"),
    )
    notes = (
        "Produces one errorbar layer over dataset 'estimates', groups in row order.",
        "Intervals are never computed; their kind and level are required and shown in the "
        "legend.",
        "Comparing two groups by whether their intervals overlap is unreliable; for a claim "
        "about a difference pass the difference and its interval, with reference=0.",
    )

    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        assert data is not None
        for fld, code, message in metadata_problems(p["interval_kind"], p["interval_level"],
                                                    p["interval_multiplier"],
                                                    p["interval_quantiles"]):
            issues.error(param_path(f"interval_{fld}"), code, message)
        names = [p[k] for k in ("group_column", "estimate_column", "lower_column",
                                "upper_column")]
        if len(set(names)) != len(names):
            issues.error(param_path("lower_column"), "conflicting_parameters",
                         f"the group, estimate and bound columns must be four different "
                         f"columns; got {names}.")
            return
        groups = data.columns[p["group_column"]].values
        if any(g is None for g in groups):
            issues.error(column_path(p["group_column"]), "null_in_key_column",
                         "a group has no name; those rows cannot be placed.")
        elif len(set(map(repr, groups))) != len(groups):
            issues.error(column_path(p["group_column"]), "invalid_column_values",
                         "each group must appear once (one estimate per group); aggregate "
                         "upstream.")
        units = {c: data.columns[c].unit for c in names[1:]}
        if len(set(units.values())) > 1:
            issues.error(column_path(p["lower_column"]), "mixed_units",
                         f"the estimate and its bounds must share one unit; got {units}. "
                         "Nothing is converted.")
        lo = data.columns[p["lower_column"]].values
        hi = data.columns[p["upper_column"]].values
        bad = [i for i, (a, b) in enumerate(zip(lo, hi))
               if a is not None and b is not None and a > b]
        if bad:
            issues.error(column_path(p["lower_column"]), "invalid_column_values",
                         f"row {bad[0]}: lower bound {lo[bad[0]]!r} is above upper bound "
                         f"{hi[bad[0]]!r} ({len(bad)} row(s)); fix the bounds upstream.")
        if units[p["estimate_column"]] is None:
            issues.warn(column_path(p["estimate_column"]), "missing_unit",
                        f"no unit is known for {p['estimate_column']!r}; the value axis will "
                        "have none.")

    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        assert data is not None
        g, est = p["group_column"], p["estimate_column"]
        lo, hi = p["lower_column"], p["upper_column"]
        cols = {}
        for name in (g, est, lo, hi):
            c = data.columns[name]
            cols[name] = Column(values=list(c.values), dtype=c.dtype, unit=c.unit,
                                description=c.description, denominator=c.denominator,
                                aggregation=c.aggregation)
        snap = DataSnapshot(columns=cols, provenance=copy.deepcopy(data.provenance))
        layer = ErrorbarMark(data="estimates",
                             encoding={"x": g, "y": est, "y_lower": lo, "y_upper": hi},
                             interval_kind=p["interval_kind"],
                             interval_level=p["interval_level"],
                             interval_multiplier=p["interval_multiplier"],
                             interval_quantiles=p["interval_quantiles"])
        ecol = data.columns[est]
        spec = FigureSpec(
            title=p["title"], caption=p["caption"], datasets={"estimates": snap},
            layers=[layer],
            axes={"x": Axis(label=p["x_label"] or data.columns[g].description or g,
                            scale="category"),
                  "y": Axis(label=p["y_label"] or ecol.description or est, unit=ecol.unit)},
        )
        if p["reference"] is not None:
            spec.annotations.append(ReferenceLine(axis="y", value=p["reference"],
                                                  label=p["reference_label"]))
        spec.metadata["template"] = {"input_hash": data.content_hash(),
                                     "group_order": [str(v) for v in cols[g].values]}
        return spec

    def example(self) -> dict[str, Any]:
        snap = DataSnapshot.from_columns(
            {"arm": ["variant B", "variant C", "variant D"],
             "uplift": [1.8, -0.4, 0.6],
             "ci_low": [0.4, -1.9, -0.7],
             "ci_high": [3.2, 1.1, 1.9]},
            units={"uplift": "pp", "ci_low": "pp", "ci_high": "pp"},
        )
        snap.columns["uplift"].description = "Uplift vs control"
        snap.columns["arm"].description = "Test arm"
        snap.provenance.source = "example"
        snap.provenance.notes = "Illustrative numbers, not a measurement."
        return {
            "data": dataset_to_dict(snap),
            "params": {"group_column": "arm", "estimate_column": "uplift",
                       "lower_column": "ci_low", "upper_column": "ci_high",
                       "interval_kind": "confidence", "interval_level": 0.95,
                       "reference": 0, "reference_label": "No difference",
                       "title": "Checkout test: uplift of each variant over control",
                       "caption": "Illustrative. Difference in conversion rate vs control, "
                                  "percentage points; 95 % CI by normal approximation."},
        }
