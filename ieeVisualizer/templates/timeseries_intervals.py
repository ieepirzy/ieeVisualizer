"""``timeseries`` 2.0.0: 1.0.0 plus intervals that say what they are.

1.0.0 drew caller-supplied ``lower_column``/``upper_column`` as two dotted,
unlabelled lines. 2.0.0 replaces them with ``intervals``: a list of
``{"lower", "upper", "kind", "level"?, "multiplier"?}``, each drawn as a
``band`` layer whose kind and level are required (as in the spec: e.g.
``{"kind": "prediction", "level": 0.8}``) and appear in the legend. Several
intervals of one series at different levels make a fan chart: they are drawn
widest level first, so the narrower ones sit on top and the fill darkens
towards the centre. Bounds are copied as given, never computed; a row
without both bounds is a gap in the band. Bands of forecast rows are
forecast-role layers (hatched, lighter). Everything else is 1.0.0;
``get("timeseries", "1.0.0")`` still builds exactly what it did.
"""

from __future__ import annotations

from typing import Any

from ..spec import BandMark, DataSnapshot, FigureSpec, ValidationIssue
from ..spec._common import ptr
from ..spec.intervals import metadata_problems
from .base import Issues, Parameter, column_path, param_path
from .timeseries import TimeseriesTemplate

_NUMERIC = ("float", "int")
_REMOVED = ("lower_column", "upper_column")


class TimeseriesTemplate2(TimeseriesTemplate):
    version = "2.0.0"
    description = (
        "Line chart of one or more numeric series over time (wide or long format), with "
        "optional observed/forecast split drawn as separate forecast-role layers, an optional "
        "target line, and caller-supplied intervals drawn as labelled bands (kind and level "
        "required; several levels make a fan chart)."
    )
    parameters = tuple(p for p in TimeseriesTemplate.parameters if p.name not in _REMOVED) + (
        Parameter("intervals", "interval_list", "Caller-supplied intervals of the single value "
                  "column: [{lower, upper, kind, level?, multiplier?, quantiles?}], bound column "
                  "names, a kind (confidence, credible, prediction, quantile, min-max, "
                  "standard-error, standard-deviation) and its level as a fraction (0.8; "
                  "central for quantile), SE/SD multiplier, or quantiles [lower, upper]. "
                  "Never computed here."),
    )
    columns = tuple(c for c in TimeseriesTemplate.columns if c.parameter not in _REMOVED)
    notes = TimeseriesTemplate.notes[:2] + (
        "No uncertainty is ever computed; bands appear only for the given intervals, each "
        "labelled with its kind and level (e.g. '80 % prediction interval').",
        TimeseriesTemplate.notes[3],
    )
    _bands_key = "intervals"

    def _check_params(self, params: dict[str, Any], issues: Issues) -> dict[str, Any]:
        resolved = super()._check_params(params, issues)
        for k, item in enumerate(issues.items):
            name = item.path.rsplit("/", 1)[-1]
            if item.code == "unknown_parameter" and name in _REMOVED:
                issues.items[k] = ValidationIssue(
                    item.path, item.code,
                    f"timeseries {self.version} has no {name}: an interval must state its kind "
                    "and level. Pass intervals=[{\"lower\": ..., \"upper\": ..., \"kind\": "
                    "\"prediction\", \"level\": 0.95}] (or build with version '1.0.0').",
                    item.severity)
        return resolved

    def _bound_params(self, p: dict[str, Any]) -> list[tuple[str, str]]:
        return [(f"intervals/{k}", c) for k, iv in enumerate(p["intervals"] or ())
                for c in (iv["lower"], iv["upper"])]

    def _check_bound_params(self, p: dict[str, Any], issues: Issues) -> None:
        if p["intervals"] and len(p["value_columns"]) != 1:
            issues.error(param_path("intervals"), "conflicting_parameters",
                         "intervals apply to a single value column; draw each series with "
                         "intervals in its own figure or use long format.")

    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        assert data is not None
        for k, iv in enumerate(p["intervals"] or ()):
            base = ptr("params", "intervals", str(k))
            for fld, code, message in metadata_problems(iv["kind"], iv["level"],
                                                        iv["multiplier"], iv["quantiles"]):
                issues.error(f"{base}/{fld}", code, message)
            for key in ("lower", "upper"):
                self._check_bound_column(data, iv[key], f"{base}/{key}", issues)
        if issues.errors:
            return
        for k, iv in enumerate(p["intervals"] or ()):
            lo, hi = data.columns[iv["lower"]].values, data.columns[iv["upper"]].values
            bad = [i for i, (a, b) in enumerate(zip(lo, hi))
                   if a is not None and b is not None and a > b]
            if bad:
                issues.error(column_path(iv["lower"]), "invalid_column_values",
                             f"interval {k}: row {bad[0]} has lower bound {lo[bad[0]]!r} above "
                             f"upper bound {hi[bad[0]]!r} ({len(bad)} row(s)); bounds are drawn "
                             "as given, so fix them upstream (swapped columns?).")
        super()._check(data, p, issues)

    @staticmethod
    def _check_bound_column(data: DataSnapshot, name: str, path: str, issues: Issues) -> None:
        col = data.columns.get(name)
        if col is None:
            issues.error(path, "missing_column",
                         f"bound column {name!r} is not in the data. Columns: "
                         f"{', '.join(sorted(data.columns))}.")
        elif col.dtype is not None and col.dtype not in _NUMERIC:
            issues.error(column_path(name), "wrong_dtype",
                         f"bound column {name!r} has dtype {col.dtype!r}; bounds must be float "
                         "or int.")
        elif not col.non_null():
            issues.error(column_path(name), "all_null_column",
                         f"bound column {name!r} has no non-null values, so no interval would "
                         "be drawn; leave the interval out rather than drawing it empty.")

    def _value_layers(self, spec: FigureSpec, snap: DataSnapshot, part: str, tname: str,
                      v: str, series: str | None, value_name: str, p: dict[str, Any],
                      bands: list[dict[str, Any]]) -> None:
        ivs = list(enumerate(p["intervals"] or ()))
        if all(iv["level"] is not None for _, iv in ivs):
            ivs.sort(key=lambda kv: -kv[1]["level"])  # widest first: a fan, darkest inside
        for k, iv in ivs:
            lo, hi = snap.columns[iv["lower"]].values, snap.columns[iv["upper"]].values
            if not any(a is not None and b is not None for a, b in zip(lo, hi)):
                continue  # no row of this part has both bounds; nothing is invented
            enc = {"x": tname, "y_lower": iv["lower"], "y_upper": iv["upper"]}
            if series is not None:
                enc["series"] = series
            bands.append({"interval": k, "value_column": v, "lower_column": iv["lower"],
                          "upper_column": iv["upper"], "role": part, "layer": len(spec.layers)})
            spec.layers.append(BandMark(
                data=part, encoding=enc, role=part, style={"color_of": value_name},
                interval_kind=iv["kind"], interval_level=iv["level"],
                interval_multiplier=iv["multiplier"], interval_quantiles=iv["quantiles"]))
        p0 = dict(p, lower_column=None, upper_column=None)
        super()._value_layers(spec, snap, part, tname, v, series, value_name, p0, [])

    def example(self) -> dict[str, Any]:
        payload = super().example()
        data = payload["data"]
        fc = [None] * 5 + [1410.0, 1450.0]
        width80, width50 = [None] * 5 + [90.0, 150.0], [None] * 5 + [40.0, 70.0]
        for name, widths, sign in (("p10", width80, -1), ("p90", width80, 1),
                                   ("p25", width50, -1), ("p75", width50, 1)):
            data["columns"][name] = {
                "dtype": "float", "unit": "EUR",
                "description": f"forecast model quantile {name[1:]} %",
                "values": [None if f is None else f + sign * w for f, w in zip(fc, widths)]}
        payload["params"]["intervals"] = [
            {"lower": "p10", "upper": "p90", "kind": "prediction", "level": 0.8},
            {"lower": "p25", "upper": "p75", "kind": "prediction", "level": 0.5},
        ]
        payload["params"]["caption"] = "Forecast and bands: model output."
        del payload["params"]["target"]  # fewer legend rows: the fan stays legible on a phone
        return payload
