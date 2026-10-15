"""Semantic validation of an AnimationSpec (never raises for a bad spec).

Issues are :class:`~ieeVisualizer.spec.ValidationIssue` with JSON pointers into
the serialized AnimationSpec (figure issues under ``/figure``). Codes:
``figure_missing``, ``figure_not_allowed``, ``invalid_value``, ``invalid_type``,
``sweep_source_missing``/``_conflict``, ``too_few_values``, ``unknown_layer``,
``not_a_symbolic_function``, ``unknown_parameter``, ``unknown_template``,
``unsupported_parameter``, ``unknown_dataset``, ``unknown_column``,
``frame_dataset_unused``, ``no_frames``, ``too_many_frames``,
``duration_too_long``, ``invalid_poster``, ``too_many_annotations``,
``text_too_long``, ``expression_too_complex``, ``data_too_large``,
``animation_too_expensive``;
warnings ``frame_value_missing``, ``quiver_rescaled_per_frame``.

Per-state work no count models is bounded
here, so no single render step runs long: drawn text is at most
:data:`MAX_TEXT_CHARS` characters (``text_too_long``) and every evaluated
expression has a :func:`minmax_cost` of at most :data:`MAX_MINMAX_COST`
(``expression_too_complex``; SymPy compares ``Min``/``Max`` arguments
pairwise on every build: 59 arguments take 5 s, 11 at most about 0.55 s). A
template sweep is checked in full at its first value; a refusal at another
value is raised when that state is built, at ``/timeline/values/<i>``.
"""

from __future__ import annotations

import ast
import math

from ..spec import DatasetRef, DataSnapshot, FunctionMark, QuiverMark, ValidationIssue, validate
from ..spec._common import is_number, suggest
from .frames import frame_keys, template_params
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

#: Longest drawn text (title, label, annotation, string data value).
MAX_TEXT_CHARS = 2000
#: Most inline data cells (rows x columns, all datasets): validating a spec
#: takes about 3.7 us per cell, in one uninterruptible step.
MAX_ANIMATION_CELLS = 1_000_000
#: Most :func:`minmax_cost` of one expression: one group of 11 arguments
#: (121) builds in at most about 0.55 s however complex its arguments.
MAX_MINMAX_COST = 128


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class _Issues(list):
    def add(self, path, code, message, severity="error"):
        self.append(ValidationIssue(path, code, message, severity))

    @property
    def errors(self) -> bool:
        """Any error so far (warnings never stop the later checks)."""
        return any(i.severity == "error" for i in self)


def validate_animation(anim: AnimationSpec, *, deadline=None) -> list[ValidationIssue]:
    """Every issue of ``anim`` (empty list = renderable as far as the spec
    goes). With a ``deadline`` (a render or export passes its own), the
    result is computed once per spec object for that call and the deadline
    is checked between the checks."""
    if deadline is None:
        return _validate(anim, None)
    key = ("validate", id(anim))
    if key not in deadline.memo:
        deadline.memo[key] = (anim, _validate(anim, deadline))  # anim kept: ids stay unique
    return list(deadline.memo[key][1])


def _validate(anim: AnimationSpec, deadline) -> list[ValidationIssue]:
    out = _Issues()
    if deadline is not None:
        deadline.check("validate")
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
    _check_text(out, "/title", anim.title)
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
        _check_figure_work(out, anim.figure)
        if out.errors:
            return list(out)
        for i in validate(anim.figure):
            out.add("/figure" + i.path, i.code, i.message, i.severity)
    if out.errors:
        return list(out)

    if isinstance(tl, ParameterSweep):
        ok = _check_sweep(anim, tl, out, deadline)
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
        # The state count is known only after resolution; the holds plus one
        # state are a lower bound checked now.
        out.extend(i for i in frame_limit_issues(anim, 1, lower_bound=True)
                   if i.code != "invalid_poster")
        out.extend(poster_issues(anim, None))
    elif isinstance(tl, FrameColumn):
        out.extend(frame_limit_issues(anim, snapshot.n_states))
    else:  # a sweep's limits were checked in _check_sweep, before any value was built
        out.extend(poster_issues(anim, frame_total(anim, sweep_state_count(tl, anim.fps))))
    return list(out)


# --------------------------------------------------------------------------
# Per-state work no count models
# --------------------------------------------------------------------------


def _check_text(out: _Issues, path: str, text) -> None:
    if isinstance(text, str) and len(text) > MAX_TEXT_CHARS:
        out.add(path, "text_too_long",
                f"this text has {len(text)} characters; an animation draws it in every state "
                f"and accepts at most {MAX_TEXT_CHARS}. Shorten it.")


def _is_minmax(node) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
        and node.func.id in ("Min", "Max")


def _flat_args(node) -> int:
    """Arguments of a ``Min``/``Max`` call once SymPy flattens directly
    nested calls of the same kind into it."""
    return sum(_flat_args(a) if _is_minmax(a) and a.func.id == node.func.id else 1
               for a in node.args)


def minmax_cost(text) -> int:
    """Sum over the ``Min``/``Max`` groups of ``text`` of (arguments)^2: SymPy
    compares a group's arguments pairwise each time it builds it. 0 for text
    that does not parse (the renderer reports that itself)."""
    if not isinstance(text, str):
        return 0
    try:
        tree = ast.parse(text.replace("^", "**").strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return 0
    inner = {id(a) for n in ast.walk(tree) if _is_minmax(n) for a in n.args
             if _is_minmax(a) and a.func.id == n.func.id}
    return sum(_flat_args(n) ** 2 for n in ast.walk(tree) if _is_minmax(n) and id(n) not in inner)


def _check_expression(out: _Issues, path: str, text) -> None:
    n = minmax_cost(text)
    if n > MAX_MINMAX_COST:
        out.add(path, "expression_too_complex",
                f"the expression's Min/Max groups cost {n} (the sum of their argument counts "
                f"squared); SymPy compares the arguments pairwise every time it builds the "
                f"expression, and an animation accepts at most {MAX_MINMAX_COST} (e.g. one "
                "Min/Max of 11 arguments). Use fewer Min/Max arguments.")


def _check_figure_work(out: _Issues, fig) -> None:
    """Text length and expression complexity of the figure (see module doc)."""
    cells = sum(sum(len(c.values) for c in ds.columns.values())
                for ds in (fig.datasets.values() if isinstance(fig.datasets, dict) else ())
                if isinstance(ds, DataSnapshot))
    if cells > MAX_ANIMATION_CELLS:
        out.add("/figure/datasets", "data_too_large",
                f"the inline datasets hold {cells} cells; an animation accepts at most "
                f"{MAX_ANIMATION_CELLS} (validating them is one step the deadline cannot "
                "interrupt). Drop unused columns and datasets or aggregate upstream.")
    _check_text(out, "/figure/title", fig.title)
    _check_text(out, "/figure/caption", fig.caption)
    _check_text(out, "/figure/legend/title", getattr(fig.legend, "title", None))
    for k, a in (fig.axes.items() if isinstance(fig.axes, dict) else ()):
        for f in ("label", "unit"):
            _check_text(out, f"/figure/axes/{k}/{f}", getattr(a, f, None))
    for i, a in enumerate(fig.annotations if isinstance(fig.annotations, list) else ()):
        for f in ("text", "label"):
            _check_text(out, f"/figure/annotations/{i}/{f}", getattr(a, f, None))
    for i, m in enumerate(fig.layers if isinstance(fig.layers, list) else ()):
        p = f"/figure/layers/{i}"
        _check_text(out, p + "/name", getattr(m, "name", None))
        if isinstance(m, FunctionMark):
            _check_expression(out, p + "/expression", m.expression)
        ds = fig.datasets.get(m.data) if getattr(m, "data", None) is not None \
            and isinstance(fig.datasets, dict) else None
        for ch, enc in (m.encoding.items() if isinstance(m.encoding, dict) else ()):
            _check_text(out, f"{p}/encoding/{ch}/title", getattr(enc, "title", None))
            fld = getattr(enc, "field", None)
            col = ds.columns.get(fld) if isinstance(ds, DataSnapshot) \
                and isinstance(fld, str) else None
            if col is not None and col.dtype in ("str", None):
                longest = max((v for v in col.values if isinstance(v, str)), key=len,
                              default=None)
                _check_text(out, f"/figure/datasets/{m.data}/columns/{fld}", longest)


# --------------------------------------------------------------------------
# Timelines
# --------------------------------------------------------------------------


def _check_sweep(anim: AnimationSpec, tl: ParameterSweep, out: _Issues, deadline) -> bool:
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
    elif not all(math.isfinite(x) for x in (tl.start, tl.end, tl.end - tl.start)):
        # end - start overflows for e.g. 1e308 -> -1e308: every interpolated
        # value would be inf or nan.
        out.add(p, "invalid_value", "start, end and end - start must be finite numbers "
                f"(got start={tl.start!r}, end={tl.end!r}).")
    elif not is_number(tl.duration) or not math.isfinite(tl.duration * anim.fps) \
            or tl.duration <= 0:
        out.add(p + "/duration", "invalid_value",
                "duration must be a positive, finite number of seconds.")
    if out.errors:
        return False
    # Frame guards from the count alone, before any sweep value is built.
    limits = [i for i in frame_limit_issues(anim, sweep_state_count(tl, anim.fps))
              if i.code != "invalid_poster"]
    if limits:
        out.extend(limits)
        return False
    if isinstance(tl.target, MarkTarget):
        return _check_mark_target(anim, tl, out)
    return _check_template_target(anim, tl, out, deadline)


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


def _check_template_target(anim: AnimationSpec, tl: ParameterSweep, out: _Issues,
                           deadline) -> bool:
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
    params = t.params if isinstance(t.params, dict) else {}
    for k, v in params.items():
        if isinstance(v, str):
            if k in ("u", "v"):
                _check_expression(out, f"{p}/params/{k}", v)
            else:
                _check_text(out, f"{p}/params/{k}", v)
    if out.errors:
        return False
    # The estimate before the template runs: the cheapest adapter at zero
    # frame area, so no request could make it acceptable.
    n = sweep_state_count(tl, anim.fps)
    work_issue = _work_issue(anim, n)
    if work_issue is not None:
        out.append(work_issue)
        return False
    if deadline is not None:
        deadline.check("validate")
    first = tl.values[0] if tl.values is not None else tl.start
    for i in tpl.check(t.data, **template_params(t, tl.parameter, first)):
        at = "/timeline/values/0" if tl.values is not None else "/timeline"
        if i.path == "/params/" + name or i.path.startswith(f"/params/{name}/"):
            out.add(at, i.code, f"at {tl.parameter} = {first:g} (state 0), {p}{i.path}: "
                    f"{i.message}", i.severity)
        else:
            out.add(p + i.path, i.code, i.message, i.severity)
    if out.errors:
        return False
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
    col = ds.columns[tl.column]
    if col.dtype in ("str", None):  # the frame value is drawn in the title
        _check_text(out, f"/figure/datasets/{tl.dataset}/columns/{tl.column}",
                    max((v for v in col.values if isinstance(v, str)), key=len, default=None))
        if out.errors:
            return None
    keys, _, nulls = frame_keys(ds, tl.column)
    if not keys:
        out.add(p + "/column", "no_frames", f"column {tl.column!r} has no non-null value.")
        return None
    if nulls:
        out.add(p + "/column", "frame_value_missing",
                f"{nulls} row(s) have no {tl.column!r} value and appear in no frame.", "warning")
    return _Counted(ds, len(keys))


class _Counted:
    """An inline frame dataset and its number of states (read once)."""

    def __init__(self, ds: DataSnapshot, n_states: int) -> None:
        self.ds, self.n_states = ds, n_states


class _Deferred(DataSnapshot):
    """A referenced frame dataset: the frame count is known after resolution."""
