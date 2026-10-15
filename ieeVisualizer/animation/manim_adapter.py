"""Optional Manim adapter (``pip install ieeVisualizer[manim]``).

A narrow, honest subset of AnimationSpec, rendered with Manim Community's
Cairo renderer:

* a ``parameter_sweep`` of a ``function`` mark: every layer of the figure is a
  symbolic one-variable function in ``cartesian2d`` (no data layers, no
  annotations); the curves morph on Manim ``Axes`` while a ``ValueTracker``
  moves through the sweep values;
* a ``parameter_sweep`` of the ``vector_field_2d`` template in expression mode
  (sweeping ``constants.<name>`` or another number parameter), drawn as an
  ``ArrowVectorField`` whose arrow length is proportional to magnitude with
  ONE scale for the whole animation (largest magnitude over all frames = 0.9
  grid cells), so changes in strength stay visible.

Everything else (frame columns, data layers, annotations, legends, 3D, polar,
other templates) is reported as unmet through :meth:`ManimAdapter.unmet` and
the capabilities, never approximated. Text uses ``Text`` (Pango), never
``MathTex``, so no LaTeX installation is needed. Frames are NOT pixel-equal
to the Matplotlib frames: this is a different renderer with its own look.

Importing this module does not import manim; :func:`manim_available` tries
once and caches the answer. Expressions go through the same checked parser as
the Matplotlib adapter (``render.mpl.function.parse``).
"""

from __future__ import annotations

import math
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..render.base import Capabilities, RenderWarning, issue
from ..spec import FunctionMark
from .base import (
    AnimationCapabilities,
    AnimationError,
    AnimationRequest,
    AnimationResult,
    base_unmet,
    timeline_kind,
)
from .frames import sweep_values, template_params
from .spec import AnimationSpec, ParameterSweep, TemplateTarget

ADAPTER_NAME = "manim"
ADAPTER_VERSION = "0.1.0"
SUPPORTED_TEMPLATES = frozenset({"vector_field_2d"})


@lru_cache(maxsize=1)
def manim_available() -> bool:
    try:
        import manim  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "not available"
        return False
    return True


CAPABILITIES = AnimationCapabilities(
    figure=Capabilities(coordinate_systems=frozenset({"cartesian2d"}),
                        mark_types=frozenset({"function"}), output_formats=frozenset({"png"}),
                        annotation_types=frozenset(), animation=True, max_points=200_000),
    timelines=frozenset({"parameter_sweep:mark", "parameter_sweep:template"}),
    output_formats=frozenset({"mp4", "gif"}),
    templates=SUPPORTED_TEMPLATES,
    notes=("only symbolic one-variable function marks (no data layers, annotations or "
           "legend) and the vector_field_2d template in expression mode",),
)


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

    def unmet(self, anim: AnimationSpec, request: AnimationRequest) -> list[str]:
        out = base_unmet(anim, request, CAPABILITIES)
        if not manim_available():
            out.append("manim is not importable (pip install 'ieeVisualizer[manim]')")
        tl = anim.timeline
        if timeline_kind(anim) == "parameter_sweep:mark" and anim.figure is not None:
            for i, m in enumerate(anim.figure.layers):
                if not isinstance(m, FunctionMark) or m.expression is None \
                        or len(m.variables) != 1:
                    out.append(f"layer {i}: only symbolic one-variable function marks")
            if anim.figure.annotations:
                out.append("annotations")
        elif isinstance(tl, ParameterSweep) and isinstance(tl.target, TemplateTarget):
            p = tl.target.params
            if tl.target.data is not None or not (p.get("u") and p.get("v")):
                out.append("vector_field_2d in expression mode only (u and v, no data)")
        return out

    def render(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
               resolver=None) -> AnimationResult:
        from ..spec import SpecValidationError
        from .validate import validate_animation

        request = request or AnimationRequest(format="mp4")
        errors = [i for i in validate_animation(anim) if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
        missing = self.unmet(anim, request)
        if missing:
            raise AnimationError([issue("", "unsupported_by_adapter",
                                        f"the manim adapter cannot provide: "
                                        f"{'; '.join(missing)}.")])
        tl: ParameterSweep = anim.timeline
        values = sweep_values(tl, anim.fps)
        warnings: list[RenderWarning] = []
        if isinstance(tl.target, TemplateTarget):
            build, title = _vector_field_scene(anim, tl, values)
        else:
            build, title = _function_scene(anim, tl, values, warnings)
        if anim.axis_limits == "auto":
            warnings.append(RenderWarning("axis_limits_fixed", "manim keeps one coordinate "
                                          "system for the whole clip; axis_limits 'auto' is "
                                          "drawn as 'fixed'.", "/axis_limits"))
        content, n_frames = _run(build, title, anim, request, len(values))
        return AnimationResult(
            content=content, format=request.format, media_type=request.media_type,
            adapter_name=self.name, adapter_version=self.version, frame_count=n_frames,
            state_count=len(values), fps=anim.fps,
            frame_duration_ms=1000.0 / anim.fps, poster_frame=-1, warnings=warnings,
            request=request,
            states=[{"index": i, "value": v, "label": f"{tl.parameter} = {v:.4g}",
                     "empty": False} for i, v in enumerate(values)],
            frames=[])


# --------------------------------------------------------------------------
# Scene builders. Each returns ``(construct(scene, M), title)`` where ``M``
# is the imported manim module; nothing here imports manim at module level.
# --------------------------------------------------------------------------


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


def _nice_range(lo: float, hi: float) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        lo, hi = lo - 1.0, lo + 1.0
    step = 10 ** math.floor(math.log10((hi - lo) / 4))
    return [math.floor(lo / step) * step, math.ceil(hi / step) * step, step]


def _function_scene(anim: AnimationSpec, tl: ParameterSweep, values, warnings):
    fig = anim.figure
    layers = []
    for i, m in enumerate(fig.layers):
        var = m.variables[0]
        others = {k: v for k, v in m.parameters.items() if k != tl.parameter}
        swept = i == tl.target.layer
        fn = _lambdify(m.expression, [var, tl.parameter] if swept else [var], others,
                       f"/figure/layers/{i}")
        xs = np.linspace(*m.domain[var], m.samples)
        layers.append((m, swept, fn, xs))
        if m.style and set(m.style) - {"color", "linewidth"}:
            warnings.append(RenderWarning("style_key_ignored", "manim draws only 'color' and "
                                          "'linewidth' styles.", f"/figure/layers/{i}/style"))
        if m.role != "observed":
            warnings.append(RenderWarning("role_not_drawn", f"role {m.role!r} is not "
                                          "distinguished by the manim adapter.",
                                          f"/figure/layers/{i}/role"))
    if fig.legend.show and len(fig.layers) > 1:
        warnings.append(RenderWarning("legend_not_drawn", "the manim adapter draws no legend.",
                                      "/legend"))

    def ys(layer, p):
        m, swept, fn, xs = layer
        with np.errstate(all="ignore"):
            out = fn(xs, p) if swept else fn(xs)
        return np.broadcast_to(np.asarray(out, dtype=float), xs.shape)

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
    xr, yr = _nice_range(x_lo, x_hi), _nice_range(y_lo, y_hi)
    labels = (_axis_label(fig, "x", layers[0][0].variables[0]), _axis_label(fig, "y", "y"))

    def construct(scene, M):
        ax = M.Axes(x_range=xr, y_range=yr, x_length=11, y_length=5.6, tips=False)
        ax.shift(0.3 * M.DOWN)
        scene.add(ax, *_tick_labels(M, ax, xr, yr), *_axis_titles(M, ax, labels))
        tracker = M.ValueTracker(0.0)
        idx = np.arange(len(values), dtype=float)

        def value():
            return float(np.interp(tracker.get_value(), idx, values))

        palette = [M.BLUE, M.ORANGE, M.GREEN, M.RED, M.PURPLE]
        for k, layer in enumerate(layers):
            m = layer[0]
            color = m.style.get("color") or palette[k % len(palette)]
            width = float(m.style.get("linewidth", 2.0)) * 2

            def curve(layer=layer, color=color, width=width):
                group = M.VGroup()
                for run in _finite_runs(layer[3], ys(layer, value())):
                    inside = [(x, min(max(y, yr[0]), yr[1])) for x, y in run]
                    if len(inside) > 1:
                        group.add(M.VMobject(stroke_color=color, stroke_width=width)
                                  .set_points_as_corners([ax.c2p(x, y) for x, y in inside]))
                return group

            scene.add(M.always_redraw(curve))
        scene.add(M.always_redraw(lambda: M.Text(
            f"{tl.parameter} = {value():.3g}", font_size=24).to_corner(M.UR)))
        return tracker

    return construct, fig.title or anim.title


def _vector_field_scene(anim: AnimationSpec, tl: ParameterSweep, values):
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
        mags = [np.hypot(np.broadcast_to(fu(gx, gy, v), gx.shape),
                         np.broadcast_to(fv(gx, gy, v), gx.shape)) for v in values]
    finite = np.concatenate([m[np.isfinite(m)] for m in mags])
    max_mag = float(finite.max()) if finite.size else 0.0
    if max_mag <= 0:
        raise AnimationError([issue("/timeline/target/params", "empty_series",
                                    "the field is zero or non-finite everywhere.")])
    s = min(11.0 / (x1 - x0), 5.6 / (y1 - y0))  # scene units per data unit, equal aspect
    cell = min((x1 - x0) / max(grid[0] - 1, 1), (y1 - y0) / max(grid[1] - 1, 1)) * s
    xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
    xr, yr = _nice_range(x0, x1), _nice_range(y0, y1)
    spec_title = p0.get("title")

    def construct(scene, M):
        ax = M.Axes(x_range=[x0, x1, xr[2]], y_range=[y0, y1, yr[2]], x_length=(x1 - x0) * s,
                    y_length=(y1 - y0) * s, tips=False)
        ax.shift(0.3 * M.DOWN)
        origin = ax.c2p(xc, yc)
        scene.add(ax, *_tick_labels(M, ax, [x0, x1], [y0, y1]),
                  *_axis_titles(M, ax, (p0.get("x_label") or "x", p0.get("y_label") or "y")))
        tracker = M.ValueTracker(0.0)
        idx = np.arange(len(values), dtype=float)

        def value():
            return float(np.interp(tracker.get_value(), idx, values))

        def field_at(pt, k):
            x = (pt[0] - origin[0]) / s + xc
            y = (pt[1] - origin[1]) / s + yc
            with np.errstate(all="ignore"):
                u, v = float(fu(x, y, k)), float(fv(x, y, k))
            if not (math.isfinite(u) and math.isfinite(v)):
                return np.zeros(3)  # no arrow: a zero vector draws nothing visible
            return np.array([u, v, 0.0]) * (0.9 * cell / max_mag)

        dx = (x1 - x0) * s / max(grid[0] - 1, 1)
        dy = (y1 - y0) * s / max(grid[1] - 1, 1)

        def field():
            k = value()
            return M.ArrowVectorField(
                lambda pt: field_at(pt, k), color=M.BLUE,
                # ArrowVectorField adds one step to the end (inclusive range);
                # ending half a step early gives exactly the template's grid.
                x_range=[origin[0] - (xc - x0) * s, origin[0] + (x1 - xc) * s - dx / 2, dx],
                y_range=[origin[1] - (yc - y0) * s, origin[1] + (y1 - yc) * s - dy / 2, dy],
                length_func=lambda norm: norm)

        scene.add(M.always_redraw(field))
        scene.add(M.always_redraw(lambda: M.Text(
            f"{tl.parameter} = {value():.3g}", font_size=24).to_corner(M.UR)))
        return tracker

    return construct, spec_title or anim.title


def _axis_label(fig, key: str, default: str) -> str:
    a = fig.axes.get(key)
    label = (a.label if a and a.label else default)
    return f"{label} ({a.unit})" if a and a.unit else label


def _tick_labels(M, ax, xr, yr):
    """End values of each axis, at the bottom/left edge of the plot box."""
    x_lo, y_lo = ax.x_range[0], ax.y_range[0]
    out = []
    for x in (xr[0], xr[1]):
        out.append(M.Text(f"{x:g}", font_size=18).next_to(ax.c2p(x, y_lo), M.DOWN, buff=0.1))
    for y in (yr[0], yr[1]):
        out.append(M.Text(f"{y:g}", font_size=18).next_to(ax.c2p(x_lo, y), M.LEFT, buff=0.1))
    return out


def _axis_titles(M, ax, labels):
    (x_lo, x_hi), (y_lo, y_hi) = ax.x_range[:2], ax.y_range[:2]
    return [M.Text(labels[0], font_size=22).next_to(ax.c2p((x_lo + x_hi) / 2, y_lo), M.DOWN,
                                                     buff=0.4),
            M.Text(labels[1], font_size=22).rotate(math.pi / 2).next_to(
                ax.c2p(x_lo, (y_lo + y_hi) / 2), M.LEFT, buff=0.45)]


def _run(construct, title, anim: AnimationSpec, request: AnimationRequest, n_values: int):
    import manim as M

    w, h = request.frame_request().size_px
    n_anim = n_values - 1  # the tracker moves between n_values states

    class _Scene(M.Scene):
        def construct(self):
            if title:
                self.add(M.Text(title, font_size=28).to_edge(M.UP))
            tracker = construct(self, M)
            # One play() covering every output frame: manim renders frames at
            # t = k / fps for t < run_time, and separate wait() segments lost
            # frames when concatenated. run_time is F - 0.5 frames so float
            # rounding can neither add nor drop a frame; the rate function
            # maps frame k to state clamp(k - hold_start, 0, n - 1).
            total = anim.hold_start + n_values + anim.hold_end
            span = total - 0.5

            def rate(alpha: float) -> float:
                k = alpha * span
                return min(max(k - anim.hold_start, 0.0), float(n_anim)) / n_anim

            self.play(tracker.animate.set_value(float(n_anim)), run_time=span / anim.fps,
                      rate_func=rate)

    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"media_dir": tmp, "pixel_width": w + (w % 2), "pixel_height": h + (h % 2),
               "frame_rate": anim.fps, "format": "mp4", "disable_caching": True,
               "verbosity": "ERROR", "progress_bar": "none", "output_file": "animation",
               "background_color": "#FFFFFF" if request.theme != "dark" else "#000000"}
        with M.tempconfig(cfg):
            M.config.renderer = "cairo"
            M.VMobject.set_default(color=M.BLACK)
            M.Text.set_default(color=M.BLACK)
            try:
                scene = _Scene()
                scene.render()
                fw = scene.renderer.file_writer
                path = Path(fw.movie_file_path)
            finally:
                M.VMobject.set_default()
                M.Text.set_default()
            frames = _decode(path)
            if request.format == "gif":
                # Manim's own GIF output (PyAV palette) bands colours badly; the
                # MP4 frames are re-encoded with the same Pillow encoder as the
                # Matplotlib adapter instead.
                from .encode import encode_gif

                content = encode_gif(frames, anim.fps, anim.loop)
            else:
                content = path.read_bytes()
    return content, len(frames)


def _decode(path: Path) -> list:
    import av

    with av.open(str(path)) as c:
        return [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]


__all__ = ["ADAPTER_NAME", "CAPABILITIES", "ManimAdapter", "manim_available"]
