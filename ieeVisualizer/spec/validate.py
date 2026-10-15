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
invalid_type               a field holds a value of the wrong JSON type (the same
                           value would be refused by from_dict after to_dict), or
                           a list/dict holds something other than the spec class
                           it should (e.g. a dict among the layers)
invalid_values             a column's values do not fit its dtype (or each other)
non_finite_number          NaN or an infinity where a number is stored; for one
                           put into a column's values after construction, an
                           infinity is an error and a NaN a warning (it is
                           stored as null, as on construction)
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
scale_dtype_mismatch       axis scale incompatible with the column dtype, or a
                           limit/annotation coordinate of the wrong kind for the
                           axis (a number on a time axis, a string on a numeric one)
axis_unit_conflict         layers on one axis disagree on unit
unknown_axis               axis key not valid for the coordinate system
invalid_limits / invalid_domain / invalid_samples / invalid_variables /
invalid_bins / function_source_conflict / function_source_missing /
missing_field              mark- or annotation-specific shape problems
                           (invalid_limits also: a limit on a time axis that is
                           not an ISO-8601 date/time, or reversed date limits)
========================== =====================================================

Time axes: an axis with ``scale="time"``, or with no scale whose bound columns
are declared ``datetime`` (see :func:`figure.time_axes`), takes ISO-8601
strings as limits and annotation coordinates. A number there is an error
(``scale_dtype_mismatch``), not an epoch offset: numbers have no unit or
timezone, so their meaning on a date axis would be renderer-defined.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from ._common import (
    SUPPORTED_SCHEMA_VERSIONS,
    ValidationIssue,
    _jtype,
    is_number,
    json_problem,
    ptr,
    suggest,
)
from .data import (
    DTYPES,
    Column,
    ColumnSchema,
    DatasetRef,
    DataSnapshot,
    Provenance,
    normalize_values,
)
from .figure import (
    ANNOTATION_TYPES,
    LEGEND_POSITIONS,
    SCALES,
    Annotation,
    Axis,
    FigureSpec,
    Legend,
    ReferenceBand,
    ReferenceLine,
    TextAnnotation,
    annotation_axes,
    parse_time_coordinate,
    time_axes,
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
    Encoding,
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
        # Containers as the checks iterate them: a container of the wrong type
        # is reported once (in _check_containers) and then treated as empty.
        self.datasets = spec.datasets if isinstance(spec.datasets, dict) else {}
        self.axes = spec.axes if isinstance(spec.axes, dict) else {}
        self.layers = spec.layers if isinstance(spec.layers, list) else []
        self.annotations = spec.annotations if isinstance(spec.annotations, list) else []
        self.times = time_axes(spec)
        # id() of columns whose types/values were reported; not checked further.
        self.bad_columns: set[int] = set()

    def add(self, path: str, code: str, message: str, severity: str = "error") -> None:
        self.issues.append(ValidationIssue(path, code, message, severity))

    def scale(self, axis: Any) -> str | None:
        a = self.axes.get(axis) if isinstance(axis, str) else None
        return a.scale if isinstance(a, Axis) and isinstance(a.scale, str) else None


# --------------------------------------------------------------------------
# Type checks. A spec built (or mutated) in Python can hold values that
# from_dict would refuse; these report them, with the parser's paths, so that
# "validate() found no errors" implies the spec survives to_dict/from_dict.
# Each returns False when it reported something; semantic checks on that part
# are then skipped (they would compare values of the wrong type).
# --------------------------------------------------------------------------


def _bad_type(c: _Ctx, path: str, what: str, expected: str, v: Any) -> bool:
    c.add(path, "invalid_type", f"{what} must be {expected}, got {_jtype(v)} {v!r}.")
    return False


def _t_number(c: _Ctx, path: str, v: Any, what: str, optional: bool = False) -> bool:
    if v is None and optional:
        return True
    if not is_number(v):
        return _bad_type(c, path, what, "a number", v)
    if not math.isfinite(v):
        c.add(path, "non_finite_number",
              f"{what} is {v!r}; NaN and infinities cannot be stored in JSON. Use a finite "
              "number.")
        return False
    return True


def _t_int(c: _Ctx, path: str, v: Any, what: str, optional: bool = False) -> bool:
    if v is None and optional:
        return True
    if not (is_number(v) and math.isfinite(v) and float(v).is_integer()):
        return _bad_type(c, path, what, "an integer", v)
    return True


def _t_bool(c: _Ctx, path: str, v: Any, what: str) -> bool:
    return isinstance(v, bool) or _bad_type(c, path, what, "true or false", v)


def _t_str(c: _Ctx, path: str, v: Any, what: str, optional: bool = False) -> bool:
    return (v is None and optional) or isinstance(v, str) or _bad_type(
        c, path, what, "a string" + (" or null" if optional else ""), v)


def _t_scalar(c: _Ctx, path: str, v: Any, what: str) -> bool:
    if v is None or isinstance(v, str):
        return True
    if is_number(v):
        return _t_number(c, path, v, what)
    return _bad_type(c, path, what, "a number, an ISO-8601 string or null", v)


def _t_json_object(c: _Ctx, path: str, v: Any, what: str) -> bool:
    if not isinstance(v, dict):
        return _bad_type(c, path, what, "a JSON object", v)
    problem = json_problem(v, path)
    if problem:
        c.add(*problem)
        return False
    return True


def _t_all(*results: bool) -> bool:
    return all(results)  # every check above has already run and reported


def _check_containers(c: _Ctx) -> None:
    """Each collection must be of its JSON type and hold the spec class it is
    declared to hold (a dict among the layers would crash serialization)."""
    spec = c.spec
    for key, want, kind in (("datasets", dict, "a JSON object"), ("axes", dict, "a JSON object"),
                            ("layers", list, "an array"), ("annotations", list, "an array")):
        if not isinstance(getattr(spec, key), want):
            _bad_type(c, "/" + key, key, kind, getattr(spec, key))
    for key, items in (("datasets", c.datasets), ("axes", c.axes)):
        for name in items:
            if not isinstance(name, str):
                c.add("/" + key, "invalid_type", f"{key} keys must be strings, got {name!r}.")
    for key, items, cls, what in (
        ("datasets", c.datasets.items(), (DataSnapshot, DatasetRef),
         "a DataSnapshot or DatasetRef"),
        ("axes", c.axes.items(), Axis, "an Axis"),
        ("layers", enumerate(c.layers), Mark, "a mark (e.g. LineMark)"),
        ("annotations", enumerate(c.annotations), Annotation,
         "an annotation (TextAnnotation, ReferenceLine or ReferenceBand)"),
    ):
        for name, v in items:
            if not isinstance(v, cls):
                c.add(ptr(key, name), "invalid_type",
                      f"{key} entries must be {what}, got {type(v).__name__} {v!r}; "
                      "build it with the class (from_dict accepts the JSON form).")
    if not isinstance(spec.legend, Legend):
        _bad_type(c, "/legend", "legend", "a Legend", spec.legend)


def _check_figure_types(c: _Ctx) -> None:
    spec = c.spec
    _check_containers(c)
    for k in ("id", "title", "caption", "theme"):
        _t_str(c, "/" + k, getattr(spec, k), k, optional=True)
    _t_json_object(c, "/metadata", spec.metadata, "metadata")
    lg = spec.legend
    if isinstance(lg, Legend):
        _t_all(_t_bool(c, "/legend/show", lg.show, "legend show"),
               _t_str(c, "/legend/title", lg.title, "legend title", optional=True))
    for name, ds in c.datasets.items():
        if isinstance(ds, DataSnapshot):
            _snapshot_types_ok(c, ptr("datasets", name), ds)
        elif isinstance(ds, DatasetRef):
            _ref_types_ok(c, ptr("datasets", name), ds)


_PROV_FIELDS = ("source", "query", "retrieved_at", "source_version", "timezone", "run_id",
                "notes")
_SCHEMA_STR_FIELDS = ("unit", "description", "denominator", "aggregation")


def _snapshot_types_ok(c: _Ctx, p: str, ds: DataSnapshot) -> bool:
    checks = []
    pp = p + "/provenance"
    if isinstance(ds.provenance, Provenance):
        checks += [_t_str(c, f"{pp}/{k}", getattr(ds.provenance, k), k, optional=True)
                   for k in _PROV_FIELDS]
        checks.append(_t_json_object(c, pp + "/extra", ds.provenance.extra, "provenance extra"))
    else:
        checks.append(_bad_type(c, pp, "provenance", "a Provenance", ds.provenance))
    if not isinstance(ds.columns, dict):
        return _bad_type(c, p + "/columns", "columns", "a JSON object of Column", ds.columns)
    for name, col in ds.columns.items():
        checks.append(_column_ok(c, f"{p}/columns/{ptr(name)[1:]}", col, Column))
    return _t_all(*checks)


def _ref_types_ok(c: _Ctx, p: str, ds: DatasetRef) -> bool:
    checks = [_t_str(c, p + "/kind", ds.kind, "kind"), _t_str(c, p + "/id", ds.id, "id")]
    checks += [_t_str(c, f"{p}/{k}", getattr(ds, k), k, optional=True)
               for k in ("query", "version", "retrieved_at")]
    if ds.columns is not None:
        if not isinstance(ds.columns, dict):
            return _bad_type(c, p + "/columns", "columns", "a JSON object of ColumnSchema",
                             ds.columns)
        for name, col in ds.columns.items():
            checks.append(_column_ok(c, f"{p}/columns/{ptr(name)[1:]}", col, ColumnSchema))
    return _t_all(*checks)


def _column_ok(c: _Ctx, cp: str, col: Any, cls: type) -> bool:
    """Type-check a column (or column schema) and, for an inline column,
    re-normalise its values against its dtype exactly as construction and
    from_dict do, so values changed after construction are caught here."""
    ok = _column_types_ok(c, cp, col, cls)
    if not ok:
        c.bad_columns.add(id(col))
    return ok


def _beyond_float64(v: Any) -> bool:
    """An exact number (e.g. a Python int) too large for a float64."""
    try:
        float(v)
    except OverflowError:
        return True
    return False


def _short_int(v: Any) -> str:
    try:
        s = str(v)
    except ValueError:  # beyond Python's int -> str digit limit
        return "(a very large integer)"
    return s if len(s) <= 24 else f"{s[:12]}... ({len(s)} digits)"


def _column_types_ok(c: _Ctx, cp: str, col: Any, cls: type) -> bool:
    if not isinstance(col, cls):
        return _bad_type(c, cp, "a column", f"a {cls.__name__}", col)
    ok = _t_all(*(_t_str(c, f"{cp}/{k}", getattr(col, k), k, optional=True)
                  for k in _SCHEMA_STR_FIELDS))
    dtype = col.dtype
    if dtype is not None and not isinstance(dtype, str):
        ok = _bad_type(c, cp + "/dtype", "dtype", "a string or null", dtype)
    elif dtype is not None and dtype not in DTYPES:
        c.add(cp + "/dtype", "invalid_value",
              f"dtype {dtype!r} is unknown.{suggest(dtype, DTYPES)} Use one of "
              f"{', '.join(DTYPES)}.")
        ok = False
    if cls is ColumnSchema or not ok:
        return ok
    values = col.values
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        return _bad_type(c, cp + "/values", "values", "an array", values)
    try:
        items = list(values)
    except Exception:  # noqa: BLE001 - an iterable that fails to iterate is a bad type
        return _bad_type(c, cp + "/values", "values", "an array", values)
    for i, v in enumerate(items):
        if is_number(v) and _beyond_float64(v):
            c.add(f"{cp}/values/{i}", "number_out_of_range",
                  f"value {_short_int(v)} is beyond the float64 range (about +-1.8e308), so "
                  "it can be stored but never drawn or compared reliably; rescale it (e.g. "
                  "change the unit) upstream.")
            ok = False
        elif is_number(v) and math.isinf(v):
            c.add(f"{cp}/values/{i}", "non_finite_number",
                  f"value {v!r} was put into the column after construction; JSON has no "
                  "infinity, and an infinity is a value, not a missing one. Use None for a "
                  "missing value or fix the computation upstream.")
            ok = False
        elif is_number(v) and v != v:  # noqa: PLR0124 - NaN
            c.add(f"{cp}/values/{i}", "non_finite_number",
                  f"value {v!r} was put into the column after construction; it is stored as "
                  "null (missing), as on construction. Use None to say so explicitly.",
                  "warning")
    if not ok:
        return False
    try:
        normalize_values(values, dtype)
    except (ValueError, TypeError) as e:
        c.add(cp + "/values", "invalid_values", str(e))
        return False
    return True


def _axis_types_ok(c: _Ctx, p: str, axis: Axis) -> bool:
    ok = _t_all(*(_t_str(c, f"{p}/{k}", getattr(axis, k), k, optional=True)
                  for k in ("label", "unit", "scale")))
    lim = axis.limits
    if lim is None:
        return ok
    if not isinstance(lim, (list, tuple)) or len(lim) != 2:
        c.add(p + "/limits", "invalid_limits",
              "limits must be [min, max]; use null for an automatic end.")
        return False
    return _t_all(ok, *(_t_scalar(c, f"{p}/limits/{i}", v, "a limit")
                        for i, v in enumerate(lim)))


def _mark_types_ok(c: _Ctx, p: str, m: Mark) -> bool:
    checks = [
        _t_str(c, p + "/data", m.data, "data", optional=True),
        _t_str(c, p + "/name", m.name, "name", optional=True),
        _t_str(c, p + "/role", m.role, "role"),
        _t_json_object(c, p + "/style", m.style, "style"),
    ]
    if isinstance(m.encoding, dict):
        for ch, e in m.encoding.items():
            ep = f"{p}/encoding/{ch}"
            if isinstance(e, str):  # shorthand, normalised by to_dict
                continue
            if not isinstance(e, Encoding):
                checks.append(_bad_type(c, ep, f"encoding {ch!r}", '{"field": "<column>"}', e))
                continue
            checks += [_t_str(c, ep + "/field", e.field, "field"),
                       _t_str(c, ep + "/title", e.title, "title", optional=True)]
    else:
        checks.append(_bad_type(c, p + "/encoding", "encoding", "a JSON object", m.encoding))
    if isinstance(m, BarMark):
        checks.append(_t_str(c, p + "/orientation", m.orientation, "orientation"))
    elif isinstance(m, HistogramMark):
        checks += [_t_int(c, p + "/bins", m.bins, "bins", optional=True),
                   _t_bool(c, p + "/density", m.density, "density"),
                   _t_number_list(c, p + "/bin_edges", m.bin_edges, "bin_edges", optional=True)]
    elif isinstance(m, QuiverMark):
        checks += [_t_number(c, p + "/scale", m.scale, "scale", optional=True),
                   _t_bool(c, p + "/normalize", m.normalize, "normalize")]
    elif isinstance(m, FunctionMark):
        checks += [_t_str(c, p + "/expression", m.expression, "expression", optional=True),
                   _t_int(c, p + "/samples", m.samples, "samples")]
        if isinstance(m.variables, list):
            checks += [_t_str(c, f"{p}/variables/{i}", v, "variable")
                       for i, v in enumerate(m.variables)]
        else:
            checks.append(_bad_type(c, p + "/variables", "variables", "an array", m.variables))
        for key, what in (("domain", "domain"), ("parameters", "parameters")):
            val = getattr(m, key)
            if not isinstance(val, dict):
                checks.append(_bad_type(c, f"{p}/{key}", what, "a JSON object", val))
                continue
            for k, v in val.items():
                kp = f"{p}/{key}/{ptr(k)[1:]}"
                checks.append(_t_number_list(c, kp, v, "domain") if key == "domain"
                              else _t_number(c, kp, v, "parameter"))
    return _t_all(*checks)


def _t_number_list(c: _Ctx, path: str, v: Any, what: str, optional: bool = False) -> bool:
    if v is None and optional:
        return True
    if not isinstance(v, (list, tuple)):
        return _bad_type(c, path, what, "an array of numbers", v)
    return _t_all(*(_t_number(c, f"{path}/{i}", x, f"{what} item")
                    for i, x in enumerate(v)))


def _annotation_types_ok(c: _Ctx, p: str, a: Annotation) -> bool:
    checks = [_t_str(c, p + "/label", a.label, "label", optional=True),
              _t_json_object(c, p + "/style", a.style, "style")]
    declared = next((k for k, cls in ANNOTATION_TYPES.items() if type(a) is cls), None)
    if declared is not None and a.type != declared:
        c.add(p + "/type", "invalid_value",
              f"a {type(a).__name__} has type {declared!r}, got {a.type!r}; do not reassign it.")
        checks.append(False)
    if isinstance(a, TextAnnotation):
        checks.append(_t_str(c, p + "/text", a.text, "text"))
    if isinstance(a, (ReferenceLine, ReferenceBand)):
        checks.append(_t_str(c, p + "/axis", a.axis, "axis"))
    for k in ("x", "y", "z", "value", "start", "end"):
        if hasattr(a, k):
            checks.append(_t_scalar(c, f"{p}/{k}", getattr(a, k), k))
    return _t_all(*checks)


def validate(spec: FigureSpec) -> list[ValidationIssue]:
    """Return every issue found in ``spec`` (empty list = valid)."""
    c = _Ctx(spec)
    _check_figure_types(c)
    if not isinstance(spec.coordinate_system, str):
        _bad_type(c, "/coordinate_system", "coordinate_system", "a string",
                  spec.coordinate_system)
        return c.issues
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
    position = spec.legend.position if isinstance(spec.legend, Legend) else "auto"
    if not isinstance(position, str):
        _bad_type(c, "/legend/position", "legend position", "a string", position)
    elif position not in LEGEND_POSITIONS:
        c.add("/legend/position", "invalid_value",
              f"legend position {position!r} is unknown; use one of "
              f"{', '.join(LEGEND_POSITIONS)}.")
    for name, ds in c.datasets.items():
        if isinstance(ds, (DataSnapshot, DatasetRef)):
            _check_dataset(c, name, ds)
    if isinstance(spec.layers, list) and not spec.layers:
        c.add("/layers", "no_layers",
              "the figure has no layers; add at least one mark (e.g. a line) bound to a dataset.")
    for i, mark in enumerate(c.layers):
        if isinstance(mark, Mark):
            _check_mark(c, i, mark)
    _check_units(c)
    for i, ann in enumerate(c.annotations):
        if isinstance(ann, Annotation):
            _check_annotation(c, i, ann)
    return c.issues


def _check_axes(c: _Ctx) -> None:
    for key, axis in c.axes.items():
        p = ptr("axes", key)
        if not isinstance(axis, Axis) or not _axis_types_ok(c, p, axis):
            continue
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
            kinds_ok = _t_all(*(_check_coordinate(c, f"{p}/limits/{i}", key, v, "a limit",
                                                  "invalid_limits")
                                for i, v in enumerate(lim)))
            if kinds_ok and axis.scale != "category":
                _check_order(c, p + "/limits", lo, hi,
                             f"limits [{lo}, {hi}] are empty or reversed; min must be below max.")
            if axis.scale == "log" and any(_num(v) and v <= 0 for v in lim):
                c.add(p + "/limits", "log_scale_nonpositive",
                      "a log axis cannot include values <= 0; raise the lower limit above 0 or "
                      "use scale 'symlog'.")


def _check_dataset(c: _Ctx, name: str, ds: DataSnapshot | DatasetRef) -> None:
    p = ptr("datasets", name)
    if isinstance(ds, DatasetRef):
        for k in ("kind", "id"):
            if isinstance(getattr(ds, k), str) and not getattr(ds, k):
                c.add(f"{p}/{k}", "missing_field", f"a dataset reference needs a non-empty {k}.")
        return
    if not isinstance(ds.columns, dict):
        return  # reported as invalid_type
    lens = {n: len(col.values) for n, col in ds.columns.items()
            if isinstance(col, Column) and hasattr(col.values, "__len__")}
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
    if not isinstance(m.type, str):
        _bad_type(c, p + "/type", "type", "a string", m.type)
        return
    cls = MARK_TYPES.get(m.type)
    if cls is None or not isinstance(m, cls):
        c.add(p + "/type", "unknown_mark_type",
              f"unknown mark type {m.type!r}.{suggest(m.type, MARK_TYPES)} Supported: "
              f"{', '.join(MARK_TYPES)}.")
        return
    if not _mark_types_ok(c, p, m):
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
    ds = c.datasets.get(m.data)
    if ds is None:
        names = [n for n in c.datasets if isinstance(n, str)]
        c.add(p + "/data", "unknown_dataset",
              f"dataset {m.data!r} does not exist.{suggest(m.data, names)} Available: "
              f"{', '.join(sorted(names)) or '(none)'}.")
        return
    if not isinstance(ds, (DataSnapshot, DatasetRef)) or not (
        isinstance(ds.columns, dict) or (isinstance(ds, DatasetRef) and ds.columns is None)
    ):
        return  # reported as invalid_type; its columns cannot be looked up
    if isinstance(ds, DatasetRef) and ds.columns is None:
        c.add(p + "/data", "unverified_reference",
              f"dataset {m.data!r} is an external reference without declared columns, so "
              "encodings and units cannot be checked until it is resolved.", "info")
        return
    for ch, enc in m.encoding.items():
        if ch not in allowed:
            continue
        if isinstance(enc, str):  # shorthand assigned after construction; to_dict normalises
            enc = Encoding(field=enc)
        ep = f"{p}/encoding/{ch}"
        col = _lookup(ds, enc.field)
        if col is not None and (not isinstance(col, (Column, ColumnSchema))
                                or id(col) in c.bad_columns):
            continue  # reported as invalid_type / invalid_values
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
        a = c.axes.get(axis)
        declared = a.unit if isinstance(a, Axis) and isinstance(a.unit, str) else None
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


def _check_coordinate(c: _Ctx, path: str, axis: Any, v: Any, what: str, code: str) -> bool:
    """Check that coordinate ``v`` (already type-checked as number, string or
    None) is the right kind for ``axis``: an ISO-8601 date/time string on a
    time axis (see :func:`figure.time_axes`), a number on a linear/log/symlog
    axis. ``code`` is reported for a string that is not a date/time."""
    if v is None:
        return True
    if axis in c.times:
        if is_number(v):
            c.add(path, "scale_dtype_mismatch",
                  f"axis {axis} is a time axis, so {what} must be an ISO-8601 date/time "
                  f"string (e.g. \"2026-01-01\"), got the number {v!r}. Numbers are not "
                  "read as timestamps.")
            return False
        if parse_time_coordinate(v) is None:
            c.add(path, code,
                  f"{what} {v!r} is not an ISO-8601 date/time, which axis {axis} (a time axis) "
                  "needs; use YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS[.ffffff]] with an optional Z "
                  "or +HH:MM offset.")
            return False
        return True
    scale = c.scale(axis)
    if scale in ("linear", "log", "symlog") and isinstance(v, str):
        hint = (" For dates, use scale 'time'." if parse_time_coordinate(v) is not None
                else " For categories, use scale 'category'.")
        c.add(path, "scale_dtype_mismatch",
              f"axis {axis} has a {scale} scale, so {what} must be a number, got the string "
              f"{v!r}.{hint}")
        return False
    return True


def _check_order(c: _Ctx, path: str, lo: Any, hi: Any, message: str) -> None:
    """Report ``lo >= hi`` for two numbers or two date/time strings (compared
    as instants, not as text). Other combinations are not ordered here."""
    if _num(lo) and _num(hi):
        if lo >= hi:
            c.add(path, "invalid_limits", message)
        return
    a, b = parse_time_coordinate(lo), parse_time_coordinate(hi)
    if a is None or b is None:
        return
    if (a.utcoffset() is None) != (b.utcoffset() is None):
        c.add(path, "invalid_limits",
              f"{lo!r} and {hi!r} mix a timezone-aware and a naive date/time, which cannot be "
              "ordered; give both an offset (e.g. +00:00) or neither.")
    elif a >= b:
        c.add(path, "invalid_limits", message)


def _check_annotation(c: _Ctx, i: int, a: Annotation) -> None:
    p = f"/annotations/{i}"
    if not _annotation_types_ok(c, p, a):
        return
    coords_ok = _t_all(*(
        _check_coordinate(c, f"{p}/{k}", axis, getattr(a, k), k, "invalid_value")
        for k, axis in annotation_axes(a).items() if hasattr(a, k)))
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
        c.add(p + "/type", "invalid_value",
              f"unknown annotation type {a.type!r}; use TextAnnotation, ReferenceLine or "
              "ReferenceBand.")
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
    if isinstance(a, ReferenceBand) and coords_ok and c.scale(a.axis) != "category":
        _check_order(c, p, a.start, a.end,
                     f"reference band start {a.start!r} must be below end {a.end!r}.")
