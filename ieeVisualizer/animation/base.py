"""Animation render contracts: request, result, capabilities, adapter registry.

Animation adapters are separate from figure adapters (``ieeVisualizer.render``)
because they consume an AnimationSpec and produce a moving image, but they
report what they can do the same way: a :class:`~ieeVisualizer.render.Capabilities`
for what a single frame may contain, plus the timelines, formats and limits
of the animation itself. Selection never downgrades: an adapter that lacks
something is not chosen, and the error lists what each one lacks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..render.base import Capabilities, CapabilityError, RenderError, RenderRequest, RenderWarning
from ..render.base import issue as _issue
from ..spec import FigureSpec, SpecValidationError
from .spec import MAX_DURATION_S, MAX_FRAMES, AnimationSpec, FrameColumn, TemplateTarget

ANIMATION_MEDIA_TYPES = {"gif": "image/gif", "mp4": "video/mp4"}
#: Largest frame area x output frame count (hold and repeated frames
#: included) any adapter renders or encodes; bounds memory and time.
#: Enforced by :func:`enforce_limits` for every adapter.
MAX_TOTAL_PIXELS = 120_000_000


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
    #: Frame ``poster_frame`` of this clip as PNG, when the adapter's frames
    #: are not the Matplotlib stills (Manim). None: the poster is the static
    #: Matplotlib render of the poster state, which equals that frame.
    poster_png: bytes | None = None

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
    #: Whether selection may pick this adapter when the request names none.
    #: False for an adapter with a different look (Manim): it is used only
    #: when asked for by name (``AnimationRequest(adapter=...)``).
    auto_select: bool = True
    #: Which render-work estimate :func:`enforce_limits` applies
    #: (``ieeVisualizer.animation.cost``): "matplotlib" (per state) or
    #: "manim" (per output frame).
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
                   figure: FigureSpec | None = None, *, cost_model: str = "matplotlib") -> int:
    """Every resource guard, from counts alone, before any state is built:
    frames, duration and poster (:class:`SpecValidationError`), frame
    area x output frames <= :data:`MAX_TOTAL_PIXELS` (:class:`AnimationError`,
    ``animation_too_large``) and the estimated render work <=
    :data:`~ieeVisualizer.animation.cost.MAX_RENDER_WORK_US`
    (``animation_too_expensive``; states x (layers + groups + points), see
    :mod:`ieeVisualizer.animation.cost`). Returns the output frame count.

    ``figure``: the figure with its references resolved (the base figure of
    a mark sweep or frame column; ``None`` for a template sweep). Defaults to
    ``anim.figure``, whose unresolved references count as no rows.

    :func:`render_animation` calls this for every adapter before its
    ``render``; adapters call it again themselves (they can be called directly)."""
    from .cost import MAX_RENDER_WORK_US, manim_work_us, matplotlib_work_us
    from .limits import frame_limit_issues, frame_total

    errors = [i for i in frame_limit_issues(anim, n_states) if i.severity == "error"]
    if errors:
        raise SpecValidationError(errors)
    n_frames = frame_total(anim, n_states)
    w, h = request.frame_request().size_px
    if w * h * n_frames > MAX_TOTAL_PIXELS:
        raise AnimationError([_issue("", "animation_too_large",
                                     f"{n_frames} frames of {w}x{h} px = {w * h * n_frames} "
                                     f"pixels exceed {MAX_TOTAL_PIXELS}; lower the size, dpi, "
                                     "fps, number of values or hold frames.")])
    if cost_model == "manim":
        work, unit = manim_work_us(anim, n_frames), f"{n_frames} output frames"
    else:
        work = matplotlib_work_us(anim, (w, h), n_states,
                                  figure if figure is not None else anim.figure)
        unit = f"{n_states} states"
    if work > MAX_RENDER_WORK_US:
        raise AnimationError([_issue("", "animation_too_expensive",
                                     f"{unit} would take about {work / 1e6:.0f} s to render "
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
               resolver=None) -> AnimationResult: ...


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
            missing = a.unmet(anim, request)
            if not request.adapter and not getattr(a.capabilities, "auto_select", True):
                missing = [*missing, f"automatic selection (request it with adapter={a.name!r})"]
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
    """Matplotlib first; the Manim adapter is added only if ``import manim``
    succeeds (checked once, on first use; importing this package never
    imports manim)."""
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
                     resolver=None, registry: AnimationRegistry | None = None
                     ) -> AnimationResult:
    """Validate, select an adapter, enforce the resource guards, render.

    The guards (:func:`enforce_limits`) run here for every adapter, before
    its ``render`` is called. A referenced frame dataset is resolved first to count its
    states; the adapter then gets a resolver that returns that same data, so
    the count checked is the count rendered and each reference is resolved
    once."""
    from .validate import validate_animation

    request = request or AnimationRequest()
    errors = [i for i in validate_animation(anim) if i.severity == "error"]
    if errors:
        raise SpecValidationError(errors)
    adapter = (registry or default_animation_registry()).select(anim, request)
    resolver = memoized(resolver)
    n_states, figure = counted_states(anim, resolver)
    enforce_limits(anim, request, n_states, figure,
                   cost_model=getattr(adapter.capabilities, "cost_model", "matplotlib"))
    return adapter.render(anim, request, resolver=resolver)


def counted_states(anim: AnimationSpec, resolver=None) -> tuple[int, FigureSpec | None]:
    """``(distinct states, base figure with references resolved)`` without
    building any state: a sweep's count is arithmetic, a frame column's is
    the number of distinct values in its resolved data. Pass a
    :func:`memoized` resolver so the render that follows sees the same data."""
    from ..render.prepare import prepare
    from .frames import resolved_frame_keys
    from .limits import sweep_state_count

    figure = prepare(anim.figure, resolver).spec if anim.figure is not None else None
    tl = anim.timeline
    if isinstance(tl, FrameColumn):
        return len(resolved_frame_keys(figure, tl)[0]), figure
    return sweep_state_count(tl, anim.fps), figure


def memoized(resolver):
    """``resolver`` answering each distinct DatasetRef once (None stays None).

    References are keyed by content (two equal references are one dataset),
    so every count, plan, limit pass, poster and manifest record made through
    the returned callable describes the same snapshot, even when the
    underlying source changes between calls. Wrapping a memoized resolver
    returns it unchanged, so callers can wrap freely."""
    if resolver is None or getattr(resolver, "_ieev_memoized", False):
        return resolver
    from ..spec._common import canonical_json
    from ..spec.data import dataset_to_dict

    cache: dict[Any, tuple[Any, Any]] = {}

    def resolve(ref):
        try:
            key: Any = canonical_json(dataset_to_dict(ref))
        except Exception:  # noqa: BLE001 - not a serializable reference: key by identity
            key = ("id", id(ref))
        hit = cache.get(key)
        if hit is None:
            hit = cache[key] = (ref, resolver(ref))  # the ref is kept: ids stay unique
        return hit[1]

    resolve._ieev_memoized = True  # type: ignore[attr-defined]
    return resolve

