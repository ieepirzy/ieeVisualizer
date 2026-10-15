"""AnimationSpec: a FigureSpec plus a timeline (a time-varying sequence).

Renderer-neutral and JSON-serializable, like :mod:`ieeVisualizer.spec`.
Two timelines exist:

* :class:`ParameterSweep` varies one number: a ``parameters`` entry of a
  ``function`` mark of :attr:`AnimationSpec.figure` (target ``{"kind": "mark",
  "layer": i}``), or a numeric parameter of a registered template (target
  ``{"kind": "template", "name", "version", "params", "data"}``; the figure is
  rebuilt from the template for every value and :attr:`AnimationSpec.figure`
  must be absent). Values are either listed (``values``) or interpolated from
  ``start`` to ``end`` over ``duration`` seconds at ``fps`` with ``easing``.
* :class:`FrameColumn` takes the distinct values of a dataset column (e.g. a
  week) in order; frame *k* shows the rows of value *k* (``display="current"``),
  every row up to it (``"cumulative"``) or the last ``window`` values
  (``"window"``).

See ``docs/ANIMATION.md``.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Union

from ..spec import FigureSpec, SpecParseError, ValidationIssue
from ..spec._common import (
    canon_float,
    canonical_json,
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
    sha256_of,
    suggest,
)

#: Version of the AnimationSpec format (independent of the FigureSpec schema).
ANIMATION_SCHEMA_VERSION = "1.0"
EASINGS = ("linear", "smooth")
DISPLAYS = ("current", "cumulative", "window")
AXIS_LIMIT_MODES = ("fixed", "auto")

#: Guards. A render refuses more frames, a longer duration or a higher rate.
MAX_FRAMES = 300
MAX_DURATION_S = 60.0
MAX_FPS = 60
#: Annotations on an animated figure. Every annotation is drawn again in
#: every state (a text annotation costs about 8 ms per state with Matplotlib),
#: so an animation refuses more (``too_many_annotations``, at validation,
#: before any state is built). A still has its own, separate limits.
MAX_ANIMATION_ANNOTATIONS = 200


@dataclass
class MarkTarget:
    """Sweep a ``parameters`` entry of ``figure.layers[layer]`` (a function mark)."""

    layer: int = 0
    kind: str = field(default="mark", init=False)


@dataclass
class TemplateTarget:
    """Sweep a numeric parameter of a registered template. ``parameter`` on
    the sweep is a top-level ``number`` parameter or ``map.key`` of a
    ``number_map`` parameter (e.g. ``constants.k``)."""

    name: str = ""
    version: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    data: Any = None
    kind: str = field(default="template", init=False)


@dataclass
class ParameterSweep:
    parameter: str
    target: MarkTarget | TemplateTarget = field(default_factory=MarkTarget)
    values: list[float] | None = None
    start: float | None = None
    end: float | None = None
    duration: float | None = None
    easing: str = "linear"
    type: str = field(default="parameter_sweep", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.values, (list, tuple)):
            self.values = [canon_float(v) for v in self.values]
        for k in ("start", "end", "duration"):
            setattr(self, k, canon_float(getattr(self, k)))


@dataclass
class FrameColumn:
    dataset: str
    column: str
    display: str = "current"
    window: int | None = None
    frames_per_value: int = 1
    type: str = field(default="frame_column", init=False)


Timeline = Union[ParameterSweep, FrameColumn]


@dataclass
class AnimationSpec:
    """A figure that changes over a timeline.

    ``hold_start``/``hold_end`` repeat the first/last state for that many
    frames. ``axis_limits="fixed"`` asks the renderer to keep every axis range
    constant over the animation (the union of the per-frame ranges) unless the
    figure sets limits; ``"auto"`` lets each frame scale itself.
    ``label_frames`` appends the frame's state (``phase = 1.57``, ``week:
    2026-09-07``) to the title. ``poster`` names the frame exported as a still
    (``"first"``, ``"last"`` or an index).
    """

    timeline: Timeline
    figure: FigureSpec | None = None
    fps: int = 15
    hold_start: int = 0
    hold_end: int = 0
    axis_limits: str = "fixed"
    label_frames: bool = True
    poster: str | int = "last"
    loop: bool = True
    id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ANIMATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    def to_json(self, indent: int | None = None) -> str:
        return to_json(self, indent)

    def validate(self) -> list[ValidationIssue]:
        from .validate import validate_animation

        return validate_animation(self)


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


def _target_to_dict(t: MarkTarget | TemplateTarget) -> dict[str, Any]:
    if isinstance(t, MarkTarget):
        return {"kind": "mark", "layer": t.layer}
    d = {"kind": "template", "name": t.name, "version": t.version,
         "params": copy.deepcopy(t.params)}
    if t.data is not None:
        d["data"] = _data_to_json(t.data)
    return d


def _data_to_json(data: Any) -> Any:
    from ..spec import DataSnapshot
    from ..spec.data import dataset_to_dict

    return dataset_to_dict(data) if isinstance(data, DataSnapshot) else copy.deepcopy(data)


def timeline_to_dict(t: Timeline) -> dict[str, Any]:
    if isinstance(t, FrameColumn):
        d = {"type": t.type, "dataset": t.dataset, "column": t.column, "display": t.display,
             "frames_per_value": t.frames_per_value}
        if t.window is not None:
            d["window"] = t.window
        return d
    d = {"type": t.type, "parameter": t.parameter, "target": _target_to_dict(t.target),
         "easing": t.easing}
    for k in ("values", "start", "end", "duration"):
        v = getattr(t, k)
        if v is not None:
            d[k] = [canon_float(x) for x in v] if k == "values" else canon_float(v)
    return d


def to_dict(anim: AnimationSpec) -> dict[str, Any]:
    d: dict[str, Any] = {
        "kind": "animation",
        "schema_version": anim.schema_version,
        "timeline": timeline_to_dict(anim.timeline),
        "fps": anim.fps,
        "hold_start": anim.hold_start,
        "hold_end": anim.hold_end,
        "axis_limits": anim.axis_limits,
        "label_frames": anim.label_frames,
        "poster": anim.poster,
        "loop": anim.loop,
        "metadata": copy.deepcopy(anim.metadata),
    }
    if anim.figure is not None:
        d["figure"] = anim.figure.to_dict()
    for k in ("id", "title"):
        if getattr(anim, k) is not None:
            d[k] = getattr(anim, k)
    return d


def to_json(anim: AnimationSpec, indent: int | None = None) -> str:
    """Canonical JSON (sorted keys, compact) when ``indent`` is None."""
    return canonical_json(to_dict(anim), indent=indent)


def animation_hash(anim: AnimationSpec) -> str:
    """``sha256:<hex>`` of the canonical JSON (figure and inline data included)."""
    return sha256_of(to_dict(anim))


_KEYS = ("kind", "schema_version", "timeline", "figure", "fps", "hold_start", "hold_end",
         "axis_limits", "label_frames", "poster", "loop", "id", "title", "metadata")


def _target_from_dict(d: Any, p: str) -> MarkTarget | TemplateTarget:
    d = expect_dict(d, p, "a sweep target")
    kind = d.get("kind")
    if kind == "mark":
        check_keys(d, p, "a mark target", ("kind", "layer"))
        return MarkTarget(layer=expect_int(d.get("layer", 0), p + "/layer", "layer"))
    if kind == "template":
        check_keys(d, p, "a template target", ("kind", "name", "version", "params", "data"),
                   required=("name", "version"))
        return TemplateTarget(
            name=expect_str(d["name"], p + "/name", "name"),
            version=expect_str(d["version"], p + "/version", "version"),
            params=copy_json(expect_dict(d.get("params", {}), p + "/params", "params"),
                             p + "/params", "params"),
            data=copy_json(d["data"], p + "/data", "data") if d.get("data") is not None
            else None)
    raise fail(p + "/kind", "unknown_target_kind",
               f"sweep target kind {kind!r} is unknown; use 'mark' or 'template'.")


def timeline_from_dict(d: Any, p: str = "/timeline") -> Timeline:
    d = expect_dict(d, p, "a timeline")
    ttype = d.get("type")
    if ttype == "frame_column":
        check_keys(d, p, "a frame_column timeline",
                   ("type", "dataset", "column", "display", "window", "frames_per_value"),
                   required=("dataset", "column"))
        return FrameColumn(
            dataset=expect_str(d["dataset"], p + "/dataset", "dataset"),
            column=expect_str(d["column"], p + "/column", "column"),
            display=expect_str(d.get("display", "current"), p + "/display", "display"),
            window=expect_int(d.get("window"), p + "/window", "window", optional=True),
            frames_per_value=expect_int(d.get("frames_per_value", 1),
                                        p + "/frames_per_value", "frames_per_value"))
    if ttype == "parameter_sweep":
        check_keys(d, p, "a parameter_sweep timeline",
                   ("type", "parameter", "target", "values", "start", "end", "duration",
                    "easing"), required=("parameter",))
        values = None
        if d.get("values") is not None:
            values = [expect_number(v, ptr_join(p, "values", i), "a sweep value")
                      for i, v in enumerate(expect_list(d["values"], p + "/values", "values"))]
        num = {k: expect_number(d.get(k), p + "/" + k, k, optional=True)
               for k in ("start", "end", "duration")}
        return ParameterSweep(
            parameter=expect_str(d["parameter"], p + "/parameter", "parameter"),
            target=_target_from_dict(d.get("target", {"kind": "mark", "layer": 0}),
                                     p + "/target"),
            values=values, easing=expect_str(d.get("easing", "linear"), p + "/easing", "easing"),
            **num)
    raise fail(p + "/type", "unknown_timeline_type",
               f"timeline type {ttype!r} is unknown."
               f"{suggest(str(ttype), ('parameter_sweep', 'frame_column'))} "
               "Use 'parameter_sweep' or 'frame_column'.")


def from_dict(d: Any) -> AnimationSpec:
    d = expect_dict(d, "", "an AnimationSpec")
    if d.get("kind") != "animation":
        raise fail("/kind", "not_an_animation",
                   "an AnimationSpec has \"kind\": \"animation\"; a plain FigureSpec is a "
                   "snapshot and is read with ieeVisualizer.spec.from_dict.")
    if d.get("schema_version") != ANIMATION_SCHEMA_VERSION:
        raise fail("/schema_version", "unsupported_schema_version",
                   f"animation schema_version {d.get('schema_version')!r} is not supported "
                   f"(supports {ANIMATION_SCHEMA_VERSION!r}); it is not reinterpreted.")
    check_keys(d, "", "an AnimationSpec", _KEYS, required=("timeline",))
    poster = d.get("poster", "last")
    if not isinstance(poster, str):
        poster = expect_int(poster, "/poster", "poster")
    figure = None
    if d.get("figure") is not None:
        try:
            figure = FigureSpec.from_dict(d["figure"])
        except SpecParseError as e:
            raise SpecParseError([ValidationIssue("/figure" + i.path, i.code, i.message,
                                                  i.severity) for i in e.issues]) from None
    return AnimationSpec(
        timeline=timeline_from_dict(d["timeline"]),
        figure=figure,
        fps=expect_int(d.get("fps", 15), "/fps", "fps"),
        hold_start=expect_int(d.get("hold_start", 0), "/hold_start", "hold_start"),
        hold_end=expect_int(d.get("hold_end", 0), "/hold_end", "hold_end"),
        axis_limits=expect_str(d.get("axis_limits", "fixed"), "/axis_limits", "axis_limits"),
        label_frames=expect_bool(d.get("label_frames", True), "/label_frames", "label_frames"),
        poster=poster,
        loop=expect_bool(d.get("loop", True), "/loop", "loop"),
        id=expect_str(d.get("id"), "/id", "id", optional=True),
        title=expect_str(d.get("title"), "/title", "title", optional=True),
        metadata=copy_json(expect_dict(d.get("metadata", {}), "/metadata", "metadata"),
                           "/metadata", "metadata"),
    )


def from_json(s: str) -> AnimationSpec:
    def refuse(name: str) -> Any:
        raise fail("", "non_finite_number", f"the JSON text contains {name}, which is not a "
                   "JSON number.")

    try:
        d = json.loads(s, parse_constant=refuse)
    except json.JSONDecodeError as e:
        raise fail("", "invalid_json", f"not valid JSON: {e}") from None
    return from_dict(d)
