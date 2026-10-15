"""Built-in ``timeseries`` template: one or more series over time, with an
optional observed/forecast split, an optional target line and optional
caller-supplied uncertainty bounds.

Input formats
-------------
* **Wide**: ``value_columns=["revenue", "costs"]`` - one line per column.
* **Long**: ``value_columns=["value"]`` plus ``series_column="channel"`` - one
  line per distinct value of the series column (spec ``series`` channel).

Forecast split
--------------
Exactly one of (or neither):

* ``role_column``: a column whose values are ``"observed"`` / ``"forecast"``
  (dtype ``str``), or ``true`` = forecast (dtype ``bool``);
* ``forecast_from``: a time value; rows with ``time >= forecast_from`` are
  forecast (the split point itself is the first forecast instant).

Observed and forecast rows go to two datasets (``observed``, ``forecast``) and
two sets of line layers; forecast layers carry ``role="forecast"`` and a
dashed style hint. By default **every row is in exactly one layer**. With
``connect_forecast=True`` the last observed row of each series (at or before
that series' first forecast time) is *also* copied into the ``forecast``
dataset as a shared boundary point, so a renderer draws the dashed line
starting from the last observation instead of leaving a visual gap. That row
is an observation shown in a forecast layer; ``metadata["template"]
["forecast"]["shared_boundary"]`` records that it happened.

Rows are ordered by time (stable sort) so lines are drawn left to right; rows
are never dropped or invented, and a null value stays null (a gap, not a
zero). A null *time* (or series/role key) is an error: the row cannot be
placed.

Units
-----
The y-axis unit comes from the value columns' ``unit``. All value (and bound)
columns must share one unit; a column without a unit next to one with a unit
is an error, not an assumption. ``y_unit`` may supply the unit only when the
columns declare none (it is then written onto the *copied* value/bound columns
in the produced datasets and onto the y axis; the caller's snapshot is not
touched, and ``metadata["template"]["input_hash"]`` still identifies the input
as given). It may never contradict a declared unit (nothing is converted,
ever).

Uncertainty
-----------
Bounds appear only when the caller names ``lower_column`` and
``upper_column``; nothing is computed or estimated here. Spec schema 1.0 has
no band mark (no ``y2`` channel), so bounds are emitted as two dotted line
layers with the role of the rows they belong to (bounds of forecast rows are
``role="forecast"`` layers, which renderers must draw as forecasts), and the
pair is recorded in ``metadata["template"]["bands"]`` (layer indexes) so a
renderer can fill between them once the spec grows a band mark.

Each bound layer carries the style hint ``color_of: "<value layer name>"``
(``revenue``, or ``revenue (forecast)`` in the forecast segment), which the
matplotlib renderer honours by drawing the bounds of each series in that
series' colour. Both bound layers of a segment share one name,
``"<value> range (<lower> to <upper>)"``, so a renderer that merges
identical legend entries (the matplotlib one does) shows one "range" entry
per series and segment instead of two.
"""

from __future__ import annotations

import copy
import datetime as _dt
import re
from typing import Any

from ..spec import Axis, Column, DataSnapshot, FigureSpec, LineMark, ReferenceLine
from ..spec.data import dataset_to_dict
from .base import (
    ColumnRequirement,
    ComponentTemplate,
    Issues,
    Parameter,
    column_path,
    param_path,
)

ROLE_VALUES = ("observed", "forecast")
_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_time(value: Any, dtype: str | None) -> Any:
    """Sort key for a time value: ``datetime`` for ISO strings, else float.
    Raises ``ValueError`` for an unparseable value."""
    if dtype == "datetime":
        if not isinstance(value, str):
            raise ValueError(f"{value!r} is not an ISO-8601 string")
        s = value.strip()
        if s[-1:] in ("Z", "z"):
            s = s[:-1] + "+00:00"
        s = _FRACTION.sub(r"\1", s)  # fromisoformat (3.9) takes at most 6 digits
        return _dt.datetime.fromisoformat(s)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{value!r} is not a number")
    return float(value)


class TimeseriesTemplate(ComponentTemplate):
    name = "timeseries"
    version = "1.0.0"
    title = "Time series"
    description = (
        "Line chart of one or more numeric series over time (wide or long format), with "
        "optional observed/forecast split drawn as separate forecast-role layers, an optional "
        "target line, and uncertainty bounds only when the caller supplies them."
    )
    parameters = (
        Parameter("time_column", "column", "Column holding the time of each row (datetime as "
                  "ISO-8601 strings, or numeric time with a unit).", default="time"),
        Parameter("value_columns", "column_list", "Numeric column(s) to draw, one line each. "
                  "With series_column, exactly one.", required=True),
        Parameter("series_column", "column", "Long format: column whose distinct values split "
                  "the single value column into separate lines."),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption (e.g. source and caveats)."),
        Parameter("x_label", "string", "X-axis label; default: the time column's description, "
                  "else its name."),
        Parameter("y_label", "string", "Y-axis label; default: the value column's description "
                  "or name when there is one value column, else none (the legend names them)."),
        Parameter("y_unit", "string", "Unit for the y axis, allowed only when the value columns "
                  "declare none. Never converts; contradicting a declared unit is an error."),
        Parameter("role_column", "column", "Column marking each row 'observed' or 'forecast' "
                  "(str), or true = forecast (bool). Excludes forecast_from."),
        Parameter("forecast_from", "scalar", "First forecast time: rows with time >= this are "
                  "forecast. ISO-8601 string for datetime time, number for numeric time. "
                  "Excludes role_column."),
        Parameter("connect_forecast", "boolean", "Also copy the last observed point of each "
                  "series into the forecast layer so the lines join (shared boundary point).",
                  default=False),
        Parameter("lower_column", "column", "Caller-supplied lower uncertainty bound (needs "
                  "upper_column and a single value column). Never computed here."),
        Parameter("upper_column", "column", "Caller-supplied upper uncertainty bound (needs "
                  "lower_column)."),
        Parameter("target", "number", "Draw a horizontal reference line at this y value."),
        Parameter("target_label", "string", "Label of the target line.", default="Target"),
    )
    columns = (
        ColumnRequirement("time", "time_column", ("datetime", "int", "float"),
                          "x position; numeric time should declare its unit (e.g. 's')."),
        ColumnRequirement("value", "value_columns", ("float", "int"),
                          "y values; null = gap, never zero. Unit becomes the y-axis unit."),
        ColumnRequirement("series", "series_column", ("str", "int"),
                          "long-format series key", unit="not_applicable", drawn=False),
        ColumnRequirement("role", "role_column", ("str", "bool"),
                          "'observed'/'forecast' per row, or bool (true = forecast)",
                          unit="not_applicable", drawn=False),
        ColumnRequirement("lower bound", "lower_column", ("float", "int"),
                          "caller-supplied lower bound, same unit as the value column"),
        ColumnRequirement("upper bound", "upper_column", ("float", "int"),
                          "caller-supplied upper bound, same unit as the value column"),
    )
    notes = (
        "Produces line layers: role 'observed' from dataset 'observed' and, when a forecast "
        "split is given, role 'forecast' (dashed) from dataset 'forecast'.",
        "Each row is in exactly one layer unless connect_forecast=true, which shares the last "
        "observed point per series with the forecast layer.",
        "No uncertainty is ever computed; bound lines appear only for lower_column/upper_column.",
        "Units are never converted; the y unit comes from the columns or, if they have none, "
        "y_unit.",
    )

    # ----------------------------------------------------------------- checks
    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        assert data is not None
        values = p["value_columns"]
        used = [("time_column", p["time_column"])] + [("value_columns", v) for v in values]
        used += [(k, p[k]) for k in ("series_column", "role_column", "lower_column",
                                     "upper_column") if p[k] is not None]
        seen: dict[str, str] = {}
        for param, col in used:
            if col in seen:
                issues.error(param_path(param), "conflicting_parameters",
                             f"column {col!r} is used both as {seen[col]} and as {param}; "
                             "each column can play one role.")
            seen.setdefault(col, param)
        if p["series_column"] is not None and len(values) != 1:
            issues.error(param_path("series_column"), "conflicting_parameters",
                         "long format (series_column) takes exactly one value column; got "
                         f"{values}. Use wide format (several value_columns, no series_column) "
                         "or reshape the data.")
        if p["role_column"] is not None and p["forecast_from"] is not None:
            issues.error(param_path("forecast_from"), "conflicting_parameters",
                         "give either role_column or forecast_from, not both.")
        if (p["lower_column"] is None) != (p["upper_column"] is None):
            missing = "upper_column" if p["upper_column"] is None else "lower_column"
            issues.error(param_path(missing), "missing_parameter",
                         "uncertainty bounds need both lower_column and upper_column.")
        if p["lower_column"] is not None and len(values) != 1:
            issues.error(param_path("lower_column"), "conflicting_parameters",
                         "bounds apply to a single value column; draw each bounded series "
                         "in its own figure or use long format.")
        if issues.errors:
            return
        self._check_keys(data, p, issues)
        self._check_units(data, p, issues)

    def _check_keys(self, data: DataSnapshot, p: dict[str, Any], issues: Issues) -> None:
        tcol = data.columns[p["time_column"]]
        for key in ("time_column", "series_column", "role_column"):
            name = p[key]
            if name is None:
                continue
            nulls = sum(v is None for v in data.columns[name].values)
            if nulls:
                issues.error(column_path(name), "null_in_key_column",
                             f"column {name!r} ({key}) has {nulls} null value(s); those rows "
                             "cannot be placed. Fill or drop them upstream.")
        if issues.errors:
            return
        try:
            keys = [parse_time(v, tcol.dtype) for v in tcol.values]
        except ValueError as e:
            issues.error(column_path(p["time_column"]), "invalid_column_values",
                         f"time value {e}; datetime columns need ISO-8601 strings.")
            return
        if tcol.dtype == "datetime" and len({k.tzinfo is None for k in keys}) > 1:
            issues.error(column_path(p["time_column"]), "invalid_column_values",
                         "the time column mixes timestamps with and without a UTC offset; "
                         "they cannot be ordered. Give every value an offset (or none).")
            return
        split = p["forecast_from"]
        if split is not None:
            try:
                sk = parse_time(split, tcol.dtype)
                if keys:
                    _ = sk < keys[0]  # comparable (naive vs aware raises)
            except (ValueError, TypeError):
                want = ("an ISO-8601 string with the same offset convention as the time column"
                        if tcol.dtype == "datetime" else "a number")
                issues.error(param_path("forecast_from"), "invalid_parameter",
                             f"forecast_from must be {want}, got {split!r}.")
        role = p["role_column"]
        if role is not None:
            rcol = data.columns[role]
            if rcol.dtype == "str":
                bad = sorted({v for v in rcol.values if v not in ROLE_VALUES})
                if bad:
                    issues.error(column_path(role), "invalid_column_values",
                                 f"role column {role!r} may hold only {list(ROLE_VALUES)}; "
                                 f"found {bad}.")

    def _check_units(self, data: DataSnapshot, p: dict[str, Any], issues: Issues) -> None:
        drawn = list(p["value_columns"]) + [c for c in (p["lower_column"], p["upper_column"]) if c]
        units = {c: data.columns[c].unit for c in drawn}
        declared = sorted({u for u in units.values() if u is not None})
        if len(declared) > 1:
            issues.error(column_path(drawn[0]), "mixed_units",
                         f"columns drawn on the y axis have different units {units}; convert "
                         "them to one unit upstream or plot them in separate figures. Units are "
                         "never converted here.")
            return
        if declared and None in units.values():
            missing = [c for c, u in units.items() if u is None]
            issues.error(column_path(missing[0]), "mixed_units",
                         f"column(s) {missing} have no unit while {declared[0]!r} is declared on "
                         "the others; declare their unit (it is not assumed).")
            return
        y_unit = p["y_unit"]
        if declared and y_unit is not None and y_unit != declared[0]:
            issues.error(param_path("y_unit"), "unit_override_conflict",
                         f"y_unit {y_unit!r} contradicts the columns' declared unit "
                         f"{declared[0]!r}; nothing is converted. Remove y_unit, or convert the "
                         "data upstream and update the column unit.")
        if not declared and y_unit is None:
            issues.warn(param_path("y_unit"), "missing_unit",
                        f"no unit is known for {drawn[0]!r}; the y axis will have none. Set the "
                        "column's unit or pass y_unit.")
        tcol = data.columns[p["time_column"]]
        if tcol.dtype in ("int", "float") and tcol.unit is None:
            issues.warn(column_path(p["time_column"]), "missing_unit",
                        f"numeric time column {p['time_column']!r} has no unit; the x axis will "
                        "have none. Set the column's unit (e.g. 's', 'day').")

    # ------------------------------------------------------------------ build
    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        assert data is not None
        tname = p["time_column"]
        tcol = data.columns[tname]
        keys = [parse_time(v, tcol.dtype) for v in tcol.values]
        n = len(keys)
        order = sorted(range(n), key=lambda i: keys[i])

        method = None
        if p["role_column"] is not None:
            method = "role_column"
            rv = data.columns[p["role_column"]].values
            is_fc = [v is True or v == "forecast" for v in rv]
        elif p["forecast_from"] is not None:
            method = "forecast_from"
            split = parse_time(p["forecast_from"], tcol.dtype)
            is_fc = [k >= split for k in keys]
        else:
            is_fc = [False] * n

        observed = [i for i in order if not is_fc[i]]
        forecast = [i for i in order if is_fc[i]]
        series = p["series_column"]
        boundary: list[int] = []
        if p["connect_forecast"] and observed and forecast:
            boundary = self._boundary_rows(data, series, keys, observed, forecast)
            forecast = sorted(boundary + forecast, key=lambda i: keys[i])
        if method is not None and not forecast:
            issues.warn(param_path(method), "no_forecast_rows",
                        "the forecast split matched no rows; the figure shows observations "
                        "only.")

        values = p["value_columns"]
        lower, upper = p["lower_column"], p["upper_column"]
        cols = [tname] + list(values) + [c for c in (series, lower, upper) if c is not None]
        # y_unit only ever fills in a unit the columns lack (checked in _check); it is
        # written onto the copied columns so the produced dataset is self-describing.
        y_unit_cols = set(values) | {c for c in (lower, upper) if c is not None}
        spec = FigureSpec(title=p["title"], caption=p["caption"])
        bands: list[dict[str, Any]] = []
        for part, rows in (("observed", observed), ("forecast", forecast)):
            if not rows:
                continue
            snap = _subset(data, cols, rows, unit_for=y_unit_cols, unit=p["y_unit"])
            spec.datasets[part] = snap
            for v in values:
                if not snap.columns[v].non_null():
                    issues.warn(column_path(v), "empty_series",
                                f"column {v!r} has no {part} values; no {part} line is drawn "
                                "for it.")
                    continue
                value_name = v if part == "observed" else f"{v} ({part})"
                spec.layers.append(_line(part, tname, v, series, value_name))
                idx = {}
                for which, b in (("lower", lower), ("upper", upper)):
                    if b is None:
                        continue
                    if not snap.columns[b].non_null():
                        continue  # caller gave no bound for these rows; nothing is invented
                    # Both bounds share one name (one legend entry) and the colour
                    # of the value series they bound (color_of, see module doc).
                    m = _line(part, tname, b, series, f"{v} range ({lower} to {upper})")
                    m.style = {"linestyle": "dotted", "linewidth": 1, "color_of": value_name}
                    idx[which] = len(spec.layers)
                    spec.layers.append(m)
                if len(idx) == 2:
                    bands.append({"value_column": v, "lower_column": lower,
                                  "upper_column": upper, "role": part,
                                  "lower_layer": idx["lower"], "upper_layer": idx["upper"]})
            if not any(m.data == part for m in spec.layers):
                del spec.datasets[part]  # nothing drawn from it; do not ship unused data

        vcol = data.columns[values[0]]
        y_label = p["y_label"]
        if y_label is None and len(values) == 1:
            y_label = vcol.description or values[0]
        spec.axes = {
            "x": Axis(label=p["x_label"] or tcol.description or tname,
                      unit=tcol.unit if tcol.dtype != "datetime" else None,
                      scale="time" if tcol.dtype == "datetime" else None),
            "y": Axis(label=y_label, unit=vcol.unit or p["y_unit"]),
        }
        if p["target"] is not None:
            spec.annotations.append(ReferenceLine(axis="y", value=p["target"],
                                                  label=p["target_label"]))
        meta: dict[str, Any] = {"input_hash": data.content_hash()}
        if method is not None:
            meta["forecast"] = {
                "method": method,
                "split": copy.deepcopy(p[method]),
                "observed_rows": len(observed),
                "forecast_rows": len(forecast) - len(boundary),
                "shared_boundary": bool(boundary),
            }
        if bands:
            meta["bands"] = bands
        spec.metadata["template"] = meta
        return spec

    @staticmethod
    def _boundary_rows(data: DataSnapshot, series: str | None, keys: list[Any],
                       observed: list[int], forecast: list[int]) -> list[int]:
        """Per series: the latest observed row at or before that series' first
        forecast time."""
        sv = data.columns[series].values if series else [None] * len(keys)
        first_fc: dict[Any, Any] = {}
        for i in forecast:
            first_fc.setdefault(sv[i], keys[i])  # forecast is time-ordered
        out = []
        for s, start in first_fc.items():
            cands = [i for i in observed if sv[i] == s and keys[i] <= start]
            if cands:
                out.append(cands[-1])  # observed is time-ordered (stable)
        return out

    def example(self) -> dict[str, Any]:
        snap = DataSnapshot.from_columns(
            {
                "week": ["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                         "2026-10-05", "2026-10-12"],
                "revenue": [1180.0, 1200.0, None, 1350.5, 1390.0, 1410.0, 1450.0],
            },
            units={"revenue": "EUR"},
            dtypes={"week": "datetime"},
        )
        snap.columns["revenue"].aggregation = "sum per ISO week"
        snap.columns["week"].description = "Week (ISO, starting Monday)"
        snap.provenance.source = "example"
        snap.provenance.notes = ("Illustrative numbers, not a measurement. Rows from "
                                 "2026-10-05 are a model forecast.")
        snap.provenance.timezone = "Europe/Helsinki"
        return {
            "data": dataset_to_dict(snap),
            "params": {
                "time_column": "week",
                "value_columns": ["revenue"],
                "title": "Weekly revenue",
                "caption": "Forecast is a model output, not a measurement.",
                "forecast_from": "2026-10-05",
                "connect_forecast": True,
                "target": 1300,
            },
        }


def _subset(data: DataSnapshot, names: list[str], rows: list[int],
            unit_for: set[str] = frozenset(), unit: str | None = None) -> DataSnapshot:
    """Copy ``rows`` of ``names``; columns in ``unit_for`` that declare no unit
    get ``unit`` (an override the checks have already allowed)."""
    cols = {}
    for name in names:
        c = data.columns[name]
        u = c.unit if (c.unit is not None or name not in unit_for) else unit
        cols[name] = Column(values=[c.values[i] for i in rows], dtype=c.dtype, unit=u,
                            description=c.description, denominator=c.denominator,
                            aggregation=c.aggregation)
    return DataSnapshot(columns=cols, provenance=copy.deepcopy(data.provenance))


def _line(part: str, x: str, y: str, series: str | None, name: str) -> LineMark:
    enc = {"x": x, "y": y}
    if series is not None:
        enc["series"] = series
    style = {"linestyle": "dashed"} if part == "forecast" else {}
    return LineMark(data=part, encoding=enc, role=part, name=name, style=style)
