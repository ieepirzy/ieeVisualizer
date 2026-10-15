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

import math
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
        for word in par.split(" "):
            cand = f"{line} {word}" if line else word
            if line and width(cand) > width_pt:
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return "\n".join(out)


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
    for t, box in boxes:
        room_pt = (right - box.x0) * 72.0 / fig.dpi
        if box.x1 > right and room_pt > 3 * t.get_fontsize():
            t.set_text(wrap(t.get_text(), t.get_fontsize(), room_pt))


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
                ctx.warn("too_many_categories_for_profile",
                         f"the {len(labels)} category labels of axis {key} do not fit side "
                         f"by side at the {ctx.profile.name} profile's minimum text size "
                         f"({ctx.profile.min_text_px:g} CSS px); none was left out, so they "
                         "touch. Show fewer categories, use horizontal bars, or a wider "
                         "profile.", f"/axes/{key}")
                break
            if not _fewer_ticks(ctx, key, info, axis, len(labels)):
                break


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


def audit(fig: Any, profile: Any, display_width: float | None) -> list[RenderWarning]:
    """Legibility warnings for ``fig`` shown ``display_width`` CSS px wide."""
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
    pairs = [(a, b) for i, a in enumerate(lines) for b in lines[i + 1:]
             if min(a.x1, b.x1) - max(a.x0, b.x0) > tol and min(a.y1, b.y1) - max(a.y0, b.y0) > tol]
    if pairs:
        out.append(RenderWarning(
            "legibility_text_overlap",
            f"{len(pairs)} pair(s) of text lines overlap when drawn: "
            + "; ".join(f"{_q(a)} and {_q(b)}" for a, b in pairs[:_MAX_EXAMPLES]) + ".", ""))
    return out
