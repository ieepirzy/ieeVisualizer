"""Semantic validation of a FigureSpec.

:func:`validate` never raises for a semantically wrong spec; it returns a list
of :class:`ValidationIssue` (path, code, message, severity) in document order.
Paths are JSON pointers into the serialized spec, so an agent can feed them
straight into :func:`apply_patch`. Codes are stable identifiers:

========================== =====================================================
code                       meaning
========================== =====================================================
unsupported_schema_version spec.schema_version not readable by this library
invalid_value              a field holds a value outside its allowed set
no_layers                  the figure has nothing to draw
empty_dataset              dataset with no columns or zero rows
column_length_mismatch     columns of one snapshot differ in length
unknown_mark_type          layer type is not a known mark
unsupported_in_coordinate_system  mark cannot be drawn in this coordinate system
missing_data / unknown_dataset    layer has no / a dangling ``data`` reference
missing_channel            a channel the mark requires is not encoded
unsupported_channel        the mark does not use this channel
unknown_column             encoding names a column the dataset lacks
all_null_column            encoded column has no non-null value
unverified_reference       (info) DatasetRef without declared columns
log_scale_nonpositive      log axis but data/limit/domain <= 0
scale_dtype_mismatch       axis scale incompatible with the column dtype
axis_unit_conflict         layers on one axis disagree on unit
unknown_axis               axis key not valid for the coordinate system
invalid_limits / invalid_domain / invalid_samples / invalid_variables /
invalid_bins / function_source_conflict / function_source_missing /
missing_field              mark- or annotation-specific shape problems
========================== =====================================================
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ._common import SUPPORTED_SCHEMA_VERSIONS, ValidationIssue, ptr, suggest
from .data import Column, ColumnSchema, DatasetRef, DataSnapshot
from .figure import (
    LEGEND_POSITIONS,
    SCALES,
    FigureSpec,
    ReferenceBand,
    ReferenceLine,
    TextAnnotation,
)
from .marks import (
    ALLOWED_CHANNELS,
    COORDINATE_SYSTEMS,
    MARK_TYPES,
    ORIENTATIONS,
    REQUIRED_3D,
    REQUIRED_CHANNELS,
    ROLES,
    BarMark,
    FunctionMark,
    HistogramMark,
    Mark,
    QuiverMark,
)

AXIS_CHANNELS = ("x", "y", "z")
_NUMERIC = ("float", "int")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Ctx:
    def __init__(self, spec: FigureSpec):
        self.spec = spec
        self.issues: list[ValidationIssue] = []
        self.axis_keys = ("x", "y", "z") if spec.coordinate_system == "cartesian3d" else ("x", "y")
        # axis -> list of (path, unit) for unit-conflict detection
        self.units: dict[str, list[tuple[str, str]]] = {a: [] for a in AXIS_CHANNELS}

    def add(self, path: str, code: str, message: str, severity: str = "error") -> None:
        self.issues.append(ValidationIssue(path, code, message, severity))

    def scale(self, axis: str) -> str | None:
        a = self.spec.axes.get(axis)
        return a.scale if a is not None else None


def validate(spec: FigureSpec) -> list[ValidationIssue]:
    """Return every issue found in ``spec`` (empty list = valid)."""
    c = _Ctx(spec)
    if spec.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        c.add("/schema_version", "unsupported_schema_version",
              f"schema_version {spec.schema_version!r} is not supported; use "
              f"{SUPPORTED_SCHEMA_VERSIONS[-1]!r}.")
    if spec.coordinate_system not in COORDINATE_SYSTEMS:
        c.add("/coordinate_system", "invalid_value",
              f"coordinate_system {spec.coordinate_system!r} is unknown."
              f"{suggest(spec.coordinate_system, COORDINATE_SYSTEMS)} Use one of "
              f"{', '.join(COORDINATE_SYSTEMS)}.")
    _check_axes(c)
    if spec.legend.position not in LEGEND_POSITIONS:
        c.add("/legend/position", "invalid_value",
              f"legend position {spec.legend.position!r} is unknown; use one of "
              f"{', '.join(LEGEND_POSITIONS)}.")
    for name, ds in spec.datasets.items():
        _check_dataset(c, name, ds)
    if not spec.layers:
        c.add("/layers", "no_layers",
              "the figure has no layers; add at least one mark (e.g. a line) bound to a dataset.")
    for i, mark in enumerate(spec.layers):
        _check_mark(c, i, mark)
    _check_units(c)
    for i, ann in enumerate(spec.annotations):
        _check_annotation(c, i, ann)
    return c.issues


def _check_axes(c: _Ctx) -> None:
    for key, axis in c.spec.axes.items():
        p = ptr("axes", key)
        if key not in c.axis_keys:
            c.add(p, "unknown_axis",
                  f"axis {key!r} does not exist in {c.spec.coordinate_system}; valid axes are "
                  f"{', '.join(c.axis_keys)} (polar uses x = angle, y = radius).")
        if axis.scale is not None and axis.scale not in SCALES:
            c.add(p + "/scale", "invalid_value",
                  f"scale {axis.scale!r} is unknown.{suggest(axis.scale, SCALES)} Use one of "
                  f"{', '.join(SCALES)}, or omit it to let the renderer infer the scale.")
        lim = axis.limits
        if lim is not None:
            lo, hi = lim
            if _num(lo) and _num(hi) and lo >= hi:
                c.add(p + "/limits", "invalid_limits",
                      f"limits [{lo}, {hi}] are empty or reversed; min must be below max.")
            if axis.scale == "log" and any(_num(v) and v <= 0 for v in lim):
                c.add(p + "/limits", "log_scale_nonpositive",
                      "a log axis cannot include values <= 0; raise the lower limit above 0 or "
                      "use scale 'symlog'.")


def _check_dataset(c: _Ctx, name: str, ds: DataSnapshot | DatasetRef) -> None:
    p = ptr("datasets", name)
    if isinstance(ds, DatasetRef):
        for k in ("kind", "id"):
            if not getattr(ds, k):
                c.add(f"{p}/{k}", "missing_field", f"a dataset reference needs a non-empty {k}.")
        return
    lens = ds.lengths()
    if not lens or all(n == 0 for n in lens.values()):
        c.add(p, "empty_dataset",
              f"dataset {name!r} has no rows; supply data or remove the dataset and the layers "
              "that use it. An empty dataset must not be drawn as an empty or zero series.")
        return
    counts = Counter(lens.values())
    expected = max(counts, key=lambda n: (counts[n], -list(lens.values()).index(n)))
    for col, n in lens.items():
        if n != expected:
            c.add(f"{p}/columns/{ptr(col)[1:]}", "column_length_mismatch",
                  f"column {col!r} has {n} values but the other columns of {name!r} have "
                  f"{expected}; every column of a snapshot must have the same length (use null "
                  "for missing values).")


def _check_mark(c: _Ctx, i: int, m: Mark) -> None:
    p = f"/layers/{i}"
    cls = MARK_TYPES.get(m.type)
    if cls is None or not isinstance(m, cls):
        c.add(p + "/type", "unknown_mark_type",
              f"unknown mark type {m.type!r}.{suggest(m.type, MARK_TYPES)} Supported: "
              f"{', '.join(MARK_TYPES)}.")
        return
    if m.role not in ROLES:
        c.add(p + "/role", "invalid_value",
              f"role {m.role!r} is unknown; use one of {', '.join(ROLES)}.")
    cs = c.spec.coordinate_system
    if cs in COORDINATE_SYSTEMS and m.type not in COORDINATE_SYSTEMS[cs]:
        c.add(p + "/type", "unsupported_in_coordinate_system",
              f"a {m.type} mark cannot be drawn in {cs}; supported there: "
              f"{', '.join(sorted(COORDINATE_SYSTEMS[cs]))}.")
    _check_mark_options(c, p, m)
    if isinstance(m, FunctionMark) and not _function_is_sampled(c, p, m):
        return
    _check_bindings(c, p, m)


def _check_mark_options(c: _Ctx, p: str, m: Mark) -> None:
    if isinstance(m, BarMark) and m.orientation not in ORIENTATIONS:
        c.add(p + "/orientation", "invalid_value",
              f"orientation must be one of {', '.join(ORIENTATIONS)}.")
    if isinstance(m, HistogramMark):
        if m.bins is not None and m.bins < 1:
            c.add(p + "/bins", "invalid_bins", "bins must be a positive integer.")
        if m.bin_edges is not None and (
            len(m.bin_edges) < 2 or any(b >= a for b, a in zip(m.bin_edges, m.bin_edges[1:]))
        ):
            c.add(p + "/bin_edges", "invalid_bins",
                  "bin_edges must hold at least two strictly increasing numbers.")
        if m.bins is not None and m.bin_edges is not None:
            c.add(p + "/bins", "invalid_bins", "give either bins or bin_edges, not both.")
    if isinstance(m, QuiverMark) and m.scale is not None and m.scale <= 0:
        c.add(p + "/scale", "invalid_value", "quiver scale must be > 0 (or omitted for auto).")


def _function_is_sampled(c: _Ctx, p: str, m: FunctionMark) -> bool:
    """Check a function mark; return True if it is the sampled form whose data
    bindings still need checking."""
    has_expr = m.expression is not None
    has_data = m.data is not None
    if has_expr and has_data:
        c.add(p, "function_source_conflict",
              "a function mark takes either an expression or sampled data, not both; remove "
              "/data (and /encoding) or remove /expression.")
        return False
    if not has_expr and not has_data:
        c.add(p, "function_source_missing",
              "a function mark needs an expression (e.g. \"sin(x)\") with a domain, or data + "
              "encoding for a sampled curve.")
        return False
    if has_data:
        return True
    if not m.expression.strip():
        c.add(p + "/expression", "invalid_value", "expression must not be empty.")
    want = 2 if c.spec.coordinate_system == "cartesian3d" else 1
    if len(m.variables) != want or len(set(m.variables)) != len(m.variables):
        c.add(p + "/variables", "invalid_variables",
              f"{c.spec.coordinate_system} needs exactly {want} distinct variable(s) "
              f"(y = f(x){' / z = f(x, y)' if want == 2 else ''}); got {m.variables}.")
    for ch in m.encoding:
        c.add(p + "/encoding/" + ch, "unsupported_channel",
              "an expression function mark is not bound to data; remove its encoding.")
    for var in m.variables:
        rng = m.domain.get(var)
        if rng is None:
            c.add(p + "/domain", "invalid_domain",
                  f"no domain for variable {var!r}; add \"{var}\": [min, max] to /domain.")
            continue
        if len(rng) != 2 or not (rng[0] < rng[1]):
            c.add(p + "/domain/" + var, "invalid_domain",
                  f"domain of {var!r} must be [min, max] with min < max, got {rng}.")
    for var in m.domain:
        if var not in m.variables:
            c.add(p + "/domain/" + var, "invalid_domain",
                  f"domain given for {var!r}, which is not in variables {m.variables}.")
    if m.samples < 2:
        c.add(p + "/samples", "invalid_samples", "samples must be at least 2.")
    for axis, var in zip(AXIS_CHANNELS, m.variables):
        rng = m.domain.get(var)
        if c.scale(axis) == "log" and rng and len(rng) == 2 and rng[0] <= 0:
            c.add(p + "/domain/" + var, "log_scale_nonpositive",
                  f"axis {axis} is log-scaled but the domain of {var!r} starts at {rng[0]}; "
                  "start the domain above 0.")
    return False


def _lookup(ds: Any, field: str) -> Column | ColumnSchema | None:
    if isinstance(ds, DataSnapshot):
        return ds.columns.get(field)
    if isinstance(ds, DatasetRef) and ds.columns is not None:
        return ds.columns.get(field)
    return None


def _check_bindings(c: _Ctx, p: str, m: Mark) -> None:
    spec = c.spec
    required = set(REQUIRED_CHANNELS.get(m.type, ()))
    allowed = set(ALLOWED_CHANNELS.get(m.type, ()))
    if spec.coordinate_system == "cartesian3d":
        required |= REQUIRED_3D.get(m.type, frozenset())
    else:
        allowed -= {"z", "w"}
    for ch in sorted(required - set(m.encoding)):
        c.add(f"{p}/encoding/{ch}", "missing_channel",
              f"a {m.type} mark requires channel {ch!r}; add \"{ch}\": {{\"field\": "
              "\"<column>\"}} to /encoding.")
    for ch in m.encoding:
        if ch not in allowed:
            c.add(f"{p}/encoding/{ch}", "unsupported_channel",
                  f"a {m.type} mark in {spec.coordinate_system} does not use channel {ch!r}; "
                  f"usable channels: {', '.join(sorted(allowed))}.")
    if m.data is None:
        c.add(p + "/data", "missing_data",
              "the layer is not bound to a dataset; set /data to a key of /datasets.")
        return
    ds = spec.datasets.get(m.data)
    if ds is None:
        c.add(p + "/data", "unknown_dataset",
              f"dataset {m.data!r} does not exist.{suggest(m.data, spec.datasets)} Available: "
              f"{', '.join(sorted(spec.datasets)) or '(none)'}.")
        return
    if isinstance(ds, DatasetRef) and ds.columns is None:
        c.add(p + "/data", "unverified_reference",
              f"dataset {m.data!r} is an external reference without declared columns, so "
              "encodings and units cannot be checked until it is resolved.", "info")
        return
    for ch, enc in m.encoding.items():
        if ch not in allowed:
            continue
        ep = f"{p}/encoding/{ch}"
        col = _lookup(ds, enc.field)
        if col is None:
            names = ds.columns.keys()
            c.add(ep + "/field", "unknown_column",
                  f"dataset {m.data!r} has no column {enc.field!r}.{suggest(enc.field, names)} "
                  f"Columns: {', '.join(sorted(names)) or '(none)'}.")
            continue
        if isinstance(col, Column) and col.values and not col.non_null():
            c.add(ep, "all_null_column",
                  f"column {enc.field!r} has no non-null values, so nothing would be drawn for "
                  "it; bind a column with data or drop this layer rather than plotting it as "
                  "empty or zero.")
            continue
        if ch in AXIS_CHANNELS and ch in c.axis_keys:
            _check_axis_binding(c, ep, ch, enc.field, col)


def _check_axis_binding(c: _Ctx, ep: str, axis: str, name: str, col: Any) -> None:
    scale = c.scale(axis)
    if scale in ("linear", "log", "symlog") and col.dtype in ("str", "datetime"):
        c.add(ep, "scale_dtype_mismatch",
              f"axis {axis} has a {scale} scale but column {name!r} is {col.dtype}; use "
              f"scale '{'time' if col.dtype == 'datetime' else 'category'}' or bind a numeric "
              "column.")
    elif scale == "time" and col.dtype not in (None, "datetime"):
        c.add(ep, "scale_dtype_mismatch",
              f"axis {axis} has a time scale but column {name!r} is {col.dtype}; store "
              "timestamps as dtype 'datetime' (ISO-8601 strings).", "warning")
    if scale == "log" and isinstance(col, Column) and col.dtype in _NUMERIC:
        bad = [v for v in col.non_null() if v <= 0]
        if bad:
            c.add(ep, "log_scale_nonpositive",
                  f"axis {axis} is log-scaled but column {name!r} has {len(bad)} value(s) <= 0 "
                  f"(e.g. {bad[0]}); they cannot be drawn. Use scale 'symlog', 'linear', or "
                  "filter the data upstream.")
    if col.unit is not None:
        c.units[axis].append((ep, col.unit))


def _check_units(c: _Ctx) -> None:
    for axis, seen in c.units.items():
        declared = c.spec.axes[axis].unit if axis in c.spec.axes else None
        ref_unit = declared
        ref_where = f"/axes/{axis}/unit" if declared is not None else None
        for path, unit in seen:
            if ref_unit is None:
                ref_unit, ref_where = unit, path
                continue
            if unit != ref_unit:
                c.add(path, "axis_unit_conflict",
                      f"this column is in {unit!r} but axis {axis} is already in {ref_unit!r} "
                      f"(from {ref_where}); convert the data to one unit, or move this layer "
                      "to its own figure. Units are never converted implicitly.")


def _check_annotation(c: _Ctx, i: int, a: Any) -> None:
    p = f"/annotations/{i}"
    if isinstance(a, TextAnnotation):
        if not a.text:
            c.add(p + "/text", "missing_field", "a text annotation needs non-empty text.")
        for k in ("x", "y") + (("z",) if c.spec.coordinate_system == "cartesian3d" else ()):
            v = getattr(a, k)
            if v is None:
                c.add(f"{p}/{k}", "missing_field", f"a text annotation needs {k!r} coordinates.")
            elif c.scale(k) == "log" and _num(v) and v <= 0:
                c.add(f"{p}/{k}", "log_scale_nonpositive",
                      f"axis {k} is log-scaled; the annotation at {k} = {v} cannot be placed.")
        return
    if not isinstance(a, (ReferenceLine, ReferenceBand)):
        c.add(p + "/type", "invalid_value", f"unknown annotation type {a.type!r}.")
        return
    if a.axis not in c.axis_keys:
        c.add(p + "/axis", "unknown_axis",
              f"axis {a.axis!r} is not valid here; use one of {', '.join(c.axis_keys)}.")
    values = [("value", a.value)] if isinstance(a, ReferenceLine) else [
        ("start", a.start), ("end", a.end)]
    for k, v in values:
        if v is None:
            c.add(f"{p}/{k}", "missing_field", f"a {a.type} annotation needs {k!r}.")
        elif c.scale(a.axis) == "log" and _num(v) and v <= 0:
            c.add(f"{p}/{k}", "log_scale_nonpositive",
                  f"axis {a.axis} is log-scaled; {k} {v} cannot be drawn.")
    if isinstance(a, ReferenceBand) and _num(a.start) and _num(a.end) and a.start >= a.end:
        c.add(p, "invalid_limits", "reference band start must be below end.")
