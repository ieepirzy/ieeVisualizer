"""Matplotlib renderer adapter: FigureSpec -> PNG / SVG / PDF bytes.

Headless and side-effect free: figures are ``matplotlib.figure.Figure``
objects created directly (never through ``pyplot``), so no global figure
manager or backend is touched and nothing needs closing. Rendering runs
inside ``matplotlib.style.context("default")`` plus a fixed ``rc_context``,
so a user's matplotlibrc or style cannot change the output, and with
metadata that strips timestamps. Same spec + same library versions -> the
same SVG bytes, in this process or a fresh one.

Known limitation: matplotlib's rcParams are process-global, so concurrent
renders from several threads must be serialised by the caller.
"""

from __future__ import annotations

import contextlib
import io
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any

from ...spec import MARK_TYPES, FigureSpec
from ..base import (
    MEDIA_TYPES,
    RASTER_FORMATS,
    Capabilities,
    CapabilityError,
    RenderRequest,
    RenderResult,
    RenderWarning,
    Resolver,
    issue,
)
from ..prepare import PreparedSpec, prepare

ADAPTER_NAME = "matplotlib"
ADAPTER_VERSION = "1.0.0"
#: Salt for SVG element ids; fixed so ids (and therefore bytes) are stable.
SVG_HASHSALT = "ieeVisualizer"

CAPABILITIES = Capabilities(
    coordinate_systems=frozenset({"cartesian2d", "cartesian3d", "polar"}),
    mark_types=frozenset(MARK_TYPES),
    output_formats=frozenset({"png", "svg", "pdf"}),
    three_d=True,
    animation=False,
    interactive=False,
    vector_export=True,
    max_points=1_000_000,
    max_bins=10_000,
)


@dataclass
class BuiltFigure:
    """A drawn figure before export (the inspection hook used by tests).

    ``layer_artists[i]`` are the matplotlib artists drawn for ``spec.layers[i]``;
    ``annotation_artists`` those for the annotations that could be placed.
    """

    figure: Any
    axes: Any
    warnings: list[RenderWarning] = field(default_factory=list)
    layer_artists: dict[int, list[Any]] = field(default_factory=dict)
    annotation_artists: list[Any] = field(default_factory=list)
    caption: Any = None


def _versions() -> tuple[str, str]:
    import matplotlib

    from ... import __version__

    return __version__, matplotlib.__version__


def _metadata(fmt: str) -> dict[str, str | None]:
    ours, mpl = _versions()
    software = f"ieeVisualizer {ours} (matplotlib {mpl})"
    if fmt == "svg":
        return {"Date": None, "Creator": software}
    if fmt == "pdf":
        return {"CreationDate": None, "ModDate": None, "Creator": software,
                "Producer": software}
    if fmt == "png":
        return {"Software": software}
    return {}


@contextlib.contextmanager
def render_context(theme: Any):
    """matplotlib defaults + theme + determinism settings, restored on exit.
    ``theme`` is a :class:`~ieeVisualizer.themes.Theme` or an rcParams dict."""
    import matplotlib
    import matplotlib.style

    from .style import theme_rc

    rc = dict(theme) if isinstance(theme, dict) else theme_rc(theme)
    rc.update({"svg.hashsalt": SVG_HASHSALT, "svg.fonttype": "path"})
    with matplotlib.style.context("default"), matplotlib.rc_context(rc):
        yield


def _figsize(request: RenderRequest) -> tuple[float, float]:
    w, h = request.size_in
    if request.format in RASTER_FORMATS:
        # Agg truncates width*dpi to an int; nudge so the PNG measures exactly
        # request.size_px instead of one pixel less after float rounding.
        pw, ph = request.size_px
        w, h = (pw + 1e-6) / request.dpi, (ph + 1e-6) / request.dpi
    return w, h


class MatplotlibAdapter:
    """Renders FigureSpecs with matplotlib (Agg / SVG / PDF backends)."""

    @property
    def name(self) -> str:
        return ADAPTER_NAME

    @property
    def version(self) -> str:
        return ADAPTER_VERSION

    @property
    def capabilities(self) -> Capabilities:
        return CAPABILITIES

    def __repr__(self) -> str:
        return f"MatplotlibAdapter(version={ADAPTER_VERSION!r})"

    # ------------------------------------------------------------------ build

    def build_figure(
        self,
        spec: FigureSpec,
        request: RenderRequest | None = None,
        *,
        resolver: Resolver | None = None,
    ) -> BuiltFigure:
        """Validate, resolve and draw ``spec`` into a new Figure (no export).

        The figure is laid out once more at the end, inside the same theme
        context, exactly as :meth:`render` lays it out when it saves: the
        artists' positions, the value-label arrangement and the layout
        warnings are those a render at this request ships. Drawing it again
        outside that context (with other rcParams) may lay it out
        differently."""
        request = request or RenderRequest()
        prepared = prepare(spec, resolver)
        self._check_capabilities(prepared.spec, request)
        warnings = list(prepared.warnings)
        theme = self._theme(prepared.spec, request, warnings)
        with render_context(theme), _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            built = self._draw(prepared, request, warnings, theme)
            built.figure.draw_without_rendering()
        _backend_warnings(caught, built.warnings)
        built.warnings[:] = _dedupe(built.warnings)  # same list: draw-time warnings land here
        return built

    def render(
        self,
        spec: FigureSpec,
        request: RenderRequest,
        *,
        resolver: Resolver | None = None,
    ) -> RenderResult:
        prepared = prepare(spec, resolver)
        self._check_capabilities(prepared.spec, request)
        warnings = list(prepared.warnings)
        theme = self._theme(prepared.spec, request, warnings)
        buf = io.BytesIO()
        with render_context(theme), _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            built = self._draw(prepared, request, warnings, theme)
            built.figure.savefig(buf, format=request.format, dpi=request.dpi,
                                 metadata=_metadata(request.format))
        _backend_warnings(caught, built.warnings)
        return RenderResult(
            content=buf.getvalue(),
            format=request.format,
            media_type=MEDIA_TYPES[request.format],
            adapter_name=self.name,
            adapter_version=self.version,
            warnings=_dedupe(built.warnings),
            request=request,
            theme=theme.record(),
        )

    # --------------------------------------------------------------- internals

    def _check_capabilities(self, spec: FigureSpec, request: RenderRequest) -> None:
        from ..registry import requirements_for

        reqs = requirements_for(spec, request)
        missing = reqs.unmet(self.capabilities)
        if missing:
            raise CapabilityError(
                [issue("", "unsupported_by_adapter",
                       f"the {self.name} adapter cannot provide: {'; '.join(missing)}.")],
                reqs.describe(), {self.name: missing},
            )

    @staticmethod
    def _theme(spec: FigureSpec, request: RenderRequest, warnings: list[RenderWarning]):
        from .style import resolve_theme

        if request.theme is not None:
            return resolve_theme(request.theme, "", warnings)
        return resolve_theme(spec.theme, "/theme", warnings)

    @staticmethod
    def _draw(prepared: PreparedSpec, request: RenderRequest,
              warnings: list[RenderWarning], theme: Any = None) -> BuiltFigure:
        from matplotlib.figure import Figure

        from .data import infer_axes
        from .decorate import apply_annotations, apply_axes, apply_caption, apply_legend
        from .marks import DRAW, DrawContext, fit_value_labels, report_bar_overlaps

        spec = prepared.spec
        axes_info = infer_axes(spec, warnings)
        figsize = _figsize(request)
        fig = Figure(figsize=figsize, dpi=request.dpi, layout="constrained")
        projection = {"cartesian3d": "3d", "polar": "polar"}.get(spec.coordinate_system)
        ax = fig.add_subplot(projection=projection)
        ctx = DrawContext(spec=spec, fig=fig, ax=ax, axes=axes_info, warnings=warnings,
                          theme=theme)
        for i, mark in enumerate(spec.layers):
            ctx.layer_artists[i] = DRAW[mark.type](ctx, i, mark)
        report_bar_overlaps(ctx)
        apply_axes(ctx)
        ann = apply_annotations(ctx)
        _label_locale(spec, theme, warnings)
        cap: dict[str, Any] = {}
        if theme is not None and theme.muted:
            cap["color"] = theme.muted
        if theme is not None and theme.typography.caption:
            cap["fontsize"] = theme.typography.caption
        caption = apply_caption(fig, spec.caption, figsize[1], figsize[0], **cap)
        # Value labels are fitted by the layout engine on every draw, so they
        # are already inside the (widened) axes when the legend pass below
        # lays the figure out to measure it: an "auto" legend that would
        # cover a value label is moved outside like one covering data.
        fit_value_labels(ctx)
        apply_legend(ctx)  # last: an "auto" legend is placed against the final layout
        return BuiltFigure(figure=fig, axes=ax, warnings=warnings,
                           layer_artists=ctx.layer_artists, annotation_artists=ann,
                           caption=caption)


def _label_locale(spec: FigureSpec, theme: Any, warnings: list[RenderWarning]) -> None:
    """A template that wrote numbers into text (e.g. funnel rates) records the
    locale it used; say so when this render's theme writes numbers
    differently, instead of silently mixing conventions."""
    meta = spec.metadata.get("template") if isinstance(spec.metadata, dict) else None
    used = meta.get("label_locale") if isinstance(meta, dict) else None
    if used is None or theme is None:
        return
    have = theme.locale.tag if theme.locale is not None else "und"
    if used != have:
        warnings.append(RenderWarning(
            "label_locale_mismatch",
            f"text written by the template uses number locale {used!r} but theme "
            f"{theme.name!r} formats numbers as {have!r}; rebuild the spec with "
            f"theme={theme.name!r} so labels and axes agree.", "/metadata/template/label_locale"))


def _backend_warnings(caught, out: list[RenderWarning]) -> None:
    """Python warnings raised while drawing become render warnings (e.g. a
    layout that could not be applied). Deprecation noise from libraries says
    nothing about this figure; it is re-issued to the caller unchanged."""
    for w in caught:
        if issubclass(w.category, (DeprecationWarning, PendingDeprecationWarning)):
            _warnings.warn_explicit(w.message, w.category, w.filename, w.lineno)
            continue
        out.append(RenderWarning("backend_warning", f"{w.category.__name__}: {w.message}", ""))


def _dedupe(ws: list[RenderWarning]) -> list[RenderWarning]:
    seen, out = set(), []
    for w in ws:
        key = (w.code, w.message, w.path)
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out


def build_figure(
    spec: FigureSpec,
    request: RenderRequest | None = None,
    *,
    resolver: Resolver | None = None,
) -> BuiltFigure:
    """Module-level shortcut for :meth:`MatplotlibAdapter.build_figure`."""
    return MatplotlibAdapter().build_figure(spec, request, resolver=resolver)
