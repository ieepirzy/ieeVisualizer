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
import math
import warnings as _warnings
from dataclasses import dataclass, field, replace
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
ADAPTER_VERSION = "1.1.0"  # 1.1.0: band and errorbar marks, interval encodings
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
    intervals=True,
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
    context: Any = None
    #: Profile renders: constrained layout gave up in a pass before the save.
    measured_collapse: bool = False


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
def render_context(theme: Any, min_pt: float = 0.0):
    """matplotlib defaults + theme + determinism settings, restored on exit.
    ``theme`` is a :class:`~ieeVisualizer.themes.Theme` or an rcParams dict;
    ``min_pt`` raises every rcParams font size below it (profile renders)."""
    import matplotlib
    import matplotlib.style

    from .legibility import font_floor_rc
    from .style import theme_rc

    rc = dict(theme) if isinstance(theme, dict) else theme_rc(theme)
    rc.update({"svg.hashsalt": SVG_HASHSALT, "svg.fonttype": "path"})
    with matplotlib.style.context("default"), matplotlib.rc_context(rc):
        from .style import with_fallbacks

        floor = font_floor_rc(min_pt) if min_pt else {}
        floor["font.family"] = with_fallbacks(matplotlib.rcParams["font.family"])
        from .style import quiet_fallback_weights

        with matplotlib.rc_context(floor), quiet_fallback_weights():
            yield


def _min_pt(request: RenderRequest) -> float:
    profile = request.render_profile
    if profile is None:
        return 0.0
    return profile.min_text_pt(_figsize(request)[0], request.display_width)


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
        with render_context(theme, _min_pt(request)), \
                _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            built = self._fitted(prepared, request, warnings, theme, caught)
            if request.profile is None:
                built.figure.draw_without_rendering()
            else:  # the draw the audit measures, as a PNG save would make it
                from .legibility import recording_canvas

                recording_canvas(built.figure).draw()
            _audit(built, request)
        _backend_warnings(caught, built.warnings)
        _collapsed(built.warnings, request)
        built.warnings[:] = _dedupe(built.warnings)  # same list: draw-time warnings land here
        return built

    def draw_into(
        self,
        spec: FigureSpec,
        ax: Any,
        *,
        resolver: Resolver | None = None,
        role_styling: bool = True,
    ) -> BuiltFigure:
        """Validate, resolve and draw ``spec`` into the caller's existing
        Matplotlib ``ax`` (and its figure), for callers that own the figure:
        the legacy ``Visualizer.visualize``/``visualize_vf``, which must honour
        ``ax=`` and ``style=`` and return a pyplot-managed figure.

        Unlike :meth:`build_figure` and :meth:`render` this does **not** enter
        :func:`render_context`: the caller's current rcParams (its style) stay
        in force, so the output is not byte-reproducible and there is no
        request size, dpi or export. The spec's theme still supplies role
        styling, colour maps and number locale. ``ax`` must match the spec's
        coordinate system (a 3D axes for ``cartesian3d``). Python warnings
        raised while drawing are returned as ``backend_warning`` entries, as
        in :meth:`build_figure`. The adapter's ``max_points`` limit, which
        guards the export path, is not applied.

        ``role_styling=False`` draws ``reference`` layers with the caller's
        styling instead of the role's (no reference colour, transparency or
        line width; a line without a style colour takes the axes' own
        property cycle, as ``ax.plot`` would). The role stays in the spec.
        Only line, function and quiver layers support it."""
        request = RenderRequest()
        if not role_styling:
            other = sorted({m.type for m in spec.layers} - {"line", "function", "quiver"})
            if other:
                raise ValueError(f"role_styling=False supports line, function and quiver "
                                 f"layers, not {', '.join(other)}")
        prepared = prepare(spec, resolver)
        # max_points guards the reproducible export path (file size, render
        # time); a caller drawing into its own axes asked for every point.
        self._check_capabilities(prepared.spec, request,
                                 replace(self.capabilities, max_points=None))
        want = {"cartesian3d": "3d", "polar": "polar"}.get(prepared.spec.coordinate_system,
                                                           "rectilinear")
        if getattr(ax, "name", None) != want:
            raise ValueError(f"a {prepared.spec.coordinate_system} spec needs a {want!r} axes, "
                             f"got {getattr(ax, 'name', None)!r}")
        warnings = list(prepared.warnings)
        theme = self._theme(prepared.spec, request, warnings)
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            built = self._draw(prepared, request, warnings, theme, ax=ax,
                               role_styling=role_styling)
        _backend_warnings(caught, built.warnings)
        built.warnings[:] = _dedupe(built.warnings)
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
        with render_context(theme, _min_pt(request)), \
                _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            built = self._fitted(prepared, request, warnings, theme, caught)
            if request.profile is not None:
                from .legibility import recording_canvas

                recording_canvas(built.figure)
            built.figure.savefig(buf, format=request.format, dpi=request.dpi,
                                 metadata=_metadata(request.format))
            _audit(built, request)
        _backend_warnings(caught, built.warnings)
        _collapsed(built.warnings, request)
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

    def _check_capabilities(self, spec: FigureSpec, request: RenderRequest,
                            capabilities: Capabilities | None = None) -> None:
        from ..registry import requirements_for

        reqs = requirements_for(spec, request)
        missing = reqs.unmet(capabilities or self.capabilities)
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

    def _fitted(self, prepared: PreparedSpec, request: RenderRequest,
                warnings: list[RenderWarning], theme: Any, caught: list) -> BuiltFigure:
        """Draw the figure; a profile render that cannot fit its content at
        the minimum text size is drawn again taller (never wider), up to
        :data:`.legibility.MAX_GROWN_ASPECT`, with a ``figure_grown`` note.
        Only the kept draw's Python warnings reach ``caught``."""
        if request.profile is None:
            return self._draw(prepared, request, warnings, theme)
        from .legibility import GROW_ROUNDS, MAX_GROWN_ASPECT, growth_warning, needed_height

        w_in, h_in = _figsize(request)
        causes: list[str] = []
        for attempt in range(GROW_ROUNDS + 1):
            with _warnings.catch_warnings(record=True) as mine:
                _warnings.simplefilter("always")
                built = self._draw(prepared, request, list(warnings), theme,
                                   None if attempt == 0 else h_in)
                if attempt == GROW_ROUNDS:
                    break
                need, why = needed_height(built, built.context)
            need = min(need, MAX_GROWN_ASPECT * w_in)
            if not why or need <= h_in + 1.0 / request.dpi:
                break
            causes += why
            h_in = (math.ceil(need * request.dpi) + 1e-6) / request.dpi
        # constrained layout giving up during a measuring pass (a legend still
        # inside the axes, value labels not yet fitted) says nothing about the
        # figure that is saved: only the final draw's (in the caller's
        # catch) becomes layout_collapsed
        caught.extend(w for w in mine if "layout not applied" not in str(w.message))
        # ... unless the saved layout is the collapsed one (checked in _audit)
        built.measured_collapse = any("layout not applied" in str(w.message) for w in mine)
        if causes:
            built.warnings.append(growth_warning(request.size_px[1],
                                                 math.floor(h_in * request.dpi), causes))
        return built

    @staticmethod
    def _draw(prepared: PreparedSpec, request: RenderRequest,
              warnings: list[RenderWarning], theme: Any = None,
              height_in: float | None = None, *,
              ax: Any = None, role_styling: bool = True) -> BuiltFigure:
        """Draw into a new figure sized by ``request`` (``height_in`` taller,
        profile renders), or into ``ax``."""
        from matplotlib.figure import Figure

        from .data import infer_axes
        from .decorate import apply_annotations, apply_axes, apply_caption, apply_legend
        from .intervals import caption_with_intervals
        from .marks import DRAW, DrawContext, fit_value_labels, report_bar_overlaps

        spec = prepared.spec
        axes_info = infer_axes(spec, warnings)
        caller_axes = ax is not None
        if ax is None:
            figsize = _figsize(request)
            if height_in is not None:
                figsize = (figsize[0], height_in)
            fig = Figure(figsize=figsize, dpi=request.dpi, layout="constrained")
            projection = {"cartesian3d": "3d", "polar": "polar"}.get(spec.coordinate_system)
            ax = fig.add_subplot(projection=projection)
        else:
            fig = ax.figure
            figsize = tuple(fig.get_size_inches())
        ctx = DrawContext(spec=spec, fig=fig, ax=ax, axes=axes_info, warnings=warnings,
                          theme=theme, role_styling=role_styling, caller_axes=caller_axes,
                          profile=request.render_profile, min_pt=_min_pt(request))
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
        if ctx.min_pt:
            from .decorate import CAPTION_FONTSIZE

            cap["fontsize"] = ctx.fs(cap.get("fontsize", CAPTION_FONTSIZE))
        text = caption_with_intervals(spec.caption, spec.legend.show, ctx.interval_labels)
        caption = apply_caption(fig, text, figsize[1], figsize[0], **cap)
        if ctx.min_pt:
            from .legibility import fit_ticks, fit_title, wrap_annotations, wrap_labels

            wrap_labels(ctx)
            fit_ticks(ctx)  # before the legend: upright labels change the axes' decorations
            fit_title(ctx)
            wrap_annotations(ctx, ann)
        # Value labels are fitted by the layout engine on every draw, so they
        # are already inside the (widened) axes when the legend pass below
        # lays the figure out to measure it: an "auto" legend that would
        # cover a value label is moved outside like one covering data.
        if ctx.min_pt:
            from matplotlib.text import Annotation

            # only text whose x is a layout position, not data: a template
            # says which (the funnel's rate column); a caller's annotation
            # keeps its data position and the audit reports a collision
            movable = row_notes(spec)
            ctx.text_annotations = [a for a in ann if isinstance(a, Annotation)
                                    and getattr(a, "_ieev_index", None) in movable]
        fit_value_labels(ctx)
        if ctx.min_pt:
            from .legibility import fit_rows_on_layout, refit_title_on_layout

            fit_rows_on_layout(ctx)
            refit_title_on_layout(ctx)
        apply_legend(ctx)  # last: an "auto" legend is placed against the final layout
        return BuiltFigure(figure=fig, axes=ax, warnings=warnings,
                           layer_artists=ctx.layer_artists, annotation_artists=ann,
                           caption=caption, context=ctx)


def row_notes(spec: FigureSpec) -> set[int]:
    """Indexes of the text annotations a template declared as row notes
    (``metadata["template"]["row_notes"]``: text, x and y of each), matched
    by content: an annotation counts only if its text, x and row are exactly
    one declared note's, so a caller's own annotation - wherever it is in
    the list - is never one."""
    from ...spec import TextAnnotation

    meta = spec.metadata.get("template") if isinstance(spec.metadata, dict) else None
    declared = meta.get("row_notes") if isinstance(meta, dict) else None
    if not isinstance(declared, list):
        return set()
    keys = {(d.get("text"), d.get("x"), d.get("y")) for d in declared if isinstance(d, dict)}
    return {i for i, a in enumerate(spec.annotations)
            if isinstance(a, TextAnnotation) and (a.text, a.x, a.y) in keys}


def row_note_issues(built: BuiltFigure) -> list[RenderWarning]:
    """A declared row note that matches no annotation (the spec was edited
    after the template built it: ``row_note_stale``, a note - gating as
    ``row_note_detached`` when the edited note's text runs past the plot's
    right edge, away from its row's bar), and one that two annotations match
    (``row_note_duplicated``, gating: the same rate drawn twice), named."""
    from ...spec import TextAnnotation

    spec = built.context.spec if built.context is not None else None
    meta = spec.metadata.get("template") if spec is not None and isinstance(
        spec.metadata, dict) else None
    declared = meta.get("row_notes") if isinstance(meta, dict) else None
    if not isinstance(declared, list):
        return []
    out = []
    keys = [(d.get("text"), d.get("x"), d.get("y")) for d in declared if isinstance(d, dict)]
    matched = {i for i, a in enumerate(spec.annotations)
               if isinstance(a, TextAnnotation) and (a.text, a.x, a.y) in keys}
    for key in keys:
        hits = [i for i, a in enumerate(spec.annotations)
                if isinstance(a, TextAnnotation) and (a.text, a.x, a.y) == key]
        if not hits:
            out.append(RenderWarning(
                "row_note_stale",
                f"the template's row note {key[0]!r} on row {key[2]!r} matches no annotation "
                "(its text, x or row was changed after the template built the spec); an "
                "annotation in its place is drawn at its own data position, not moved under "
                "the row's value label.", "/metadata/template/row_notes"))
            out += _detached(built, key, matched)
        elif len(hits) > 1:
            out.append(RenderWarning(
                "row_note_duplicated",
                f"the row note {key[0]!r} on row {key[2]!r} is drawn {len(hits)} times "
                f"(annotations {', '.join(map(str, hits))}); remove the duplicate.",
                f"/annotations/{hits[1]}"))
    return out


def _detached(built: BuiltFigure, key: tuple, matched: set[int]) -> list[RenderWarning]:
    """The edited form of a stale row note (same row, same text or same x,
    matching no declared note) whose drawn text runs past the plot's right
    edge: it no longer sits by its row's bar and reads as a loose label."""
    from matplotlib.text import Annotation

    from ...spec import TextAnnotation

    ax = built.axes
    right = ax.get_window_extent().x1
    out = []
    for art in built.annotation_artists:
        i = getattr(art, "_ieev_index", None)
        if i is None or i in matched or not isinstance(art, Annotation):
            continue
        a = built.context.spec.annotations[i]
        if not (isinstance(a, TextAnnotation) and a.y == key[2]
                and (a.text == key[0] or a.x == key[1])):
            continue
        if art.get_window_extent().x1 > right + 1.0:
            out.append(RenderWarning(
                "row_note_detached",
                f"annotation {a.text!r} replaces the template's row note on row {key[2]!r} "
                "but runs past the plot's right edge, away from the row's bar; restore the "
                "note or place it inside the plot.", f"/annotations/{i}"))
    return out


def _audit(built: BuiltFigure, request: RenderRequest) -> None:
    """Profile renders: append the legibility audit of the figure as drawn."""
    from .legibility import annotations_outside, audit, figure_glyphs, title_outside

    built.warnings.extend(annotations_outside(built))
    built.warnings.extend(title_outside(built))
    built.warnings.extend(figure_glyphs(built.figure))  # inside the render's rc context
    profile = request.render_profile
    if profile is not None:
        from .legibility import annotation_rows

        found = audit(built.figure, profile, request.display_width, built.axes)
        built.warnings.extend(found)
        if built.measured_collapse and any(w.code == "legibility_plot_collapsed"
                                           for w in found):
            # constrained layout gave up and the saved plot is the collapsed
            # one: the final layout, not a measuring pass
            built.warnings.append(RenderWarning(
                "layout_collapsed", "the figure's text left no room for the plot at this "
                "size, so matplotlib could not lay it out; shorten the text or use a larger "
                "figure.", ""))
        built.warnings.extend(annotation_rows(built))
        built.warnings.extend(row_note_issues(built))


def _collapsed(warnings: list[RenderWarning], request: RenderRequest) -> None:
    """Profile renders: matplotlib giving up on the layout is a gating
    ``layout_collapsed`` warning, not only a backend message."""
    if request.profile is not None and any(
            w.code == "backend_warning" and "layout not applied" in w.message for w in warnings):
        warnings.append(RenderWarning(
            "layout_collapsed", "the figure's text left no room for the plot at this size, so "
            "matplotlib could not lay it out; shorten the text or use a larger figure.", ""))


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
