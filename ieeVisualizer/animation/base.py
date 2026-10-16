"""Animation render contracts: request, result, capabilities, adapter registry.

Adapters report what one frame may contain (a figure
:class:`~ieeVisualizer.render.Capabilities`) plus timelines, formats and
limits. Selection never downgrades: an adapter that lacks something is not
chosen, and the error lists what each one lacks.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..render.base import Capabilities, CapabilityError, RenderError, RenderRequest, RenderWarning
from ..render.base import issue as _issue
from ..spec import FigureSpec, SpecValidationError
from .spec import MAX_DURATION_S, MAX_FRAMES, AnimationSpec, FrameColumn, TemplateTarget

ANIMATION_MEDIA_TYPES = {"gif": "image/gif", "mp4": "video/mp4"}
#: Largest frame area x output frame count (holds included). With
#: :data:`MAX_FRAME_PIXELS` it bounds memory (frames are streamed; measured
#: peaks in ``docs/ANIMATION.md``).
MAX_TOTAL_PIXELS = 120_000_000
#: Largest single frame (3840x2160).
MAX_FRAME_PIXELS = 8_294_400


class AnimationError(RenderError):
    """The animation is valid but cannot be produced (e.g. no ffmpeg for MP4)."""


@dataclass(frozen=True)
class AnimationRequest:
    """Output format and the size/dpi/theme of every frame (as for a still)."""

    format: str = "gif"
    width: float = 6.4
    height: float = 4.8
    units: str = "in"
    dpi: float = 100.0
    theme: str | None = None
    adapter: str | None = None

    def __post_init__(self) -> None:
        if self.format not in ANIMATION_MEDIA_TYPES:
            raise ValueError(f"unknown animation format {self.format!r}; use one of "
                             f"{', '.join(ANIMATION_MEDIA_TYPES)}.")
        try:
            self.frame_request()  # validates size/dpi/units
        except OverflowError:  # width x dpi beyond float range: no pixel size exists
            raise ValueError(f"width={self.width!r}, height={self.height!r}, dpi={self.dpi!r} "
                             f"({self.units}) give no representable pixel size; lower the "
                             "size or dpi.") from None

    def frame_request(self, fmt: str = "png") -> RenderRequest:
        return RenderRequest(format=fmt, width=self.width, height=self.height,
                             units=self.units, dpi=self.dpi, theme=self.theme)

    @property
    def media_type(self) -> str:
        return ANIMATION_MEDIA_TYPES[self.format]

    def to_dict(self) -> dict[str, Any]:
        d = {"format": self.format, "width": self.width, "height": self.height,
             "units": self.units, "dpi": self.dpi}
        for k in ("theme", "adapter"):
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        return d


@dataclass(frozen=True)
class AnimationResult:
    content: bytes
    format: str
    media_type: str
    adapter_name: str
    adapter_version: str
    frame_count: int
    state_count: int
    fps: int
    #: Delay actually stored per frame (GIF rounds to 10 ms).
    frame_duration_ms: float
    poster_frame: int
    warnings: list[RenderWarning] = field(default_factory=list)
    request: AnimationRequest | None = None
    #: Per-state record (value, label, omitted layers); see FrameState.to_dict.
    states: list[dict[str, Any]] = field(default_factory=list)
    #: State index shown in each frame.
    frames: list[int] = field(default_factory=list)
    #: Frame ``poster_frame`` of this clip as PNG, kept when it was drawn
    #: (Matplotlib: the static render of the poster state, i.e. its still).
    poster_png: bytes | None = None
    poster_warnings: list[RenderWarning] = field(default_factory=list)
    #: The poster state's FigureSpec, when the adapter drew one (its data is
    #: recorded in an export's manifest for a template sweep).
    poster_spec: FigureSpec | None = None

    @property
    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.content).hexdigest()

    @property
    def duration_s(self) -> float:
        return self.frame_count * self.frame_duration_ms / 1000.0


@dataclass(frozen=True)
class AnimationCapabilities:
    #: What one frame may contain (marks, coordinate systems, annotations).
    figure: Capabilities
    #: ``parameter_sweep:mark``, ``parameter_sweep:template``, ``frame_column``.
    timelines: frozenset[str]
    output_formats: frozenset[str]
    max_frames: int = MAX_FRAMES
    max_duration_s: float = MAX_DURATION_S
    #: Templates a template sweep may use (``None`` = any registered template).
    templates: frozenset[str] | None = None
    #: Why a format or feature is missing (e.g. "mp4: ffmpeg not found").
    notes: tuple[str, ...] = ()
    #: Whether selection may pick this adapter when the request names none
    #: (False: used only when named, ``AnimationRequest(adapter=...)``).
    auto_select: bool = True
    #: Which render-work estimate :func:`enforce_limits` applies
    #: (``ieeVisualizer.animation.cost``).
    cost_model: str = "matplotlib"

    def to_dict(self) -> dict[str, Any]:
        return {"figure": self.figure.to_dict(), "timelines": sorted(self.timelines),
                "output_formats": sorted(self.output_formats), "max_frames": self.max_frames,
                "max_duration_s": self.max_duration_s,
                "templates": None if self.templates is None else sorted(self.templates),
                "notes": list(self.notes), "auto_select": self.auto_select,
                "cost_model": self.cost_model}


def timeline_kind(anim: AnimationSpec) -> str:
    tl = anim.timeline
    if isinstance(tl, FrameColumn):
        return "frame_column"
    return "parameter_sweep:" + ("template" if isinstance(tl.target, TemplateTarget) else "mark")


def base_unmet(anim: AnimationSpec, request: AnimationRequest,
               caps: AnimationCapabilities) -> list[str]:
    """Requirements of ``anim`` + ``request`` that ``caps`` does not meet,
    judged on the timeline, the format and the base figure (if any)."""
    from ..render.registry import requirements_for

    out = []
    kind = timeline_kind(anim)
    if kind not in caps.timelines:
        out.append(f"timeline {kind!r} (supports {', '.join(sorted(caps.timelines))})")
    if request.format not in caps.output_formats:
        note = "; ".join(caps.notes)
        out.append(f"output format {request.format!r} (supports "
                   f"{', '.join(sorted(caps.output_formats)) or 'none'}"
                   f"{'; ' + note if note else ''})")
    tl = anim.timeline
    if caps.templates is not None and kind.endswith("template") \
            and tl.target.name not in caps.templates:
        out.append(f"template {tl.target.name!r} (supports {', '.join(sorted(caps.templates))})")
    if anim.figure is not None:
        reqs = requirements_for(anim.figure, request.frame_request())
        out.extend(m for m in reqs.unmet(caps.figure) if not m.startswith("output format"))
    return out


def enforce_limits(anim: AnimationSpec, request: AnimationRequest, n_states: int,
                   figure: FigureSpec | None = None, *, cost_model: str = "matplotlib",
                   index=None) -> int:
    """Every guard from counts alone, before any state is built: frames,
    duration and poster (:class:`SpecValidationError`), frame and total pixels
    (``animation_too_large``) and the estimated work
    (``animation_too_expensive``, an early refusal only: the deadline bounds
    time). ``figure``: the resolved base figure (``None`` for a template
    sweep; default ``anim.figure``). Returns the output frame count."""
    from .cost import MAX_RENDER_WORK_US, manim_work_us, matplotlib_work_us
    from .limits import frame_limit_issues, frame_total

    errors = [i for i in frame_limit_issues(anim, n_states) if i.severity == "error"]
    if errors:
        raise SpecValidationError(errors)
    n_frames = frame_total(anim, n_states)
    w, h = request.frame_request().size_px
    if w * h > MAX_FRAME_PIXELS:
        raise AnimationError([_issue("", "animation_too_large",
                                     f"a {w}x{h} px frame has {w * h} pixels; an animation "
                                     f"frame has at most {MAX_FRAME_PIXELS} (3840x2160). Lower "
                                     "the size or dpi.")])
    if w * h * n_frames > MAX_TOTAL_PIXELS:
        raise AnimationError([_issue("", "animation_too_large",
                                     f"{n_frames} frames of {w}x{h} px = {w * h * n_frames} "
                                     f"pixels exceed {MAX_TOTAL_PIXELS}; lower the size, dpi, "
                                     "fps, number of values or hold frames.")])
    if cost_model == "manim":
        work, unit = manim_work_us(anim, n_frames), f"{n_frames} output frames"
    else:
        work = matplotlib_work_us(anim, (w, h), n_states,
                                  figure if figure is not None else anim.figure, index)
        unit = f"{n_states} states"
    if work > MAX_RENDER_WORK_US:
        raise AnimationError([_issue("", "animation_too_expensive",
                                     f"{unit} would take an estimated {work / 1e6:.0f} s to render "
                                     f"with {cost_model} (estimated from the number of states, "
                                     "layers, series and points); at most "
                                     f"{MAX_RENDER_WORK_US / 1e6:g} s is accepted. Use fewer "
                                     "states, layers, samples or rows, or a smaller frame "
                                     "size.")])
    return n_frames


def output_timing(fmt: str, fps: int, loop: bool) -> tuple[float, list[RenderWarning]]:
    """``(frame_duration_ms actually stored, warnings)`` for an output format;
    shared by every adapter so results report timing the same way."""
    from .encode import gif_delay_ms

    if fmt == "gif":
        delay = float(gif_delay_ms(fps))
        if abs(delay - 1000.0 / fps) > 1e-9:
            return delay, [RenderWarning(
                "frame_timing_rounded", f"GIF stores delays in 10 ms steps: {fps} fps is "
                f"played at {delay:g} ms per frame ({1000.0 / delay:.3g} fps).", "/fps")]
        return delay, []
    warnings = []
    if loop:
        warnings.append(RenderWarning("loop_ignored", "MP4 has no loop flag; looping is up to "
                                      "the player.", "/loop"))
    return 1000.0 / fps, warnings


def size_warnings(request: AnimationRequest) -> list[RenderWarning]:
    """``frame_size_padded`` for an MP4 of odd width or height: yuv420p needs
    even sizes, so the stored frames are one pixel larger (the GIF and the
    poster keep the requested size)."""
    w, h = request.frame_request().size_px
    if request.format != "mp4" or not (w % 2 or h % 2):
        return []
    return [RenderWarning("frame_size_padded", f"MP4 (yuv420p) stores even frame sizes: the "
                          f"requested {w}x{h} px frames are stored as {w + w % 2}x{h + h % 2} "
                          "px.", "")]


@runtime_checkable
class AnimationAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def capabilities(self) -> AnimationCapabilities: ...

    def unmet(self, anim: AnimationSpec, request: AnimationRequest) -> list[str]: ...

    def render(self, anim: AnimationSpec, request: AnimationRequest, *,
               resolver=None, deadline=None) -> AnimationResult: ...


class AnimationRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AnimationAdapter] = {}

    def register(self, adapter: AnimationAdapter, *, replace: bool = False) -> AnimationAdapter:
        if adapter.name in self._adapters and not replace:
            raise ValueError(f"an animation adapter named {adapter.name!r} is registered.")
        self._adapters[adapter.name] = adapter
        return adapter

    def names(self) -> list[str]:
        return list(self._adapters)

    def get(self, name: str) -> AnimationAdapter:
        try:
            return self._adapters[name]
        except KeyError:
            raise KeyError(f"no animation adapter named {name!r}; registered: "
                           f"{', '.join(self._adapters) or '(none)'}.") from None

    def select(self, anim: AnimationSpec, request: AnimationRequest) -> AnimationAdapter:
        cands = [self.get(request.adapter)] if request.adapter else list(self._adapters.values())
        unmet: dict[str, list[str]] = {}
        for a in cands:
            if not request.adapter and not getattr(a.capabilities, "auto_select", True):
                # not asked about further: checking may be costly (manim's import)
                unmet[a.name] = [f"automatic selection (request it with adapter={a.name!r})"]
                continue
            missing = a.unmet(anim, request)
            if not missing:
                return a
            unmet[a.name] = missing
        detail = "; ".join(f"{n} cannot provide {', '.join(m)}" for n, m in unmet.items())
        raise CapabilityError([_issue("", "no_capable_adapter",
                                      f"no animation adapter can produce this as "
                                      f"{request.format}: {detail or 'none registered'}.")],
                              [timeline_kind(anim), request.format], unmet)


_DEFAULT: AnimationRegistry | None = None


def default_animation_registry() -> AnimationRegistry:
    """Matplotlib first; the Manim adapter only if ``import manim`` succeeds
    (checked once, on first use; importing this package never imports manim)."""
    global _DEFAULT
    if _DEFAULT is None:
        from .manim_adapter import ManimAdapter, manim_available
        from .mpl import MatplotlibAnimationAdapter

        reg = AnimationRegistry()
        reg.register(MatplotlibAnimationAdapter())
        if manim_available():
            reg.register(ManimAdapter())
        _DEFAULT = reg
    return _DEFAULT


def render_animation(anim: AnimationSpec, request: AnimationRequest | None = None, *,
                     resolver=None, registry: AnimationRegistry | None = None,
                     deadline=None, isolation: str | None = None) -> AnimationResult:
    """Validate, select an adapter, enforce the guards, render, all under
    ``deadline`` (seconds or a shared
    :class:`~ieeVisualizer.animation.deadline.Deadline`; default 60 s), in a
    worker process unless ``isolation="inline"`` (see
    :mod:`ieeVisualizer.animation.isolate`). Each reference is resolved once,
    so the count checked is the count rendered."""
    from .deadline import Deadline
    from .isolate import isolation_mode, resolved_payload, run_isolated, table_resolver
    from .validate import validate_animation

    request = request or AnimationRequest()
    deadline = Deadline.of(deadline)
    resolver = memoized(resolver)
    if isolation_mode(isolation) == "process":
        table = resolved_payload(anim.figure, resolver)
        return run_isolated("render", {"anim": anim, "request": request, "registry": registry,
                                       "resolver": table_resolver(table) if table else None},
                            deadline)
    errors = [i for i in validate_animation(anim, deadline=deadline) if i.severity == "error"]
    if errors:
        raise SpecValidationError(errors)
    adapter = (registry or default_animation_registry()).select(anim, request)
    n_states, figure, index = counted_states(anim, resolver, deadline)
    enforce_limits(anim, request, n_states, figure, index=index,
                   cost_model=getattr(adapter.capabilities, "cost_model", "matplotlib"))
    return adapter.render(anim, request, resolver=resolver, deadline=deadline)


def counted_states(anim: AnimationSpec, resolver=None, deadline=None):
    """``(states, resolved base figure without undrawn datasets, frame index
    or None)`` without building any state (kept for the call with a
    ``deadline``)."""
    from .spec import animation_hash

    key = ("counted", animation_hash(anim), id(resolver)) if deadline is not None else None
    if key in (deadline.memo if deadline is not None else ()):
        return deadline.memo[key]
    from ..render.prepare import prepare
    from .frames import drawn_only, resolved_frame_index
    from .limits import sweep_state_count

    if deadline is not None:
        deadline.check("prepare")
    figure = None
    if anim.figure is not None:
        drawn = drawn_only(anim.figure)
        if resolver is not None:  # the data caps hold for resolved data too
            from ..render.prepare import bound_refs
            from .validate import data_issues

            resolved = {k: resolver(r) for k, r in bound_refs(drawn).items()}
            errors = data_issues(dataclasses.replace(drawn, datasets={**drawn.datasets,
                                                                      **resolved}))
            if errors:
                raise SpecValidationError(errors)
        figure = prepare(drawn, resolver).spec
    tl = anim.timeline
    if isinstance(tl, FrameColumn):
        index = resolved_frame_index(figure, tl)
        out = (len(index.keys), figure, index)
    else:
        out = (sweep_state_count(tl, anim.fps), figure, None)
    if deadline is not None:
        deadline.memo[key] = out
    return out


def memoized(resolver):
    """``resolver`` answering each distinct DatasetRef once (None stays None).

    References are keyed by content (two equal references are one dataset),
    so every count, plan, limit pass, poster and manifest record made through
    the returned callable describes the same snapshot, even when the
    underlying source changes between calls. Wrapping a memoized resolver
    returns it unchanged, so callers can wrap freely."""
    if resolver is None or getattr(resolver, "_ieev_memoized", False):
        return resolver
    cache: dict[Any, tuple[Any, Any]] = {}

    def resolve(ref):
        key = ref_key(ref)
        hit = cache.get(key)
        if hit is None:
            hit = cache[key] = (ref, resolver(ref))  # the ref is kept: ids stay unique
        return hit[1]

    resolve._ieev_memoized = True  # type: ignore[attr-defined]
    return resolve


def ref_key(ref) -> Any:
    """A DatasetRef's identity by content (its canonical JSON)."""
    from ..spec._common import canonical_json
    from ..spec.data import dataset_to_dict

    try:
        return canonical_json(dataset_to_dict(ref))
    except Exception:  # noqa: BLE001 - not a serializable reference: key by identity
        return ("id", id(ref))
