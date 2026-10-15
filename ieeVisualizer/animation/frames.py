"""Turn an AnimationSpec into frames: one FigureSpec per distinct state.

Renderer-neutral (no matplotlib). :func:`plan` returns a :class:`FramePlan`:
``states[i].spec`` is the complete static FigureSpec of state *i* (what a
still of that moment is), ``frames[k]`` the state shown in output frame *k*
(hold frames and ``frames_per_value`` repeat a state, they never re-render it).

Missing data stays missing: a frame-column frame keeps rows whose values are
null (the renderer draws a gap). A layer with no complete row in a frame is
left out of that frame (``omitted_layers``, plus a ``frame_layer_empty``
issue) instead of being drawn as zero; a frame where no layer has data has
``spec=None`` and renderers draw an explicit "no data" frame.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from ..spec import (
    REQUIRED_3D,
    REQUIRED_CHANNELS,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    ValidationIssue,
)
from .limits import frame_limit_issues, frame_mapping, poster_index, sweep_state_count
from .spec import AnimationSpec, FrameColumn, MarkTarget, ParameterSweep, TemplateTarget


@dataclass(frozen=True)
class FrameState:
    index: int
    value: Any
    label: str
    spec: FigureSpec | None
    omitted_layers: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = {"index": self.index, "value": self.value, "label": self.label,
             "empty": self.spec is None}
        if self.omitted_layers:
            d["omitted_layers"] = list(self.omitted_layers)
        return d


@dataclass
class FramePlan:
    states: list[FrameState]
    frames: list[int]
    fps: int
    poster: int
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def duration_s(self) -> float:
        return len(self.frames) / self.fps

    def state_of(self, frame: int) -> FrameState:
        return self.states[self.frames[frame]]


# --------------------------------------------------------------------------
# Sweep values
# --------------------------------------------------------------------------


def _ease(t: float, easing: str) -> float:
    return t * t * (3.0 - 2.0 * t) if easing == "smooth" else t


def sweep_values(tl: ParameterSweep, fps: int) -> list[float]:
    """The parameter value of each sweep state, endpoints included.

    Builds the whole list: check :func:`~ieeVisualizer.animation.limits.sweep_state_count`
    against the frame guards first (validation and :func:`plan` do)."""
    if tl.values is not None:
        return list(tl.values)
    n = sweep_state_count(tl, fps)
    return [tl.start + (tl.end - tl.start) * _ease(i / (n - 1), tl.easing) for i in range(n)]


def template_params(target: TemplateTarget, parameter: str, value: float) -> dict[str, Any]:
    """The template's params with ``parameter`` (``name`` or ``map.key``) set."""
    params = copy.deepcopy(target.params)
    if "." in parameter:
        name, key = parameter.split(".", 1)
        inner = dict(params.get(name) or {})
        inner[key] = value
        params[name] = inner
    else:
        params[parameter] = value
    return params


# --------------------------------------------------------------------------
# Frame column values
# --------------------------------------------------------------------------


def _key(value: Any, dtype: str | None) -> Any:
    if dtype == "datetime":
        d = _dt.datetime.fromisoformat(value)
        return d if d.tzinfo is None else d.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return value


def frame_keys(snapshot: DataSnapshot, column: str) -> tuple[list[Any], list[Any], int]:
    """``(keys, values, n_null)``: distinct non-null values of ``column`` in
    frame order (numbers and datetimes ascending, anything else by first
    appearance), their stored form, and how many rows have no frame value."""
    col = snapshot.columns[column]
    seen: dict[Any, Any] = {}
    nulls = 0
    for v in col.values:
        if v is None:
            nulls += 1
            continue
        seen.setdefault(_key(v, col.dtype), v)
    keys = list(seen)
    if col.dtype in ("datetime", "float", "int") or (
            col.dtype is None and all(isinstance(k, (int, float)) for k in keys)):
        keys.sort()
    return keys, [seen[k] for k in keys], nulls


def _show(v: Any) -> str:
    if isinstance(v, str) and v.endswith("T00:00:00"):
        return v[:-9]
    if isinstance(v, float):
        return format(v, ".4g")
    return str(v)


def _complete_rows(spec: FigureSpec, mark, snap: DataSnapshot) -> bool:
    req = set(REQUIRED_CHANNELS.get(mark.type, ()))
    if spec.coordinate_system == "cartesian3d":
        req |= set(REQUIRED_3D.get(mark.type, ()))
    cols = [snap.columns[mark.encoding[ch].field].values for ch in req if ch in mark.encoding]
    n = max(snap.lengths().values(), default=0)
    return any(all(c[i] is not None for c in cols) for i in range(n))


def _frame_spec(base: FigureSpec, tl: FrameColumn, keys: list[Any], k: int
                ) -> tuple[FigureSpec | None, tuple[int, ...]]:
    snap: DataSnapshot = base.datasets[tl.dataset]
    col = snap.columns[tl.column]
    if tl.display == "current":
        chosen = {keys[k]}
    elif tl.display == "cumulative":
        chosen = set(keys[:k + 1])
    else:
        chosen = set(keys[max(0, k - tl.window + 1):k + 1])
    rows = [i for i, v in enumerate(col.values) if v is not None and _key(v, col.dtype) in chosen]
    columns = {name: dataclasses.replace(c, values=[c.values[i] for i in rows])
               for name, c in snap.columns.items()}
    sub = DataSnapshot(columns=columns, provenance=snap.provenance)
    datasets = dict(base.datasets)
    layers, omitted = [], []
    for i, m in enumerate(base.layers):
        if m.data == tl.dataset and (not rows or not _complete_rows(base, m, sub)):
            omitted.append(i)
        else:
            layers.append(m)
    if rows:
        datasets[tl.dataset] = sub
    else:
        del datasets[tl.dataset]
    if not layers:
        return None, tuple(omitted)
    return dataclasses.replace(base, datasets=datasets, layers=layers), tuple(omitted)


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


def _clone(spec: FigureSpec) -> FigureSpec:
    return FigureSpec.from_dict(spec.to_dict())


def _labelled(spec: FigureSpec | None, anim: AnimationSpec, label: str) -> FigureSpec | None:
    if spec is None or not anim.label_frames:
        return spec
    base = spec.title or anim.title
    return dataclasses.replace(spec, title=f"{base} ({label})" if base else label)


def count_states(anim: AnimationSpec, snapshot: DataSnapshot | None = None) -> int:
    """Distinct states (not output frames), without building them."""
    tl = anim.timeline
    if isinstance(tl, ParameterSweep):
        return sweep_state_count(tl, anim.fps)
    return len(frame_keys(snapshot, tl.column)[0])


def resolved_frame_keys(spec: FigureSpec, tl: FrameColumn) -> tuple[list[Any], list[Any]]:
    """``(keys, values)`` of the frame column in a resolved figure; raises
    :class:`~ieeVisualizer.spec.SpecValidationError` (``unknown_column``,
    ``no_frames``) when the resolved data cannot drive the timeline."""
    from ..spec import SpecValidationError

    snap = spec.datasets.get(tl.dataset)
    if not isinstance(snap, DataSnapshot) or tl.column not in snap.columns:
        raise SpecValidationError([ValidationIssue(
            "/timeline/column", "unknown_column",
            f"the resolved dataset {tl.dataset!r} has no column {tl.column!r}.")])
    keys, values, _ = frame_keys(snap, tl.column)
    if not keys:
        raise SpecValidationError([ValidationIssue(
            "/timeline/column", "no_frames",
            f"column {tl.column!r} of the resolved data has no non-null value.")])
    return keys, values


def _build_template(tl: ParameterSweep, i: int, v: float) -> FigureSpec:
    """The template's figure at sweep state ``i``; a refusal is reported at
    the sweep value (``/timeline/values/<i>``, or ``/timeline`` for an
    interpolated sweep) naming the template path, never as a bare
    template-internal path."""
    from .. import templates
    from ..spec import SpecError, SpecValidationError

    t = tl.target
    try:
        return templates.get(t.name, t.version).build(t.data, **template_params(t, tl.parameter, v))
    except SpecError as e:
        at = f"/timeline/values/{i}" if tl.values is not None else "/timeline"
        raise SpecValidationError([
            ValidationIssue(at, x.code, f"at {tl.parameter} = {v:g} (state {i}), template "
                            f"{t.name!r} {x.path or '/'}: {x.message}", x.severity)
            for x in getattr(e, "issues", []) if x.severity == "error"
        ] or [ValidationIssue(at, "template_build_failed",
                              f"at {tl.parameter} = {v:g} (state {i}): {e}")]) from None


def _guard(anim: AnimationSpec, n_states: int, resolved: bool) -> None:
    from ..spec import SpecValidationError

    errors = [i for i in frame_limit_issues(anim, n_states, resolved=resolved)
              if i.severity == "error"]
    if errors:
        raise SpecValidationError(errors)


def plan(anim: AnimationSpec, resolver=None) -> FramePlan:
    """Build every state's FigureSpec. The spec must validate
    (:func:`~ieeVisualizer.animation.validate_animation`); callers check first.

    The frame, duration and poster guards are checked again here, from the
    state count alone, before any value, state or frame list is built
    (:class:`~ieeVisualizer.spec.SpecValidationError` if they fail)."""
    tl = anim.timeline
    if isinstance(tl, ParameterSweep):
        _guard(anim, sweep_state_count(tl, anim.fps), resolved=False)
    issues: list[ValidationIssue] = []
    states: list[FrameState] = []
    base = None
    if anim.figure is not None:
        from ..render.prepare import prepare

        base = prepare(anim.figure, resolver).spec  # references resolved once
    if isinstance(tl, ParameterSweep):
        for i, v in enumerate(sweep_values(tl, anim.fps)):
            label = f"{tl.parameter} = {format(v, '.4g')}"
            if isinstance(tl.target, MarkTarget):
                spec = _clone(base)
                mark = spec.layers[tl.target.layer]
                assert isinstance(mark, FunctionMark)
                mark.parameters = {**mark.parameters, tl.parameter: v}
            else:
                spec = _build_template(tl, i, v)
            states.append(FrameState(i, v, label, _labelled(spec, anim, label)))
    else:
        keys, values = resolved_frame_keys(base, tl)
        _guard(anim, len(keys), resolved=True)
        for k, v in enumerate(values):
            if tl.display == "current":
                label = f"{tl.column}: {_show(v)}"
            elif tl.display == "cumulative":
                label = f"{tl.column}: through {_show(v)}"
            else:
                first = values[max(0, k - tl.window + 1)]
                label = f"{tl.column}: {_show(first)} to {_show(v)}"
            spec, omitted = _frame_spec(base, tl, keys, k)
            for i in omitted:
                issues.append(ValidationIssue(
                    f"/figure/layers/{i}", "frame_layer_empty",
                    f"frame {k} ({label}): layer {i} has no complete row, so it is left out of "
                    "this frame rather than drawn as zero.", "warning"))
            states.append(FrameState(k, v, label, _labelled(spec, anim, label), omitted))
    frames = frame_mapping(anim, len(states))
    return FramePlan(states=states, frames=frames, fps=anim.fps,
                     poster=poster_index(anim, len(frames)), issues=issues)
