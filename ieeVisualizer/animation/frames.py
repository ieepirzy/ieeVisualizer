"""Turn an AnimationSpec into frames: one FigureSpec per distinct state.

Renderer-neutral. :func:`plan` returns a :class:`FramePlan`: ``states[i].spec``
is the static FigureSpec of state *i* (its still), ``frames[k]`` the state
shown in output frame *k* (holds and ``frames_per_value`` repeat a state).
Missing data stays missing: nulls stay null (a gap), a layer with no complete
row in a frame is left out of it (``frame_layer_empty``), a frame with no
drawable layer has ``spec=None`` ("no data" frame).

Per-state work is only what the state changes: undrawn datasets are dropped
once, a mark sweep replaces only the swept layer (data is shared), a frame
column reads each row's frame value once (:class:`FrameIndex`). A deadline
is checked before every state.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np

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


@dataclass(frozen=True)
class FrameIndex:
    """A frame column read once: ``keys`` (distinct non-null values in frame
    order: numbers and datetimes ascending, anything else by first
    appearance), ``values`` (their stored form), ``row_key[i]`` (the position
    of row *i*'s value in ``keys``, -1 for null) and ``nulls``."""

    keys: list[Any]
    values: list[Any]
    row_key: np.ndarray
    nulls: int

    def counts(self) -> list[int]:
        """Rows per key, in key order."""
        return np.bincount(self.row_key[self.row_key >= 0], minlength=len(self.keys)).tolist()

    def rows(self, tl: FrameColumn, k: int) -> np.ndarray:
        """Row indices shown in state ``k`` (in data order)."""
        lo = {"current": k, "cumulative": 0}.get(tl.display, k - (tl.window or 1) + 1)
        return np.flatnonzero((self.row_key >= max(lo, 0)) & (self.row_key <= k))


def frame_index(snapshot: DataSnapshot, column: str) -> FrameIndex:
    col = snapshot.columns[column]
    first: dict[Any, int] = {}
    raw = []
    for v in col.values:
        if v is None:
            raw.append(None)
            continue
        k = _key(v, col.dtype)
        first.setdefault(k, len(first))
        raw.append(k)
    keys = list(first)
    stored = {}
    for v, k in zip(col.values, raw):
        if k is not None:
            stored.setdefault(k, v)
    if col.dtype in ("datetime", "float", "int") or (
            col.dtype is None and all(isinstance(k, (int, float)) for k in keys)):
        keys.sort()
    pos = {k: i for i, k in enumerate(keys)}
    row_key = np.fromiter((-1 if k is None else pos[k] for k in raw), dtype=np.int64,
                          count=len(raw))
    return FrameIndex(keys, [stored[k] for k in keys], row_key, raw.count(None))


def frame_keys(snapshot: DataSnapshot, column: str) -> tuple[list[Any], list[Any], int]:
    """``(keys, values, n_null)`` of :func:`frame_index`."""
    fi = frame_index(snapshot, column)
    return fi.keys, fi.values, fi.nulls


def _show(v: Any, digits: int = 4) -> str:
    if isinstance(v, str) and v.endswith("T00:00:00"):
        return v[:-9]
    if isinstance(v, float):
        return format(v, f".{digits}g")
    return str(v)


#: Significant digits of a numeric frame label, and how many more the values
#: of a colliding group get ("in most cases 3 orders of magnitude is
#: plenty"); past that, ``frame_labels_ambiguous`` says so.
LABEL_DIGITS = 4
LABEL_EXTRA_DIGITS = 3


def value_texts(values: list[Any], numbers: bool = False) -> tuple[list[str], list[list[int]]]:
    """``(text of each value, groups of states still sharing a text)``.

    Numbers (every value when ``numbers``, else floats) get
    :data:`LABEL_DIGITS` significant digits. Only the values of a group of
    distinct values that would read the same are widened: one digit at a
    time, up to :data:`LABEL_EXTRA_DIGITS` more, or, for integer-valued
    floats, printed in full; every other value keeps exactly its ``.4g``
    text. A group nothing tells apart keeps its ``.4g`` texts and is
    returned (``frame_labels_ambiguous``)."""

    def text(v, d):
        return format(v, f".{d}g") if numbers else _show(v, d)

    def full(v):  # an integer-valued number, every digit
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if isinstance(v, float) and not (v.is_integer() and abs(v) < 1e16):
            return None
        return str(int(v))

    texts = [text(v, LABEL_DIGITS) for v in values]
    groups: dict[str, list[int]] = {}
    for i, t in enumerate(texts):
        groups.setdefault(t, []).append(i)
    clash = []
    for idx in groups.values():
        if len({_value_key(values[i]) for i in idx}) < 2:
            continue
        others = {t for k, t in enumerate(texts) if k not in idx}
        candidates = [[text(values[i], d) for i in idx]
                      for d in range(LABEL_DIGITS + 1, LABEL_DIGITS + LABEL_EXTRA_DIGITS + 1)]
        if all(full(values[i]) is not None for i in idx):
            candidates.append([full(values[i]) for i in idx])
        for wide in candidates:
            if _tells_apart(wide, [values[i] for i in idx]) and not others & set(wide):
                for i, t in zip(idx, wide):
                    texts[i] = t
                break
        else:
            clash.append(idx)
    return texts, clash


def _value_key(v: Any) -> Any:
    """What makes two values the same value: numbers by number (1 and 1.0
    are one value, every nan is one), anything else by its repr."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        return ("number", "nan" if f != f else f)
    return ("other", repr(v))


def _tells_apart(texts: list[str], values: list[Any]) -> bool:
    """Distinct values get distinct texts (equal values may share one)."""
    seen: dict[str, Any] = {}
    for t, v in zip(texts, values):
        if seen.setdefault(t, _value_key(v)) != _value_key(v):
            return False
    return True


def state_labels(parameter: str, values: list[Any]) -> list[str]:
    """The label of each sweep state, ``parameter = value``: distinct values
    get distinct labels where :func:`value_texts` can tell them apart."""
    return [f"{parameter} = {t}" for t in value_texts(values, numbers=True)[0]]


#: Groups described in one ``frame_labels_ambiguous`` warning, and states
#: and values per group.
MAX_AMBIGUOUS_GROUPS = 5
MAX_AMBIGUOUS_STATES = 5


def ambiguous_labels(values: list[Any], clash: list[list[int]],
                     path: str = "/timeline") -> list[ValidationIssue]:
    """One ``frame_labels_ambiguous`` warning for the groups of distinct
    states whose labels read the same even with the extra digits (the first
    :data:`MAX_AMBIGUOUS_GROUPS` described)."""
    if not clash:
        return []

    def group(idx):
        cut = idx[:MAX_AMBIGUOUS_STATES]
        more = f" and {len(idx) - len(cut)} more" if len(idx) > len(cut) else ""
        return (f"states {cut}{more} ({', '.join(repr(values[i]) for i in cut)}"
                f"{', ...' if more else ''})")

    shown = "; ".join(group(idx) for idx in clash[:MAX_AMBIGUOUS_GROUPS])
    head = (f"{len(clash)} groups of distinct states read" if len(clash) > 1
            else "distinct states read")
    rest = f"; first {MAX_AMBIGUOUS_GROUPS}: " if len(clash) > MAX_AMBIGUOUS_GROUPS else ": "
    digits = LABEL_DIGITS + LABEL_EXTRA_DIGITS
    return [ValidationIssue(
        path, "frame_labels_ambiguous",
        f"{head} the same even with {digits} significant digits{rest}{shown}. Their frames "
        f"are labelled alike: labels tell values apart when they differ by more than about "
        f"1 part in 10^{digits - 1} of their size (relative spacing), so space the values "
        "further apart relative to their magnitude or rescale them.", "warning")]


def _complete_rows(spec: FigureSpec, mark, snap: DataSnapshot) -> bool:
    req = set(REQUIRED_CHANNELS.get(mark.type, ()))
    if spec.coordinate_system == "cartesian3d":
        req |= set(REQUIRED_3D.get(mark.type, ()))
    cols = [snap.columns[mark.encoding[ch].field].values for ch in req if ch in mark.encoding]
    n = max(snap.lengths().values(), default=0)
    return any(all(c[i] is not None for c in cols) for i in range(n))


def _subset(snap: DataSnapshot, rows: np.ndarray) -> DataSnapshot:
    """``snap`` restricted to ``rows``; the values are already normalized, so
    columns are copied without normalizing them again."""
    columns = {}
    for name, c in snap.columns.items():
        sub = copy.copy(c)
        sub.values = [c.values[i] for i in rows.tolist()]
        columns[name] = sub
    return DataSnapshot(columns=columns, provenance=snap.provenance)


def _frame_spec(base: FigureSpec, tl: FrameColumn, fi: FrameIndex, k: int
                ) -> tuple[FigureSpec | None, tuple[int, ...]]:
    rows = fi.rows(tl, k)
    sub = _subset(base.datasets[tl.dataset], rows)
    datasets = dict(base.datasets)
    layers, omitted = [], []
    for i, m in enumerate(base.layers):
        if m.data == tl.dataset and (not rows.size or not _complete_rows(base, m, sub)):
            omitted.append(i)
        else:
            layers.append(m)
    if rows.size:
        datasets[tl.dataset] = sub
    else:
        del datasets[tl.dataset]
    if not layers:
        return None, tuple(omitted)
    return dataclasses.replace(base, datasets=datasets, layers=layers), tuple(omitted)


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


def drawn_only(spec: FigureSpec) -> FigureSpec:
    """``spec`` without the datasets no layer draws (they cannot change a
    frame, but every state would carry and validate them)."""
    used = {m.data for m in spec.layers if m.data is not None}
    if set(spec.datasets) <= used:
        return spec
    return dataclasses.replace(spec, datasets={k: v for k, v in spec.datasets.items()
                                               if k in used})


def _labelled(spec: FigureSpec | None, anim: AnimationSpec, label: str) -> FigureSpec | None:
    if spec is None or not anim.label_frames:
        return spec
    base = spec.title or anim.title
    return dataclasses.replace(spec, title=f"{base} ({label})" if base else label)


def resolved_frame_index(spec: FigureSpec, tl: FrameColumn) -> FrameIndex:
    """The frame index of a resolved figure; raises
    :class:`~ieeVisualizer.spec.SpecValidationError` (``unknown_column``,
    ``no_frames``) when the resolved data cannot drive the timeline."""
    from ..spec import SpecValidationError

    snap = spec.datasets.get(tl.dataset)
    if not isinstance(snap, DataSnapshot) or tl.column not in snap.columns:
        raise SpecValidationError([ValidationIssue(
            "/timeline/column", "unknown_column",
            f"the resolved dataset {tl.dataset!r} has no column {tl.column!r}.")])
    fi = frame_index(snap, tl.column)
    if not fi.keys:
        raise SpecValidationError([ValidationIssue(
            "/timeline/column", "no_frames",
            f"column {tl.column!r} of the resolved data has no non-null value.")])
    return fi


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
    except Exception as e:  # noqa: BLE001 - a template that raises: located, structured
        at = f"/timeline/values/{i}" if tl.values is not None else "/timeline"
        raise SpecValidationError([ValidationIssue(
            at, "template_build_failed", f"at {tl.parameter} = {v:g} (state {i}): "
            f"{type(e).__name__}: {e}")]) from None


def _guard(anim: AnimationSpec, n_states: int, resolved: bool) -> None:
    from ..spec import SpecValidationError

    errors = [i for i in frame_limit_issues(anim, n_states, resolved=resolved)
              if i.severity == "error"]
    if errors:
        raise SpecValidationError(errors)


def plan(anim: AnimationSpec, resolver=None, *, deadline=None, base: FigureSpec | None = None,
         index: FrameIndex | None = None) -> FramePlan:
    """Build every state's FigureSpec. The spec must validate
    (:func:`~ieeVisualizer.animation.validate_animation`); callers check first.

    The frame, duration and poster guards are checked again here, from the
    state count alone, before any value, state or frame list is built
    (:class:`~ieeVisualizer.spec.SpecValidationError` if they fail).
    ``deadline`` is checked before every state. ``base``/``index``: the
    resolved figure and frame index when the caller already has them."""
    tl = anim.timeline
    if isinstance(tl, ParameterSweep):
        _guard(anim, sweep_state_count(tl, anim.fps), resolved=False)
    if base is None and anim.figure is not None:
        from ..render.prepare import prepare

        base = prepare(drawn_only(anim.figure), resolver).spec  # resolved once

    issues: list[ValidationIssue] = []
    states: list[FrameState] = []
    if isinstance(tl, ParameterSweep):
        values = sweep_values(tl, anim.fps)
        texts, clash = value_texts(values, numbers=True)
        issues.extend(ambiguous_labels(values, clash))
        for i, v in enumerate(values):
            if deadline is not None:
                deadline.check("plan")
            label = f"{tl.parameter} = {texts[i]}"
            if isinstance(tl.target, MarkTarget):
                mark = base.layers[tl.target.layer]
                assert isinstance(mark, FunctionMark)
                layers = list(base.layers)
                layers[tl.target.layer] = dataclasses.replace(
                    mark, parameters={**mark.parameters, tl.parameter: v})
                spec = dataclasses.replace(base, layers=layers)
            else:
                spec = _build_template(tl, i, v)
            states.append(FrameState(i, v, label, _labelled(spec, anim, label)))
            if deadline is not None:
                deadline.count("states_built")
    else:
        fi = index or resolved_frame_index(base, tl)
        _guard(anim, len(fi.keys), resolved=True)
        texts, clash = value_texts(fi.values)
        issues.extend(ambiguous_labels(fi.values, clash))
        for k, v in enumerate(fi.values):
            if deadline is not None:
                deadline.check("plan")
            if tl.display == "current":
                label = f"{tl.column}: {texts[k]}"
            elif tl.display == "cumulative":
                label = f"{tl.column}: through {texts[k]}"
            else:
                label = f"{tl.column}: {texts[max(0, k - tl.window + 1)]} to {texts[k]}"
            spec, omitted = _frame_spec(base, tl, fi, k)
            for i in omitted:
                issues.append(ValidationIssue(
                    f"/figure/layers/{i}", "frame_layer_empty",
                    f"frame {k} ({label}): layer {i} has no complete row, so it is left out of "
                    "this frame rather than drawn as zero.", "warning"))
            states.append(FrameState(k, v, label, _labelled(spec, anim, label), omitted))
            if deadline is not None:
                deadline.count("states_built")
    frames = frame_mapping(anim, len(states))
    return FramePlan(states=states, frames=frames, fps=anim.fps,
                     poster=poster_index(anim, len(frames)), issues=issues)
