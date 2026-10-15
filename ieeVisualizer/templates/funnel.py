"""Built-in ``funnel`` template: ordered stages with counts, drawn as
horizontal bars (first stage on top) with each step's conversion rate.

Rows are the stages in funnel order. The template derives, transparently
and nothing more:

* step conversion ``count[i] / count[i-1] x 100`` for every stage after the
  first, and
* overall conversion ``count[last] / count[first] x 100``.

Each rate is recorded with its numerator and denominator in
``metadata["template"]["derived"]`` and in the dataset column
``step_conversion`` (unit ``%``, with its denominator described). A rate
whose denominator is 0 is ``null`` ("undefined"), never 0 or infinity.

A null count is an error (the stage cannot be drawn or used as a
denominator). Counts that rise from one stage to the next are allowed
with a ``non_monotone_counts`` warning: stages may be counted over
different windows or entry points, so a "rate" above 100 % is reported, not
corrected. Text the template writes (rates, the overall line in the
caption) uses the ``theme`` parameter's number locale; bar values are
labelled by the renderer in the render theme's locale.
"""

from __future__ import annotations

from typing import Any

from ..spec import Axis, BarMark, Column, DataSnapshot, FigureSpec, TextAnnotation
from ..spec.data import dataset_to_dict
from ..themes import format_number, format_percent
from ._business import THEME_PARAM, check_key_column, check_theme, copy_rows, locale_for
from .base import ColumnRequirement, ComponentTemplate, Issues, Parameter, column_path

#: x-axis room for the value labels and the rate column, as multiples of
#: the largest count.
RATE_COLUMN_X = 1.22
AXIS_MAX = 1.75


class FunnelTemplate(ComponentTemplate):
    name = "funnel"
    version = "1.0.0"
    title = "Conversion funnel"
    description = (
        "Horizontal bars for ordered stages with counts, annotated with each step's conversion "
        "rate and the overall rate; every rate's numerator and denominator is recorded."
    )
    parameters = (
        Parameter("stage_column", "column", "Stage names, one row per stage, in funnel order.",
                  default="stage"),
        Parameter("count_column", "column", "Count reaching each stage (non-negative, no nulls).",
                  default="count"),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption (source, window, caveats); the overall "
                  "conversion line is appended."),
        Parameter("count_label", "string", "Count-axis label; default: the count column's "
                  "description or name."),
        THEME_PARAM,
    )
    columns = (
        ColumnRequirement("stage", "stage_column", ("str",), "stage name",
                          unit="not_applicable", drawn=False),
        ColumnRequirement("count", "count_column", ("int", "float"),
                          "how many reached the stage; the unit names what is counted"),
    )
    notes = (
        "Derived values: step conversion count[i]/count[i-1]*100 and overall "
        "count[last]/count[first]*100, recorded with numerators and denominators.",
        "A zero denominator gives a null rate ('undefined'), never 0 or infinity.",
        "A null count is an error; rising counts are a warning, not corrected.",
    )

    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        assert data is not None
        check_theme(p, issues)
        stage, count = p["stage_column"], p["count_column"]
        if stage == count:
            issues.error(column_path(stage), "conflicting_parameters",
                         "stage_column and count_column must be different columns.")
            return
        check_key_column(data, stage, "stage", issues)
        counts = data.columns[count].values
        if len(counts) < 2:
            issues.error("/data", "invalid_data",
                         "a funnel needs at least two stages; one stage has no conversion.")
        nulls = [data.columns[stage].values[i] for i, v in enumerate(counts) if v is None]
        if nulls:
            issues.error(column_path(count), "invalid_column_values",
                         f"stage(s) {nulls} have no count; a funnel stage cannot be drawn or "
                         "used as a denominator without one. Supply the count or drop the "
                         "stage upstream (and say so in the caption).")
        negative = [v for v in counts if v is not None and v < 0]
        if negative:
            issues.error(column_path(count), "invalid_column_values",
                         f"counts cannot be negative; found {negative}.")
        if issues.errors:
            return
        rises = [(data.columns[stage].values[i - 1], data.columns[stage].values[i])
                 for i in range(1, len(counts)) if counts[i] > counts[i - 1]]
        if rises:
            issues.warn(column_path(count), "non_monotone_counts",
                        f"counts rise between {rises}; the stages may be counted over different "
                        "windows or entry points. Step rates above 100 % are shown as computed.")
        if data.columns[count].unit is None:
            issues.warn(column_path(count), "missing_unit",
                        f"count column {count!r} has no unit; say what is counted (e.g. "
                        "'sessions').")

    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        assert data is not None
        loc = locale_for(p["theme"])
        stage, count = p["stage_column"], p["count_column"]
        names = data.columns[stage].values
        counts = data.columns[count].values
        steps, rates = [], [None]
        for i in range(1, len(counts)):
            rate = _rate(counts[i], counts[i - 1])
            rates.append(rate)
            steps.append({"from": names[i - 1], "to": names[i], "numerator": counts[i],
                          "denominator": counts[i - 1], "rate_percent": rate})
        overall = {"from": names[0], "to": names[-1], "numerator": counts[-1],
                   "denominator": counts[0], "rate_percent": _rate(counts[-1], counts[0])}

        rows = list(reversed(range(len(names))))  # first stage drawn on top
        snap = copy_rows(data, [stage, count], rows)
        snap.columns["step_conversion"] = Column(
            values=[rates[i] for i in rows], dtype="float", unit="%",
            description="Step conversion: this stage's count / previous stage's count x 100",
            denominator=f"{count} of the previous stage (row above in funnel order)")
        top = max(counts)
        spec = FigureSpec(title=p["title"], theme=p["theme"], datasets={"stages": snap})
        spec.layers.append(BarMark(data="stages", encoding={"x": count, "y": stage},
                                   orientation="horizontal", name=count,
                                   style={"value_labels": True}))
        ccol = data.columns[count]
        spec.axes = {
            "x": Axis(label=p["count_label"] or ccol.description or count, unit=ccol.unit,
                      limits=[0.0, float(top) * AXIS_MAX if top > 0 else 1.0]),
            "y": Axis(scale="category"),
        }
        for i in range(1, len(names)):
            text = (f"{format_percent(rates[i], loc)} of previous" if rates[i] is not None
                    else "rate undefined (previous stage is 0)")
            spec.annotations.append(TextAnnotation(text=text, x=float(top) * RATE_COLUMN_X
                                                   if top > 0 else 0.5, y=names[i]))
        if overall["rate_percent"] is None:
            line = f"Overall conversion undefined: {names[0]} has a count of 0."
        else:
            line = (f"Overall conversion {names[0]} → {names[-1]}: "
                    f"{format_percent(overall['rate_percent'], loc)} "
                    f"({format_number(counts[-1], loc)} of {format_number(counts[0], loc)}).")
        spec.caption = f"{p['caption']}\n{line}" if p["caption"] else line
        spec.metadata["template"] = {
            "input_hash": data.content_hash(),
            "stage_order": list(names),
            "derived": {
                "formula": {"step": "count[i] / count[i-1] * 100",
                            "overall": "count[last] / count[first] * 100"},
                "steps": steps, "overall": overall,
                "zero_denominator": "rate is null (undefined), never 0 or infinity",
            },
            "label_locale": loc.tag,
        }
        return spec

    def example(self) -> dict[str, Any]:
        snap = DataSnapshot.from_columns(
            {"stage": ["Visited site", "Started quote", "Submitted quote", "Booked move"],
             "sessions": [12480, 2315, 1162, 287]},
            units={"sessions": "sessions"})
        snap.columns["sessions"].description = "Reached the stage"
        snap.columns["sessions"].aggregation = "distinct sessions, 2026-10-01..2026-10-14"
        snap.provenance.source = "example"
        snap.provenance.timezone = "Europe/Helsinki"
        snap.provenance.notes = "Illustrative numbers, not a measurement."
        return {
            "data": dataset_to_dict(snap),
            "params": {
                "stage_column": "stage",
                "count_column": "sessions",
                "title": "Booking funnel, last 14 days",
                "caption": "Illustrative data. Each stage counts sessions that reached it.",
            },
        }


def _rate(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator * 100.0
