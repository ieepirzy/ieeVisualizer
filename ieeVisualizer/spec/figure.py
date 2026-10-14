"""FigureSpec and its non-mark parts: axes, legend, annotations."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Union

from ._common import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    canon_float,
    check_keys,
    copy_json,
    expect_bool,
    expect_dict,
    expect_list,
    expect_str,
    fail,
    non_finite_error,
    ptr_join,
    suggest,
)
from .data import Dataset, dataset_from_dict, dataset_to_dict
from .marks import Mark, mark_from_dict, mark_to_dict

SCALES = ("linear", "log", "symlog", "time", "category")
LEGEND_POSITIONS = (
    "auto", "top", "bottom", "left", "right",
    "upper-left", "upper-right", "lower-left", "lower-right", "outside-right",
)

Scalar = Union[float, int, str]


@dataclass
class Axis:
    """One positional axis. ``scale=None`` lets the renderer infer it from
    the data (category for strings, time for datetimes, else linear).
    ``limits`` is ``[min, max]``; either end may be ``None`` (auto). Time axes
    use ISO-8601 strings as limits."""

    label: str | None = None
    unit: str | None = None
    scale: str | None = None
    limits: list[Scalar | None] | None = None

    def __post_init__(self) -> None:
        try:
            self.limits = _canon_limits(self.limits)
        except ValueError as e:
            raise ValueError(f"axis limits: {e}") from None


def _canon_limits(limits: Any) -> Any:
    """Numbers in ``limits`` as canonical floats; strings/None unchanged."""
    return [canon_float(x) for x in limits] if isinstance(limits, (list, tuple)) else limits


@dataclass
class Legend:
    show: bool = True
    position: str = "auto"
    title: str | None = None


@dataclass
class Annotation:
    """Base annotation; use a subclass."""

    type: str
    label: str | None = None
    style: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in _COORD_FIELDS:
            if hasattr(self, name):
                try:
                    setattr(self, name, canon_float(getattr(self, name)))
                except ValueError as e:
                    raise ValueError(f"{self.type} annotation {name}: {e}") from None


#: Number-or-ISO-string annotation fields; numbers are stored as canonical floats.
_COORD_FIELDS = ("x", "y", "z", "value", "start", "end")


@dataclass
class TextAnnotation(Annotation):
    """``text`` placed at data coordinates (``x``, ``y``[, ``z``])."""

    type: str = field(default="text", init=False)
    text: str = ""
    x: Scalar | None = None
    y: Scalar | None = None
    z: Scalar | None = None


@dataclass
class ReferenceLine(Annotation):
    """A line across the plot at ``value`` on ``axis`` (``"x"`` or ``"y"``)."""

    type: str = field(default="reference_line", init=False)
    axis: str = "y"
    value: Scalar | None = None


@dataclass
class ReferenceBand(Annotation):
    """A shaded span ``[start, end]`` on ``axis``. For marking a known
    interval (e.g. a holiday period), not for inventing uncertainty."""

    type: str = field(default="reference_band", init=False)
    axis: str = "x"
    start: Scalar | None = None
    end: Scalar | None = None


ANNOTATION_TYPES: dict[str, type[Annotation]] = {
    "text": TextAnnotation,
    "reference_line": ReferenceLine,
    "reference_band": ReferenceBand,
}


@dataclass
class FigureSpec:
    """A complete, renderer-independent figure description.

    ``datasets`` maps a name to a :class:`DataSnapshot` or :class:`DatasetRef`;
    each layer's ``data`` refers to one of those names. ``axes`` is keyed by
    ``"x"``, ``"y"`` (and ``"z"`` in ``cartesian3d``). ``metadata`` is
    free-form JSON (template name/version, report run id, ...).
    """

    title: str | None = None
    layers: list[Mark] = field(default_factory=list)
    datasets: dict[str, Dataset] = field(default_factory=dict)
    axes: dict[str, Axis] = field(default_factory=dict)
    coordinate_system: str = "cartesian2d"
    legend: Legend = field(default_factory=Legend)
    annotations: list[Annotation] = field(default_factory=list)
    caption: str | None = None
    theme: str | None = None
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

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
            v = canon_float(v)
        out[f.name] = list(v) if isinstance(v, (list, tuple)) else (
            dict(v) if isinstance(v, dict) else v)
    return out


def figure_to_dict(spec: FigureSpec) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": spec.schema_version,
        "coordinate_system": spec.coordinate_system,
        "datasets": {k: dataset_to_dict(v) for k, v in spec.datasets.items()},
        "layers": [mark_to_dict(m) for m in spec.layers],
        "axes": {k: _simple_to_dict(a) for k, a in spec.axes.items()},
        "legend": _simple_to_dict(spec.legend),
        "annotations": [_simple_to_dict(a) for a in spec.annotations],
        "metadata": dict(spec.metadata),
    }
    for k in ("id", "title", "caption", "theme"):
        if getattr(spec, k) is not None:
            out[k] = getattr(spec, k)
    return out


_FIG_KEYS = (
    "schema_version", "id", "title", "caption", "coordinate_system", "axes", "datasets",
    "layers", "legend", "annotations", "theme", "metadata",
)


def _scalar(v: Any, path: str, what: str) -> Scalar | None:
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return canon_float(v)
        except ValueError:
            raise non_finite_error(path, what, v) from None
    raise fail(path, "invalid_type", f"{what} must be a number, an ISO-8601 string or null.")


def _axis_from_dict(d: Any, path: str) -> Axis:
    d = expect_dict(d, path, "an axis")
    check_keys(d, path, "an axis", ("label", "unit", "scale", "limits"))
    limits = None
    if d.get("limits") is not None:
        lim = expect_list(d["limits"], ptr_join(path, "limits"), "limits")
        if len(lim) != 2:
            raise fail(ptr_join(path, "limits"), "invalid_limits",
                       "limits must be [min, max]; use null for an automatic end.")
        limits = [_scalar(x, ptr_join(path, "limits", i), "a limit") for i, x in enumerate(lim)]
    return Axis(
        label=expect_str(d.get("label"), ptr_join(path, "label"), "label", True),
        unit=expect_str(d.get("unit"), ptr_join(path, "unit"), "unit", True),
        scale=expect_str(d.get("scale"), ptr_join(path, "scale"), "scale", True),
        limits=limits,
    )


def _annotation_from_dict(d: Any, path: str) -> Annotation:
    d = expect_dict(d, path, "an annotation")
    atype = d.get("type")
    cls = ANNOTATION_TYPES.get(atype) if isinstance(atype, str) else None
    if cls is None:
        raise fail(ptr_join(path, "type"), "unknown_annotation_type",
                   f"unknown annotation type {atype!r}.{suggest(str(atype), ANNOTATION_TYPES)} "
                   f"Supported: {', '.join(ANNOTATION_TYPES)}.")
    names = [f.name for f in fields(cls)]
    check_keys(d, path, f"a {atype} annotation", names)
    kw: dict[str, Any] = {}
    for k in names:
        if k == "type" or k not in d:
            continue
        kp = ptr_join(path, k)
        if k == "style":
            kw[k] = copy_json(expect_dict(d[k], kp, "style"), kp, "style")
        elif k in ("label", "text", "axis"):
            kw[k] = expect_str(d[k], kp, k, optional=(k == "label"))
        else:
            kw[k] = _scalar(d[k], kp, k)
    return cls(**kw)


def figure_from_dict(d: Any) -> FigureSpec:
    d = expect_dict(d, "", "a FigureSpec")
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
    check_keys(d, "", "a FigureSpec", _FIG_KEYS)

    kw: dict[str, Any] = {"schema_version": ver}
    for k in ("id", "title", "caption", "theme"):
        kw[k] = expect_str(d.get(k), "/" + k, k, optional=True)
    if "coordinate_system" in d:
        kw["coordinate_system"] = expect_str(d["coordinate_system"], "/coordinate_system",
                                             "coordinate_system")
    kw["datasets"] = {
        name: dataset_from_dict(ds, ptr_join("/datasets", name))
        for name, ds in expect_dict(d.get("datasets", {}), "/datasets", "datasets").items()
    }
    kw["layers"] = [
        mark_from_dict(m, f"/layers/{i}")
        for i, m in enumerate(expect_list(d.get("layers", []), "/layers", "layers"))
    ]
    kw["axes"] = {
        name: _axis_from_dict(a, ptr_join("/axes", name))
        for name, a in expect_dict(d.get("axes", {}), "/axes", "axes").items()
    }
    if "legend" in d:
        ld = expect_dict(d["legend"], "/legend", "legend")
        check_keys(ld, "/legend", "legend", ("show", "position", "title"))
        kw["legend"] = Legend(
            show=expect_bool(ld.get("show", True), "/legend/show", "show"),
            position=expect_str(ld.get("position", "auto"), "/legend/position", "position"),
            title=expect_str(ld.get("title"), "/legend/title", "title", optional=True),
        )
    kw["annotations"] = [
        _annotation_from_dict(a, f"/annotations/{i}")
        for i, a in enumerate(expect_list(d.get("annotations", []), "/annotations", "annotations"))
    ]
    kw["metadata"] = copy_json(
        expect_dict(d.get("metadata", {}), "/metadata", "metadata"), "/metadata", "metadata")
    return FigureSpec(**kw)
