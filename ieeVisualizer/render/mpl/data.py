"""Columns -> numpy arrays for drawing, without ever turning a null into 0.

Every conversion returns a ``numpy.ma.MaskedArray`` whose mask is exactly the
null rows. Line-like marks pass the masked arrays straight to matplotlib, which
breaks the line at masked points (a gap). Point-like marks (scatter, bar,
quiver, heatmap cells) leave the row out. A series with no drawable row left
is a :class:`RenderError`, never an empty or zero series.

Axis kinds are decided per axis from every column bound to it:

* ``category`` when the axis scale says so or the columns are strings/bools.
  Categories are positioned 0..n-1 in order of first appearance across layers.
* ``time`` for datetime columns (ISO-8601 strings in the spec), drawn as
  matplotlib date numbers with a date locator/formatter.
* ``numeric`` otherwise.

Mixing kinds on one axis (e.g. strings and numbers without
``scale: "category"``) is refused rather than guessed. A category axis with
more than :data:`MAX_CATEGORIES` distinct values is refused
(``too_many_categories``): drawing time grows with it and the labels could
not be read anyway.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ...spec import Column, DataSnapshot, FigureSpec, FunctionMark, HistogramMark
from ...spec._common import ptr
from ...spec.data import finite_plain_floats
from ..base import RenderError, RenderWarning, issue

AXES = ("x", "y", "z")
#: Most distinct values one category axis may have.
MAX_CATEGORIES = 5_000
#: Units that mark a polar angle column as degrees (otherwise radians).
DEGREE_UNITS = frozenset({"deg", "degree", "degrees", "°"})
_FRACTION = re.compile(r"(\.\d{6})\d+")


@dataclass
class AxisInfo:
    """Everything decided about one positional axis before drawing."""

    key: str
    kind: str = "numeric"  # numeric | time | category
    categories: dict[str, int] = field(default_factory=dict)
    tz: Any = None  # tzinfo for date formatting (None = values as given / UTC)
    # first bound column, for default label and unit
    label_column: str | None = None
    label_title: str | None = None
    unit: str | None = None
    # set by histogram (y is a count/density, not a column)
    derived_label: str | None = None
    # polar angle given in degrees (column unit says so): converted to radians
    degrees: bool = False

    def category_pos(self, value: Any) -> int:
        key = category_key(value)
        if key not in self.categories:
            self.categories[key] = len(self.categories)
        return self.categories[key]


def category_key(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_datetime(value: str) -> _dt.datetime:
    """ISO-8601 -> datetime. Accepts a trailing ``Z`` and more than six
    fractional digits (numpy writes nanoseconds), which Python 3.9's
    ``fromisoformat`` does not."""
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = _FRACTION.sub(r"\1", s)
    return _dt.datetime.fromisoformat(s)


def _datetimes_to_num(values: list[Any], path: str) -> tuple[np.ndarray, np.ndarray, list]:
    from matplotlib import dates as mdates

    nums = np.full(len(values), np.nan)
    mask = np.ones(len(values), dtype=bool)
    parsed: list[_dt.datetime | None] = []
    for i, v in enumerate(values):
        if v is None:
            parsed.append(None)
            continue
        try:
            d = parse_datetime(v)
        except (TypeError, ValueError):
            raise RenderError([issue(f"{path}/values/{i}", "invalid_datetime",
                                     f"{v!r} is not an ISO-8601 date/time; use e.g. "
                                     "'2026-09-07' or '2026-09-07T06:00:00+03:00'.")]) from None
        parsed.append(d)
        nums[i] = mdates.date2num(d)
        mask[i] = False
    return nums, mask, parsed


def column_path(spec: FigureSpec, data: str, name: str) -> str:
    return ptr("datasets", data, "columns", name)


def numbers(col: Column, path: str) -> np.ma.MaskedArray:
    """A numeric (non-positional) channel as a masked float array."""
    if col.dtype not in (None, "float", "int", "bool"):
        raise RenderError([issue(path, "unsupported_dtype",
                                 f"this channel needs numbers but the column is {col.dtype}.")])
    if finite_plain_floats(col.values):  # finite floats / None: nothing to refuse
        vals = np.array(col.values, dtype=float)  # None -> nan
        return np.ma.masked_array(vals, mask=np.isnan(vals))
    vals = np.full(len(col.values), np.nan)
    for i, v in enumerate(col.values):
        if v is not None:
            vals[i] = to_float(v, f"{path}/values/{i}")
    return np.ma.masked_array(vals, mask=np.array([v is None for v in col.values], dtype=bool))


def to_float(value: Any, path: str) -> float:
    """``float(value)``, refusing a number float64 cannot hold.

    The spec allows exact integers of any size (JSON has no limit), so
    ``10**400`` passes validation; it cannot be drawn, which is a structured
    ``value_out_of_range`` render error rather than a raw ``OverflowError``.
    """
    try:
        out = float(value)
    except OverflowError:
        out = math.inf
    if math.isinf(out):
        raise RenderError([issue(path, "value_out_of_range",
                                 f"{_short_number(value)} is outside the range a float64 can "
                                 "draw (about +-1.8e308); rescale it (e.g. change the unit) "
                                 "upstream.")])
    return out


def _short_number(value: Any) -> str:
    try:
        s = str(value)
    except ValueError:  # an int beyond Python's str() digit limit
        return "a number"
    return s if len(s) <= 24 else f"{s[:12]}...({len(s)} digits)"


def positions(col: Column, info: AxisInfo, path: str) -> np.ma.MaskedArray:
    """A positional channel on ``info``'s axis as a masked float array."""
    if info.kind == "category":
        vals = np.full(len(col.values), np.nan)
        for i, v in enumerate(col.values):
            if v is not None:
                vals[i] = info.category_pos(v)
        mask = np.array([v is None for v in col.values], dtype=bool)
        return np.ma.masked_array(vals, mask=mask)
    if info.kind == "time":
        nums, mask, _ = _datetimes_to_num(col.values, path)
        return np.ma.masked_array(nums, mask=mask)
    out = numbers(col, path)
    return np.deg2rad(out) if info.degrees else out


def scalar_position(value: Any, info: AxisInfo) -> float | None:
    """Annotation / limit value on an axis; ``None`` if it cannot be placed."""
    if value is None:
        return None
    if info.kind == "category":
        key = category_key(value)
        return float(info.categories[key]) if key in info.categories else None
    if info.kind == "time":
        if not isinstance(value, str):
            return None
        from matplotlib import dates as mdates

        try:
            return float(mdates.date2num(parse_datetime(value)))
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(np.deg2rad(value)) if info.degrees else float(value)


# --------------------------------------------------------------------------
# Axis kind inference
# --------------------------------------------------------------------------


def _bound(spec: FigureSpec):
    """Yield (layer_index, axis_key, encoding, column, dataset_name, channel)
    for every positional binding, honouring that channels map literally to
    axes; interval bounds (``y_lower``, ``y_upper``) are bound to y."""
    axes = AXES if spec.coordinate_system == "cartesian3d" else ("x", "y")
    for i, m in enumerate(spec.layers):
        if m.data is None:
            continue
        ds = spec.datasets.get(m.data)
        if not isinstance(ds, DataSnapshot):
            continue
        for ch in axes + ("y_lower", "y_upper"):
            enc = m.encoding.get(ch)
            if enc is None:
                continue
            yield i, ch[0], enc, ds.columns[enc.field], m.data, ch


def infer_axes(spec: FigureSpec, warnings: list[RenderWarning]) -> dict[str, AxisInfo]:
    keys = AXES if spec.coordinate_system == "cartesian3d" else ("x", "y")
    infos = {k: AxisInfo(key=k) for k in keys}
    kinds: dict[str, dict[str, str]] = {k: {} for k in keys}  # axis -> {path: kind}
    aware: dict[str, list[tuple[str, Any]]] = {k: [] for k in keys}
    bound_cols: dict[str, list[Column]] = {k: [] for k in keys}

    for i, axis, enc, col, data, ch in _bound(spec):
        info = infos[axis]
        bound_cols[axis].append(col)
        p = f"/layers/{i}/encoding/{ch}"
        if col.dtype in ("str", "bool"):
            k = "category"
        elif col.dtype == "datetime":
            k = "time"
            _, _, parsed = _datetimes_to_num(col.values, column_path(spec, data, enc.field))
            tzs = {d.utcoffset() is not None for d in parsed if d is not None}
            ds = spec.datasets[data]
            aware[axis].append((p, (tzs, ds.provenance.timezone, parsed)))
        else:
            k = "numeric"
        kinds[axis][p] = k
        if ch != axis:  # an interval bound: its unit, never the axis' name
            info.unit = info.unit or col.unit
        elif info.label_column is None:
            info.label_column, info.label_title, info.unit = enc.field, enc.title, col.unit
        elif info.unit is None:
            info.unit = col.unit

    for i, m in enumerate(spec.layers):
        if isinstance(m, FunctionMark) and m.expression is not None:
            for axis, var in zip(keys, m.variables):
                kinds[axis][f"/layers/{i}/expression"] = "numeric"
                if infos[axis].label_column is None:
                    infos[axis].label_column = var
            out_axis = keys[len(m.variables)] if len(m.variables) < len(keys) else None
            if out_axis is not None and infos[out_axis].label_column is None:
                infos[out_axis].label_column = m.name or m.expression
        if isinstance(m, HistogramMark):
            infos["y"].derived_label = "Density" if m.density else "Count"

    for axis, found in kinds.items():
        scale = spec.axes[axis].scale if axis in spec.axes else None
        distinct = set(found.values())
        if scale == "category":
            infos[axis].kind = "category"
        elif len(distinct) > 1:
            listing = ", ".join(f"{p} is {k}" for p, k in sorted(found.items()))
            raise RenderError([issue(f"/axes/{axis}", "axis_kind_conflict",
                                     f"axis {axis} mixes value kinds ({listing}); bind columns "
                                     "of one kind to an axis, or set scale 'category' to "
                                     "treat every value as a label.")])
        elif distinct:
            infos[axis].kind = distinct.pop()
        if infos[axis].kind == "time":
            infos[axis].tz = _display_tz(axis, aware[axis], warnings)
        if infos[axis].kind == "category":
            n = len({category_key(v) for c in bound_cols[axis] for v in c.values
                     if v is not None})
            if n > MAX_CATEGORIES:
                raise RenderError([issue(f"/axes/{axis}", "too_many_categories",
                                         f"axis {axis} has {n} distinct categories; at most "
                                         f"{MAX_CATEGORIES} can be drawn. Aggregate or filter "
                                         "upstream, or use a numeric/time axis.")])
    if spec.coordinate_system == "polar":
        unit = (spec.axes["x"].unit if "x" in spec.axes else None) or infos["x"].unit
        infos["x"].degrees = unit in DEGREE_UNITS
    if spec.coordinate_system == "polar" and infos["x"].kind != "numeric":
        raise RenderError([issue("/axes/x", "unsupported_dtype",
                                 f"a polar angle must be numeric (radians, or a unit of 'deg'); "
                                 f"the x axis here is {infos['x'].kind}.")])
    return infos


def _display_tz(axis: str, entries, warnings: list[RenderWarning]):
    """Timezone in which to label a time axis.

    Naive timestamps are shown exactly as written. If timestamps carry UTC
    offsets they are placed on the axis in UTC; the labels then use the
    datasets' common ``provenance.timezone`` when there is one, else UTC with
    a warning.
    """
    any_aware = any(True in tzs for _, (tzs, _, _) in entries)
    if not any_aware:
        return None
    any_naive = any(False in tzs for _, (tzs, _, _) in entries)
    if any_naive:
        warnings.append(RenderWarning(
            "mixed_timezone_awareness",
            f"axis {axis} mixes timestamps with and without a UTC offset; naive ones are "
            "treated as UTC.", f"/axes/{axis}"))
    zones = {tzname for _, (tzs, tzname, _) in entries if True in tzs}
    if len(zones) == 1 and None not in zones:
        name = zones.pop()
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - unknown zone or no tz database
            warnings.append(RenderWarning(
                "unknown_timezone",
                f"provenance timezone {name!r} could not be loaded; axis {axis} is labelled "
                "in UTC.", f"/axes/{axis}"))
            return _dt.timezone.utc
    offsets = {d.utcoffset() for _, (_, _, parsed) in entries for d in parsed
               if d is not None and d.utcoffset() is not None}
    if offsets - {_dt.timedelta(0)}:
        warnings.append(RenderWarning(
            "datetime_displayed_in_utc",
            f"axis {axis} has timestamps with non-UTC offsets and no single provenance "
            "timezone; they are labelled in UTC.", f"/axes/{axis}"))
    return _dt.timezone.utc


# --------------------------------------------------------------------------
# Grouping (series / categorical colour)
# --------------------------------------------------------------------------


def groups(col: Column | None, n: int) -> list[tuple[str | None, np.ndarray]]:
    """Row index arrays per distinct value (first-appearance order). Rows
    whose group value is null are returned under the label ``None`` only when
    there is no grouping column."""
    if col is None:
        return [(None, np.arange(n))]
    order: dict[str, list[int]] = {}
    for i, v in enumerate(col.values):
        if v is None:
            continue
        order.setdefault(category_key(v), []).append(i)
    return [(k, np.array(v, dtype=int)) for k, v in order.items()]


def null_group_rows(col: Column | None) -> int:
    return 0 if col is None else sum(v is None for v in col.values)
