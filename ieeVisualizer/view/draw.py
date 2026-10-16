"""A tiny display list and its two painters (SVG text and matplotlib/PNG).

The view layout (:mod:`.layout`) emits positioned primitives in CSS px:
:class:`Rect`, :class:`Label` (one text run at a baseline), :class:`Path`,
:class:`Embed` (an embedded FigureSpec) and :class:`Group` (offset/scale and
SVG attributes). Painting the *same* list to SVG and to a matplotlib canvas
keeps the PNG and SVG outputs of a view in agreement without a rasteriser
dependency. All coordinates are written with at most two decimals, so the
SVG bytes are stable.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Union
from xml.sax.saxutils import escape as _xml_escape

from ..spec import FigureSpec

SANS = "DejaVu Sans, Verdana, Arial, sans-serif"
MONO = "DejaVu Sans Mono, Menlo, Consolas, monospace"
INK = "#1f2328"
MUTED = "#57606a"
LINE = "#d0d7de"


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    fill: str | None = None
    stroke: str | None = None
    rx: float = 0.0
    dash: bool = False
    sw: float = 1.0


@dataclass
class Label:
    x: float
    y: float  # baseline
    text: str
    size: float = 14.0
    color: str = INK
    bold: bool = False
    italic: bool = False
    mono: bool = False
    anchor: str = "start"  # start | middle | end


@dataclass
class Path:
    points: list[tuple[float, float]]
    stroke: str | None = INK
    sw: float = 1.5
    dash: bool = False
    fill: str | None = None
    closed: bool = False


@dataclass
class Embed:
    x: float
    y: float
    w: float
    h: float
    spec: FigureSpec
    pointer: str  # JSON pointer of the figure in the view (for warnings / ids)
    index: int = 0


@dataclass
class Group:
    items: list[Item] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)
    dx: float = 0.0
    dy: float = 0.0
    scale: float = 1.0
    title: str | None = None
    desc: str | None = None


Item = Union[Rect, Label, Path, Embed, Group]


def esc(s: Any) -> str:
    """XML/HTML text and attribute escape (``& < > " '``)."""
    return _xml_escape(str(s), {'"': "&quot;", "'": "&#39;"})


def num(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def walk_items(items: list[Item]):
    for it in items:
        yield it
        if isinstance(it, Group):
            yield from walk_items(it.items)


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------


def _svg_item(it: Item, embed: Callable[[Embed], str], out: list[str]) -> None:
    if isinstance(it, Rect):
        a = f'x="{num(it.x)}" y="{num(it.y)}" width="{num(it.w)}" height="{num(it.h)}"'
        if it.rx:
            a += f' rx="{num(it.rx)}"'
        a += f' fill="{it.fill or "none"}"'
        if it.stroke:
            a += f' stroke="{it.stroke}" stroke-width="{num(it.sw)}"'
        if it.dash:
            a += ' stroke-dasharray="5 4"'
        out.append(f"<rect {a}/>")
    elif isinstance(it, Label):
        a = f'x="{num(it.x)}" y="{num(it.y)}" font-size="{num(it.size)}" fill="{it.color}"'
        if it.bold:
            a += ' font-weight="bold"'
        if it.italic:
            a += ' font-style="italic"'
        if it.mono:
            a += f' font-family="{MONO}"'
        if it.anchor != "start":
            a += f' text-anchor="{it.anchor}"'
        out.append(f"<text {a}>{esc(it.text)}</text>")
    elif isinstance(it, Path):
        pts = " ".join(f"{num(x)},{num(y)}" for x, y in it.points)
        tag = "polygon" if it.closed else "polyline"
        a = f'points="{pts}" fill="{it.fill or "none"}"'
        if it.stroke:
            a += f' stroke="{it.stroke}" stroke-width="{num(it.sw)}"'
        if it.dash:
            a += ' stroke-dasharray="6 4"'
        out.append(f"<{tag} {a}/>")
    elif isinstance(it, Embed):
        out.append(embed(it))
    elif isinstance(it, Group):
        a = "".join(f' {k}="{esc(v)}"' for k, v in sorted(it.attrs.items()))
        if it.dx or it.dy or it.scale != 1:
            a += f' transform="translate({num(it.dx)} {num(it.dy)})'
            a += f' scale({it.scale:.4f})"' if it.scale != 1 else '"'
        out.append(f"<g{a}>")
        if it.title:
            out.append(f"<title>{esc(it.title)}</title>")
        if it.desc:
            out.append(f"<desc>{esc(it.desc)}</desc>")
        for c in it.items:
            _svg_item(c, embed, out)
        out.append("</g>")


def to_svg(items: list[Item], width: float, height: float, embed: Callable[[Embed], str],
           title: str | None = None, xml_header: bool = True, css_width: str | None = None
           ) -> str:
    out = []
    if xml_header:
        out.append('<?xml version="1.0" encoding="utf-8"?>')
    size = (f'width="{css_width}"' if css_width
            else f'width="{num(width)}" height="{num(height)}"')
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" {size} '
               f'viewBox="0 0 {num(width)} {num(height)}" font-family="{SANS}" role="img">')
    if title:
        out.append(f"<title>{esc(title)}</title>")
    for it in items:
        _svg_item(it, embed, out)
    out.append("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# matplotlib -> PNG
# --------------------------------------------------------------------------

PT = 0.75  # one CSS px in points


def _mpl_item(ax, fig, it: Item, t: tuple[float, float, float], scale: float, height: float,
              embed_png: Callable[[Embed, int, int], Any], z: list[int]) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyBboxPatch, Polygon

    ox, oy, s = t
    z[0] += 1  # paint in list order, as SVG does (matplotlib would sort by artist kind)

    def X(x):  # noqa: N802
        return ox + s * x

    def Y(y):  # noqa: N802
        return oy + s * y

    if isinstance(it, Rect):
        ax.add_patch(FancyBboxPatch(
            (X(it.x), Y(it.y)), it.w * s, it.h * s,
            boxstyle=f"round,pad=0,rounding_size={it.rx * s}" if it.rx else "square,pad=0",
            facecolor=it.fill or "none", edgecolor=it.stroke or "none",
            linewidth=it.sw * s * PT if it.stroke else 0,
            linestyle=(0, (5, 4)) if it.dash else "solid", zorder=z[0]))
    elif isinstance(it, Label):
        ax.text(X(it.x), Y(it.y), it.text, fontsize=it.size * s * PT, color=it.color,
                ha={"start": "left", "middle": "center", "end": "right"}[it.anchor],
                va="baseline", fontproperties=label_font(it), parse_math=False, zorder=z[0])
    elif isinstance(it, Path):
        pts = [(X(x), Y(y)) for x, y in it.points]
        lw = it.sw * s * PT
        if it.closed or it.fill:
            ax.add_patch(Polygon(pts, closed=it.closed, facecolor=it.fill or "none",
                                 edgecolor=it.stroke or "none", linewidth=lw if it.stroke else 0,
                                 zorder=z[0]))
        else:
            ax.add_line(Line2D([p[0] for p in pts], [p[1] for p in pts], color=it.stroke,
                               linewidth=lw, linestyle=(0, (6, 4)) if it.dash else "solid",
                               solid_joinstyle="round", zorder=z[0]))
    elif isinstance(it, Embed):
        w, h = round(it.w * s * scale), round(it.h * s * scale)
        img = embed_png(it, w, h)
        fig.figimage(img, xo=round(X(it.x) * scale), yo=round((height - Y(it.y)) * scale) - h,
                     origin="upper", zorder=z[0])
    elif isinstance(it, Group):
        nt = (X(it.dx), Y(it.dy), s * it.scale)
        for c in it.items:
            _mpl_item(ax, fig, c, nt, scale, height, embed_png, z)


def to_png(items: list[Item], width: float, height: float, scale: float,
           embed_png: Callable[[Embed, int, int], Any]) -> bytes:
    from matplotlib.figure import Figure

    from ..render.mpl.adapter import render_context

    with render_context({}):
        # Agg truncates size*dpi; nudge so the PNG is exactly round(width*scale) px wide.
        fig = Figure(figsize=((round(width * scale) + 1e-6) / (96 * scale),
                              (round(height * scale) + 1e-6) / (96 * scale)),
                     dpi=96 * scale, facecolor="white")
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.set_axis_off()
        z = [0]
        for it in items:
            _mpl_item(ax, fig, it, (0.0, 0.0, 1.0), scale, height, embed_png, z)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=96 * scale, metadata={"Software": None})
    return buf.getvalue()


def label_font(it: Label) -> Any:
    """The FontProperties a PNG draws ``it`` with (also what its glyphs are
    checked against)."""
    from matplotlib.font_manager import FontProperties

    from ..render.mpl.style import with_fallbacks

    return FontProperties(family=with_fallbacks("DejaVu Sans Mono" if it.mono else "DejaVu Sans"),
                          weight="bold" if it.bold else "normal",
                          style="italic" if it.italic else "normal")
