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

from dataclasses import field, fields
from typing import Any

from ._common import (
    SpecParseError,
    canon_float,
    canon_int,
    expect_dict,
    expect_list,
    expect_number,
    expect_str,
    fail,
    ptr,
    ptr_join,
    suggest,
)
from ._model import (
    Bool,
    Int,
    JsonObject,
    OptInt,
    OptNumber,
    OptStr,
    SpecModel,
    Str,
    WireShape,
    at,
    model,
    parse_array,
    parse_model,
    parse_object,
    wire,
)

CHANNELS = ("x", "y", "z", "u", "v", "w", "color", "size", "series")
ROLES = ("observed", "forecast", "reference")
ORIENTATIONS = ("vertical", "horizontal")


@model
class Encoding(SpecModel):
    """Maps one channel to one column. ``title`` overrides the column name in
    legends/colorbars."""

    __wire__ = WireShape("encoding {name!r}", required=("field",))

    field: Str("field")
    title: OptStr("title") = None


def _parse_encoding(v: Any) -> dict[str, Encoding]:
    # Shorthand {"y": "col"} is normalised to {"field": "col"}.
    return parse_object(v, "encoding", lambda ch, e: Encoding(field=e) if isinstance(e, str)
                        else parse_model(Encoding, e, ch))


@model
class Mark(SpecModel):
    """Base mark. Use a concrete subclass; constructing ``Mark`` directly with
    an unrecognised ``type`` is allowed so :func:`validate` can report it."""

    __wire__ = WireShape("a {name} mark", drop=("type",))

    type: Str("type")
    data: OptStr("data") = None
    encoding: wire(_parse_encoding, "dict[str, Encoding]") = field(default_factory=dict)
    role: Str("role") = "observed"
    name: OptStr("name") = None
    style: JsonObject("style") = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ergonomics: encoding={"y": "revenue"} is accepted from Python.
        self.encoding = {
            ch: Encoding(field=e) if isinstance(e, str) else e for ch, e in self.encoding.items()
        }
        for name, canon in _CANON.items():
            v = getattr(self, name, None)
            if v is not None:
                try:
                    setattr(self, name, canon(v, "/" + name, f"{self.type} mark {name}"))
                except SpecParseError:
                    raise  # number_out_of_range, already structured (path relative to the mark)
                except ValueError as e:
                    raise ValueError(f"{self.type} mark {name}: {e}") from None


@model
class LineMark(Mark):
    type: Str("type") = field(default="line", init=False)


@model
class ScatterMark(Mark):
    type: Str("type") = field(default="scatter", init=False)


@model
class AreaMark(Mark):
    """Filled area between ``y`` and zero."""

    type: Str("type") = field(default="area", init=False)


@model
class BarMark(Mark):
    """Channels map literally to axes: a horizontal bar chart puts the
    categories on ``y`` and the values on ``x``."""

    type: Str("type") = field(default="bar", init=False)
    orientation: Str("orientation") = "vertical"


def _parse_bin_edges(v: Any) -> list[float] | None:
    if v is None:
        return None
    return parse_array(v, "bin_edges", lambda x: expect_number(x, "", "bin edge"))


@model
class HistogramMark(Mark):
    """Bins the raw values of ``x``; the y axis is a count (or density)."""

    type: Str("type") = field(default="histogram", init=False)
    bins: OptInt("bins") = None
    bin_edges: wire(_parse_bin_edges, "list[float] | None") = None
    density: Bool("density") = False


@model
class HeatmapMark(Mark):
    """Long-format grid: one row per cell with ``x``, ``y`` and the cell value
    on ``color``."""

    type: Str("type") = field(default="heatmap", init=False)


@model
class QuiverMark(Mark):
    """Arrows at (``x``, ``y``[, ``z``]) with components (``u``, ``v``[, ``w``])."""

    type: Str("type") = field(default="quiver", init=False)
    scale: OptNumber("scale") = None
    normalize: Bool("normalize") = False


def _parse_variables(v: Any) -> list[str]:
    return parse_array(v, "variables", lambda x: expect_str(x, "", "variable"))


def _parse_range(var: str, rng: Any) -> list[float]:
    rng = expect_list(rng, "", f"domain of {var!r}")
    if len(rng) != 2:
        raise fail("", "invalid_domain", f"domain of {var!r} must be [min, max].")
    return [expect_number(x, ptr(i), "domain bound") for i, x in enumerate(rng)]


def _parse_domain(v: Any) -> dict[str, list[float]]:
    return parse_object(v, "domain", _parse_range)


def _parse_parameters(v: Any) -> dict[str, float]:
    return parse_object(v, "parameters", lambda k, x: expect_number(x, "", "parameter"))


@model
class FunctionMark(Mark):
    """Symbolic ``expression`` over ``variables`` on ``domain``, or a sampled
    reference curve via ``data`` + ``encoding``."""

    type: Str("type") = field(default="function", init=False)
    expression: OptStr("expression") = None
    variables: wire(_parse_variables, "list[str]") = field(default_factory=lambda: ["x"])
    domain: wire(_parse_domain, "dict[str, list[float]]") = field(default_factory=dict)
    samples: Int("samples") = 200
    parameters: wire(_parse_parameters, "dict[str, float]") = field(default_factory=dict)


def _canon_float_list(v: Any, path: str = "", what: str = "value") -> Any:
    if not isinstance(v, (list, tuple)):
        return v
    return [canon_float(x, ptr_join(path, i), what) for i, x in enumerate(v)]


def _canon_float_map(v: Any, path: str = "", what: str = "value") -> Any:
    if not isinstance(v, dict):
        return v
    return {k: canon_float(x, ptr_join(path, k), what) for k, x in v.items()}


def _canon_domain(v: Any, path: str = "", what: str = "value") -> Any:
    if not isinstance(v, dict):
        return v
    return {k: _canon_float_list(r, ptr_join(path, k), what) for k, r in v.items()}


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
            v = _CANON[f.name](v, "/" + f.name, f"{m.type} mark {f.name}")
        out[f.name] = v
    return out


def mark_from_dict(d: Any, path: str) -> Mark:
    """Parse a serialized layer; errors carry pointers under ``path``."""
    return at(path, _parse_mark, d)


def _parse_mark(d: Any) -> Mark:
    d = expect_dict(d, "", "a layer")
    if "type" not in d:
        raise fail("/type", "missing_field",
                   f"a layer needs a 'type'; one of {', '.join(MARK_TYPES)}.")
    mtype = expect_str(d["type"], "/type", "type")
    cls = MARK_TYPES.get(mtype)
    if cls is None:
        raise fail(
            "/type", "unknown_mark_type",
            f"unknown mark type {mtype!r}.{suggest(mtype, MARK_TYPES)} "
            f"Supported: {', '.join(MARK_TYPES)}.",
        )
    return parse_model(cls, d, mtype)
