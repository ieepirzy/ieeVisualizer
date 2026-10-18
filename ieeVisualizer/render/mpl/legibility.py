"""Profile rendering for the matplotlib adapter: minimum text size, narrow
layout adaptations and the legibility audit (``docs/MOBILE_AND_EMAIL.md``).

Only used when the request names a profile (``RenderRequest(profile=...)``);
without one nothing here runs, so other renders keep their bytes.

* :func:`font_floor_rc` raises every rcParams font size below the minimum.
  Text the adapter sizes itself goes through ``DrawContext.fs``.
* :func:`wrap` breaks a title or axis label into lines that fit, measured
  with the font's own metrics.
* :func:`fit_ticks` removes tick-label collisions: fewer ticks on numeric and
  date axes, upright (90 degree) labels on a category x axis. Category labels
  are never dropped to make room; when they still collide the render says so
  (``too_many_categories_for_profile``).
* :func:`audit` measures every line of text the saved PNG drew (through
  :func:`recording_canvas`; a vector save is drawn once more on it) and
  reports text below the minimum, text over text and text past the image
  edge as ``legibility_*`` warnings.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any

from ..base import RenderWarning

#: rcParams holding font sizes (absolute or relative to ``font.size``).
FONT_KEYS = ("font.size", "axes.titlesize", "axes.labelsize", "xtick.labelsize",
             "ytick.labelsize", "legend.fontsize", "legend.title_fontsize", "figure.titlesize",
             "figure.labelsize")
#: Two texts must overlap by more than this many pixels in both directions to
#: count (anti-aliasing touches are not collisions).
OVERLAP_TOLERANCE_PX = 1.0
#: Tick labels closer than this fraction of their font size count as touching.
TICK_GAP_EM = 0.3
_MAX_EXAMPLES = 3
#: Annotations are wrapped only into columns at least this many ems wide.
MIN_WRAP_EM = 8.0
#: A plot area smaller than this share of the figure (either way) has collapsed.
MIN_PLOT_SHARE = 0.25


def points(size: Any) -> float:
    """A matplotlib font size (number or name such as ``"small"``) in points
    under the current rcParams."""
    from matplotlib.font_manager import FontProperties

    return float(FontProperties(size=size).get_size_in_points())


def font_floor_rc(min_pt: float) -> dict[str, float]:
    """rcParams raising each font size below ``min_pt`` to it (call inside
    the theme's rc context, so relative sizes resolve against its base)."""
    from matplotlib import rcParams

    return {k: min_pt for k in FONT_KEYS
            if rcParams.get(k) is not None and points(rcParams[k]) < min_pt}


def wrap(text: str, size_pt: float, width_pt: float, bold: bool = False) -> str:
    """``text`` with line breaks so each line is at most ``width_pt`` wide at
    ``size_pt`` (greedy, at spaces; a single longer word stays whole and is
    caught by the audit)."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextToPath

    prop = FontProperties(size=size_pt, weight="bold" if bold else "normal")
    measure = TextToPath()

    def width(s: str) -> float:
        return measure.get_text_width_height_descent(s, prop, ismath=False)[0]

    out = []
    for par in text.split("\n"):
        line = ""
        for word in _units_kept(par.split(" ")):
            cand = f"{line} {word}" if line else word
            if line and width(cand) > width_pt:
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return "\n".join(out)


#: A word made only of these stays on the line of the word before it: a
#: number and its unit read as one ("20 %", "1 234,50 €", "5 °C").
_UNIT_CHARS = frozenset("%‰€$£¥°℃℉")


def _units_kept(words: list[str]) -> list[str]:
    """``words`` with a unit-only word (``%``, ``€``...) joined to the word
    before it, so wrapping never puts a value and its unit on two lines."""
    out: list[str] = []
    for w in words:
        if out and w and set(w) <= _UNIT_CHARS | set(".,;:)"):
            out[-1] = f"{out[-1]} {w}"
        else:
            out.append(w)
    return out


def wrap_labels(ctx: Any) -> None:
    """Wrap the title and the 2D axis labels to the figure."""
    if ctx.three_d:
        return
    ax, fig = ctx.ax, ctx.fig
    w_pt, h_pt = (v * 72.0 for v in fig.get_size_inches())
    title = ax.title
    if title.get_text():
        title.set_text(wrap(title.get_text(), title.get_fontsize(), 0.94 * w_pt,
                            title.get_fontweight() == "bold"))
    if ctx.spec.coordinate_system == "polar":
        return
    x_room = 0.94 * w_pt
    info = ctx.axes.get("x")
    if info is not None and info.kind == "time" and ax.xaxis.label.get_text():
        # the date offset ("2026-Oct") sits at the right end under the ticks:
        # keep the axis label left of it
        ax.set_xlabel(ax.xaxis.label.get_text(), loc="left")
        x_room = 0.6 * w_pt
    for label, room in ((ax.xaxis.label, x_room), (ax.yaxis.label, 0.8 * h_pt)):
        if label.get_text():
            label.set_text(wrap(label.get_text(), label.get_fontsize(), room))


def fit_title(ctx: Any) -> None:
    """Re-wrap a title centred over the axes that still runs past the image
    edge (the axes sit right of the y labels, so a title the figure's width
    can stick out on the right)."""
    from .legend_layout import _measuring

    title, fig = ctx.ax.title, ctx.fig
    for _ in range(3):
        if not title.get_text():
            return
        with _measuring(fig):
            box = title.get_window_extent()
        over = max(0.0, -box.x0, box.x1 - fig.bbox.width)
        if over <= OVERLAP_TOLERANCE_PX:
            return
        room = (box.width - 2 * over - 4) * 72.0 / fig.dpi
        if room < 4 * title.get_fontsize():
            return
        title.set_text(wrap(" ".join(title.get_text().split("\n")), title.get_fontsize(), room,
                            title.get_fontweight() == "bold"))


def refit_title_on_layout(ctx: Any) -> None:
    """Profile renders: after every layout (value labels, legends and
    constrained layout move the axes, and the title is centred over them),
    re-wrap a title that runs past the image edge to the room it has there
    and lay out once more. :func:`fit_title` before layout measures a
    position the axes have not reached yet."""
    if ctx.three_d:
        return
    engine = ctx.fig.get_layout_engine()
    if engine is None:
        return
    inner, title = engine.execute, ctx.ax.title
    original = " ".join(title.get_text().split("\n"))
    x0 = title.get_position()[0]

    def execute(figure: Any) -> Any:
        title.set_x(x0)  # each layout decides again from the title as drawn
        out = inner(figure)
        for _ in range(2):
            if not original or not _title_off_edge(title, figure):
                break
            box = title.get_window_extent()
            centre = (box.x0 + box.x1) / 2
            room = 2 * min(centre, figure.bbox.width - centre) - 8
            if room * 72.0 / figure.dpi < 4 * title.get_fontsize():
                break  # no room to wrap into: the audit reports it clipped
            title.set_text(wrap(original, title.get_fontsize(), room * 72.0 / figure.dpi,
                                title.get_fontweight() == "bold"))
            out = inner(figure)
            if _title_off_edge(title, figure):
                _shift_into_figure(title, ctx.ax, figure)
        return out

    engine.execute = execute


def _shift_into_figure(title: Any, ax: Any, fig: Any) -> None:
    """A title still past the edge after wrapping (a word wider than the room
    either side of the axes' centre, e.g. a long compound word) moves
    sideways, no further than needed, to lie inside the figure: it stays the
    figure's title, only no longer centred over the plot."""
    box, plot = title.get_window_extent(), ax.get_window_extent()
    if box.width > fig.bbox.width - 8 or plot.width <= 0:
        return  # wider than the image: the audit reports it clipped
    centre = (box.x0 + box.x1) / 2
    lo, hi = box.width / 2 + 4, fig.bbox.width - box.width / 2 - 4
    target = min(max(centre, lo), hi)
    x, _ = title.get_position()
    title.set_x(x + (target - centre) / plot.width)


def _title_off_edge(title: Any, fig: Any) -> bool:
    box = title.get_window_extent()
    return box.x0 < -OVERLAP_TOLERANCE_PX or box.x1 > fig.bbox.width + OVERLAP_TOLERANCE_PX


def wrap_annotations(ctx: Any, texts: list[Any]) -> None:
    """Wrap text annotations that would run past the right edge of the image
    onto more lines at their place (their anchor and words stay)."""
    from matplotlib.text import Text

    from .legend_layout import _measuring

    if ctx.three_d:
        return
    fig = ctx.fig
    with _measuring(fig):
        boxes = [(t, t.get_window_extent()) for t in texts
                 if isinstance(t, Text) and t.get_visible() and t.get_text()]
    right = 0.98 * fig.bbox.width
    rows = (ctx.axes.get("y") is not None and ctx.axes["y"].kind == "category")
    for t, box in boxes:
        room_pt = (right - box.x0) * 72.0 / fig.dpi
        # a column narrower than MIN_WRAP_EM would stack the words one per line and
        # squeeze the plot; such a text is left as it is and the audit reports it
        if box.x1 > right and room_pt >= MIN_WRAP_EM * t.get_fontsize():
            t.set_text(wrap(t.get_text(), t.get_fontsize(), room_pt))
            if rows and getattr(t, "xyann", None) is not None:
                ctx.row_wrapped.append(t)  # placed in its row after layout


def fit_rows_on_layout(ctx: Any) -> None:
    """Profile renders: after every layout, an annotation anchored on a
    category row that wrapping made taller keeps its own placement (just
    above the anchor) when that stays in its row; only if it would climb
    into the row above does it try, in turn, centred on the anchor and
    hanging below it - each only if it stays in its row and clear of the
    row's value labels. If none fits it stays as placed, and the audit
    refuses it (``annotation_outside_row``)."""
    engine = ctx.fig.get_layout_engine()
    if engine is None or not ctx.row_wrapped:
        return
    inner = engine.execute
    # a template's row notes are placed under their value labels instead
    # (value_labels._clear_texts); everything else is placed here
    placed = [(t, t.get_va(), tuple(t.xyann)) for t in ctx.row_wrapped
              if not any(t is n for n in ctx.text_annotations)]

    def execute(figure: Any) -> Any:
        out = inner(figure)
        labels = [lab.text for lab in ctx.value_labels if lab.text.get_visible()]
        for t, va, off in placed:
            try:
                y = float(ctx.ax.yaxis.convert_units(t.xy[1]))
            except (TypeError, ValueError):
                continue
            lo, hi = sorted(ctx.ax.transData.transform([(0, y - 0.5), (0, y + 0.5)])[:, 1])
            for cand_va, cand_off in ((va, off), ("center", (off[0], 0.0)),
                                      ("top", (off[0], -abs(off[1])))):
                t.set_va(cand_va)
                t.xyann = cand_off
                box = t.get_window_extent()
                if box.y0 >= lo - OVERLAP_TOLERANCE_PX and box.y1 <= hi + OVERLAP_TOLERANCE_PX \
                        and not any(box.overlaps(lab.get_window_extent()) for lab in labels):
                    break
            else:
                t.set_va(va)
                t.xyann = off
        return out

    engine.execute = execute


def annotation_rows(built: Any) -> list[RenderWarning]:
    """Profile renders: a text annotation anchored on a category row (y)
    whose drawn text reaches into another row's band reads as that row's:
    ``annotation_outside_row`` (gating)."""
    from matplotlib.text import Annotation

    ax, ctx = built.axes, built.context
    if ctx is None or ctx.three_d or ctx.axes.get("y") is None \
            or ctx.axes["y"].kind != "category":
        return []
    out = []
    for a in built.annotation_artists:
        if not (isinstance(a, Annotation) and a.get_visible() and a.get_text()):
            continue
        try:
            y = float(ax.yaxis.convert_units(a.xy[1]))
        except (TypeError, ValueError):
            continue
        lo, hi = sorted(ax.transData.transform([(0, y - 0.5), (0, y + 0.5)])[:, 1])
        box = a.get_window_extent()
        if box.y0 < lo - OVERLAP_TOLERANCE_PX or box.y1 > hi + OVERLAP_TOLERANCE_PX:
            text = " ".join(a.get_text().split())
            out.append(RenderWarning(
                "annotation_outside_row",
                f"annotation {text[:40]!r} is anchored on row {a.xy[1]!r} but its text reaches "
                "into a neighbouring row, so it reads as that row's. Shorten it or anchor it "
                "where it has room.", f"/annotations/{getattr(a, '_ieev_index', '')}"))
    return out


# --------------------------------------------------------------------------
# tick labels
# --------------------------------------------------------------------------


def _drawn_ticklabels(axis: Any) -> list[Any]:
    """Major tick labels inside the view interval, with their extents."""
    lo, hi = sorted(axis.get_view_interval())
    tol = 1e-9 * max(abs(hi - lo), 1.0)
    out = []
    for tick in axis.get_major_ticks():
        lab = tick.label1
        if lo - tol <= tick.get_loc() <= hi + tol and lab.get_visible() and lab.get_text():
            out.append(lab)
    return out


def _collisions(labels: list[Any], horizontal: bool) -> int:
    """Neighbouring labels closer than :data:`TICK_GAP_EM` of a line height
    (the smaller side of a label's box, whichever way it is turned)."""
    boxes = sorted((lab.get_window_extent() for lab in labels),
                   key=lambda b: b.x0 if horizontal else b.y0)
    n = 0
    for a, b in zip(boxes, boxes[1:]):
        gap = (b.x0 - a.x1) if horizontal else (b.y0 - a.y1)
        if gap < TICK_GAP_EM * min(a.width, a.height, b.width, b.height):
            n += 1
    return n


def fit_ticks(ctx: Any) -> None:
    """Thin numeric/date ticks and turn category x labels upright until no
    two tick labels touch; report category labels that still do."""
    from .legend_layout import _measuring

    if ctx.three_d or ctx.spec.coordinate_system == "polar":
        return
    for key, axis in (("x", ctx.ax.xaxis), ("y", ctx.ax.yaxis)):
        info = ctx.axes.get(key)
        if info is None:
            continue
        rotated = False
        for _ in range(8):
            with _measuring(ctx.fig):
                labels = _drawn_ticklabels(axis)
                hits = _collisions(labels, key == "x")
            if not hits:
                break
            if info.kind == "category":
                if key == "x" and not rotated:
                    ctx.ax.tick_params(axis="x", labelrotation=90)
                    rotated = True
                    continue
                if key == "y":
                    with _measuring(ctx.fig):
                        ctx.plot_growth["category_labels"] = _label_growth(labels)
                fix = ("use horizontal bars, show fewer categories or a wider profile"
                       if key == "x" else "show fewer categories or make the figure taller")
                ctx.warn("too_many_categories_for_profile",
                         f"the {len(labels)} category labels of axis {key} do not fit at the "
                         f"{ctx.profile.name} profile's minimum text size "
                         f"({ctx.profile.min_text_px:g} CSS px); all are drawn, so they "
                         f"touch: {fix}.", f"/axes/{key}")
                break
            if not _fewer_ticks(ctx, key, info, axis, len(labels)):
                break


def _label_growth(labels: list[Any]) -> float:
    """How many times taller a y axis must be so its labels stop touching."""
    boxes = sorted((lab.get_window_extent() for lab in labels), key=lambda b: b.y0)
    steps = [b.y0 - a.y0 for a, b in zip(boxes, boxes[1:])]
    need = max(b.height for b in boxes) * (1 + TICK_GAP_EM) + 2
    return need / max(min(steps, default=need), 1e-9) * 1.04


def _fewer_ticks(ctx: Any, key: str, info: Any, axis: Any, shown: int) -> bool:
    from matplotlib.ticker import MaxNLocator

    if shown <= 2:
        return False
    loc = axis.get_major_locator()
    if info.kind == "time":
        from .decorate import set_time_axis

        set_time_axis(ctx, key, info, maxticks=shown - 1)
        return True
    if isinstance(loc, MaxNLocator):
        loc.set_params(nbins=max(1, shown - 2))
        return True
    return False  # log axes and fixed ticks: left as they are; the audit reports


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DrawnLine:
    """One line of text as a renderer drew it: the string, its font size
    (pt), and its box in device pixels (origin bottom left; the axis-aligned
    box of a rotated line), from the renderer's own text metrics."""

    text: str
    size_pt: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def recording_canvas(fig: Any) -> Any:
    """Attach an Agg canvas whose renderer records every line of text it
    draws (:class:`DrawnLine`). A PNG saved through it (``fig.savefig`` with
    ``format="png"`` uses the figure's own Agg canvas) is the draw
    :func:`audit` measures, so the audit sees exactly the saved image."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg, RendererAgg

    class Recorder(RendererAgg):
        def __init__(self, width: int, height: int, dpi: float) -> None:
            super().__init__(width, height, dpi)
            self.lines: list[DrawnLine] = []

        def clear(self) -> None:
            super().clear()
            self.lines = []

        def draw_text(self, gc, x, y, s, prop, angle, ismath=False, mtext=None):
            if s.strip():
                self.lines.append(_line_box(self, x, y, s, prop, angle, ismath))
            return super().draw_text(gc, x, y, s, prop, angle, ismath=ismath, mtext=mtext)

    class RecordingCanvas(FigureCanvasAgg):
        def get_renderer(self) -> Any:
            w, h = self.figure.bbox.size
            key = (w, h, self.figure.dpi)
            if getattr(self, "_recorder_key", None) != key:
                self._recorder_key = key
                self.renderer = Recorder(int(w), int(h), self.figure.dpi)
            return self.renderer

    return RecordingCanvas(fig)


def _line_box(renderer: Any, x: float, y: float, s: str, prop: Any, angle: float,
              ismath: Any) -> DrawnLine:
    """``x, y`` is the left end of the baseline; matplotlib passes ``y`` from
    the top of the canvas (``renderer.flipy()``)."""
    w, h, d = renderer.get_text_width_height_descent(s, prop, ismath=ismath)
    if renderer.flipy():
        y = renderer.height - y
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    xs, ys = [], []
    for cx, cy in ((0, -d), (w, -d), (w, h - d), (0, h - d)):
        xs.append(x + cx * ca - cy * sa)
        ys.append(y + cx * sa + cy * ca)
    return DrawnLine(s, float(prop.get_size_in_points()), min(xs), min(ys), max(xs), max(ys))


def drawn_lines(fig: Any) -> list[DrawnLine]:
    """Text lines of the figure's last draw on a recording canvas (drawing it
    now if there was none, e.g. after an SVG or PDF save)."""
    renderer = getattr(fig.canvas, "renderer", None)
    if not getattr(renderer, "lines", None):
        canvas = recording_canvas(fig)
        canvas.draw()
        renderer = canvas.renderer
    return list(renderer.lines)


def _q(line: DrawnLine) -> str:
    s = " ".join(line.text.split())
    return repr(s if len(s) <= 40 else s[:39] + "…")


def audit(fig: Any, profile: Any, display_width: float | None, ax: Any = None
          ) -> list[RenderWarning]:
    """Legibility warnings for ``fig`` shown ``display_width`` CSS px wide
    (``ax``: the plot whose area and legend are checked)."""
    lines = drawn_lines(fig)
    shown_w = display_width or profile.css_width
    css_per_pt = shown_w / (fig.get_size_inches()[0] * 72.0)
    shown = f"shown {shown_w:g} CSS px wide ({profile.name})"
    out: list[RenderWarning] = []
    small = sorted((ln.size_pt * css_per_pt, i) for i, ln in enumerate(lines)
                   if ln.size_pt * css_per_pt < profile.min_text_px - 1e-6)
    if small:
        out.append(RenderWarning(
            "legibility_text_too_small",
            f"{len(small)} line(s) of text are below {profile.min_text_px:g} CSS px when "
            f"{shown}; smallest: " + ", ".join(f"{_q(lines[i])} at {px:.1f} px"
                                               for px, i in small[:_MAX_EXAMPLES]) + ".", ""))
    W, H, tol = fig.bbox.width, fig.bbox.height, OVERLAP_TOLERANCE_PX
    clipped = [ln for ln in lines
               if ln.x0 < -tol or ln.y0 < -tol or ln.x1 > W + tol or ln.y1 > H + tol]
    if clipped:
        out.append(RenderWarning(
            "legibility_text_clipped",
            f"{len(clipped)} line(s) of text run past the image edge and are cut off: "
            + ", ".join(_q(ln) for ln in clipped[:_MAX_EXAMPLES]) + ".", ""))
    out += _plot_and_legend(fig, ax)
    pairs = [(a, b) for i, a in enumerate(lines) for b in lines[i + 1:]
             if min(a.x1, b.x1) - max(a.x0, b.x0) > tol and min(a.y1, b.y1) - max(a.y0, b.y0) > tol]
    if pairs:
        out.append(RenderWarning(
            "legibility_text_overlap",
            f"{len(pairs)} pair(s) of text lines overlap when drawn: "
            + "; ".join(f"{_q(a)} and {_q(b)}" for a, b in pairs[:_MAX_EXAMPLES]) + ".", ""))
    return out


def _plot_and_legend(fig: Any, ax: Any) -> list[RenderWarning]:
    if ax is None:
        return []
    out = []
    W, H = fig.bbox.width, fig.bbox.height
    box = ax.get_window_extent()
    if box.width < MIN_PLOT_SHARE * W or box.height < MIN_PLOT_SHARE * H:
        out.append(RenderWarning(
            "legibility_plot_collapsed",
            f"the plot area is {box.width / W:.0%} x {box.height / H:.0%} of the image: the "
            "text around it (labels, legend, annotations, caption) leaves no room for the "
            "data. Shorten the text, make the figure taller or use a wider profile.", ""))
    legend = ax.get_legend()
    if legend is not None and legend.get_visible():
        lb = legend.get_window_extent()
        tol = OVERLAP_TOLERANCE_PX
        if lb.x0 < -tol or lb.y0 < -tol or lb.x1 > W + tol or lb.y1 > H + tol:
            out.append(RenderWarning(
                "legend_not_visible",
                f"the legend ({len(legend.get_texts())} entries) does not fit in the image "
                "and is partly or wholly cut off, so series cannot be told apart. Use fewer "
                "or shorter series names, a taller figure or a wider profile.", "/legend"))
    return out


def annotations_outside(built: Any) -> list[RenderWarning]:
    """Text annotations matplotlib does not draw because their anchor lies
    outside the plot (``annotation_outside_view``), instead of losing them
    silently."""
    from matplotlib.text import Annotation

    ax = built.axes
    if getattr(ax, "name", "") != "rectilinear":
        return []
    box = ax.get_window_extent()
    out = []
    for a in built.annotation_artists:
        if not isinstance(a, Annotation) or a.xycoords != "data" or not a.get_text():
            continue
        x, y = ax.transData.transform(a.xy)
        if not (box.x0 - 0.5 <= x <= box.x1 + 0.5 and box.y0 - 0.5 <= y <= box.y1 + 0.5):
            out.append(RenderWarning(
                "annotation_outside_view",
                f"annotation {a.get_text()!r} is anchored outside the axes' limits and is not "
                "drawn; widen the limits or move the annotation.", ""))
    return out


def title_outside(built: Any) -> list[RenderWarning]:
    """``title_clipped`` when the axes title runs past the figure's edge (its
    end, often the part that tells figures apart, is cut off)."""
    ax = built.axes
    title = getattr(ax, "title", None)
    if title is None or not title.get_text() or not title.get_visible():
        return []
    fig = built.figure
    try:
        box = title.get_window_extent().transformed(fig.transFigure.inverted())
    except Exception:  # noqa: BLE001 - no layout to measure: nothing to report
        return []
    tol = 0.5 / max(fig.bbox.width, 1.0)  # half a pixel
    if box.x0 >= -tol and box.x1 <= 1 + tol and box.y1 <= 1 + tol:
        return []
    return [RenderWarning(
        "title_clipped", f"the title {title.get_text()[:60]!r} runs past the figure's edge and "
        "is cut off; shorten it or use a wider figure.", "/title")]


#: The glyph mathtext substitutes for a character no math font has.
_MATH_DUMMY = "\u00a4"
#: Characters Agg lays out without drawing a glyph: everything else
#: (tabs, carriage returns, other spaces, controls) is looked up in the font
#: and, if the font lacks it, drawn as an empty box.
_LAID_OUT_AS_SPACE = frozenset(" \n")
#: Unicode Default_Ignorable_Code_Point (DerivedCoreProperties.txt): with text
#: shaping (matplotlib >= 3.11, HarfBuzz) a font lacking one draws nothing
#: (and a lacking space separator draws a space); without shaping (3.6-3.10)
#: each is drawn as an empty box like any other. The pixel ground-truth test
#: (tests/render/test_glyph_truth.py) checks both on the running version.
_DEFAULT_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C),
                      (0x17B4, 0x17B5), (0x180B, 0x180E),
                      (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F),
                      (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
                      (0xFFF0, 0xFFF8), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
# Not here although Default_Ignorable: the Hangul fillers U+115F, U+1160,
# U+3164, U+FFA0, U+180F and the shorthand format controls U+1BCA0-1BCA3 -
# HarfBuzz draws them as the missing-glyph box (pixel-verified).


def _shapes_text() -> bool:
    import matplotlib

    major, minor = (int(x) for x in matplotlib.__version__.split(".")[:2])
    return (major, minor) >= (3, 11)


#: Spaces HarfBuzz draws as space when the font lacks them (its "space
#: fallback": no-break, en/em/thin/hair..., narrow no-break, medium
#: mathematical and ideographic space). Other separators (U+1680 OGHAM SPACE
#: MARK, which has ink) are boxes like any glyph.
_SHAPED_SPACES = frozenset([0x00A0, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000])


def _ignorable(ch: str) -> bool:
    """With shaping, a missing default-ignorable draws nothing and a missing
    fallback space is drawn as a space."""
    c = ord(ch)
    return c in _SHAPED_SPACES or any(lo <= c <= hi for lo, hi in _DEFAULT_IGNORABLE)


def _font_paths(prop: Any) -> list[str]:
    """The fonts Matplotlib's Agg text path draws ``prop`` with: the family
    list resolved to files, first to last fallback (``RendererAgg`` uses the
    same ``fontManager._find_fonts_by_props``, matplotlib >= 3.6)."""
    from matplotlib import font_manager as fm

    return list(fm.fontManager._find_fonts_by_props(prop))


class _NoGlyphLog(logging.Filter):
    """Drops mathtext's own "does not have a glyph" records emitted by the
    checking thread while it parses (the check reports them itself); other
    threads' records, e.g. of a draw running at the same time, pass."""

    def __init__(self) -> None:
        super().__init__()
        self.thread = threading.get_ident()

    def filter(self, record: logging.LogRecord) -> bool:
        return (record.thread != self.thread
                or "does not have a glyph" not in record.getMessage())


#: matplotlib's mathtext parser (pyparsing) is shared and not thread-safe;
#: the checker's own parses are serialised.
_MATH_LOCK = threading.Lock()


class GlyphCheckFailed(Exception):
    """The checker could not parse a mathtext string the draw did render."""


def _parse_math(text: str, prop: Any) -> Any:
    from matplotlib.mathtext import MathTextParser

    log, quiet = logging.getLogger("matplotlib.mathtext"), _NoGlyphLog()
    with _MATH_LOCK:
        log.addFilter(quiet)  # only that logger, only this thread, only now
        try:
            for attempt in (1, 2):  # a draw on another thread can still race
                try:
                    return MathTextParser("path").parse(text, 72, prop)
                except ValueError:
                    if attempt == 2:
                        raise GlyphCheckFailed(text) from None
        finally:
            log.removeFilter(quiet)


def missing_glyphs_in(text: str, prop: Any, math: bool = False) -> str:
    """Characters of ``text`` that ``prop``'s fonts (with fallbacks) have no
    glyph for, in order: looked up in the fonts themselves, so the answer
    depends only on the text and its font, not on warnings or logging.
    ``math``: the text is mathtext; it is parsed by Matplotlib's mathtext
    parser, and a substituted dummy glyph beyond the ``¤`` the text itself
    holds means a character is missing."""
    if math:
        parsed = _parse_math(text, prop)  # GlyphCheckFailed: fail closed
        drawn = [chr(g[2]) for g in parsed.glyphs]
        if drawn.count(_MATH_DUMMY) > text.count(_MATH_DUMMY):
            have = set(drawn)
            return "".join(dict.fromkeys(ch for ch in text
                                         if ord(ch) > 127 and ch not in have)) or "?"
        return ""
    from matplotlib.font_manager import get_font

    fonts = [get_font(p) for p in _font_paths(prop)]
    shaped = _shapes_text()
    return "".join(dict.fromkeys(
        ch for ch in text
        if ch not in _LAID_OUT_AS_SPACE and not (shaped and _ignorable(ch))
        and not any(f.get_char_index(ord(ch)) for f in fonts)))


def glyph_warning(found: list[tuple[str, str]], path: str = "") -> list[RenderWarning]:
    """``glyph_missing`` for ``[(text, missing characters), ...]``."""
    if not found:
        return []
    found = list(dict.fromkeys(found))
    shown = "; ".join(f"{t[:40]!r} lacks {m[:10]!r}" for t, m in found[:_MAX_EXAMPLES])
    return [RenderWarning(
        "glyph_missing",
        f"{len(found)} text(s) use characters no available font has a glyph for, drawn as "
        f"empty boxes: {shown}. Install and select a font that covers them.", path)]


def figure_glyphs(fig: Any) -> list[RenderWarning]:
    """``glyph_missing`` for every visible text of ``fig``, each checked with
    its own font properties (family list, style, weight) under the rcParams in
    effect: call inside the render's rc context."""
    from matplotlib import cbook
    from matplotlib.text import Text

    found, failed = [], []
    for t in fig.findobj(Text):
        s = t.get_text()
        if not (t.get_visible() and s.strip()):
            continue
        math = t.get_parse_math() and cbook.is_math_text(s)
        try:
            m = missing_glyphs_in(s, t.get_fontproperties(), math)
        except GlyphCheckFailed:
            failed.append(s)
            continue
        if m:
            found.append((s, m))
    out = glyph_warning(found)
    if failed:  # fail closed: an unchecked text is not reported as fine
        out.append(RenderWarning(
            "glyph_check_failed",
            f"{len(failed)} mathtext text(s) were drawn but could not be checked for missing "
            "glyphs (the shared mathtext parser failed twice, e.g. under concurrent "
            "renders): " + ", ".join(repr(x[:40]) for x in failed[:_MAX_EXAMPLES])
            + ". Render again, one figure at a time.", ""))
    return out


# --------------------------------------------------------------------------
# profile-aware figure height
# --------------------------------------------------------------------------

#: A profile render grows its figure at most to this height / width.
MAX_GROWN_ASPECT = 4.0
#: Rebuilds a profile render may take while growing its figure.
GROW_ROUNDS = 3
_GROWTH_CAUSES = {"value_labels": "bar value labels", "category_labels": "category labels",
                  "legend": "the legend", "plot": "the plot"}


def needed_height(built: Any, ctx: Any) -> tuple[float, list[str]]:
    """The figure height (inches) a profile render needs so that what the
    last layout could not fit at the minimum text size does fit: rows of
    horizontal bars tall enough for their value labels, y category labels
    that do not touch, a legend inside the image and a plot not squeezed
    below :data:`MIN_PLOT_SHARE` of it. Width never changes. Returns the
    height and the causes (empty: the current height is enough)."""
    from .legend_layout import _measuring

    fig, ax = built.figure, built.axes
    dpi = fig.dpi
    with _measuring(fig):
        H = fig.bbox.height
        plot = ax.get_window_extent()
        extra, causes = 0.0, []
        factor = max(ctx.plot_growth.values(), default=1.0)
        if factor > 1.0:
            extra = plot.height * (factor - 1.0)
            causes += [k for k, v in ctx.plot_growth.items() if v > 1.0]
        legend = ax.get_legend()
        if legend is not None and legend.get_visible():
            lb = legend.get_window_extent()
            over = max(0.0, -lb.y0) + max(0.0, lb.y1 - H)
            if over > OVERLAP_TOLERANCE_PX:
                extra = max(extra, over + 0.1 * dpi)
                causes.append("legend")
        if not ctx.three_d and plot.height < MIN_PLOT_SHARE * H * 1.2:
            # the text around the plot keeps its size: grow until the plot
            # has 35 % of the height
            want = (H - plot.height) / 0.65 - H
            if want > extra:
                extra = want
                causes.append("plot")
    if extra <= OVERLAP_TOLERANCE_PX:
        return H / dpi, []
    return (H + extra) / dpi, causes


def growth_warning(requested_px: int, grown_px: int, causes: list[str]) -> RenderWarning:
    """Not gating: the image is taller than the request asked for."""
    what = ", ".join(dict.fromkeys(_GROWTH_CAUSES.get(c, c) for c in causes))
    return RenderWarning(
        "figure_grown", f"the figure is {grown_px} px tall instead of the requested "
        f"{requested_px} px so that {what} fit at the profile's minimum text size; its width "
        "is unchanged.", "")
