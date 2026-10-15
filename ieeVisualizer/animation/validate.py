"""Semantic validation of an AnimationSpec (never raises for a bad spec).

Issues use the same :class:`~ieeVisualizer.spec.ValidationIssue` shape as
FigureSpec validation; paths are JSON pointers into the serialized
AnimationSpec, so a figure problem is reported under ``/figure/...``.

Codes (besides every FigureSpec code, prefixed with ``/figure``):
``figure_missing`` / ``figure_not_allowed``, ``invalid_value``, ``invalid_type``,
``sweep_source_missing`` / ``sweep_source_conflict``, ``too_few_values``,
``unknown_layer``, ``not_a_symbolic_function``, ``unknown_parameter``,
``unknown_template``, ``unsupported_parameter``, ``unknown_dataset``,
``unknown_column``, ``frame_dataset_unused``, ``no_frames``,
``frame_value_missing`` (warning), ``too_many_frames``, ``duration_too_long``,
``invalid_poster``, ``quiver_rescaled_per_frame`` (warning),
``too_many_annotations``, ``animation_too_expensive`` (a template sweep whose
cheapest render estimate is over budget, refused before any value is checked).
"""

from __future__ import annotations

import math

from ..spec import DatasetRef, DataSnapshot, FunctionMark, QuiverMark, ValidationIssue, validate
from ..spec._common import is_number, suggest
from .frames import count_states, frame_keys, sweep_values, template_params
from .limits import frame_limit_issues, frame_total, poster_issues, sweep_state_count
from .spec import (
    ANIMATION_SCHEMA_VERSION,
    AXIS_LIMIT_MODES,
    DISPLAYS,
    EASINGS,
    MAX_ANIMATION_ANNOTATIONS,
    MAX_FPS,
    AnimationSpec,
    FrameColumn,
    MarkTarget,
    ParameterSweep,
    TemplateTarget,
)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class _Issues(list):
    def add(self, path, code, message, severity="error"):
        self.append(ValidationIssue(path, code, message, severity))

    @property
    def errors(self) -> bool:
        """Any error so far (warnings never stop the later checks)."""
        return any(i.severity == "error" for i in self)


def validate_animation(anim: AnimationSpec) -> list[ValidationIssue]:
    """Every issue of ``anim`` (empty list = renderable as far as the spec
    goes). Inside a render or export call chain the result is computed once
    per ``animation_hash`` (see :mod:`ieeVisualizer.animation._scope`)."""
    from ._scope import anim_key, cache

    memo = cache("validate")
    key = anim_key(anim) if memo is not None else None
    if key is None:
        return _validate(anim)
    if key not in memo:
        memo[key] = _validate(anim)
    return list(memo[key])


def _validate(anim: AnimationSpec) -> list[ValidationIssue]:
    out = _Issues()
    if anim.schema_version != ANIMATION_SCHEMA_VERSION:
        out.add("/schema_version", "unsupported_schema_version",
                f"animation schema_version {anim.schema_version!r} is not supported.")
    if not _int(anim.fps) or not 1 <= anim.fps <= MAX_FPS:
        out.add("/fps", "invalid_value", f"fps must be an integer from 1 to {MAX_FPS}.")
    for k in ("hold_start", "hold_end"):
        if not _int(getattr(anim, k)) or getattr(anim, k) < 0:
            out.add("/" + k, "invalid_value", f"{k} must be a non-negative integer.")
    if anim.axis_limits not in AXIS_LIMIT_MODES:
        out.add("/axis_limits", "invalid_value",
                f"axis_limits must be one of {', '.join(AXIS_LIMIT_MODES)}.")
    tl = anim.timeline
    templated = isinstance(tl, ParameterSweep) and isinstance(tl.target, TemplateTarget)
    if templated and anim.figure is not None:
        out.add("/figure", "figure_not_allowed",
                "a template sweep rebuilds the figure from the template for every value; "
                "remove 'figure' (the template payload is the source).")
    elif not templated and anim.figure is None:
        out.add("/figure", "figure_missing", "this timeline animates a figure; add 'figure'.")
    if anim.figure is not None:
        n_ann = len(anim.figure.annotations) if isinstance(anim.figure.annotations, list) else 0
        if n_ann > MAX_ANIMATION_ANNOTATIONS:
            out.add("/figure/annotations", "too_many_annotations",
                    f"the figure has {n_ann} annotations; an animation draws every annotation "
                    f"in every state and accepts at most {MAX_ANIMATION_ANNOTATIONS}. Keep the "
                    "ones the animation needs.")
            return list(out)
        for i in validate(anim.figure):
            out.add("/figure" + i.path, i.code, i.message, i.severity)
    if out.errors:
        return list(out)

    if isinstance(tl, ParameterSweep):
        ok = _check_sweep(anim, tl, out)
        snapshot = None
    elif isinstance(tl, FrameColumn):
        snapshot = _check_frame_column(anim, tl, out)
        ok = snapshot is not None
    else:
        out.add("/timeline", "invalid_type", "timeline must be a ParameterSweep or FrameColumn.")
        return list(out)
    if not ok:
        return list(out)

    if anim.figure is not None and not isinstance(snapshot, _Deferred):
        for i, m in enumerate(anim.figure.layers):
            if isinstance(m, QuiverMark) and m.scale is None:
                out.add(f"/figure/layers/{i}/scale", "quiver_rescaled_per_frame",
                        "no 'scale' is set, so arrow lengths are rescaled in every frame and "
                        "changes in magnitude are not visible; set 'scale' to compare frames.",
                        "warning")
    if isinstance(snapshot, _Deferred):
        # The state count is known only after resolution (check_frame_count
        # then); the holds plus one state are a lower bound checked now.
        out.extend(i for i in frame_limit_issues(anim, 1, lower_bound=True)
                   if i.code != "invalid_poster")
        out.extend(poster_issues(anim, None))
    elif isinstance(tl, FrameColumn):
        out.extend(frame_limit_issues(anim, count_states(anim, snapshot)))
    else:  # a sweep's limits were checked in _check_sweep, before any value was built
        out.extend(poster_issues(anim, frame_total(anim, sweep_state_count(tl, anim.fps))))
    return list(out)


def _check_sweep(anim: AnimationSpec, tl: ParameterSweep, out: _Issues) -> bool:
    p = "/timeline"
    if tl.easing not in EASINGS:
        out.add(p + "/easing", "invalid_value", f"easing must be one of {', '.join(EASINGS)}.")
    ranged = [tl.start, tl.end, tl.duration]
    if tl.values is not None and any(v is not None for v in ranged):
        out.add(p, "sweep_source_conflict",
                "give either 'values' or 'start'/'end'/'duration', not both.")
    elif tl.values is not None:
        if len(tl.values) < 2 or not all(is_number(v) for v in tl.values):
            out.add(p + "/values", "too_few_values", "a sweep needs at least two numbers.")
    elif any(v is None for v in ranged):
        out.add(p, "sweep_source_missing",
                "a sweep needs 'values', or all of 'start', 'end' and 'duration' (seconds).")
    elif any(not is_number(v) for v in ranged):
        for k, v in zip(("start", "end", "duration"), ranged):
            if not is_number(v):  # bool is not a number here
                out.add(f"{p}/{k}", "invalid_type",
                        f"{k} must be a number, got {type(v).__name__} {v!r}.")
    elif not math.isfinite(tl.start) or not math.isfinite(tl.end):
        out.add(p, "invalid_value", "start and end must be finite numbers.")
    elif not is_number(tl.duration) or not math.isfinite(tl.duration * anim.fps) \
            or tl.duration <= 0:
        out.add(p + "/duration", "invalid_value",
                "duration must be a positive, finite number of seconds.")
    if out.errors:
        return False
    # Frame guards from the count alone, before any sweep value is built
    # (the template check below evaluates the template at every value).
    limits = [i for i in frame_limit_issues(anim, sweep_state_count(tl, anim.fps))
              if i.code != "invalid_poster"]
    if limits:
        out.extend(limits)
        return False
    if isinstance(tl.target, MarkTarget):
        return _check_mark_target(anim, tl, out)
    return _check_template_target(anim, tl, out)


def _check_mark_target(anim: AnimationSpec, tl: ParameterSweep, out: _Issues) -> bool:
    i = tl.target.layer
    layers = anim.figure.layers
    if not _int(i) or not 0 <= i < len(layers):
        out.add("/timeline/target/layer", "unknown_layer",
                f"layer {i!r} does not exist; the figure has {len(layers)} layer(s).")
        return False
    m = layers[i]
    if not isinstance(m, FunctionMark) or m.expression is None:
        out.add("/timeline/target/layer", "not_a_symbolic_function",
                f"layer {i} is a {m.type} mark; a mark sweep varies a parameter of a function "
                "mark with an expression.")
        return False
    if tl.parameter not in m.parameters:
        out.add("/timeline/parameter", "unknown_parameter",
                f"layer {i} has no parameter {tl.parameter!r}.{suggest(tl.parameter, m.parameters)}"
                f" Add it to /figure/layers/{i}/parameters with a base value (used by stills).")
        return False
    return True


def _check_template_target(anim: AnimationSpec, tl: ParameterSweep, out: _Issues) -> bool:
    from .. import templates

    t: TemplateTarget = tl.target
    p = "/timeline/target"
    try:
        tpl = templates.get(t.name, t.version)
    except Exception as e:  # noqa: BLE001 - registry errors carry the reason
        out.add(p, "unknown_template", f"template {t.name!r} version {t.version!r}: {e}")
        return False
    if tpl.version != t.version:
        out.add(p + "/version", "unknown_template",
                f"pin an exact template version; {t.name!r} resolved to {tpl.version!r}.")
        return False
    name, _, key = tl.parameter.partition(".")
    param = tpl.parameter(name)
    want = "number_map" if key else "number"
    if param is None or param.type != want:
        out.add("/timeline/parameter", "unsupported_parameter",
                f"{tl.parameter!r} is not a sweepable parameter of {t.name!r}: use a 'number' "
                "parameter, or 'name.key' of a 'number_map' parameter (e.g. 'constants.k').")
        return False
    values = sweep_values(tl, anim.fps)
    # (1) One full check, at the first value: everything that does not
    # depend on the swept value (and the SymPy dry-run) is found here once.
    seen = {values[0]: tpl.check(t.data, **template_params(t, tl.parameter, values[0]))}
    if any(i.severity == "error" for i in seen[values[0]]):
        other = next((v for v in reversed(values) if v != values[0]), None)
        if other is not None:
            seen[other] = tpl.check(t.data, **template_params(t, tl.parameter, other))
        _report(tl, values, seen, out, complete=other is None)  # one distinct value: all
        return False
    # (2) The render-work estimate before any per-value work: the cheapest
    # adapter at zero frame area, so no request could make it acceptable.
    work_issue = _work_issue(anim, len(values))
    if work_issue is not None:
        out.append(work_issue)
        return False
    # (3) Every value: the template's fast path where it has one (no SymPy
    # per value), plus full checks at the extremes and at 0, where
    # value-dependent refusals concentrate.
    hook = getattr(tpl, "sweep_checker", None)
    fast = hook(t.data, template_params(t, tl.parameter, values[0]), tl.parameter) \
        if callable(hook) else None
    for v in {min(values), max(values), *(v for v in values if v == 0)} - set(seen):
        seen[v] = tpl.check(t.data, **template_params(t, tl.parameter, v))
    for v in values:
        if v in seen:
            continue
        params = template_params(t, tl.parameter, v)
        if fast is not None:
            try:
                seen[v] = fast(params)
                continue
            except Exception:  # noqa: BLE001 - the fast path gave up: check in full
                pass
        seen[v] = tpl.check(t.data, **params)
    _report(tl, values, seen, out, complete=True)
    if out.errors:
        return False
    params = template_params(t, tl.parameter, 0.0)
    if any(q.type == "number" and q.name == "scale" for q in tpl.parameters) \
            and params.get("scale") is None:
        out.add(p + "/params", "quiver_rescaled_per_frame",
                "no 'scale' is set, so arrow lengths are rescaled in every frame and changes in "
                "magnitude are not visible; set 'scale' to compare frames.", "warning")
    return True


def _work_issue(anim: AnimationSpec, n_states: int) -> ValidationIssue | None:
    from .cost import MAX_RENDER_WORK_US, min_work_us

    work = min_work_us(anim, n_states, frame_total(anim, n_states))
    if work <= MAX_RENDER_WORK_US:
        return None
    return ValidationIssue("", "animation_too_expensive",
                           f"{n_states} states would take at least {work / 1e6:.0f} s to render "
                           "with any adapter at any frame size (estimated from the number of "
                           "states, the template's grid and expressions); at most "
                           f"{MAX_RENDER_WORK_US / 1e6:g} s is accepted. Use fewer values, a "
                           "smaller grid or simpler expressions.")


def _report(tl: ParameterSweep, values: list[float], seen: dict[float, list[ValidationIssue]],
            out: _Issues, complete: bool) -> None:
    """Report the template issues found at the checked values (``seen``).

    ``complete``: every value was checked. Then an issue raised identically
    at every value, not on the swept parameter, does not depend on the swept
    value and is reported once under ``/timeline/target``; any other issue
    is reported at the first value raising it, ``/timeline/values/<i>`` (or
    ``/timeline`` for an interpolated sweep, with the state index), naming
    the template path and how many other values fail the same way.

    Otherwise the first value failed and only it and the last distinct
    value were checked: an issue identical at both, not on the swept
    parameter, is reported as value-independent, any other at the first
    value, and the rest of the sweep is checked once it is fixed."""
    swept_path = "/params/" + tl.parameter.partition(".")[0]
    if complete:
        checked = list(enumerate(values))
    else:
        last = max(k for k, v in enumerate(values) if v != values[0])
        checked = [(0, values[0]), (last, values[last])]
    hits: dict[tuple[str, str, str], list[tuple[int, float, str]]] = {}
    for k, v in checked:
        for i in seen[v]:
            hits.setdefault((i.path, i.code, i.severity), []).append((k, v, i.message))
    for (path, code, severity), where in hits.items():
        k, v, message = where[0]
        target = "/timeline/target" + path
        n_at = len({j for j, *_ in where})
        swept = path == swept_path or path.startswith(swept_path + "/")
        if n_at == len(checked) and not swept and len({m for *_, m in where}) == 1:
            out.add(target, code, message, severity)  # independent of the swept value
            continue
        if not complete:
            others = " (the other values are checked once this is fixed)"
        else:
            others = f" ({n_at - 1} other value(s) fail the same way)" if n_at > 1 else ""
        at = f"/timeline/values/{k}" if tl.values is not None else "/timeline"
        out.add(at, code, f"at {tl.parameter} = {v:g} (state {k}), {target}: {message}{others}",
                severity)


def _check_frame_column(anim: AnimationSpec, tl: FrameColumn, out: _Issues):
    p = "/timeline"
    fig = anim.figure
    if tl.display not in DISPLAYS:
        out.add(p + "/display", "invalid_value", f"display must be one of {', '.join(DISPLAYS)}.")
    if tl.display == "window" and (not _int(tl.window) or tl.window < 1):
        out.add(p + "/window", "invalid_value", "display 'window' needs window >= 1 (values).")
    if tl.display != "window" and tl.window is not None:
        out.add(p + "/window", "invalid_value", "window is only used with display 'window'.")
    if not _int(tl.frames_per_value) or tl.frames_per_value < 1:
        out.add(p + "/frames_per_value", "invalid_value", "frames_per_value must be >= 1.")
    ds = fig.datasets.get(tl.dataset)
    if ds is None:
        out.add(p + "/dataset", "unknown_dataset",
                f"no dataset {tl.dataset!r}.{suggest(tl.dataset, fig.datasets)}")
        return None
    if not any(m.data == tl.dataset for m in fig.layers):
        out.add(p + "/dataset", "frame_dataset_unused",
                f"no layer draws dataset {tl.dataset!r}, so every frame would look the same.")
    if isinstance(ds, DatasetRef):
        if ds.columns and tl.column not in ds.columns:
            out.add(p + "/column", "unknown_column", f"reference {tl.dataset!r} declares no "
                    f"column {tl.column!r}.")
        return None if out.errors else _Deferred()
    if tl.column not in ds.columns:
        out.add(p + "/column", "unknown_column",
                f"dataset {tl.dataset!r} has no column {tl.column!r}."
                f"{suggest(tl.column, ds.columns)}")
        return None
    if out.errors:
        return None
    keys, _, nulls = frame_keys(ds, tl.column)
    if not keys:
        out.add(p + "/column", "no_frames", f"column {tl.column!r} has no non-null value.")
        return None
    if nulls:
        out.add(p + "/column", "frame_value_missing",
                f"{nulls} row(s) have no {tl.column!r} value and appear in no frame.", "warning")
    return ds


class _Deferred(DataSnapshot):
    """A referenced frame dataset: the frame count is known after resolution."""


def check_frame_count(anim: AnimationSpec, n_states: int) -> list[ValidationIssue]:
    """Frame, duration and poster guards once the real number of distinct
    states is known (a referenced frame dataset after resolution)."""
    return frame_limit_issues(anim, n_states, resolved=True)
