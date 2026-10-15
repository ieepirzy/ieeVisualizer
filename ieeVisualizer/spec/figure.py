"""FigureSpec and its non-mark parts: axes, legend, annotations."""

from __future__ import annotations

from dataclasses import field, fields
from typing import Any, Union

from ._common import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    SpecParseError,
    canon_float,
    expect_dict,
    expect_list,
    fail,
    non_finite_error,
    ptr,
    suggest,
)
from ._model import (
    Bool,
    JsonObject,
    Nested,
    OptStr,
    SpecModel,
    Str,
    WireShape,
    model,
    parse_array,
    parse_model,
    parse_object,
    wire,
)
from .data import (
    Dataset,
    DatasetRef,
    DataSnapshot,
    dataset_from_dict,
    dataset_to_dict,
    format_iso_datetime,
    parse_iso_datetime,
)
from .marks import Encoding, Mark, mark_from_dict, mark_to_dict

SCALES = ("linear", "log", "symlog", "time", "category")
LEGEND_POSITIONS = (
    "auto", "top", "bottom", "left", "right",
    "upper-left", "upper-right", "lower-left", "lower-right", "outside-right",
)

Scalar = Union[float, int, str]


def _scalar(v: Any, path: str, what: str) -> Scalar | None:
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return canon_float(v, path, what)
        except SpecParseError:
            raise  # number_out_of_range
        except ValueError:
            raise non_finite_error(path, what, v) from None
    raise fail(path, "invalid_type", f"{what} must be a number, an ISO-8601 string or null.")


def Coordinate(what: str) -> Any:  # noqa: N802 - a type factory, used in annotations
    """A number (stored as a canonical float), an ISO-8601 string or null."""
    return wire(lambda v: _scalar(v, "", what), "Scalar | None")


def _parse_limits(v: Any) -> list[Scalar | None] | None:
    if v is None:
        return None
    lim = expect_list(v, "", "limits")
    if len(lim) != 2:
        raise fail("", "invalid_limits",
                   "limits must be [min, max]; use null for an automatic end.")
    return [_scalar(x, ptr(i), "a limit") for i, x in enumerate(lim)]


@model
class Axis(SpecModel):
    """One positional axis. ``scale=None`` lets the renderer infer it from
    the data (category for strings, time for datetimes, else linear).
    ``limits`` is ``[min, max]``; either end may be ``None`` (auto). Time axes
    use ISO-8601 strings as limits; on a ``scale="time"`` axis they are stored
    in the canonical datetime form (see :mod:`ieeVisualizer.spec.data`)."""

    __wire__ = WireShape("an axis", order=("limits", "label", "unit", "scale"))

    label: OptStr("label") = None
    unit: OptStr("unit") = None
    scale: OptStr("scale") = None
    limits: wire(_parse_limits, "list[Scalar | None] | None") = None

    def __post_init__(self) -> None:
        try:
            self.limits = _canon_limits(self.limits)
        except SpecParseError:
            raise  # number_out_of_range, already structured (path relative to the axis)
        except ValueError as e:
            raise ValueError(f"axis limits: {e}") from None
        if self.scale == "time" and isinstance(self.limits, list):
            self.limits = [canon_time(x) for x in self.limits]


def _canon_limits(limits: Any) -> Any:
    """Numbers in ``limits`` as canonical floats; strings/None unchanged."""
    if not isinstance(limits, (list, tuple)):
        return limits
    return [canon_float(x, f"/limits/{i}", "an axis limit") for i, x in enumerate(limits)]


def parse_time_coordinate(v: Any) -> Any:
    """The ``datetime`` a coordinate string names, or ``None`` if ``v`` is not
    an accepted ISO-8601 string."""
    if not isinstance(v, str):
        return None
    try:
        return parse_iso_datetime(v)
    except ValueError:
        return None


def canon_time(v: Any) -> Any:
    """The canonical stored form of a date/time coordinate string; any other
    value (a number, None, a string that is not an ISO-8601 date/time) is
    returned unchanged for :func:`validate` to report."""
    d = parse_time_coordinate(v)
    if d is None:
        return v
    try:
        return format_iso_datetime(d)
    except ValueError:
        return v


def time_axes(spec: Any) -> set[str]:
    """Axis keys whose coordinates are dates/times: an axis with
    ``scale="time"``, or with no scale when at least one column bound to it is
    declared ``datetime`` and none is declared otherwise (the renderer infers
    a time scale there). Coordinates (limits, annotation positions) on these
    axes are ISO-8601 strings, canonicalised on serialization; validate
    rejects numbers and non-date strings there. Robust to malformed specs."""
    axes = spec.axes if isinstance(spec.axes, dict) else {}
    out: set[str] = set()
    for key in ("x", "y", "z"):
        a = axes.get(key)
        scale = a.scale if isinstance(a, Axis) else None
        if scale == "time":
            out.add(key)
        elif scale is None and _bound_dtypes(spec, key) == {"datetime"}:
            out.add(key)
    return out


def _bound_dtypes(spec: Any, axis: str) -> set[Any]:
    """Declared dtypes of the columns bound to ``axis`` (unknown ones skipped)."""
    datasets = spec.datasets if isinstance(spec.datasets, dict) else {}
    layers = spec.layers if isinstance(spec.layers, list) else []
    out: set[Any] = set()
    for m in layers:
        enc = getattr(m, "encoding", None)
        e = enc.get(axis) if isinstance(enc, dict) else None
        name = e if isinstance(e, str) else (e.field if isinstance(e, Encoding) else None)
        data = getattr(m, "data", None)
        ds = datasets.get(data) if isinstance(data, str) else None
        cols = ds.columns if isinstance(ds, (DataSnapshot, DatasetRef)) else None
        col = cols.get(name) if isinstance(cols, dict) and isinstance(name, str) else None
        dtype = getattr(col, "dtype", None)
        if isinstance(dtype, str):
            out.add(dtype)
    return out


@model
class Legend(SpecModel):
    __wire__ = WireShape("legend")

    show: Bool("show") = True
    position: Str("position") = "auto"
    title: OptStr("title") = None


@model
class Annotation(SpecModel):
    """Base annotation; use a subclass."""

    __wire__ = WireShape("a {name} annotation", drop=("type",))

    type: Str("type")
    label: OptStr("label") = None
    style: JsonObject("style") = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in _COORD_FIELDS:
            if hasattr(self, name):
                try:
                    setattr(self, name, canon_float(getattr(self, name), "/" + name,
                                                    f"{self.type} annotation {name}"))
                except SpecParseError:
                    raise  # number_out_of_range, already structured (path relative to it)
                except ValueError as e:
                    raise ValueError(f"{self.type} annotation {name}: {e}") from None


#: Number-or-ISO-string annotation fields; numbers are stored as canonical floats.
_COORD_FIELDS = ("x", "y", "z", "value", "start", "end")


@model
class TextAnnotation(Annotation):
    """``text`` placed at data coordinates (``x``, ``y``[, ``z``])."""

    type: Str("type") = field(default="text", init=False)
    text: Str("text") = ""
    x: Coordinate("x") = None
    y: Coordinate("y") = None
    z: Coordinate("z") = None


@model
class ReferenceLine(Annotation):
    """A line across the plot at ``value`` on ``axis`` (``"x"`` or ``"y"``)."""

    type: Str("type") = field(default="reference_line", init=False)
    axis: Str("axis") = "y"
    value: Coordinate("value") = None


@model
class ReferenceBand(Annotation):
    """A shaded span ``[start, end]`` on ``axis``. For marking a known
    interval (e.g. a holiday period), not for inventing uncertainty."""

    type: Str("type") = field(default="reference_band", init=False)
    axis: Str("axis") = "x"
    start: Coordinate("start") = None
    end: Coordinate("end") = None


ANNOTATION_TYPES: dict[str, type[Annotation]] = {
    "text": TextAnnotation,
    "reference_line": ReferenceLine,
    "reference_band": ReferenceBand,
}


def _check_schema_version(d: dict[str, Any]) -> None:
    if "schema_version" not in d:
        raise fail("/schema_version", "missing_schema_version",
                   f"a serialized FigureSpec must carry schema_version; add "
                   f"\"schema_version\": \"{SCHEMA_VERSION}\".")
    ver = d["schema_version"]
    if ver not in SUPPORTED_SCHEMA_VERSIONS:
        raise fail("/schema_version", "unsupported_schema_version",
                   f"schema_version {ver!r} is not supported by this library (supports "
                   f"{', '.join(SUPPORTED_SCHEMA_VERSIONS)}). Upgrade ieeVisualizer to read newer "
                   "specs, or migrate the spec; it is not reinterpreted silently.")


def _parse_datasets(v: Any) -> dict[str, Dataset]:
    return parse_object(v, "datasets", lambda name, ds: dataset_from_dict(ds, ""))


def _parse_layers(v: Any) -> list[Mark]:
    return parse_array(v, "layers", lambda m: mark_from_dict(m, ""))


def _parse_axes(v: Any) -> dict[str, Axis]:
    return parse_object(v, "axes", lambda name, a: parse_model(Axis, a, name))


def _parse_annotation(d: Any) -> Annotation:
    d = expect_dict(d, "", "an annotation")
    atype = d.get("type")
    cls = ANNOTATION_TYPES.get(atype) if isinstance(atype, str) else None
    if cls is None:
        raise fail("/type", "unknown_annotation_type",
                   f"unknown annotation type {atype!r}.{suggest(str(atype), ANNOTATION_TYPES)} "
                   f"Supported: {', '.join(ANNOTATION_TYPES)}.")
    return parse_model(cls, d, atype)


def _parse_annotations(v: Any) -> list[Annotation]:
    return parse_array(v, "annotations", _parse_annotation)


@model
class FigureSpec(SpecModel):
    """A complete, renderer-independent figure description.

    ``datasets`` maps a name to a :class:`DataSnapshot` or :class:`DatasetRef`;
    each layer's ``data`` refers to one of those names. ``axes`` is keyed by
    ``"x"``, ``"y"`` (and ``"z"`` in ``cartesian3d``). ``metadata`` is
    free-form JSON (template name/version, report run id, ...).
    """

    __wire__ = WireShape("a FigureSpec", pre=_check_schema_version, order=(
        "schema_version", "id", "title", "caption", "theme", "coordinate_system", "datasets",
        "layers", "axes", "legend", "annotations", "metadata"))

    title: OptStr("title") = None
    layers: wire(_parse_layers, "list[Mark]") = field(default_factory=list)
    datasets: wire(_parse_datasets, "dict[str, Dataset]") = field(default_factory=dict)
    axes: wire(_parse_axes, "dict[str, Axis]") = field(default_factory=dict)
    coordinate_system: Str("coordinate_system") = "cartesian2d"
    legend: Nested(Legend) = field(default_factory=Legend)
    annotations: wire(_parse_annotations, "list[Annotation]") = field(default_factory=list)
    caption: OptStr("caption") = None
    theme: OptStr("theme") = None
    id: OptStr("id") = None
    metadata: JsonObject("metadata") = field(default_factory=dict)
    schema_version: Str("schema_version") = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Date/time strings on time axes get their one stored form (the same
        # rule as datetime columns), so equal specs compare and hash equally.
        canonicalize_time_coordinates(self)

    # Convenience wrappers around the module-level functions.
    def to_dict(self) -> dict[str, Any]:
        from .serialize import to_dict

        return to_dict(self)

    def to_json(self, indent: int | None = None) -> str:
        from .serialize import to_json

        return to_json(self, indent=indent)

    @classmethod
    def from_dict(cls, d: Any) -> FigureSpec:
        return figure_from_dict(d)

    @classmethod
    def from_json(cls, s: str) -> FigureSpec:
        from .serialize import from_json

        return from_json(s)

    def validate(self) -> list:
        from .validate import validate

        return validate(self)


def _simple_to_dict(obj: Any) -> dict[str, Any]:
    out = {}
    for f in fields(obj):
        v = getattr(obj, f.name)
        if v is None:
            continue
        if f.name == "limits":
            v = _canon_limits(v)
        elif f.name in _COORD_FIELDS:
            v = canon_float(v, "/" + f.name, f.name)
        out[f.name] = list(v) if isinstance(v, (list, tuple)) else (
            dict(v) if isinstance(v, dict) else v)
    return out


def annotation_axes(a: Any) -> dict[str, Any]:
    """``{coordinate field: axis key}`` for an annotation's coordinates."""
    if isinstance(a, TextAnnotation):
        return {"x": "x", "y": "y", "z": "z"}
    if isinstance(a, ReferenceLine):
        return {"value": a.axis}
    if isinstance(a, ReferenceBand):
        return {"start": a.axis, "end": a.axis}
    return {}


def _canon_time_coords(spec: FigureSpec, axes: dict[str, Any], annotations: list[Any]) -> None:
    """Canonicalise date/time strings on time axes (see :func:`time_axes`) in
    the serialized ``axes`` / ``annotations``, in place."""
    times = time_axes(spec)
    for k, d in axes.items():
        if k in times and isinstance(d.get("limits"), list):
            d["limits"] = [canon_time(v) for v in d["limits"]]
    for a, d in zip(spec.annotations, annotations):
        for name, axis in annotation_axes(a).items():
            if axis in times and name in d:
                d[name] = canon_time(d[name])


def canonicalize_time_coordinates(spec: FigureSpec) -> None:
    """Store date/time coordinate strings on time axes in canonical form, in
    place, on the spec's own Axis/Annotation objects. Run on construction and
    on parse; :func:`figure_to_dict` applies the same rule to its output, so a
    value assigned later is canonical there too. Malformed parts are skipped
    (validate reports them)."""
    times = time_axes(spec)
    axes = spec.axes if isinstance(spec.axes, dict) else {}
    for k, a in axes.items():
        if k in times and isinstance(a, Axis) and isinstance(a.limits, list):
            a.limits = [canon_time(v) for v in a.limits]
    for a in spec.annotations if isinstance(spec.annotations, list) else []:
        for name, axis in annotation_axes(a).items():
            if axis in times:
                setattr(a, name, canon_time(getattr(a, name)))


def figure_to_dict(spec: FigureSpec) -> dict[str, Any]:
    axes = {k: _simple_to_dict(a) for k, a in spec.axes.items()}
    annotations = [_simple_to_dict(a) for a in spec.annotations]
    _canon_time_coords(spec, axes, annotations)
    out: dict[str, Any] = {
        "schema_version": spec.schema_version,
        "coordinate_system": spec.coordinate_system,
        "datasets": {k: dataset_to_dict(v) for k, v in spec.datasets.items()},
        "layers": [mark_to_dict(m) for m in spec.layers],
        "axes": axes,
        "legend": _simple_to_dict(spec.legend),
        "annotations": annotations,
        "metadata": dict(spec.metadata),
    }
    for k in ("id", "title", "caption", "theme"):
        if getattr(spec, k) is not None:
            out[k] = getattr(spec, k)
    return out


def figure_from_dict(d: Any) -> FigureSpec:
    return parse_model(FigureSpec, d)
