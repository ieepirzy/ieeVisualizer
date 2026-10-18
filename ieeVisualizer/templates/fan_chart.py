"""Built-in ``fan_chart`` template: nested quantile bands over time.

Two inputs:

* **Ensemble** (``member_column`` + ``value_column``): many paths in long
  format (e.g. :func:`ieeVisualizer.stochastic.gbm`). At each time the
  template takes the empirical quantiles of the paths (type 7, numpy's
  default) for each central ``level`` and their median. A level whose tail
  is thinner than one path (P2.5 from fewer than 40 paths) is refused: the
  band edge would be read off the most extreme path or two.
* **Given quantiles** (``quantile_columns``: ``{column: quantile}``, e.g.
  ``{"p05": 0.05, "p95": 0.95}``): bands drawn as supplied, never
  computed. Quantiles pair from the outside in (lowest with highest); a
  symmetric pair is a central level, any other pair an explicit
  ``[lower, upper]`` quantile interval. ``interval_kind`` says what they
  are (``quantile``; ``prediction`` or ``credible`` for a forecast's or a
  posterior's quantiles).

The figure is built by the ``timeseries`` 2.0.1 template (its band layers,
forecast role and interval labels: "P10–P90 percentiles (80%, forecast)"),
whose metadata is kept under ``metadata["template"]["timeseries"]``; the
bands (columns, quantiles, kind, level), the ensemble's size per time and
the quantile method are recorded beside it. Synthetic input (provenance
``extra.synthetic``) is said to be synthetic in the caption, with its seed.
"""

from __future__ import annotations

import copy
from decimal import ROUND_CEILING, Decimal
from typing import Any

from ..spec import DataSnapshot, FigureSpec
from ..spec._common import ptr
from ..spec.data import Column
from ..spec.intervals import metadata_problems, percentile_text
from ._business import THEME_PARAM, check_theme, locale_for
from .base import ColumnRequirement, ComponentTemplate, Issues, Parameter, column_path, param_path
from .distribution import number_text, store
from .sample_distribution import quantile7
from .timeseries import parse_time
from .timeseries_intervals import TimeseriesTemplate201

MAX_ROWS = 200_000
MAX_TIMES = 2000
MAX_LEVELS = 4
QUANTILE_METHOD = "type 7: linear interpolation between order statistics (numpy's default)"


def _level_text(levels: list[float], loc: Any) -> str:
    parts = [number_text(lv * 100, loc, 6) for lv in levels]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"{joined}{loc.percent_sep}%"


class FanChartTemplate(ComponentTemplate):
    name = "fan_chart"
    version = "1.0.0"
    title = "Fan chart"
    description = (
        "Nested quantile bands over time from a forecast ensemble (empirical quantiles of its "
        "paths) or from given quantile columns, each band labelled with its kind and level, "
        "around the median line."
    )
    parameters = (
        Parameter("time_column", "column", "Time of each row (datetime or numeric with a "
                  "unit).", default="time"),
        Parameter("member_column", "column", "Ensemble input: the path / member id of each "
                  "row (long format). Excludes quantile_columns."),
        Parameter("value_column", "column", "Ensemble input: the simulated value."),
        Parameter("levels", "number_list", "Ensemble input: central levels of the bands "
                  "(fractions).", default=[0.5, 0.8, 0.95]),
        Parameter("quantile_columns", "number_map", "Given quantiles: {column: quantile}, "
                  "e.g. {\"p05\": 0.05, \"p95\": 0.95}. Excludes member_column."),
        Parameter("median_column", "column", "Given quantiles: the central line (default: "
                  "the 0.5 quantile column)."),
        Parameter("interval_kind", "string", "Given quantiles: what they are.",
                  default="quantile", allowed=("quantile", "prediction", "credible")),
        Parameter("role", "string", "'forecast' (dashed line, hatched bands) or 'observed'.",
                  default="forecast", allowed=("forecast", "observed")),
        Parameter("median_label", "string", "Legend name of the central line; default "
                  "'Median of N paths' (ensemble) or 'Median'."),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption; the template appends what the line "
                  "and bands are."),
        Parameter("x_label", "string", "X-axis label."),
        Parameter("y_label", "string", "Y-axis label; default: the value column's "
                  "description or name."),
        THEME_PARAM,
    )
    columns = (
        ColumnRequirement("time", "time_column", ("datetime", "int", "float"), "x position",
                          unit="not_applicable", drawn=False),
        ColumnRequirement("member", "member_column", ("int", "str"), "path id",
                          unit="not_applicable", drawn=False),
        ColumnRequirement("value", "value_column", ("float", "int"), "simulated values"),
        ColumnRequirement("median", "median_column", ("float", "int"), "central line"),
    )
    notes = (
        "Ensemble bands are empirical quantiles of the paths at each time, never smoothed; "
        "given quantiles are drawn as supplied.",
        "Each band is labelled with its kind and level; the caption says how many paths and "
        "whether the input is synthetic.",
    )

    # ----------------------------------------------------------------- checks
    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        assert data is not None
        check_theme(p, issues)
        ens, given = p["member_column"] is not None, p["quantile_columns"] is not None
        if ens == given:
            issues.error(param_path("member_column" if ens else "quantile_columns"),
                         "conflicting_parameters" if ens else "missing_parameter",
                         "give either an ensemble (member_column + value_column) or "
                         "quantile_columns, " + ("not both." if ens else "one of them."))
            return
        if data.num_rows > MAX_ROWS:
            issues.error("/data", "invalid_data",
                         f"{data.num_rows} rows; at most {MAX_ROWS} are summarised in bounded "
                         "time. Thin the ensemble upstream.")
            return
        tname = p["time_column"]
        tvals = data.columns[tname].values
        if any(v is None for v in tvals):
            issues.error(column_path(tname), "null_in_key_column",
                         "some rows have no time; they cannot be placed.")
            return
        try:
            keys = [parse_time(v, data.columns[tname].dtype) for v in tvals]
        except ValueError as e:
            issues.error(column_path(tname), "invalid_column_values", f"time value {e}.")
            return
        if len(set(keys)) > MAX_TIMES:
            issues.error(column_path(tname), "invalid_column_values",
                         f"{len(set(keys))} distinct times; at most {MAX_TIMES}.")
            return
        if ens:
            self._check_ensemble(data, p, keys, issues)
        else:
            self._check_given(data, p, issues)

    def _check_ensemble(self, data: DataSnapshot, p: dict[str, Any], keys: list[Any],
                        issues: Issues) -> None:
        if p["value_column"] is None:
            issues.error(param_path("value_column"), "missing_parameter",
                         "an ensemble needs value_column: the simulated value of each row.")
            return
        if p["median_column"] is not None or p["interval_kind"] != "quantile":
            issues.error(param_path("median_column" if p["median_column"] else "interval_kind"),
                         "conflicting_parameters",
                         "an ensemble's median and bands are computed as empirical quantiles; "
                         "median_column and interval_kind apply to given quantiles only.")
        members = data.columns[p["member_column"]].values
        if any(m is None for m in members):
            issues.error(column_path(p["member_column"]), "null_in_key_column",
                         "some rows have no member id; they cannot be assigned to a path.")
            return
        pairs = set()
        for k, m in zip(keys, members):
            if (k, m) in pairs:
                issues.error(column_path(p["member_column"]), "invalid_column_values",
                             f"member {m!r} has two rows at one time; each path has one value "
                             "per time.")
                return
            pairs.add((k, m))
        values = data.columns[p["value_column"]].values
        nulls = sum(v is None for v in values)
        if nulls:
            issues.warn(column_path(p["value_column"]), "null_samples_dropped",
                        f"{nulls} null value(s) are not path values; they are left out of the "
                        "quantiles at their time (the count per time is recorded).")
        counts: dict[Any, int] = {}
        for k, v in zip(keys, values):
            counts[k] = counts.get(k, 0) + (v is not None)
        fewest = min(counts.values())
        levels = p["levels"]
        if len(levels) > MAX_LEVELS or len(set(levels)) != len(levels):
            issues.error(param_path("levels"), "invalid_parameter",
                         f"give 1 to {MAX_LEVELS} distinct levels, got {levels}.")
            return
        for k, lv in enumerate(levels):
            path = ptr("params", "levels", str(k))
            problems = metadata_problems("quantile", lv, None)
            for _, code, message in problems:
                issues.error(path, code, message)
            if problems:
                continue
            need = int((Decimal(2) / (Decimal(1) - Decimal(repr(lv)))).to_integral_value(
                rounding=ROUND_CEILING))
            if fewest < need:
                tail = percentile_text((1 - lv) / 2)
                issues.error(path, "invalid_parameter",
                             f"a central {lv:g} band needs {tail} of the paths, i.e. at least "
                             f"{need} paths at every time; the fewest at one time is {fewest}. "
                             "Simulate more paths or use a lower level.")
        if issues.errors:
            return
        n_paths = len(set(members))
        taken = {p["time_column"]: "the time column",
                 p["median_label"] or f"Median of {n_paths} paths": "the median line"}
        for k, lv in enumerate(levels):
            tail = float((Decimal(1) - Decimal(repr(lv))) / 2)
            for name in (percentile_text(tail), percentile_text(1 - tail)):
                if name in taken:
                    issues.error(ptr("params", "levels", str(k)), "invalid_parameter",
                                 f"level {lv!r}'s band edge {name} would have the same name as "
                                 f"{taken[name]}; give levels whose percentiles print "
                                 "differently, and do not name another column like a band "
                                 "edge (P2.5, P97.5, ...).")
                    return
                taken[name] = f"an edge of level {lv!r}"

    def _check_given(self, data: DataSnapshot, p: dict[str, Any], issues: Issues) -> None:
        if p["value_column"] is not None:
            issues.error(param_path("value_column"), "conflicting_parameters",
                         "value_column is the ensemble's value; with quantile_columns name the "
                         "central line in median_column (or include the 0.5 quantile).")
            return
        qmap = p["quantile_columns"]
        for col, q in qmap.items():
            path = ptr("params", "quantile_columns", col)
            c = data.columns.get(col)
            if c is None:
                issues.error(path, "missing_column",
                             f"quantile column {col!r} is not in the data. Columns: "
                             f"{', '.join(sorted(data.columns))}.")
            elif c.dtype not in (None, "float", "int"):
                issues.error(column_path(col), "wrong_dtype",
                             f"quantile column {col!r} must be numeric, not {c.dtype!r}.")
            if not 0 < q < 1:
                issues.error(path, "invalid_parameter",
                             f"a quantile is a probability strictly between 0 and 1, got {q!r}.")
        if issues.errors:
            return
        median, bands = self._pairs(p)
        if median is None:
            issues.error(param_path("median_column"), "missing_parameter",
                         "a fan chart draws its central line: give median_column or a 0.5 "
                         "quantile column.")
        if bands is None:
            issues.error(param_path("quantile_columns"), "conflicting_parameters",
                         "quantiles pair from the outside in (lowest with highest): give as "
                         "many below 0.5 as above it.")
            return
        for lo, hi, iv in bands:
            where = ptr("params", "quantile_columns", lo)
            for _, code, message in metadata_problems(iv["kind"], iv["level"], None,
                                                      iv["quantiles"]):
                issues.error(where, code, f"band {lo!r}–{hi!r}: {message}")
        if issues.errors or median is None:
            return
        order = [b[0] for b in bands] + [median] + [b[1] for b in reversed(bands)]
        cols = [data.columns[c].values for c in order]
        for r in range(data.num_rows):
            row = [c[r] for c in cols if c[r] is not None]
            if any(a > b for a, b in zip(row, row[1:])):
                issues.error(column_path(order[0]), "invalid_column_values",
                             f"row {r}: the quantiles {order} are not in increasing order "
                             f"({[c[r] for c in cols]}); quantiles cannot cross. Fix them "
                             "upstream.")
                return
        units = {data.columns[c].unit for c in order}
        if len(units) > 1:
            issues.error(column_path(order[0]), "mixed_units",
                         f"the quantile columns must share one unit; got {sorted(map(str, units))}"
                         ". Nothing is converted.")

    @staticmethod
    def _pairs(p: dict[str, Any]) -> tuple[str | None, list[tuple[str, str, dict]] | None]:
        qmap = p["quantile_columns"]
        median = p["median_column"] or next((c for c, q in qmap.items() if q == 0.5), None)
        below = sorted(((q, c) for c, q in qmap.items() if q < 0.5))
        above = sorted(((q, c) for c, q in qmap.items() if q > 0.5), reverse=True)
        if len(below) != len(above):
            return median, None
        bands = []
        for (ql, cl), (qh, ch) in zip(below, above):
            if abs(ql + qh - 1) < 1e-9:
                level = float(Decimal(repr(qh)) - Decimal(repr(ql)))
                iv = {"kind": p["interval_kind"], "level": level, "quantiles": None}
            else:
                iv = {"kind": p["interval_kind"], "level": None, "quantiles": [ql, qh]}
            bands.append((cl, ch, iv))
        return median, bands

    # ------------------------------------------------------------------ build
    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        assert data is not None
        loc = locale_for(p["theme"])
        tname = p["time_column"]
        tcol = data.columns[tname]
        meta: dict[str, Any] = {"input_hash": data.content_hash(), "label_locale": loc.tag}
        lines = [p["caption"]] if p["caption"] else []
        if p["member_column"] is not None:
            snap, median, bands, y_default = self._summarise(data, p, meta, lines, loc)
        else:
            median, pairs = self._pairs(p)
            cols = [tname, median] + [c for lo, hi, _ in pairs for c in (lo, hi)]
            snap = DataSnapshot(columns={c: _copy(data.columns[c]) for c in cols},
                                provenance=copy.deepcopy(data.provenance))
            bands = [{"lower": lo, "upper": hi, **iv} for lo, hi, iv in pairs]
            y_default = data.columns[median].description or median
            lines.append("Line and bands: quantiles as supplied, not computed here.")
            meta["bands"] = [{"lower_column": lo, "upper_column": hi, **iv,
                              "quantiles": [p["quantile_columns"][lo],
                                            p["quantile_columns"][hi]]}
                             for lo, hi, iv in pairs]
        extra = data.provenance.extra if isinstance(data.provenance.extra, dict) else {}
        if extra.get("synthetic"):
            seed = f" (seed {extra['seed']})" if "seed" in extra else ""
            lines.append(f"Synthetic data: a seeded simulation{seed}, not a measurement.")
        label = p["median_label"] or (median if p["member_column"] else "Median")
        if label != median and label not in snap.columns:
            snap.columns[label] = snap.columns.pop(median)
        inner_params: dict[str, Any] = {
            "time_column": tname, "value_columns": [label],
            "intervals": sorted(bands, key=lambda b: b["quantiles"][0] if b["quantiles"]
                                else (1 - b["level"]) / 2),
            "title": p["title"], "caption": "\n".join(lines),
            "x_label": p["x_label"] or tcol.description or tname,
            "y_label": p["y_label"] or y_default}
        if p["role"] == "forecast":
            inner_params["forecast_from"] = min(snap.columns[tname].values,
                                                key=lambda v: parse_time(v, tcol.dtype))
        spec = TimeseriesTemplate201().build(snap, **inner_params)
        spec.theme = p["theme"]
        meta["timeseries"] = spec.metadata.pop("template")
        spec.metadata["template"] = meta
        return spec

    def _summarise(self, data: DataSnapshot, p: dict[str, Any], meta: dict[str, Any],
                   lines: list[str], loc: Any) -> tuple[DataSnapshot, str, list[dict], str]:
        tname, vname = p["time_column"], p["value_column"]
        tcol, vcol = data.columns[tname], data.columns[vname]
        by_time: dict[Any, list[float]] = {}
        raw: dict[Any, Any] = {}
        for t, v in zip(tcol.values, vcol.values):
            key = parse_time(t, tcol.dtype)
            raw.setdefault(key, t)
            if v is not None:
                by_time.setdefault(key, []).append(float(v))
        times = sorted(raw)
        levels = sorted(p["levels"])
        n_paths = len(set(data.columns[p["member_column"]].values))
        median = p["median_label"] or f"Median of {number_text(n_paths, loc, 12)} paths"
        cols: dict[str, Column] = {tname: Column([raw[t] for t in times], dtype=tcol.dtype,
                                                 unit=tcol.unit, description=tcol.description)}
        series = {t: sorted(by_time.get(t, [])) for t in times}
        cols[median] = Column([store(quantile7(series[t], 0.5)) if series[t] else None
                               for t in times], dtype="float", unit=vcol.unit,
                              description="median of the paths at each time")
        bands = []
        for lv in levels:
            tail = float((Decimal(1) - Decimal(repr(lv))) / 2)
            names = []
            for q in (tail, 1 - tail):
                name = percentile_text(q)
                names.append(name)
                cols[name] = Column([store(quantile7(series[t], q)) if series[t] else None
                                     for t in times], dtype="float", unit=vcol.unit,
                                    description=f"{name} of the paths at each time")
            bands.append({"lower": names[0], "upper": names[1], "kind": "quantile",
                          "level": lv, "quantiles": None})
        counts = [len(series[t]) for t in times]
        meta["ensemble"] = {"paths": n_paths, "values_per_time": {"min": min(counts),
                                                                  "max": max(counts)},
                            "quantile_method": QUANTILE_METHOD}
        meta["bands"] = [{"lower_column": b["lower"], "upper_column": b["upper"],
                          "kind": "quantile", "level": b["level"]} for b in bands]
        lines.append(f"Line: median of {number_text(n_paths, loc, 12)} paths; bands: central "
                     f"{_level_text(levels, loc)} of the paths at each time (empirical "
                     "quantiles).")
        snap = DataSnapshot(columns=cols, provenance=copy.deepcopy(data.provenance))
        return snap, median, bands, vcol.description or vname

    def example(self) -> dict[str, Any]:
        from ..spec.data import dataset_to_dict
        from ..stochastic import gbm

        snap = gbm(paths=200, steps=24, seed=7, s0=100.0, mu=0.01, sigma=0.06, unit="EUR",
                   time_unit="month")
        snap.columns["value"].description = "Simulated price"
        snap.columns["time"].description = "Time ahead"
        return {"data": dataset_to_dict(snap), "params": {
            "time_column": "time", "member_column": "path", "value_column": "value",
            "title": "Price paths: 200 simulated scenarios",
            "caption": "Geometric Brownian motion, 1% drift and 6% volatility per month."}}


def _copy(c: Column) -> Column:
    return Column(values=list(c.values), dtype=c.dtype, unit=c.unit, description=c.description,
                  denominator=c.denominator, aggregation=c.aggregation)
