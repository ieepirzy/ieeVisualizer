"""Matplotlib animation adapter: AnimationSpec -> GIF (Pillow) or MP4 (ffmpeg).

Every distinct state is rendered as a PNG by the static
:class:`~ieeVisualizer.render.MatplotlibAdapter` (the same validation, theme,
determinism settings and drawing code as a still) and decoded to pixels, so
frame *k* is, pixel for pixel, the static render of :meth:`frame_specs`\\[k].
``matplotlib.animation`` is not used: it mutates one figure between frames,
which would make a frame differ from the still of the same state.

``axis_limits="fixed"`` is implemented in two passes: each state is first
built (not exported) to read matplotlib's automatic x/y range; the union
becomes ``limits`` in every frame spec for axes the figure left open.
Category axes and polar/3D figures keep automatic limits, with a warning.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import io

import numpy as np

from ..render.base import RenderWarning, issue
from ..render.mpl import CAPABILITIES as FIGURE_CAPABILITIES
from ..render.mpl import MatplotlibAdapter
from ..spec import Axis, FigureSpec, SpecValidationError
from .base import (
    AnimationCapabilities,
    AnimationError,
    AnimationRequest,
    AnimationResult,
    base_unmet,
    enforce_limits,
    output_timing,
)
from .encode import encode_gif, encode_mp4, find_ffmpeg
from .frames import FramePlan, plan
from .spec import AnimationSpec, FrameColumn
from .validate import validate_animation

ADAPTER_NAME = "matplotlib-animation"
ADAPTER_VERSION = "1.0.0"


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
             resolver=None) -> tuple[FramePlan, list[RenderWarning]]:
        """The frame plan with fixed axis limits applied to every state spec.
        Every resource guard (:func:`~ieeVisualizer.animation.base.enforce_limits`)
        is checked before any state is built or drawn."""
        errors = [i for i in validate_animation(anim) if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
        request = request or AnimationRequest()
        fp = plan(anim, resolver)  # checks frames/duration/poster before building
        enforce_limits(anim, request, len(fp.states))
        warnings = [RenderWarning(i.code, i.message, i.path) for i in fp.issues]
        if anim.axis_limits == "fixed":
            # A frame column's range is that of all its frames' data together
            # (the full figure): per-frame autoscaling of a lone point or date
            # is degenerate (matplotlib widens one date to +-2 years). A sweep
            # takes the union of its states, each of which is a whole figure.
            source = None
            if isinstance(anim.timeline, FrameColumn):
                from ..render.prepare import prepare

                source = [prepare(anim.figure, resolver).spec]
            self._fix_limits(fp, request, warnings, source)
        return fp, warnings

    def frame_specs(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
                    resolver=None) -> list[FigureSpec | None]:
        """The FigureSpec drawn in each output frame (None = "no data" frame)."""
        fp, _ = self.plan(anim, request, resolver=resolver)
        return [fp.state_of(k).spec for k in range(fp.frame_count)]

    def _fix_limits(self, fp: FramePlan, request: AnimationRequest,
                    warnings: list[RenderWarning], source: list[FigureSpec] | None) -> None:
        specs = source or [s.spec for s in fp.states if s.spec is not None]
        if not specs or not any(s.spec is not None for s in fp.states):
            return
        if specs[0].coordinate_system != "cartesian2d":
            warnings.append(RenderWarning(
                "axis_limits_auto", f"axis_limits 'fixed' is not applied in "
                f"{specs[0].coordinate_system}; each frame scales itself.", "/axis_limits"))
            return
        from ..render.mpl.data import infer_axes

        ranges: dict[str, list[float]] = {}
        kinds = {k: info.kind for k, info in infer_axes(specs[0], []).items()}
        for spec in specs:
            built = self._static.build_figure(spec, request.frame_request())
            for key in ("x", "y"):
                lo, hi = getattr(built.axes, f"get_{key}lim")()
                r = ranges.setdefault(key, [lo, hi])
                r[0], r[1] = min(r[0], lo), max(r[1], hi)
        new_limits = {}
        for key, (lo, hi) in ranges.items():
            current = specs[0].axes.get(key)
            have = list(current.limits) if current and current.limits else [None, None]
            if None not in have:
                continue
            if kinds.get(key) == "category":
                warnings.append(RenderWarning(
                    "axis_limits_auto", f"the {key} axis is categorical; its range is not held "
                    "fixed across frames.", "/axis_limits"))
                continue
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
                      resolver=None):
        """``(plan, warnings, pixels)``; ``pixels[i]`` is state *i* as HxWx3 uint8."""
        from PIL import Image

        request = request or AnimationRequest()
        fp, warnings = self.plan(anim, request, resolver=resolver)
        pixels = []
        per_state: dict[tuple[str, str, str], list[int]] = {}
        for s in fp.states:
            if s.spec is None:
                pixels.append(_no_data_frame(s.label, request))
                warnings.append(RenderWarning(
                    "frame_no_data", f"state {s.index} ({s.label}) has no data in any layer; "
                    "drawn as a 'no data' frame.", "/timeline"))
                continue
            res = self._static.render(s.spec, request.frame_request())
            for wn in res.warnings:
                per_state.setdefault((wn.code, wn.message, wn.path), []).append(s.index)
            pixels.append(np.asarray(Image.open(io.BytesIO(res.content)).convert("RGB")))
        for (code, msg, path), idx in per_state.items():
            where = "every state" if len(idx) == len(fp.states) else f"states {idx}"
            warnings.append(RenderWarning(code, f"{msg} ({where})",
                                          "/figure" + path if path else ""))
        return fp, _dedupe(warnings), pixels

    def render(self, anim: AnimationSpec, request: AnimationRequest | None = None, *,
               resolver=None) -> AnimationResult:
        request = request or AnimationRequest()
        if request.format == "mp4" and self.ffmpeg is None:
            raise AnimationError([issue("", "format_unavailable",
                                        "MP4 needs an ffmpeg executable and none was found; "
                                        "install ffmpeg (or set IEEVISUALIZER_FFMPEG), or "
                                        "request format='gif'.")])
        fp, warnings, pixels = self.render_frames(anim, request, resolver=resolver)
        frames = [pixels[i] for i in fp.frames]  # references; holds are not copies
        if request.format == "gif":
            content = encode_gif(frames, fp.fps, anim.loop)
        else:
            content = encode_mp4(frames, fp.fps, self.ffmpeg)
        delay, timing = output_timing(request.format, fp.fps, anim.loop)
        warnings.extend(timing)
        return AnimationResult(
            content=content, format=request.format, media_type=request.media_type,
            adapter_name=self.name, adapter_version=self.version,
            frame_count=len(frames), state_count=len(fp.states), fps=fp.fps,
            frame_duration_ms=delay, poster_frame=fp.poster, warnings=warnings,
            request=request, states=[s.to_dict() for s in fp.states], frames=list(fp.frames))


def _num_to_iso(x: float) -> str:
    from matplotlib import dates

    d = dates.num2date(x, tz=_dt.timezone.utc).replace(tzinfo=None)
    return d.isoformat()


def _no_data_frame(label: str, request: AnimationRequest) -> np.ndarray:
    """An explicit empty frame: the state label and "no data", never an empty chart."""
    from matplotlib.figure import Figure
    from PIL import Image

    from ..render.mpl.adapter import _figsize, render_context

    req = request.frame_request()
    with render_context({}):
        fig = Figure(figsize=_figsize(req), dpi=req.dpi)
        fig.text(0.5, 0.55, label, ha="center", va="center", fontsize=12)
        fig.text(0.5, 0.45, "no data", ha="center", va="center", fontsize=11, color="0.4")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=req.dpi)
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))


def _dedupe(ws: list[RenderWarning]) -> list[RenderWarning]:
    seen, out = set(), []
    for w in ws:
        if (w.code, w.message, w.path) not in seen:
            seen.add((w.code, w.message, w.path))
            out.append(w)
    return out


__all__ = ["ADAPTER_NAME", "ADAPTER_VERSION", "MatplotlibAnimationAdapter"]
