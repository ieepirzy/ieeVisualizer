"""Optional Manim adapter (``pip install ieeVisualizer[manim]``).

A narrow, honest subset of AnimationSpec, drawn by Manim Community's Cairo
camera:

* a ``parameter_sweep`` of a ``function`` mark: every layer of the figure is a
  symbolic one-variable function in ``cartesian2d``, on Manim ``Axes``;
* a ``parameter_sweep`` of the ``vector_field_2d`` template in expression mode
  (sweeping ``constants.<name>`` or ``scale``), drawn as an ``ArrowVectorField``
  with the template's ``scale`` (length = scale x magnitude) or, without one,
  ONE scale for the whole clip (largest magnitude = 0.9 grid cells);
  ``normalize`` draws every arrow the same length. Arrow tails are on the grid
  points (as Matplotlib's quiver) and the axes extend past the grid by the
  longest arrow, so every arrow is inside the plot. A ``reference`` layer
  (the template's default) is translucent with the theme's reference alpha.

Support is an allowlist (:data:`DRAWN`): every field of the spec, and every
template parameter, set to a value other than its default and not drawn here
is reported unmet by :meth:`ManimAdapter.unmet`, as is a swept parameter
Manim cannot sweep and ``axis_limits`` other than ``fixed``. Drawn besides
the curves and arrows, by Matplotlib's rules: the title (with the state's
label, exactly the result's ``states[i].label``, when ``label_frames``), the
caption (wrapped), axis titles (the variable for x, the layer name or
expression for y; ``label (unit)``, the unit not repeated), the requested
axis limits exactly, the theme's background, text and series colours,
``color``/``linewidth`` read by the Matplotlib style parser, and the same
``nonfinite_samples_omitted`` / ``null_values_omitted`` warnings. Text uses
``Text`` (Pango, a sans font), never ``MathTex``: no LaTeX.

Layout: text keeps its point size at the request's dpi (as in Matplotlib);
each text has its own band, so none overlaps another or the plot; the plot
gets the rest, and a frame that leaves less than :data:`MIN_PLOT_IN` for it
is ``frame_too_small``. Known differences from Matplotlib: Manim's axes
cross at the origin inside the box and only the end values are
tick-labelled.

Frames are drawn one output frame at a time by the camera (a held state
once) and streamed into the same encoders as the Matplotlib adapter (Pillow
GIF, ffmpeg MP4): Manim never writes a movie, so a spec gives the same
frames every time. Never chosen automatically (``auto_select=False``): it is
used only when the request names it. Resource guards run before Manim
starts; a curve cut into more than :data:`MAX_CURVE_PIECES` pieces is
refused (counted by the same function that draws them). The render runs
under the caller's deadline (in the killable worker by default; inline,
checked before every frame). Manim's configuration is process-global, so
inline renders are serialized behind :data:`RENDER_LOCK`, acquired within
the deadline.

Importing this module does not import manim: :func:`manim_available` only
looks for it, and :func:`import_problem` tries the import (once) when the
adapter is asked for.
"""

from __future__ import annotations

import dataclasses
import math
import tempfile
import threading
from functools import lru_cache

import numpy as np

from ..render.base import Capabilities, RenderWarning, issue
from ..spec import FunctionMark
from .base import (
    AnimationCapabilities,
    AnimationError,
    AnimationRequest,
    AnimationResult,
    base_unmet,
    enforce_limits,
    output_timing,
    size_warnings,
)
from .frames import (
    ambiguous_labels,
    state_labels,
    sweep_values,
    template_params,
    value_texts,
)
from .limits import frame_mapping, frame_total, poster_index, sweep_state_count
from .spec import AnimationSpec, ParameterSweep, TemplateTarget

ADAPTER_NAME = "manim"
ADAPTER_VERSION = "0.1.0"
SUPPORTED_TEMPLATES = frozenset({"vector_field_2d"})
#: Held for the whole of every Manim render: ``manim.config`` is global.
RENDER_LOCK = threading.Lock()
#: Most pieces (runs inside the axes) of all curves in one frame.
MAX_CURVE_PIECES = 1000


@lru_cache(maxsize=1)
def manim_available() -> bool:
    """Whether manim is installed, without importing it (an import costs
    about 0.5 s and configures logging), for registering the adapter. Whether
    it also imports is :func:`import_problem`, checked only when the adapter
    is asked for (in the render worker by default)."""
    from importlib.util import find_spec

    try:
        return find_spec("manim") is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def import_problem() -> str | None:
    """``None`` if ``import manim`` works, else why not (tried once)."""
    try:
        import manim  # noqa: F401
    except Exception as e:  # noqa: BLE001 - any import failure: not usable
        return f"{type(e).__name__}: {e}"
    return None


CAPABILITIES = AnimationCapabilities(
    figure=Capabilities(coordinate_systems=frozenset({"cartesian2d"}),
                        mark_types=frozenset({"function"}), output_formats=frozenset({"png"}),
                        annotation_types=frozenset(), animation=True, max_points=200_000),
    timelines=frozenset({"parameter_sweep:mark", "parameter_sweep:template"}),
    output_formats=frozenset({"mp4", "gif"}),
    templates=SUPPORTED_TEMPLATES,
    notes=("only symbolic one-variable function marks (no data layers, annotations or "
           "legend) and the vector_field_2d template in expression mode",
           "used only when requested by name (adapter='manim'), never selected automatically"),
    auto_select=False,
    cost_model="manim",
)


#: What this adapter draws: every other field of the spec (or parameter of
#: the template) set to a value other than its default is reported unmet by
#: :meth:`ManimAdapter.unmet`. An allowlist: a field added to the spec later
#: is unmet here until it is drawn.
DRAWN = {
    "animation": {"timeline", "figure", "fps", "hold_start", "hold_end", "axis_limits",
                  "label_frames", "poster", "loop", "id", "title", "metadata",
                  "schema_version"},
    "figure": {"title", "layers", "datasets", "axes", "coordinate_system", "legend",
               "annotations", "caption", "theme", "id", "metadata", "schema_version"},
    "axis": {"label", "unit", "limits", "scale"},
    "legend": {"show"},
    "function": {"type", "data", "encoding", "name", "style", "expression", "variables",
                 "domain", "samples", "parameters", "role"},
    "style": {"color", "linewidth"},
    "vector_field_2d": {"u", "v", "constants", "x_range", "y_range", "grid", "x_unit",
                        "y_unit", "title", "caption", "x_label", "y_label", "normalize",
                        "scale", "role"},
}
#: Template parameters a sweep may vary here (``constants.<name>`` or these).
SWEPT = {"vector_field_2d": {"constants", "scale"}}


def _not_drawn(obj, kind: str, path: str) -> list[str]:
    """Fields of dataclass ``obj`` outside ``DRAWN[kind]`` not at their default."""
    out = []
    for f in dataclasses.fields(obj):
        default = f.default if f.default is not dataclasses.MISSING else (
            f.default_factory() if f.default_factory is not dataclasses.MISSING else None)
        if f.name not in DRAWN[kind] and getattr(obj, f.name) != default:
            out.append(f"{path}/{f.name} (not drawn by manim)")
    return out


def unmet_fields(anim: AnimationSpec) -> list[str]:
    """Everything set in ``anim`` that this adapter does not draw."""
    out = _not_drawn(anim, "animation", "")
    tl = anim.timeline
    fig = anim.figure
    if fig is not None:
        out += _not_drawn(fig, "figure", "/figure") + _not_drawn(fig.legend, "legend",
                                                                  "/figure/legend")
        for k, a in fig.axes.items():
            out += _not_drawn(a, "axis", f"/figure/axes/{k}")
            if a.scale not in (None, "linear"):
                out.append(f"axis {k} scale {a.scale!r} (linear axes only)")
        for i, m in enumerate(fig.layers):
            p = f"/figure/layers/{i}"
            if not isinstance(m, FunctionMark) or m.expression is None \
                    or len(m.variables) != 1:
                out.append(f"layer {i}: only symbolic one-variable function marks")
                continue
            out += _not_drawn(m, "function", p)
            if m.role != "observed":
                out.append(f"{p}/role {m.role!r} (not drawn by manim)")
            out += [f"{p}/style/{k} (not drawn by manim)" for k in m.style
                    if k not in DRAWN["style"]]
    if isinstance(tl, ParameterSweep) and isinstance(tl.target, TemplateTarget):
        t = tl.target
        try:
            from .. import templates

            params = {q.name: q.default for q in templates.get(t.name, t.version).parameters}
        except Exception:  # noqa: BLE001 - an unknown template is a validation error
            params = {}
        drawn = DRAWN.get(t.name, set())
        out += [f"/timeline/target/params/{k} (not drawn by manim)"
                for k, v in (t.params or {}).items()
                if k not in drawn and v != params.get(k)]
        if tl.parameter.partition(".")[0] not in SWEPT.get(t.name, set()):
            out.append(f"manim cannot sweep {tl.parameter!r}")
    return out


class ManimAdapter:
    @property
    def name(self) -> str:
        return ADAPTER_NAME

    @property
    def version(self) -> str:
        return ADAPTER_VERSION

    @property
    def capabilities(self) -> AnimationCapabilities:
        return CAPABILITIES

    @property
    def ffmpeg(self) -> str | None:
        """The executable MP4 frames are encoded with (as for Matplotlib)."""
        from .encode import find_ffmpeg

        return find_ffmpeg()

    def library_versions(self) -> dict[str, str]:
        import manim

        return {"manim": manim.__version__}

    def unmet(self, anim: AnimationSpec, request: AnimationRequest) -> list[str]:
        out = base_unmet(anim, request, CAPABILITIES)
        problem = import_problem() if manim_available() else "not installed"
        if problem is not None:
            out.append(f"manim is not importable: {problem} (pip install "
                       "'ieeVisualizer[manim]')")
        if request.format == "mp4" and self.ffmpeg is None:
            out.append("mp4: no ffmpeg executable found (install ffmpeg or set "
                       "IEEVISUALIZER_FFMPEG)")
        tl = anim.timeline
        if anim.figure is not None and anim.figure.annotations:
            out.append("annotations")
        if anim.axis_limits != "fixed":
            out.append(f"axis_limits {anim.axis_limits!r} (manim keeps one coordinate system "
                       "for the whole clip: 'fixed' only)")
        if isinstance(tl, ParameterSweep) and isinstance(tl.target, TemplateTarget) \
                and tl.target.name == "vector_field_2d":
            p = tl.target.params
            if tl.target.data is not None or not (p.get("u") and p.get("v")):
                out.append("vector_field_2d in expression mode only (u and v, no data)")
        return out + unmet_fields(anim)

    def render(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
               resolver=None, deadline=None) -> AnimationResult:
        from ..spec import SpecValidationError
        from .deadline import Deadline
        from .validate import validate_animation

        request = request or AnimationRequest(format="mp4")
        deadline = Deadline.of(deadline)
        errors = [i for i in validate_animation(anim, deadline=deadline)
                  if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
        if isinstance(anim.timeline, ParameterSweep):  # guards first: spec + request only
            enforce_limits(anim, request, sweep_state_count(anim.timeline, anim.fps),
                           cost_model="manim")
        missing = self.unmet(anim, request)
        if missing:
            raise AnimationError([issue("", "unsupported_by_adapter",
                                        f"the manim adapter cannot provide: "
                                        f"{'; '.join(missing)}.")])
        tl: ParameterSweep = anim.timeline
        n_frames = frame_total(anim, sweep_state_count(tl, anim.fps))
        values = sweep_values(tl, anim.fps)
        frames = frame_mapping(anim, len(values))
        poster = poster_index(anim, n_frames)
        plot, labels, theme, warnings = _plan(anim, request)
        deadline.check("draw")
        # in a worker process (the default) the lock is uncontended; inline it
        # serializes renders sharing manim.config, within this render's deadline
        if not RENDER_LOCK.acquire(timeout=max(deadline.remaining(), 1e-3)):
            deadline.phase = "draw: waiting for another manim render"
            raise deadline.exceeded()
        try:
            content, drawn, poster_png = _run(plot, frames, poster, request, anim, theme,
                                              self.ffmpeg, deadline)
        finally:
            RENDER_LOCK.release()
        if drawn != n_frames:
            raise AnimationError([issue("", "frame_count_mismatch",
                                        f"manim drew {drawn} frames, {n_frames} were planned; "
                                        "the frame-to-state mapping would be wrong.")])
        delay, timing = output_timing(request.format, anim.fps, anim.loop)
        warnings.extend(timing)
        warnings.extend(size_warnings(request))
        return AnimationResult(
            content=content, format=request.format, media_type=request.media_type,
            adapter_name=self.name, adapter_version=self.version, frame_count=n_frames,
            state_count=len(values), fps=anim.fps,
            frame_duration_ms=delay, poster_frame=poster, warnings=warnings,
            request=request,
            states=[{"index": i, "value": v, "label": labels[i], "empty": False}
                    for i, v in enumerate(values)],
            frames=frames, poster_png=poster_png)


def _plan(anim: AnimationSpec, request: AnimationRequest):
    """``(plot, state labels, theme, warnings)`` of a supported spec."""
    tl: ParameterSweep = anim.timeline
    values = sweep_values(tl, anim.fps)
    # exactly the result's state labels (the ones Matplotlib draws)
    labels = state_labels(tl.parameter, values)
    warnings = [RenderWarning(i.code, i.message, i.path) for i in
                ambiguous_labels(values, value_texts(values, numbers=True)[1])]
    theme = _theme(request.theme or (anim.figure.theme if anim.figure else None), warnings)
    try:
        if isinstance(tl.target, TemplateTarget):
            plot = _vector_field_plot(anim, tl, values, theme, warnings)
        else:
            plot = _function_plot(anim, tl, values, warnings, theme)
    except AnimationError:
        raise
    except (ValueError, OverflowError, ZeroDivisionError, FloatingPointError) as e:
        raise AnimationError([issue("", "frame_not_drawable",
                                    f"manim cannot draw this: {type(e).__name__}: {e}")]
                             ) from None
    plot.titles = [_framed_title(plot.title, anim, lab) for lab in labels]
    return plot, labels, theme, warnings


# --------------------------------------------------------------------------
# What a clip draws. A builder returns a :class:`_Plot`; :func:`_compose`
# lays it out in the frame and :func:`_run` draws each output frame.
# Nothing here imports manim at module level.
# --------------------------------------------------------------------------


@dataclasses.dataclass
class _Plot:
    #: Data range of the axes ``(x0, x1, y0, y1)`` and their tick steps.
    box: tuple[float, float, float, float]
    steps: tuple[float, float]
    #: Tick-labelled values of each axis.
    ticks: tuple[list[float], list[float]]
    labels: tuple[str, str]
    #: ``draw(scene, M, ax, state)``: adds the marks; ``state()`` is the
    #: index of the state being drawn.
    draw: object
    title: str | None = None
    caption: str | None = None
    #: One scene unit per data unit on both axes (a vector field).
    equal_aspect: bool = False
    #: The title of each state (set by the adapter: label_frames).
    titles: list[str | None] = dataclasses.field(default_factory=list)


def _lambdify(expression: str, variables: list[str], params: dict[str, float], path: str):
    import sympy as sp

    from ..render.mpl.function import parse

    mark = FunctionMark(expression=expression, variables=variables, parameters=params)
    expr, syms = parse(mark, path)
    return sp.lambdify(syms, expr, modules="numpy")


def _finite_runs(xs, ys):
    ok = np.isfinite(ys)
    runs, cur = [], []
    for x, y, good in zip(xs, ys, ok):
        if good:
            cur.append((x, y))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def _clip(run, xr, yr) -> list[list[tuple[float, float]]]:
    """The parts of polyline ``run`` inside the box ``xr[:2]`` x ``yr[:2]``,
    each segment cut where it crosses the box edge (Liang-Barsky): a curve
    leaving the axis range is interrupted, never drawn along the edge."""
    (x0, x1), (y0, y1) = xr[:2], yr[:2]
    parts: list[list[tuple[float, float]]] = []
    for (ax_, ay), (bx, by) in zip(run, run[1:]):
        t0, t1, dx, dy = 0.0, 1.0, bx - ax_, by - ay
        for p, q in ((-dx, ax_ - x0), (dx, x1 - ax_), (-dy, ay - y0), (dy, y1 - ay)):
            if p == 0:
                if q < 0:
                    t0, t1 = 1.0, 0.0
            elif p < 0:
                t0 = max(t0, q / p)
            else:
                t1 = min(t1, q / p)
        if t0 > t1:
            continue
        a, b = (ax_ + t0 * dx, ay + t0 * dy), (ax_ + t1 * dx, ay + t1 * dy)
        if parts and t0 == 0.0 and parts[-1][-1] == (ax_, ay):
            parts[-1].append(b)
        else:
            parts.append([a, b])
    return parts


def _theme(name, warnings) -> tuple[str, str, list[str], float | None]:
    """``(background, foreground, series colours, reference alpha)`` of the
    registered theme ``name``, as a Matplotlib render of the same spec uses
    them (series colours: its palette, else Matplotlib's default cycle)."""
    from matplotlib import colors

    from ..render.mpl.style import roles_for
    from ..themes import resolve_for_render

    theme, problem = resolve_for_render(name)
    if problem is not None:
        warnings.append(RenderWarning("unknown_theme", f"{problem} The default theme was used.",
                                      ""))
    palette = list(theme.palette or [f"C{i}" for i in range(10)])
    return (theme.background or "#FFFFFF", theme.foreground or "#000000",
            [colors.to_hex(c) for c in palette], roles_for(theme).reference_alpha)


def _style(m, k: int, palette: list[str], path: str, warnings) -> tuple[str, float]:
    """``(hex colour, stroke width)`` of a layer, read by the Matplotlib
    renderer's own style parser: the same values are accepted, and an
    invalid one warns (``invalid_style_value``) and falls back the same way."""
    from matplotlib import colors

    from ..render.mpl.style import parse_style

    style = parse_style(m.style, DRAWN["style"], f"{path}/style", "a function mark", warnings)
    return (colors.to_hex(style.get("color", palette[k % len(palette)])),
            float(style.get("linewidth", 2.0)) * 2)


def _nice_range(lo: float, hi: float, keep: tuple[bool, bool] = (False, False)) -> list[float]:
    """``[lo, hi, tick step]``: ends rounded out to the step unless ``keep``
    says the figure set them (requested limits are drawn exactly). A range
    Manim cannot draw (not finite, empty, beyond float precision) is a
    structured error."""
    if lo == hi and math.isfinite(lo):
        lo, hi = lo - 1.0, hi + 1.0
    span = hi - lo
    if not (math.isfinite(lo) and math.isfinite(hi) and math.isfinite(span)) or span <= 0 \
            or span < 1e-9 * max(abs(lo), abs(hi), 1e-300) or span < 1e-200:
        raise AnimationError([issue("/figure/axes", "axis_range_unrenderable",
                                    f"the axis range [{lo!r}, {hi!r}] cannot be drawn by manim "
                                    "(not finite, empty or below float precision).")])
    step = 10 ** math.floor(math.log10(span / 4))
    return [lo if keep[0] else math.floor(lo / step) * step,
            hi if keep[1] else math.ceil(hi / step) * step, step]


def _corners(pieces) -> np.ndarray:
    """Bezier points (4 per segment) of polylines given as Nx3 point arrays,
    joined into one array (a gap between pieces starts a subpath)."""
    out = []
    for c in pieces:
        c = np.asarray(c, dtype=float)
        if len(c) < 2:
            continue
        a, b = c[:-1], c[1:]
        out.append(np.stack([a, a + (b - a) / 3, a + 2 * (b - a) / 3, b], axis=1)
                   .reshape(-1, 3))
    return np.concatenate(out) if out else np.zeros((0, 3))


def _label(label: str | None, unit: str | None) -> str | None:
    """Matplotlib's axis title rule: ``label (unit)``, the unit not repeated
    when the label already ends with it."""
    if label is None:
        return f"({unit})" if unit else None
    if unit and not label.rstrip().endswith(f"({unit})"):
        return f"{label} ({unit})"
    return label


def _framed_title(title: str | None, anim: AnimationSpec, label: str) -> str | None:
    """The title of one state as Matplotlib draws it (``label_frames``)."""
    if not anim.label_frames:
        return title
    return f"{title} ({label})" if title else label


def _per_state(found: dict, n: int) -> list[RenderWarning]:
    """One warning per (code, message, path), naming the states it holds in
    (the Matplotlib adapter's aggregation)."""
    out = []
    for (code, msg, path), idx in found.items():
        where = "every state" if len(idx) == n else f"states {idx}"
        out.append(RenderWarning(code, f"{msg} ({where})", path))
    return out


def _function_plot(anim: AnimationSpec, tl: ParameterSweep, values, warnings, theme) -> _Plot:
    fig = anim.figure
    layers = []
    for i, m in enumerate(fig.layers):
        var = m.variables[0]
        swept = i == tl.target.layer
        # only the target layer's parameter is swept; another layer's
        # parameter of the same name keeps its own value
        others = {k: v for k, v in m.parameters.items() if not (swept and k == tl.parameter)}
        fn = _lambdify(m.expression, [var, tl.parameter] if swept else [var], others,
                       f"/figure/layers/{i}")
        xs = np.linspace(*m.domain[var], m.samples)
        layers.append((m, swept, fn, xs))
    if fig.legend.show and len(fig.layers) > 1:
        warnings.append(RenderWarning("legend_not_drawn", "the manim adapter draws no legend.",
                                      "/legend"))

    def ys(layer, p):
        m, swept, fn, xs = layer
        with np.errstate(all="ignore"):
            out = fn(xs, p) if swept else fn(xs)
        return np.broadcast_to(np.asarray(out, dtype=float), xs.shape)

    omitted: dict = {}
    for s, p in enumerate(values):  # the warning Matplotlib's sampler gives
        for i, layer in enumerate(layers):
            bad = int((~np.isfinite(ys(layer, p))).sum())
            if bad:
                omitted.setdefault(("nonfinite_samples_omitted",
                                    f"{bad} of {layer[3].size} sampled points are non-finite "
                                    "(nan/inf) and were left out of the plot",
                                    f"/figure/layers/{i}"), []).append(s)
    warnings.extend(_per_state(omitted, len(values)))
    all_y = np.concatenate([ys(layer, p) for layer in layers for p in values])
    all_y = all_y[np.isfinite(all_y)]
    if all_y.size == 0:
        raise AnimationError([issue("/figure/layers", "empty_series",
                                    "no layer has a finite value at any sweep value.")])
    x_lo = min(float(layer[3][0]) for layer in layers)
    x_hi = max(float(layer[3][-1]) for layer in layers)
    limits_x = fig.axes.get("x").limits if fig.axes.get("x") else None
    limits_y = fig.axes.get("y").limits if fig.axes.get("y") else None
    pad = 0.05 * (float(all_y.max()) - float(all_y.min()) or 1.0)
    y_lo = limits_y[0] if limits_y and limits_y[0] is not None else float(all_y.min()) - pad
    y_hi = limits_y[1] if limits_y and limits_y[1] is not None else float(all_y.max()) + pad
    if limits_x:
        x_lo = limits_x[0] if limits_x[0] is not None else x_lo
        x_hi = limits_x[1] if limits_x[1] is not None else x_hi
    xr = _nice_range(x_lo, x_hi, tuple(bool(limits_x) and v is not None
                                       for v in (limits_x or [None, None])))
    yr = _nice_range(y_lo, y_hi, tuple(bool(limits_y) and v is not None
                                       for v in (limits_y or [None, None])))
    first = layers[0][0]
    # Matplotlib's defaults: the variable for x, the layer name or expression for y
    labels = (_axis_label(fig, "x", first.variables[0]),
              _axis_label(fig, "y", first.name or first.expression))

    def pieces(layer, p):  # what one frame draws: the same function counts and draws
        return [piece for run in _finite_runs(layer[3], ys(layer, p))
                for piece in _clip(run, xr, yr)]

    for p in values:  # before manim starts
        n = sum(len(pieces(layer, p)) for layer in layers)
        if n > MAX_CURVE_PIECES:
            raise AnimationError([issue("/figure/layers", "curve_too_fragmented",
                                        f"at {tl.parameter} = {p:g} the curves leave and re-enter "
                                        f"the axes {n} times; manim draws each piece in "
                                        f"Python per frame and accepts at most "
                                        f"{MAX_CURVE_PIECES}. Use fewer samples or wider "
                                        "limits.")])
    styles = [_style(m, k, theme[2], f"/figure/layers/{k}", warnings)
              for k, (m, *_) in enumerate(layers)]

    def draw(scene, M, ax, state):
        for k, layer in enumerate(layers):
            color, width = styles[k]

            def curve(layer=layer, color=color, width=width):
                # ONE mobject per curve; each piece inside the axes is a
                # subpath; the bezier points are built in one array
                path = M.VMobject(stroke_color=color, stroke_width=width)
                pts = _corners([np.asarray(ax.coords_to_point(*np.array(piece).T)).T
                                for piece in pieces(layer, values[state()])])
                if len(pts):
                    path.set_points(pts)
                return path

            scene.add(M.always_redraw(curve))

    return _Plot(box=(xr[0], xr[1], yr[0], yr[1]), steps=(xr[2], yr[2]),
                 ticks=([xr[0], xr[1]], [yr[0], yr[1]]), labels=labels, draw=draw,
                 title=fig.title or anim.title, caption=fig.caption)


def _vector_field_plot(anim: AnimationSpec, tl: ParameterSweep, values, theme,
                       warnings) -> _Plot:
    from .. import templates

    t: TemplateTarget = tl.target
    tpl = templates.get(t.name, t.version)
    p0 = {q.name: q.default for q in tpl.parameters}
    p0.update(template_params(t, tl.parameter, values[0]))
    name, _, key = tl.parameter.partition(".")
    swept = key or name
    consts = {k: float(v) for k, v in (p0.get("constants") or {}).items() if k != swept}
    fu = _lambdify(p0["u"], ["x", "y", swept], consts, "/timeline/target/params/u")
    fv = _lambdify(p0["v"], ["x", "y", swept], consts, "/timeline/target/params/v")
    (x0, x1), (y0, y1) = p0["x_range"], p0["y_range"]
    grid = p0["grid"] if isinstance(p0["grid"], list) else [p0["grid"], p0["grid"]]
    gx, gy = np.meshgrid(np.linspace(x0, x1, grid[0]), np.linspace(y0, y1, grid[1]))
    with np.errstate(all="ignore"):
        uv = [(np.broadcast_to(fu(gx, gy, v), gx.shape).astype(float),
               np.broadcast_to(fv(gx, gy, v), gx.shape).astype(float)) for v in values]
    mags = [np.hypot(u, v) for u, v in uv]
    omitted: dict = {}
    for s, (u, v) in enumerate(uv):  # the warning Matplotlib's quiver gives
        bad = int((~(np.isfinite(u) & np.isfinite(v))).sum())
        if bad:
            omitted.setdefault(("null_values_omitted",
                                f"{bad} row(s) of layer 0 have null values; they are left out "
                                "(no arrow), not drawn as 0.", "/figure/layers/0"), []).append(s)
    warnings.extend(_per_state(omitted, len(values)))
    finite = np.concatenate([m[np.isfinite(m)] for m in mags])
    max_mag = float(finite.max()) if finite.size else 0.0
    if max_mag <= 0:
        raise AnimationError([issue("/timeline/target/params", "empty_series",
                                    "the field is zero or non-finite everywhere.")])
    _nice_range(x0, x1), _nice_range(y0, y1)  # refuses undrawable ranges
    cell = min((x1 - x0) / max(grid[0] - 1, 1), (y1 - y0) / max(grid[1] - 1, 1))

    def length(k: int, mag: float) -> float:
        """Arrow length in data units, as the template defines it: scale x
        magnitude; without a scale ONE scale for the whole clip (largest
        magnitude = 0.9 grid cells); normalize: every arrow 0.9 cells."""
        if p0.get("normalize"):
            return 0.9 * cell if mag else 0.0
        scale = values[k] if swept == "scale" else p0.get("scale")
        return mag * (scale if scale is not None else 0.9 * cell / max_mag)

    # an arrow's tail is on its grid point (as Matplotlib's quiver): the axes
    # reach the longest arrow (at least half a cell) past the grid on every
    # side, so every arrow is inside the plot whatever its direction
    half = max([0.5 * cell] + [length(k, float(m[np.isfinite(m)].max()))
                               for k, m in enumerate(mags) if np.isfinite(m).any()])
    box = (x0 - half, x1 + half, y0 - half, y1 + half)
    labels = tuple(_label(p0.get(a + "_label") or a, p0.get(a + "_unit")) for a in ("x", "y"))
    role = p0.get("role") or "reference"  # the template's default for expressions
    alpha = theme[3] if role == "reference" else None

    def draw(scene, M, ax, state):
        (sx0, sy0), (sx1, sy1) = ax.c2p(x0, y0)[:2], ax.c2p(x1, y1)[:2]
        dx = (sx1 - sx0) / max(grid[0] - 1, 1)
        dy = (sy1 - sy0) / max(grid[1] - 1, 1)
        per_unit = (ax.c2p(1, 0)[0] - ax.c2p(0, 0)[0])  # scene units per data unit

        def field_at(pt, k):
            x, y = ax.p2c(pt)[:2]
            with np.errstate(all="ignore"):
                u, v = float(fu(x, y, values[k])), float(fv(x, y, values[k]))
            if not (math.isfinite(u) and math.isfinite(v)):
                return np.zeros(3)  # no arrow: a zero vector draws nothing visible
            norm = math.hypot(u, v)
            if not norm:
                return np.zeros(3)
            return np.array([u, v, 0.0]) / norm * length(k, norm) * per_unit

        def field():
            k = state()
            f = M.ArrowVectorField(
                lambda pt: field_at(pt, k), color=theme[2][0],
                # ArrowVectorField adds one step to the end (inclusive range);
                # ending half a step early gives exactly the template's grid.
                x_range=[sx0, sx1 - dx / 2, dx], y_range=[sy0, sy1 - dy / 2, dy],
                # a reference layer is translucent, as in Matplotlib
                length_func=lambda norm: norm, opacity=1.0 if alpha is None else alpha)
            return f

        scene.add(M.always_redraw(field))

    return _Plot(box=box, steps=(_nice_range(*box[:2])[2], _nice_range(*box[2:])[2]),
                 ticks=([x0, x1], [y0, y1]), labels=labels, draw=draw,
                 title=p0.get("title") or anim.title, caption=p0.get("caption"),
                 equal_aspect=True)


def _axis_label(fig, key: str, default: str) -> str:
    a = fig.axes.get(key)
    return _label(a.label if a and a.label else default, a.unit if a else None)


# --------------------------------------------------------------------------
# Layout: text keeps its physical size (as in Matplotlib: points at the
# request's dpi); the plot gets what is left. Every text has its own band,
# so nothing overlaps.
# --------------------------------------------------------------------------

#: Scene units per inch of the output frame (the default 6.4 x 4.8 in frame
#: is 8 units tall, Manim's default).
UNITS_PER_IN = 8 / 4.8
#: Manim font size per typographic point: a capital is 0.0101 units per
#: font size; Matplotlib's is 0.7 pt per point.
FONT_PER_PT = 0.7 / 72 * UNITS_PER_IN / 0.0101
#: Point sizes of each text (Matplotlib's defaults: large title, medium
#: labels and ticks, a smaller caption).
TEXT_PT = {"title": 12.0, "label": 10.0, "tick": 10.0, "caption": 9.0}
#: Smallest plot area (inches) left after the text: a smaller frame is
#: refused (``frame_too_small``) rather than drawn unreadably.
MIN_PLOT_IN = (1.2, 0.8)
GAP = 0.06 * UNITS_PER_IN


def _text(M, s: str, kind: str, max_width: float | None = None):
    t = M.Text(s, font_size=TEXT_PT[kind] * FONT_PER_PT, font="Sans")
    if max_width is not None and t.width > max_width:
        t.scale_to_fit_width(max_width)
    return t


def _wrap(text: str, width_units: float, pt: float) -> str:
    """Matplotlib's caption wrap: about 0.55 em per character."""
    import textwrap

    chars = max(20, int(width_units / UNITS_PER_IN * 72 / (pt * 0.55)))
    return "\n".join(textwrap.fill(line, chars) for line in text.splitlines() or [""])


def _compose(scene, M, plot: _Plot, fw: float, fh: float, state):
    """Lay out ``plot`` in a ``fw`` x ``fh`` scene and add everything to it.
    Returns the Axes."""
    left_edge, bottom_edge, top_edge = -fw / 2, -fh / 2, fh / 2
    fmt = [[f"{v:g}" for v in vs] for vs in plot.ticks]
    xt = [_text(M, s, "tick") for s in fmt[0]]
    yt = [_text(M, s, "tick") for s in fmt[1]]
    xtitle = _text(M, plot.labels[0], "label", fw - 2 * GAP)
    ytitle = _text(M, plot.labels[1], "label", fh - 2 * GAP).rotate(math.pi / 2)
    caption = (_text(M, _wrap(plot.caption, fw - 2 * GAP, TEXT_PT["caption"]), "caption",
                     fw - 2 * GAP) if plot.caption else None)
    titled = [t for t in plot.titles if t]
    # one text measured (the longest; the font gives every title one height)
    title_h = _text(M, max(titled, key=len), "title").height if titled else 0.0
    top = GAP + (title_h + GAP if titled else 0.0)
    bottom = GAP + (caption.height + GAP if caption else 0.0) + xtitle.height + GAP \
        + max(t.height for t in xt) + GAP
    left = GAP + ytitle.width + GAP + max(t.width for t in yt) + GAP
    right = GAP
    bw, bh = fw - left - right, fh - top - bottom
    if bw < MIN_PLOT_IN[0] * UNITS_PER_IN or bh < MIN_PLOT_IN[1] * UNITS_PER_IN:
        raise AnimationError([issue("", "frame_too_small",
                                    f"the frame leaves {bw / UNITS_PER_IN:.2f} x "
                                    f"{bh / UNITS_PER_IN:.2f} in for the plot after its text; "
                                    f"manim needs at least {MIN_PLOT_IN[0]:g} x "
                                    f"{MIN_PLOT_IN[1]:g} in. Use a larger size or a lower dpi "
                                    "(text keeps its point size).")])
    x0, x1, y0, y1 = plot.box
    if plot.equal_aspect:
        s = min(bw / (x1 - x0), bh / (y1 - y0))
        w, h = (x1 - x0) * s, (y1 - y0) * s
    else:
        w, h = bw, bh
    ax = M.Axes(x_range=[x0, x1, plot.steps[0]], y_range=[y0, y1, plot.steps[1]],
                x_length=w, y_length=h, tips=False)
    centre = np.array([left_edge + left + bw / 2, bottom_edge + bottom + bh / 2, 0.0])
    lo, hi = ax.c2p(x0, y0), ax.c2p(x1, y1)
    ax.shift(centre - (lo + hi) / 2)
    (rx0, ry0), (rx1, ry1) = ax.c2p(x0, y0)[:2], ax.c2p(x1, y1)[:2]
    for t, v in zip(xt, plot.ticks[0]):  # inside the plot's x extent, below it
        x = min(max(ax.c2p(v, y0)[0], rx0 + t.width / 2), rx1 - t.width / 2)
        t.move_to([x, ry0 - GAP - t.height / 2, 0])
    for t, v in zip(yt, plot.ticks[1]):  # inside the plot's y extent, left of it
        y = min(max(ax.c2p(x0, v)[1], ry0 + t.height / 2), ry1 - t.height / 2)
        t.move_to([rx0 - GAP - t.width / 2, y, 0])
    xtitle.move_to([(rx0 + rx1) / 2, min(t.get_bottom()[1] for t in xt) - GAP
                    - xtitle.height / 2, 0])
    ytitle.move_to([min(t.get_left()[0] for t in yt) - GAP - ytitle.width / 2,
                    (ry0 + ry1) / 2, 0])
    scene.add(ax, *xt, *yt, xtitle, ytitle)
    if caption is not None:
        caption.move_to([left_edge + GAP + caption.width / 2,
                         bottom_edge + GAP + caption.height / 2, 0])
        scene.add(caption)
    if titled:
        def title():
            t = plot.titles[state()]
            if not t:
                return M.VMobject()
            mob = _text(M, t, "title", fw - 2 * GAP)
            return mob.move_to([0, top_edge - GAP - title_h / 2, 0])

        scene.add(M.always_redraw(title))
    plot.draw(scene, M, ax, state)
    return ax


def _scene(plot: _Plot, request: AnimationRequest, theme):
    """``(scene, set_state)``: the composed scene, inside ``manim.tempconfig``."""
    import manim as M

    holder = {"k": 0}

    def state() -> int:
        return holder["k"]

    w, h = request.frame_request().size_px
    dpi = request.frame_request().dpi
    fw, fh = w / dpi * UNITS_PER_IN, h / dpi * UNITS_PER_IN
    M.config.frame_width, M.config.frame_height = fw, fh

    class _Scene(M.Scene):
        def construct(self):
            _compose(self, M, plot, fw, fh, state)

    scene = _Scene()
    scene.setup()
    scene.construct()

    def set_state(k: int) -> None:
        holder["k"] = k
        scene.update_mobjects(0)

    return scene, set_state


def _config(request: AnimationRequest, theme, tmp: str) -> dict:
    w, h = request.frame_request().size_px
    return {"media_dir": tmp, "pixel_width": w, "pixel_height": h,
            "disable_caching": True, "write_to_movie": False, "save_last_frame": False,
            "verbosity": "ERROR", "progress_bar": "none", "background_color": theme[0],
            "renderer": "cairo"}


def _run(plot: _Plot, frames: list[int], poster: int, request: AnimationRequest,
         anim: AnimationSpec, theme, ffmpeg: str | None, deadline):
    """``(content, frames drawn, poster PNG)``. Manim's Cairo camera draws
    each output frame of its state (a held state is drawn once); the frames
    are streamed one at a time into the same encoders as the Matplotlib
    adapter (Pillow GIF, ffmpeg MP4), never written as a Manim movie: no
    lossy round trip, and the same spec gives the same frames every time."""
    import manim as M

    from .encode import encode_gif, encode_mp4

    seen = {"n": 0, "poster": None}
    with tempfile.TemporaryDirectory() as tmp, M.tempconfig(_config(request, theme, tmp)):
        M.VMobject.set_default(color=theme[1])
        M.Text.set_default(color=theme[1])
        try:
            scene, set_state = _scene(plot, request, theme)

            def stream():
                last_state, frame = None, None
                for k, s in enumerate(frames):
                    deadline.check("draw")
                    if s != last_state:  # a held state is the same frame object
                        set_state(s)
                        scene.renderer.update_frame(scene)
                        frame = np.ascontiguousarray(scene.renderer.get_frame()[:, :, :3])
                        deadline.count("states_drawn")
                        last_state = s
                    seen["n"] = k + 1
                    if k == poster:
                        seen["poster"] = _png(frame)
                    yield frame

            if request.format == "gif":
                content = encode_gif(stream(), anim.fps, anim.loop, deadline)
            else:
                content = encode_mp4(stream(), anim.fps, ffmpeg, deadline)
        finally:
            M.VMobject.set_default()
            M.Text.set_default()
    return content, seen["n"], seen["poster"]


def _png(frame) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(frame, "RGB").save(buf, format="PNG")
    return buf.getvalue()


__all__ = ["ADAPTER_NAME", "CAPABILITIES", "ManimAdapter", "manim_available"]
