"""Renderer-neutral contracts: requests, results, warnings, capabilities, errors.

Nothing here imports matplotlib. A :class:`RendererAdapter` turns a validated
:class:`~ieeVisualizer.spec.FigureSpec` into bytes; the request says what
output is wanted, the result carries the bytes plus every fidelity warning the
adapter raised while rendering (an option it ignored, rows it could not draw,
a theme it does not know). Warnings are part of the result, never only a log
line, so an agent or a report pipeline can surface them next to the figure.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from ..spec import DatasetRef, DataSnapshot, FigureSpec, SpecError, ValidationIssue

#: Media type per output format. A format being listed here only means a
#: request may name it; whether an adapter can produce it is a capability.
MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "html": "text/html",
}
#: Formats that are vector (scalable) outputs.
VECTOR_FORMATS = frozenset({"svg", "pdf"})
#: Formats that are rasterised at ``dpi``.
RASTER_FORMATS = frozenset({"png"})

SIZE_UNITS = ("in", "px")
MAX_PIXELS_PER_SIDE = 20000

#: Signature of the callable that turns a :class:`DatasetRef` into data.
Resolver = Callable[[DatasetRef], DataSnapshot]


@dataclass(frozen=True)
class RenderWarning:
    """A fidelity warning attached to a render.

    ``code`` is a stable identifier, ``message`` a sentence naming what was
    lost or approximated, ``path`` a JSON pointer into the spec ("" when the
    warning is about the request or the figure as a whole).
    """

    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class RenderRequest:
    """What output to produce.

    ``width``/``height`` are in ``units`` (``"in"`` or ``"px"``); ``dpi``
    converts between the two and sets raster resolution. ``theme`` overrides
    the spec's theme name. ``adapter`` pins a renderer by name; otherwise one
    is selected by capability.
    """

    format: str = "png"
    width: float = 6.4
    height: float = 4.8
    units: str = "in"
    dpi: float = 100.0
    theme: str | None = None
    adapter: str | None = None

    def __post_init__(self) -> None:
        if self.format not in MEDIA_TYPES:
            raise ValueError(
                f"unknown output format {self.format!r}; use one of {', '.join(MEDIA_TYPES)}."
            )
        if self.units not in SIZE_UNITS:
            raise ValueError(f"units must be 'in' or 'px', got {self.units!r}.")
        for name in ("width", "height", "dpi"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
                    or v <= 0:
                raise ValueError(f"{name} must be a positive finite number, got {v!r}.")
        w, h = self.size_px
        if max(w, h) > MAX_PIXELS_PER_SIDE:
            raise ValueError(
                f"requested {w}x{h} px exceeds {MAX_PIXELS_PER_SIDE} px per side; lower the "
                "size or dpi."
            )
        if min(w, h) < 1:
            raise ValueError(f"requested size rounds to {w}x{h} px; increase the size or dpi.")

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES[self.format]

    @property
    def size_in(self) -> tuple[float, float]:
        """Figure size in inches."""
        if self.units == "in":
            return float(self.width), float(self.height)
        return self.width / self.dpi, self.height / self.dpi

    @property
    def size_px(self) -> tuple[int, int]:
        """Raster size in pixels (what a PNG of this request measures)."""
        if self.units == "px":
            return int(round(self.width)), int(round(self.height))
        return int(round(self.width * self.dpi)), int(round(self.height * self.dpi))

    def to_dict(self) -> dict[str, Any]:
        d = {"format": self.format, "width": self.width, "height": self.height,
             "units": self.units, "dpi": self.dpi}
        if self.theme is not None:
            d["theme"] = self.theme
        if self.adapter is not None:
            d["adapter"] = self.adapter
        return d


@dataclass(frozen=True)
class RenderResult:
    """Bytes of one rendered output plus how they were made."""

    content: bytes
    format: str
    media_type: str
    adapter_name: str
    adapter_version: str
    warnings: list[RenderWarning] = field(default_factory=list)
    request: RenderRequest | None = None

    @property
    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.content).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class Capabilities:
    """What an adapter can draw and produce. Selection compares these with
    what a spec + request actually need (:func:`requirements_for`)."""

    coordinate_systems: frozenset[str]
    mark_types: frozenset[str]
    output_formats: frozenset[str]
    annotation_types: frozenset[str] = frozenset({"text", "reference_line", "reference_band"})
    three_d: bool = False
    animation: bool = False
    interactive: bool = False
    vector_export: bool = False
    #: Most data points the adapter draws without downsampling; ``None`` = no limit.
    max_points: int | None = None
    #: Mark types the adapter cannot draw in a given coordinate system even
    #: though it supports both separately.
    unsupported: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate_systems": sorted(self.coordinate_systems),
            "mark_types": sorted(self.mark_types),
            "output_formats": sorted(self.output_formats),
            "annotation_types": sorted(self.annotation_types),
            "three_d": self.three_d,
            "animation": self.animation,
            "interactive": self.interactive,
            "vector_export": self.vector_export,
            "max_points": self.max_points,
            "unsupported": {k: sorted(v) for k, v in sorted(self.unsupported.items())},
        }


@runtime_checkable
class RendererAdapter(Protocol):
    """A backend that renders FigureSpecs.

    ``render`` must refuse a spec with validation errors, must not draw an
    unresolved :class:`DatasetRef`, and must report every option it could not
    honour as a :class:`RenderWarning` on the result.
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def capabilities(self) -> Capabilities: ...

    def render(
        self,
        spec: FigureSpec,
        request: RenderRequest,
        *,
        resolver: Resolver | None = None,
    ) -> RenderResult: ...


# --------------------------------------------------------------------------
# Errors. All carry structured ``issues`` (path, code, message, severity) like
# spec errors, so one handler can report any failure to an agent.
# --------------------------------------------------------------------------


class RenderError(SpecError):
    """The spec is valid but cannot be drawn honestly (e.g. a series that is
    empty once null rows are left out, an unparsable datetime)."""


class UnresolvedDatasetError(RenderError):
    """A layer is bound to a :class:`DatasetRef` and no resolver was given
    (or the resolver failed / returned something unusable)."""


class CapabilityError(RenderError):
    """No adapter (or not the pinned one) can satisfy what the spec needs.

    ``requirements`` lists what was needed; ``unmet`` maps each candidate
    adapter name to the requirements it does not meet.
    """

    def __init__(self, issues, requirements: list[str], unmet: dict[str, list[str]]):
        self.requirements = list(requirements)
        self.unmet = {k: list(v) for k, v in unmet.items()}
        super().__init__(issues)


def issue(path: str, code: str, message: str, severity: str = "error") -> ValidationIssue:
    return ValidationIssue(path, code, message, severity)
