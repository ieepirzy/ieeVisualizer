"""Matplotlib animation adapter: AnimationSpec -> GIF (Pillow) or MP4 (ffmpeg).

Every distinct state is rendered as a PNG by the static
:class:`~ieeVisualizer.render.MatplotlibAdapter` and decoded, so frame *k* is,
pixel for pixel, the still of :meth:`frame_specs` [k]. States are drawn
once each, in frame order, and streamed to the encoders (one RGB frame
exists at a time); the poster is the PNG of the poster state kept as it is
drawn. Expressions are built once per render. ``axis_limits="fixed"`` holds
x/y ranges (a build pass per state, or of the whole frame-column figure) and
continuous colour ranges (``color_range``) across frames. Everything runs
under the render's :class:`~ieeVisualizer.animation.deadline.Deadline`. See
``docs/ANIMATION.md``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import io
import math

import numpy as np

from ..render.base import RenderWarning, issue
from ..render.mpl import CAPABILITIES as FIGURE_CAPABILITIES
from ..render.mpl import MatplotlibAdapter
from ..render.mpl.adapter import _dedupe
from ..render.mpl.function import expression_cache
from ..spec import REQUIRED_CHANNELS, Axis, FigureSpec, FunctionMark, SpecValidationError
from .base import (
    AnimationCapabilities,
    AnimationError,
    AnimationRequest,
    AnimationResult,
    base_unmet,
    counted_states,
    enforce_limits,
    memoized,
    output_timing,
    size_warnings,
)
from .deadline import Deadline
from .encode import encode_mp4, find_ffmpeg, gif_frames, write_gif
from .frames import FramePlan, plan
from .spec import AnimationSpec, FrameColumn
from .validate import validate_animation

ADAPTER_NAME = "matplotlib-animation"
ADAPTER_VERSION = "1.0.0"
#: Marks whose y range in one frame is not that of their rows pooled over
#: every frame (a histogram counts the rows of its frame).
_PER_FRAME_Y = frozenset({"histogram"})


class MatplotlibAnimationAdapter:
    """``ffmpeg_path`` overrides discovery (a missing path disables MP4)."""

    def __init__(self, ffmpeg_path: str | None = None) -> None:
        self._ffmpeg_arg = ffmpeg_path
        self._static = MatplotlibAdapter()

    @property
    def name(self) -> str:
        return ADAPTER_NAME

    @property
    def version(self) -> str:
        return ADAPTER_VERSION

    @property
    def ffmpeg(self) -> str | None:
        return find_ffmpeg(self._ffmpeg_arg)

    @property
    def capabilities(self) -> AnimationCapabilities:
        exe = self.ffmpeg
        figure = dataclasses.replace(FIGURE_CAPABILITIES, animation=True,
                                     output_formats=frozenset({"png"}))
        return AnimationCapabilities(
            figure=figure,
            timelines=frozenset({"parameter_sweep:mark", "parameter_sweep:template",
                                 "frame_column"}),
            output_formats=frozenset({"gif", "mp4"} if exe else {"gif"}),
            notes=() if exe else ("mp4: no ffmpeg executable found (install ffmpeg or set "
                                  "IEEVISUALIZER_FFMPEG)",),
        )

    def unmet(self, anim: AnimationSpec, request: AnimationRequest) -> list[str]:
        return base_unmet(anim, request, self.capabilities)

    # ---------------------------------------------------------------- frames

    def plan(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
             resolver=None, deadline=None) -> tuple[FramePlan, list[RenderWarning]]:
        """The frame plan with fixed axis limits and colour ranges applied to
        every state spec. Every resource guard
        (:func:`~ieeVisualizer.animation.base.enforce_limits`) is checked, from
        the state count, before any state is built or drawn. Each dataset
        reference is resolved once."""
        deadline = Deadline.of(deadline)
        errors = [i for i in validate_animation(anim, deadline=deadline)
                  if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
        request = request or AnimationRequest()
        resolver = memoized(resolver)
        n_states, figure, index = counted_states(anim, resolver, deadline)
        enforce_limits(anim, request, n_states, figure, index=index)
        with expression_cache():
            fp = plan(anim, resolver, deadline=deadline, base=figure, index=index)
            _parse_expressions(fp, deadline)
            warnings = [RenderWarning(i.code, i.message, i.path) for i in fp.issues]
            _fix_colors(fp, anim, warnings, deadline)
            if anim.axis_limits == "fixed":
                if index is not None:  # only rows some frame draws (not null-frame rows)
                    from .frames import _subset

                    ds = anim.timeline.dataset
                    figure = dataclasses.replace(figure, datasets={
                        **figure.datasets, ds: _subset(figure.datasets[ds],
                                                       np.flatnonzero(index.row_key >= 0))})
                self._fix_limits(fp, anim, figure, request, warnings, deadline)
        return fp, warnings

    def frame_specs(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
                    resolver=None, deadline=None) -> list[FigureSpec | None]:
        """The FigureSpec drawn in each output frame (None = "no data" frame)."""
        fp, _ = self.plan(anim, request, resolver=resolver, deadline=deadline)
        return [fp.state_of(k).spec for k in range(fp.frame_count)]

    def _fix_limits(self, fp: FramePlan, anim: AnimationSpec, full: FigureSpec | None,
                    request: AnimationRequest, warnings: list[RenderWarning],
                    deadline: Deadline) -> None:
        specs = [s.spec for s in fp.states if s.spec is not None]
        if not specs:
            return
        if specs[0].coordinate_system != "cartesian2d":
            warnings.append(RenderWarning(
                "axis_limits_auto", f"axis_limits 'fixed' is not applied in "
                f"{specs[0].coordinate_system}; each frame scales itself.", "/axis_limits"))
            return
        from ..render.mpl.data import infer_axes

        kinds = {k: info.kind for k, info in infer_axes(specs[0], []).items()}
        open_keys = []
        for key in ("x", "y"):
            current = specs[0].axes.get(key)
            have = list(current.limits) if current and current.limits else [None, None]
            if None not in have:
                continue
            if kinds.get(key) == "category":
                warnings.append(RenderWarning(
                    "axis_limits_auto", f"the {key} axis is categorical; its range is not held "
                    "fixed across frames.", "/axis_limits"))
                continue
            open_keys.append((key, have))
        if not open_keys:
            return  # nothing to measure: no figure is built
        # A frame column uses all its rows at once (autoscaling one date
        # widens it to +-2 years), except a heatmap and a histogram's y.
        per_frame = {"x", "y"}
        if isinstance(anim.timeline, FrameColumn):
            framed = {m.type for m in full.layers if m.data == anim.timeline.dataset}
            per_frame = {"x", "y"} if "heatmap" in framed else \
                {"y"} if framed & _PER_FRAME_Y else set()
        ranges: dict[str, list[float]] = {}
        req = request.frame_request()

        def measure(spec: FigureSpec, keys) -> None:
            try:
                built = self._static.build_figure(spec, req)
            except RecursionError:  # text the layout engine cannot nest that deep
                raise AnimationError([issue("", "frame_not_drawable",
                                            "the figure could not be laid out to fix its axis "
                                            "limits: its text or data nests too deeply.")]
                                     ) from None
            for key in keys:
                lo, hi = getattr(built.axes, f"get_{key}lim")()
                r = ranges.setdefault(key, [lo, hi])
                r[0], r[1] = min(r[0], lo), max(r[1], hi)

        whole = [k for k, _ in open_keys if k not in per_frame]
        if whole:
            deadline.check("limits")
            measure(full, whole)
        each = [k for k, _ in open_keys if k in per_frame]
        if each:
            for spec in specs:
                deadline.check("limits")
                measure(spec, each)
                deadline.count("states_limited")
        new_limits = {}
        for key, have in open_keys:
            lo, hi = ranges[key]
            conv = _num_to_iso if kinds.get(key) == "time" else float
            new_limits[key] = [have[0] if have[0] is not None else conv(lo),
                               have[1] if have[1] is not None else conv(hi)]
        for i, s in enumerate(fp.states):
            if s.spec is None:
                continue
            axes = dict(s.spec.axes)
            for key, lim in new_limits.items():
                old = axes.get(key) or Axis()
                axes[key] = dataclasses.replace(old, limits=list(lim))
            fp.states[i] = dataclasses.replace(s, spec=dataclasses.replace(s.spec, axes=axes))

    # ---------------------------------------------------------------- render

    def render_frames(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
                      resolver=None, deadline=None):
        """``(plan, warnings, pixels)``; ``pixels[i]`` is state *i* as HxWx3
        uint8. Holds every state's pixels at once: for inspection and tests
        (:meth:`render` streams them instead)."""
        request = request or AnimationRequest()
        deadline = Deadline.of(deadline)
        with expression_cache():
            fp, warnings = self.plan(anim, request, resolver=resolver, deadline=deadline)
            theme = anim.figure.theme if anim.figure is not None else None
            drawn = _Drawn(self._static, fp, request, theme, deadline)
            pixels = []
            for s in fp.states:
                deadline.check("draw")
                pixels.append(_decode(drawn.draw(s)[0]))
        return fp, _dedupe([*warnings, *drawn.warnings(len(fp.states))]), pixels

    def render(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
               resolver=None, deadline=None) -> AnimationResult:
        request = request or AnimationRequest()
        return self.render_formats(anim, request, [request.format], resolver=resolver,
                                   deadline=deadline)[request.format]

    def render_formats(self, anim: AnimationSpec, request: AnimationRequest, formats, *,
                       resolver=None, deadline=None) -> dict[str, AnimationResult]:
        """One result per format in ``formats`` (``request.format`` is ignored)
        from ONE plan and ONE drawing of every state: the frames are streamed
        to every encoder at once, under one deadline."""
        deadline = Deadline.of(deadline)
        if "mp4" in formats and self.ffmpeg is None:
            raise AnimationError([issue("", "format_unavailable",
                                        "MP4 needs an ffmpeg executable and none was found; "
                                        "install ffmpeg (or set IEEVISUALIZER_FFMPEG), or "
                                        "request format='gif'.")])
        with expression_cache():
            fp, warnings = self.plan(anim, request, resolver=resolver, deadline=deadline)
            deadline.check("draw")
            theme = anim.figure.theme if anim.figure is not None else None
            drawn = _Drawn(self._static, fp, request, theme, deadline)
            gif_images: list = []
            contents = {}
            if "mp4" in formats:
                stream = _tap(drawn.frames(), gif_images) if "gif" in formats \
                    else drawn.frames()
                contents["mp4"] = encode_mp4(stream, fp.fps, self.ffmpeg, deadline)
            else:
                gif_images = gif_frames(drawn.frames(), deadline)
            if "gif" in formats:
                deadline.check("encode")
                contents["gif"] = write_gif(gif_images, fp.fps, anim.loop, deadline)
        warnings.extend(drawn.warnings(len(fp.states)))
        out = {}
        for fmt in formats:
            req = dataclasses.replace(request, format=fmt)
            delay, timing = output_timing(fmt, fp.fps, anim.loop)
            out[fmt] = AnimationResult(
                content=contents[fmt], format=fmt, media_type=req.media_type,
                adapter_name=self.name, adapter_version=self.version,
                frame_count=fp.frame_count, state_count=len(fp.states), fps=fp.fps,
                frame_duration_ms=delay, poster_frame=fp.poster,
                warnings=_dedupe([*warnings, *timing, *size_warnings(req)]), request=req,
                states=[s.to_dict() for s in fp.states], frames=list(fp.frames),
                poster_png=drawn.poster_png, poster_warnings=list(drawn.poster_warnings),
                poster_spec=fp.state_of(fp.poster).spec)
        return out


class _Drawn:
    """Draws each state once, in frame order, and keeps the poster's PNG."""

    def __init__(self, static: MatplotlibAdapter, fp: FramePlan, request: AnimationRequest,
                 theme: str | None, deadline: Deadline) -> None:
        self.static, self.fp, self.request, self.theme = static, fp, request, theme
        self.deadline = deadline
        self.poster_png: bytes | None = None
        self.poster_warnings: list[RenderWarning] = []
        self._per_state: dict[tuple[str, str, str], list[int]] = {}
        self._no_data: list[int] = []

    def frames(self):
        last, pixels = None, None
        for i in self.fp.frames:
            if i != last:
                self.deadline.check("draw")
                png, ws = self.draw(self.fp.states[i])
                pixels, last = _decode(png), i
                self.deadline.count("states_drawn")
                if self.fp.frames[self.fp.poster] == i:
                    self.poster_png, self.poster_warnings = png, ws
            yield pixels

    def draw(self, s) -> tuple[bytes, list[RenderWarning]]:
        if s.spec is None:
            self._no_data.append(s.index)
            return no_data_png(s.label, self.request, self.theme), [RenderWarning(
                "frame_no_data", f"state {s.index} ({s.label}) has no data in any layer; drawn "
                "as a 'no data' frame.", "/timeline")]
        try:
            res = self.static.render(s.spec, self.request.frame_request())
        except RecursionError:  # e.g. text the layout engine cannot nest that deep
            raise AnimationError([issue("", "frame_not_drawable",
                                        f"state {s.index} ({s.label}) could not be drawn: "
                                        "its text or data nests too deeply.")]) from None
        for wn in res.warnings:
            self._per_state.setdefault((wn.code, wn.message, wn.path), []).append(s.index)
        return res.content, list(res.warnings)

    def warnings(self, n_states: int) -> list[RenderWarning]:
        out = [RenderWarning("frame_no_data", f"state {i} has no data in any layer; drawn as "
                             "a 'no data' frame.", "/timeline") for i in self._no_data]
        for (code, msg, path), idx in self._per_state.items():
            where = "every state" if len(idx) == n_states else f"states {idx}"
            out.append(RenderWarning(code, f"{msg} ({where})", "/figure" + path if path else ""))
        return out


def _tap(frames, sink: list):
    """Yield ``frames`` unchanged while quantizing each distinct one into
    ``sink`` (the GIF's palette images, for a GIF made alongside an MP4)."""
    last = image = None
    for f in frames:
        if f is not last:
            last, image = f, gif_frames([f])[0]
        sink.append(image)
        yield f


def _parse_expressions(fp: FramePlan, deadline: Deadline) -> None:
    """Build every state's expressions into the render's expression cache,
    one at a time under the deadline (a state's draw then only looks them
    up, instead of building every layer's in one uninterruptible call)."""
    from ..render.base import RenderError
    from ..render.mpl.function import parse

    for s in fp.states:
        for j, m in enumerate(s.spec.layers if s.spec is not None else ()):
            if isinstance(m, FunctionMark) and m.expression is not None:
                deadline.check("parse")
                try:
                    parse(m, f"/layers/{j}")
                except RenderError:
                    pass  # cached: the state's draw reports it


def _decode(png: bytes) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


def _num_to_iso(x: float) -> str:
    from matplotlib import dates

    d = dates.num2date(x, tz=_dt.timezone.utc).replace(tzinfo=None)
    return d.isoformat()


def _continuous_color(spec: FigureSpec, m) -> str | None:
    """The column a layer maps to a continuous colour scale, if any."""
    enc = m.encoding.get("color") if isinstance(m.encoding, dict) else None
    if enc is None or m.type not in ("heatmap", "scatter", "quiver") or "series" in m.encoding:
        return None
    ds = spec.datasets.get(m.data) if m.data is not None else None
    col = getattr(ds, "columns", {}).get(enc.field)
    if col is None or (m.type != "heatmap" and col.dtype not in ("float", "int")):
        return None
    return enc.field


def _fix_colors(fp: FramePlan, anim: AnimationSpec, warnings: list[RenderWarning],
                deadline: Deadline) -> None:
    """One colour range for every continuous colour scale across the frames
    (``axis_limits="fixed"``), else a ``color_scale_per_frame`` warning."""
    ranges: dict[tuple, list[float]] = {}
    for s in fp.states:
        if s.spec is None:
            continue
        deadline.check("limits")
        for m in s.spec.layers:
            fld = _continuous_color(s.spec, m)
            if fld is None or "color_range" in m.style:
                continue
            cols = s.spec.datasets[m.data].columns
            need = [cols[m.encoding[ch].field].values for ch in REQUIRED_CHANNELS[m.type]
                    if ch in m.encoding]  # a value is drawn only with all its positions
            vals = [v for i, v in enumerate(cols[fld].values)
                    if isinstance(v, (int, float)) and math.isfinite(v)
                    and all(c[i] is not None for c in need)]
            if vals:
                r = ranges.setdefault((m.type, m.data, fld), [min(vals), max(vals)])
                r[0], r[1] = min(r[0], min(vals)), max(r[1], max(vals))
    if not ranges:
        return
    if anim.axis_limits != "fixed":
        warnings.append(RenderWarning(
            "color_scale_per_frame", "axis_limits is 'auto', so every frame maps its own value "
            "range to the colormap and the same colour means different values in different "
            "frames; use 'fixed' (or a style color_range) to compare frames.", "/axis_limits"))
        return
    for i, s in enumerate(fp.states):
        if s.spec is None:
            continue
        layers = []
        for m in s.spec.layers:
            fld = _continuous_color(s.spec, m)
            r = ranges.get((m.type, m.data, fld)) if fld is not None else None
            if r is not None and r[0] < r[1] and "color_range" not in m.style:
                m = dataclasses.replace(m, style={**m.style, "color_range": list(r)})
            layers.append(m)
        fp.states[i] = dataclasses.replace(s, spec=dataclasses.replace(s.spec, layers=layers))


def no_data_png(label: str, request: AnimationRequest, theme: str | None = None) -> bytes:
    """An explicit empty frame in the render's theme: the state label and
    "no data", never an empty chart."""
    from matplotlib.figure import Figure

    from ..render.mpl.adapter import _figsize, render_context
    from ..render.mpl.style import resolve_theme

    req = request.frame_request()
    th = resolve_theme(request.theme if request.theme is not None else theme, "", [])
    with render_context(th):
        fig = Figure(figsize=_figsize(req), dpi=req.dpi)
        fig.text(0.5, 0.55, label, ha="center", va="center", fontsize=12)
        fig.text(0.5, 0.45, "no data", ha="center", va="center", fontsize=11,
                 color=th.muted or "0.4")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=req.dpi)
    return buf.getvalue()


__all__ = ["ADAPTER_NAME", "ADAPTER_VERSION", "MatplotlibAnimationAdapter", "no_data_png"]
