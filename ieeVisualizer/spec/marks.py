"""Marks: the visual primitives a FigureSpec layers on top of each other.

Each layer is one mark. A mark names a dataset (``data``, a key into
``FigureSpec.datasets``) and maps visual *channels* to that dataset's columns
through ``encoding``. Channels:

``x``, ``y``, ``z``   position (in ``polar``: ``x`` = angle, ``y`` = radius)
``u``, ``v``, ``w``   vector components (quiver)
``color``             colour by value (heatmap: the cell value)
``size``              marker size by value (scatter)
``series``            split rows into separate series by a column (long format)

``role`` marks whether the layer shows ``observed`` data, a ``forecast`` or a
``reference`` (e.g. a target line or an analytic curve). Renderers must draw
forecasts visibly differently from observations.

``style`` is a free-form, backend-neutral hint dict. Keys renderers are
expected to understand: ``color``, ``colormap``, ``linewidth``, ``linestyle``,
``marker``, ``opacity``. Unknown keys may be ignored with a fidelity warning.

A ``function`` mark is either symbolic (``expression`` + ``variables`` +
``domain`` + ``samples``; the expression is a SymPy-parsable string that the
renderer evaluates, the spec itself never evaluates it) or sampled (``data`` +
``encoding`` like a line). Exactly one of the two forms must be used.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from ._common import (
    canon_float,
    canon_int,
    check_keys,
    copy_json,
    expect_bool,
    expect_dict,
    expect_int,
    expect_list,
    expect_number,
    expect_str,
    fail,
    ptr_join,
    suggest,
)

CHANNELS = ("x", "y", "z", "u", "v", "w", "color", "size", "series")
ROLES = ("observed", "forecast", "reference")
ORIENTATIONS = ("vertical", "horizontal")


@dataclass
class Encoding:
    """Maps one channel to one column. ``title`` overrides the column name in
    legends/colorbars."""

    field: str
    title: str | None = None


@dataclass
class Mark:
    """Base mark. Use a concrete subclass; constructing ``Mark`` directly with
    an unrecognised ``type`` is allowed so :func:`validate` can report it."""

    type: str
    data: str | None = None
    encoding: dict[str, Encoding] = field(default_factory=dict)
    role: str = "observed"
    name: str | None = None
    style: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ergonomics: encoding={"y": "revenue"} is accepted from Python.
        self.encoding = {
            ch: Encoding(field=e) if isinstance(e, str) else e for ch, e in self.encoding.items()
        }
        for name, canon in _CANON.items():
            v = getattr(self, name, None)
            if v is not None:
                try:
                    setattr(self, name, canon(v))
                except ValueError as e:
                    raise ValueError(f"{self.type} mark {name}: {e}") from None


@dataclass
class LineMark(Mark):
    type: str = field(default="line", init=False)


@dataclass
class ScatterMark(Mark):
    type: str = field(default="scatter", init=False)


@dataclass
class AreaMark(Mark):
    """Filled area between ``y`` and zero."""

    type: str = field(default="area", init=False)


@dataclass
class BarMark(Mark):
    """Channels map literally to axes: a horizontal bar chart puts the
    categories on ``y`` and the values on ``x``."""

    type: str = field(default="bar", init=False)
    orientation: str = "vertical"


@dataclass
class HistogramMark(Mark):
    """Bins the raw values of ``x``; the y axis is a count (or density)."""

    type: str = field(default="histogram", init=False)
    bins: int | None = None
    bin_edges: list[float] | None = None
    density: bool = False


@dataclass
class HeatmapMark(Mark):
    """Long-format grid: one row per cell with ``x``, ``y`` and the cell value
    on ``color``."""

    type: str = field(default="heatmap", init=False)


@dataclass
class QuiverMark(Mark):
    """Arrows at (``x``, ``y``[, ``z``]) with components (``u``, ``v``[, ``w``])."""

    type: str = field(default="quiver", init=False)
    scale: float | None = None
    normalize: bool = False


@dataclass
class FunctionMark(Mark):
    """Symbolic ``expression`` over ``variables`` on ``domain``, or a sampled
    reference curve via ``data`` + ``encoding``."""

    type: str = field(default="function", init=False)
    expression: str | None = None
    variables: list[str] = field(default_factory=lambda: ["x"])
    domain: dict[str, list[float]] = field(default_factory=dict)
    samples: int = 200
    parameters: dict[str, float] = field(default_factory=dict)


def _canon_float_list(v: Any) -> Any:
    return [canon_float(x) for x in v] if isinstance(v, (list, tuple)) else v


def _canon_float_map(v: Any) -> Any:
    return {k: canon_float(x) for k, x in v.items()} if isinstance(v, dict) else v


def _canon_domain(v: Any) -> Any:
    return {k: _canon_float_list(r) for k, r in v.items()} if isinstance(v, dict) else v


#: Declared-numeric mark fields and their canonical form (see
#: ``_common.canon_float``): applied on construction and again on
#: serialization, so a value assigned later is canonical in the output too.
#: Values of the wrong type pass through unchanged for :func:`validate` to report.
_CANON = {
    "bins": canon_int,
    "samples": canon_int,
    "bin_edges": _canon_float_list,
    "scale": canon_float,
    "domain": _canon_domain,
    "parameters": _canon_float_map,
}


MARK_TYPES: dict[str, type[Mark]] = {
    "line": LineMark,
    "scatter": ScatterMark,
    "area": AreaMark,
    "bar": BarMark,
    "histogram": HistogramMark,
    "heatmap": HeatmapMark,
    "quiver": QuiverMark,
    "function": FunctionMark,
}

#: Channels a mark needs in 2D (cartesian2d / polar).
REQUIRED_CHANNELS: dict[str, frozenset[str]] = {
    "line": frozenset({"x", "y"}),
    "scatter": frozenset({"x", "y"}),
    "area": frozenset({"x", "y"}),
    "bar": frozenset({"x", "y"}),
    "histogram": frozenset({"x"}),
    "heatmap": frozenset({"x", "y", "color"}),
    "quiver": frozenset({"x", "y", "u", "v"}),
    "function": frozenset({"x", "y"}),  # sampled form only
}
#: Additional channels required in cartesian3d.
REQUIRED_3D: dict[str, frozenset[str]] = {
    "line": frozenset({"z"}),
    "scatter": frozenset({"z"}),
    "quiver": frozenset({"z", "w"}),
    "function": frozenset({"z"}),
}
ALLOWED_CHANNELS: dict[str, frozenset[str]] = {
    "line": frozenset({"x", "y", "z", "color", "series"}),
    "scatter": frozenset({"x", "y", "z", "color", "size", "series"}),
    "area": frozenset({"x", "y", "color", "series"}),
    "bar": frozenset({"x", "y", "color", "series"}),
    "histogram": frozenset({"x", "color", "series"}),
    "heatmap": frozenset({"x", "y", "color"}),
    "quiver": frozenset({"x", "y", "z", "u", "v", "w", "color"}),
    "function": frozenset({"x", "y", "z", "series"}),
}
#: Mark types each coordinate system supports.
COORDINATE_SYSTEMS: dict[str, frozenset[str]] = {
    "cartesian2d": frozenset(MARK_TYPES),
    "cartesian3d": frozenset({"line", "scatter", "quiver", "function"}),
    "polar": frozenset({"line", "scatter", "area", "bar", "function"}),
}

_BASE_KEYS = ("type", "data", "encoding", "role", "name", "style")


def _p(path: str, key: str) -> str:
    return ptr_join(path, key)


def mark_to_dict(m: Mark) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(m):
        v = getattr(m, f.name)
        if v is None:
            continue
        if f.name == "encoding":
            v = {ch: Encoding(field=e) if isinstance(e, str) else e for ch, e in v.items()}
            v = {
                ch: ({"field": e.field, "title": e.title} if e.title is not None
                     else {"field": e.field})
                for ch, e in v.items()
            }
        elif isinstance(v, dict):
            v = {k: (list(x) if isinstance(x, (list, tuple)) else x) for k, x in v.items()}
        elif isinstance(v, (list, tuple)):
            v = list(v)
        if f.name in _CANON:
            v = _CANON[f.name](v)
        out[f.name] = v
    return out


def mark_from_dict(d: Any, path: str) -> Mark:
    d = expect_dict(d, path, "a layer")
    if "type" not in d:
        raise fail(ptr_join(path, "type"), "missing_field",
                   f"a layer needs a 'type'; one of {', '.join(MARK_TYPES)}.")
    mtype = expect_str(d["type"], ptr_join(path, "type"), "type")
    cls = MARK_TYPES.get(mtype)
    if cls is None:
        raise fail(
            ptr_join(path, "type"), "unknown_mark_type",
            f"unknown mark type {mtype!r}.{suggest(mtype, MARK_TYPES)} "
            f"Supported: {', '.join(MARK_TYPES)}.",
        )
    allowed = [f.name for f in fields(cls)]
    check_keys(d, path, f"a {mtype} mark", allowed)

    kw: dict[str, Any] = {}
    if "data" in d:
        kw["data"] = expect_str(d["data"], ptr_join(path, "data"), "data", optional=True)
    enc_in = expect_dict(d.get("encoding", {}), ptr_join(path, "encoding"), "encoding")
    enc: dict[str, Encoding] = {}
    for ch, e in enc_in.items():
        ep = ptr_join(path, "encoding", ch)
        if isinstance(e, str):  # shorthand {"y": "col"}; normalised to {"field": "col"}
            enc[ch] = Encoding(field=e)
            continue
        e = expect_dict(e, ep, f"encoding {ch!r}")
        check_keys(e, ep, f"encoding {ch!r}", ("field", "title"), ("field",))
        enc[ch] = Encoding(
            field=expect_str(e["field"], ptr_join(ep, "field"), "field"),
            title=expect_str(e.get("title"), ptr_join(ep, "title"), "title", optional=True),
        )
    kw["encoding"] = enc
    for key in ("role", "name"):
        if key in d:
            kw[key] = expect_str(d[key], ptr_join(path, key), key, optional=(key == "name"))
    if "style" in d:
        sp = ptr_join(path, "style")
        kw["style"] = copy_json(expect_dict(d["style"], sp, "style"), sp, "style")

    if cls is BarMark and "orientation" in d:
        kw["orientation"] = expect_str(d["orientation"], _p(path, "orientation"), "orientation")
    elif cls is HistogramMark:
        if "bins" in d:
            kw["bins"] = expect_int(d["bins"], _p(path, "bins"), "bins", optional=True)
        if "bin_edges" in d and d["bin_edges"] is not None:
            edges = expect_list(d["bin_edges"], _p(path, "bin_edges"), "bin_edges")
            kw["bin_edges"] = [
                expect_number(x, ptr_join(path, "bin_edges", i), "bin edge")
                for i, x in enumerate(edges)
            ]
        if "density" in d:
            kw["density"] = expect_bool(d["density"], _p(path, "density"), "density")
    elif cls is QuiverMark:
        if "scale" in d:
            kw["scale"] = expect_number(d["scale"], _p(path, "scale"), "scale", optional=True)
        if "normalize" in d:
            kw["normalize"] = expect_bool(d["normalize"], _p(path, "normalize"), "normalize")
    elif cls is FunctionMark:
        _parse_function_fields(d, path, kw)
    return cls(**kw)


def _parse_function_fields(d: dict[str, Any], path: str, kw: dict[str, Any]) -> None:
    if "expression" in d:
        kw["expression"] = expect_str(d["expression"], _p(path, "expression"), "expression", True)
    if "variables" in d:
        vs = expect_list(d["variables"], _p(path, "variables"), "variables")
        kw["variables"] = [
            expect_str(v, ptr_join(path, "variables", i), "variable") for i, v in enumerate(vs)
        ]
    if "domain" in d:
        dom = expect_dict(d["domain"], _p(path, "domain"), "domain")
        out = {}
        for var, rng in dom.items():
            rp = ptr_join(path, "domain", var)
            rng = expect_list(rng, rp, f"domain of {var!r}")
            if len(rng) != 2:
                raise fail(rp, "invalid_domain", f"domain of {var!r} must be [min, max].")
            out[var] = [
                expect_number(x, ptr_join(rp, i), "domain bound") for i, x in enumerate(rng)
            ]
        kw["domain"] = out
    if "samples" in d:
        kw["samples"] = expect_int(d["samples"], _p(path, "samples"), "samples")
    if "parameters" in d:
        params = expect_dict(d["parameters"], _p(path, "parameters"), "parameters")
        kw["parameters"] = {
            k: expect_number(v, ptr_join(path, "parameters", k), "parameter")
            for k, v in params.items()
        }
