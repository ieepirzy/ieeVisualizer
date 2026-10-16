"""Profile renders of node-link networks: text at the profile's minimum size,
labels placed in full (wrapped, never shortened, when there is room) and a
figure tall enough for them (``docs/MOBILE_AND_EMAIL.md``).

A :class:`Frame` tells :func:`.render._draw` the font sizes, the figure
height and where the graph, the legend sections, the title and the caption
go. On a narrow profile (a phone) the graph takes the full width and the
legend sections sit below it, side by side where they fit; elsewhere the
legend keeps its column on the right. :func:`fit_network` draws, measures
what did not fit (legend sections past the image, labels left out or
shortened) and draws again taller - up to
:data:`~ieeVisualizer.render.mpl.legibility.MAX_GROWN_ASPECT` times the
width. A graph whose labels still do not fit keeps the existing
``labels_skipped`` / ``labels_abbreviated`` warning, which then says the
network is too big for the profile; the send gate refuses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

#: Space around the parts of a narrow frame, inches.
PAD_IN = 0.08
#: How much taller the graph gets per round while labels do not fit.
GROW = 1.35
ROUNDS = 4


@dataclass(frozen=True)
class Frame:
    small: float  # labels, legend, caption (pt)
    large: float  # title (pt)
    width_in: float
    height_in: float
    narrow: bool
    graph_in: float  # narrow: the graph's height
    title: str = ""
    caption: str = ""
    legend_xy: tuple = ()  # figure-fraction upper-left corner per legend section
    axes_rect: tuple = (0.02, 0.07, 0.68, 0.84)
    profile: str = ""
    capped: bool = False
    sizes: tuple = field(default=(), compare=False)

    def legend_at(self, k: int, sections: list) -> tuple[float, float]:
        if k < len(self.legend_xy):
            return self.legend_xy[k]
        return (0.72, 0.93) if not self.narrow else (0.03, 0.0)

    def wrapped(self, label: str, size: float) -> list[str]:
        """``label`` broken at spaces into 2, 3, ... lines (every word kept)."""
        from ..render.mpl.legibility import wrap

        words = label.split()
        if len(words) < 2:
            return []
        out: list[str] = []
        full = _width(label, size)
        for k in range(2, len(words) + 1):
            text = wrap(" ".join(words), size, full / k)
            if text not in out and "\n" in text:
                out.append(text)
        return out

    @staticmethod
    def spots(r: float) -> list[tuple]:
        """Places tried after a layout's own: above, right, left of the node."""
        return [((0.0, r + 2), "center", "bottom", False),
                ((r + 3, 0.0), "left", "center", False),
                ((-(r + 3), 0.0), "right", "center", False),
                ((r, -(r + 2)), "right", "top", False),  # below, kept inside an edge
                ((-r, -(r + 2)), "left", "top", False),
                ((r, r + 2), "right", "bottom", False),
                ((-r, r + 2), "left", "bottom", False)]

    def inside(self, bb: Any, fig: Any) -> bool:
        return bb.x0 >= 0 and bb.y0 >= 0 and bb.x1 <= fig.bbox.width and bb.y1 <= fig.bbox.height

    def too_big_advice(self) -> str:
        if not self.capped:
            return ""
        return (f" At the {self.profile} profile's minimum text size the labels do not fit even "
                f"with the figure grown to {self.height_in / self.width_in:.1f} times its width: "
                "the network is too big for this screen. Use a wider profile, show fewer nodes "
                "or send the text outline.")


def _width(text: str, size: float, bold: bool = False) -> float:
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextToPath

    prop = FontProperties(size=size, weight="bold" if bold else "normal")
    return max(TextToPath().get_text_width_height_descent(line, prop, ismath=False)[0]
               for line in text.split("\n"))


def _lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def fit_network(spec: Any, request: Any, draw: Any) -> Any:
    """Draw with ``draw(frame)`` until the labels and legend fit (or the
    figure reaches its height limit); returns the last drawing."""
    from ..render.mpl.adapter import _figsize, _min_pt
    from ..render.mpl.legibility import MAX_GROWN_ASPECT, wrap

    profile = request.render_profile
    w_in, h_in = _figsize(request)
    small = max(7.5, _min_pt(request))
    large = max(11.0, small + 3.0)
    text_w = 0.94 * w_in * 72.0
    frame = Frame(small, large, w_in, h_in, profile.narrow,
                  graph_in=w_in * 0.94 * (1.0 if spec.layout.algorithm in ("force", "circular")
                                          else 0.8),
                  title=wrap(spec.title or "", large, text_w),
                  caption=wrap(spec.caption or "", small, text_w), profile=profile.name)
    built, grow = None, 1.0
    # top-down layers are crowded across, which a taller figure does not change
    tall_helps = not (spec.layout.algorithm == "layered" and spec.layout.direction == "TB")
    for _ in range(ROUNDS):
        built = draw(frame)
        dpi = built.figure.dpi
        sizes = tuple((leg.get_window_extent(built.figure.canvas.get_renderer()).width / dpi,
                       leg.get_window_extent(built.figure.canvas.get_renderer()).height / dpi)
                      for leg in built.legends)
        if (built.skipped_labels or built.abbreviated_labels) and tall_helps:
            grow *= GROW
        new = _layout(frame, sizes, grow, h_in, MAX_GROWN_ASPECT * w_in)
        if new == frame:
            break
        frame = new
    if built.skipped_labels or built.abbreviated_labels:  # say so, with what to do
        built = draw(replace(frame, capped=True))
    return built


def _layout(f: Frame, sizes: tuple, grow: float, min_h: float, max_h: float) -> Frame:
    """The frame for legend sections of ``sizes`` (inches) and a graph
    ``grow`` times its starting height, at most ``max_h`` tall."""
    gap = 0.12
    title_in = _lines(f.title) * f.large * 1.3 / 72.0 + (0.12 if f.title else 0.0)
    cap_in = _lines(f.caption) * f.small * 1.3 / 72.0 + (0.08 if f.caption else 0.0)
    if f.narrow:
        rows, row, used = [], [], 0.0
        for k, (w, _h) in enumerate(sizes):
            if row and used + gap + w > 0.94 * f.width_in:
                rows.append(row)
                row, used = [], 0.0
            used += (gap if row else 0.0) + w
            row.append(k)
        if row:
            rows.append(row)
        legend_in = sum(max(sizes[k][1] for k in r) for r in rows) + gap * len(rows)
        fixed = PAD_IN * 3 + title_in + legend_in + cap_in
        graph = f.graph_in * grow
        H = max(min_h, fixed + graph)
        capped = H > max_h
        if capped:
            H, graph = max_h, max(max_h - fixed, 0.5)
        graph = H - fixed  # a figure taller than needed gives the rest to the graph
        y0 = PAD_IN + cap_in + legend_in + PAD_IN
        xy, top = [None] * len(sizes), y0 - PAD_IN
        for r in rows:
            x = 0.03 * f.width_in
            for k in r:
                xy[k] = (x / f.width_in, top / H)
                x += sizes[k][0] + gap
            top -= max(sizes[k][1] for k in r) + gap
        return replace(f, height_in=H, axes_rect=(0.03, y0 / H, 0.94, graph / H),
                       legend_xy=tuple(xy), capped=capped, sizes=sizes)
    legend_in = sum(h for _, h in sizes) + 0.025 * f.height_in * max(len(sizes) - 1, 0)
    H = max(min_h * grow, (legend_in + 0.2) / 0.86, (cap_in + title_in + 1.0) / 0.84)
    capped = H > max_h
    H = min(H, max_h)
    bottom = max(0.07, (cap_in + 0.06) / H)
    top = min(0.91, 1.0 - (title_in + 0.1) / H)
    xy, y = [], 0.93
    for _, h in sizes:
        xy.append((0.72, y))
        y -= h / H + 0.025
    return replace(f, height_in=H, axes_rect=(0.02, bottom, 0.68, top - bottom),
                   legend_xy=tuple(xy), capped=capped, sizes=sizes)
