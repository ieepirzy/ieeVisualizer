"""Lay a ViewSpec out into a display list (:mod:`.draw`) at a given width.

Box model: every node is drawn at ``(x, y)`` with a fixed width and reports
the height it used. ``stack`` vertical places children one below another,
``stack`` horizontal and ``grid`` split the width into equal columns (a grid
row is as tall as its tallest cell), ``card`` draws a titled frame around a
vertical stack. Component sizes come from measured text (:mod:`.text`), so
wrapping is the same in SVG and PNG.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from decimal import Decimal, localcontext
from functools import lru_cache

from ..render import RenderError
from ..spec import DataSnapshot, ValidationIssue
from ..spec._common import ptr_join
from . import flow as _flow
from .draw import INK, LINE, MUTED, Embed, Group, Item, Label, Path, Rect
from .limits import MAX_TABLE_ROWS
from .model import Card, FigureComponent, Flow, Grid, Kpi, Node, Note, Stack, Table, Text, ViewSpec
from .text import (
    NO_DATA,
    NULL_CELL,
    SCI_FROM,
    as_decimal,
    fit_len,
    format_compact,
    format_number,
    parse_markdown,
    run_width,
    text_width,
    with_unit,
    wrap_runs,
    wrap_text,
)

GAP = 16.0
PAD = 24.0
TEXT_STYLES = {  # size, line height, bold, colour
    "body": (15.0, 22.0, False, INK),
    "lead": (18.0, 26.0, False, INK),
    "heading": (20.0, 28.0, True, INK),
    "small": (12.0, 17.0, False, MUTED),
}
NODE_STYLE = {  # fill, stroke, rx, dashed
    "card": ("#ffffff", "#57606a", 8.0, False),
    "process": ("#ddf4ff", "#0969da", 0.0, False),
    "data": ("#dafbe1", "#1a7f37", 14.0, False),
    "external": ("#f6f8fa", "#57606a", 8.0, True),
}
EDGE_STYLE = {  # colour, dashed
    "dataflow": (INK, False),
    "dependency": (MUTED, True),
    "causal": ("#8250df", True),
}


#: On a narrow profile, grids and horizontal stacks drop columns until each
#: slot is at least this wide (CSS px): KPI tiles and figures stack.
NARROW_MIN_SLOT = 150.0


@dataclass
class Ctx:
    view: ViewSpec
    embeds: list[Embed] = field(default_factory=list)
    #: Smallest text size (CSS px) a profile allows (0: no profile) and the
    #: narrowest slot before columns stack (None: never).
    min_text: float = 0.0
    min_slot: float | None = None
    warnings: list = field(default_factory=list)
    #: Narrow profiles draw figures at least this tall (height / width): at
    #: phone width a short figure has no room left for its axes.
    min_aspect: float = 0.0


def layout_view(view: ViewSpec, width: float = 960.0, profile=None,
                warnings: list | None = None) -> tuple[list[Item], float, list[Embed]]:
    """``(items, height, embeds)`` for ``view`` drawn ``width`` px wide.
    With a :class:`~ieeVisualizer.render.RenderProfile`, text keeps its
    minimum size and narrow profiles stack columns; layout warnings are
    appended to ``warnings``."""
    from .validate import too_narrow_at  # (validate imports layout lazily too)

    min_slot = NARROW_MIN_SLOT if profile is not None and profile.narrow else None
    narrow = too_narrow_at(view, width, min_slot)
    if narrow is not None:  # never draw negative widths; render_view checks first
        raise RenderError([narrow])
    ctx = Ctx(view, min_text=profile.min_text_px if profile is not None else 0.0,
              min_slot=min_slot, warnings=warnings if warnings is not None else [],
              min_aspect=profile.aspect if min_slot is not None else 0.0)
    items: list[Item] = [Rect(0, 0, width, 0, fill="#ffffff")]
    y = PAD
    if view.title:
        for line in wrap_text(view.title, 26, width - 2 * PAD, bold=True):
            items.append(Label(PAD, y + 26, line, 26, bold=True))
            y += 34
        y += 8
    sub, h = draw_node(view.root, "/root", PAD, y, width - 2 * PAD, ctx)
    items.extend(sub)
    height = float(math.ceil(y + h + PAD))
    items[0].h = height
    return items, height, ctx.embeds


def draw_node(n: Node, path: str, x: float, y: float, w: float, ctx: Ctx
              ) -> tuple[list[Item], float]:
    if isinstance(n, (Stack, Grid, Card)):
        return _container(n, path, x, y, w, ctx)
    fn = {FigureComponent: _figure, Text: _text, Kpi: _kpi, Table: _table, Note: _note,
          Flow: _flow_component}[type(n)]
    return fn(n, path, x, y, w, ctx)


def _container(n, path: str, x: float, y: float, w: float, ctx: Ctx):
    items: list[Item] = []
    kids = [(c, ptr_join(path, "children", i)) for i, c in enumerate(n.children)]
    if isinstance(n, Card):
        inner_y, inner_x, inner_w = y + 16, x + 16, w - 32
        if n.title:
            for line in wrap_text(n.title, 16, inner_w, bold=True):
                items.append(Label(inner_x, inner_y + 16, line, 16, bold=True))
                inner_y += 22
            inner_y += 8
        sub, h = _rows([[k] for k in kids], 1, inner_x, inner_y, inner_w, ctx)
        total = inner_y - y + h + 16
        return [Rect(x, y, w, total, fill="#ffffff", stroke=LINE, rx=10)] + items + sub, total
    cols = slot_columns(n, w, ctx.min_slot)
    rows = [kids[i:i + cols] for i in range(0, len(kids), cols)]
    return _rows(rows, cols, x, y, w, ctx)


def slot_columns(n: Stack | Grid, w: float | None = None, min_slot: float | None = None
                 ) -> int:
    """Columns a grid / stack splits its width into. A grid never has more
    columns than children (``columns=200`` with 4 children is 4 columns, not
    196 empty ones squeezing the 4), so its children are as wide as they can
    be; :func:`.validate.too_narrow_at` refuses layouts whose slots would
    still be narrower than ``MIN_SLOT_WIDTH``. With ``min_slot`` (narrow
    profiles) columns are dropped until each slot of ``w`` px is that wide."""
    if isinstance(n, Grid):  # a non-integer `columns` is reported by validation
        cols = n.columns if isinstance(n.columns, int) and not isinstance(n.columns, bool) else 1
        cols = max(1, min(cols, len(n.children)))
    else:
        cols = max(1, len(n.children)) if n.direction == "horizontal" else 1
    while min_slot is not None and w is not None and cols > 1 \
            and (w - GAP * (cols - 1)) / cols < min_slot:
        cols -= 1
    return cols


def _rows(rows, cols: int, x: float, y: float, w: float, ctx: Ctx):
    items: list[Item] = []
    cw = (w - GAP * (cols - 1)) / cols
    cy = y
    for r, row in enumerate(rows):
        hmax = 0.0
        for i, (child, cpath) in enumerate(row):
            sub, h = draw_node(child, cpath, x + i * (cw + GAP), cy, cw, ctx)
            items.extend(sub)
            hmax = max(hmax, h)
        cy += hmax + (GAP if r < len(rows) - 1 else 0)
    return items, cy - y


def _figure(n: FigureComponent, path: str, x, y, w, ctx: Ctx):
    spec = n.spec if n.spec is not None else ctx.view.figures[n.figure]
    pointer = ptr_join(path, "spec") if n.spec is not None else ptr_join("/figures", n.figure)
    e = Embed(x, y, w, round(w * max(n.aspect, ctx.min_aspect), 2), spec, pointer,
              index=len(ctx.embeds))
    ctx.embeds.append(e)
    return [e], e.h


def _paragraphs(text: str, size: float, lh: float, bold: bool, color: str, x, y, w):
    items: list[Item] = []
    cy = y
    for bi, (kind, runs) in enumerate(parse_markdown(text)):
        if bi:
            cy += lh * 0.4
        indent = 18.0 if kind == "li" else 0.0
        if bold:
            runs = [type(r)(r.text, True, r.italic, r.code) for r in runs]
        for li, line in enumerate(wrap_runs(runs, size, w - indent)):
            base = cy + lh * 0.75
            if kind == "li" and li == 0:
                items.append(Label(x + 4, base, "•", size, color))
            cx = x + indent
            merged: list = []  # one Label per same-style stretch: no per-word gaps
            for r in line:
                if merged and (merged[-1].bold, merged[-1].italic, merged[-1].code) == (
                        r.bold, r.italic, r.code):
                    merged[-1] = type(r)(merged[-1].text + r.text, r.bold, r.italic, r.code)
                else:
                    merged.append(r)
            for r in merged:
                lead = r.text[:len(r.text) - len(r.text.lstrip())]
                fs = size * (0.92 if r.code else 1)
                items.append(Label(cx + text_width(lead, fs, r.bold, r.italic, r.code), base,
                                   r.text.strip(), size * (0.92 if r.code else 1), color,
                                   bold=r.bold, italic=r.italic, mono=r.code))
                cx += run_width(r, size * (0.92 if r.code else 1))
            cy += lh
    return items, cy - y


def _text(n: Text, path, x, y, w, ctx):
    size, lh, bold, color = TEXT_STYLES.get(n.style, TEXT_STYLES["body"])
    return _paragraphs(n.text, size, lh, bold, color, x, y, w)


def _note(n: Note, path, x, y, w, ctx):
    if n.type == "footnote":
        items, h = _paragraphs(n.text, 12, 17, False, MUTED, x, y + 8, w)
        return [Path([(x, y), (x + min(w, 160), y)], LINE, 1)] + items, h + 8
    items, h = _paragraphs("**Caveat:** " + n.text, 13, 19, False, INK, x + 16, y + 10, w - 28)
    return [Rect(x, y, w, h + 20, fill="#fff8c5", rx=4),
            Rect(x, y, 4, h + 20, fill="#bf8700")] + items, h + 20


def exact_delta(value: float, ref: float) -> Decimal:
    """``value - ref`` computed exactly on the numbers as written (``0.3 -
    0.1`` is ``0.2``, not ``0.19999999999999998``). Decimal, so it cannot
    overflow: ``1e308 - -1e308`` is ``2e+308``, never ``inf``."""
    with localcontext() as ctx:
        ctx.prec = 1000  # > 17 digits + the 632 decimal places between 1e308 and 5e-324
        return as_decimal(value) - as_decimal(ref)


def _delta_text(delta: Decimal, decimals: int | None, fmt=None) -> str:
    fmt = fmt or format_number
    if abs(delta) < SCI_FROM:  # the float of an exact difference prints at its shortest
        return fmt(float(delta), decimals)
    return fmt(delta, decimals)


def kpi_lines(n: Kpi, fmt=None) -> tuple[str, str | None]:
    """(value text, comparison text). Shared with the HTML renderer. The
    arrow follows the sign of the exact delta, and a non-zero delta is never
    written as zero: one below the last decimal reads "▲ <+0.1", only an
    exact tie reads "= 0.0". A ``%`` unit makes 100 a bound too (99.97 at
    one decimal is ">99.9%"). ``fmt`` replaces :func:`.text.format_number`
    (the tile uses it for compact notation when a number does not fit)."""
    fmt = fmt or format_number
    pct = n.unit == "%"

    def num(v):
        return with_unit(fmt(v, n.decimals, percent=pct), n.unit)

    value = num(n.value)
    cmp = None
    if n.comparison is not None:
        c = n.comparison
        if c.value is None:
            cmp = f"{c.label}: {NO_DATA}"
        elif n.value is None:
            cmp = f"{c.label}: {num(c.value)}"
        else:
            delta = exact_delta(n.value, c.value)
            text = _delta_text(delta, n.decimals, fmt)
            if not delta:
                arrow, sign = "=", ""
            elif delta < 0:
                arrow, sign = "▼", ""
            elif text.startswith("<"):
                arrow, sign, text = "▲", "<+", text[1:]
            else:
                arrow, sign = "▲", "+"
            cmp = f"{arrow} {sign}{with_unit(text, n.unit)} vs {c.label} ({num(c.value)})"
    return value, cmp


#: Font sizes a KPI value may take (px): it shrinks before it wraps.
KPI_VALUE_SIZES = (30, 28, 26, 24, 22, 20, 18, 16, 14)
#: Smaller sizes, used only to keep a number on one line (never split it).
KPI_SMALL_SIZES = (13, 12, 11, 10, 9, 8)
#: Significant digits tried, in order, when a number does not fit even so.
COMPACT_DIGITS = (6, 3, 2, 1)


def _kpi_variants(n: Kpi) -> list[tuple[tuple[str, str | None], list[str]]]:
    """``kpi_lines`` as written, then in compact notation of fewer and fewer
    significant digits; each with the number strings it contains."""
    out = []
    for digits in (None, *COMPACT_DIGITS):
        nums: list[str] = []

        def fmt(v, decimals, percent=False, digits=digits, nums=nums):
            s = (format_number(v, decimals, percent=percent) if digits is None
                 else format_compact(v, decimals, digits, percent=percent))
            nums.append(s)
            return s

        out.append((kpi_lines(n, fmt), nums))
    return out


def _numbers_fit(text: str, nums: list[str], size: float, w: float, bold: bool) -> bool:
    """Is every word of ``text`` that holds a number at most ``w`` px wide?
    Lines break only at ASCII spaces, so such a word is never split."""
    return all(text_width(t, size, bold=bold) <= w for t in text.split(" ")
               if any(x in t for x in nums))


def _fit_number_text(variants, which: int, sizes: tuple[float, ...], w: float, bold: bool,
                     shrink_first: bool, small: tuple[float, ...] = KPI_SMALL_SIZES
                     ) -> tuple[list[str], float]:
    """Lines and size for ``variants[k][0][which]``: the first variant whose
    numbers fit whole, at the largest size it does; ``shrink_first``: first a
    single line at a size in ``sizes``, before any wrapping."""
    text = variants[0][0][which]
    if shrink_first:
        for size in sizes:
            if text_width(text, size, bold=bold) <= w:
                return [text], size
        sizes = sizes[-1:]  # as written, wrapped at the smallest size first
    for lines, nums in variants:
        text = lines[which]
        for size in sizes + small:
            if _numbers_fit(text, nums, size, w, bold):
                return wrap_text(text, size, w, bold=bold), size
    # not even one significant digit fits whole: draw it unbroken (lines only
    # break at ASCII spaces) and let it overflow, rather than split a number
    size = (small or sizes)[-1]
    return wrap_text(variants[-1][0][which], size, math.inf, bold=bold), size


def _compacted(ctx: Ctx, exact: str, lines: list[str], n: Kpi, path: str) -> None:
    """Profile renders: a KPI number rounded to fewer digits to fit its tile
    is a ``kpi_value_compacted`` warning (the send gate refuses it)."""
    if ctx.min_text and "".join(lines).replace(" ", "") != exact.replace(" ", ""):
        from ..render import RenderWarning

        ctx.warnings.append(RenderWarning(
            "kpi_value_compacted", f"KPI {n.label!r} is shown as '{' '.join(lines)}' instead "
            f"of '{exact}': the exact number does not fit its tile at the minimum text size.",
            path))


def _kpi(n: Kpi, path, x, y, w, ctx):
    """Every line stays inside the tile: the value shrinks from 30 to 14 px
    and then wraps between words. A number is never split across lines: if
    one does not fit at 14 px it shrinks further (to 8 px), then is written
    in compact notation with fewer significant digits (6, 3, 2, 1)."""
    items: list[Item] = []
    ix, iw, cy = x + 16, w - 32, y + 14
    for line in wrap_text(n.label, 13, iw):
        items.append(Label(ix, cy + 13, line, 13, MUTED))
        cy += 18
    variants = _kpi_variants(n)
    if n.value is None:
        items.append(Label(ix, cy + 30, NO_DATA, 22, MUTED, italic=True))
        cy += 42
    else:
        small = tuple(s for s in KPI_SMALL_SIZES if s >= ctx.min_text)
        lines, size = _fit_number_text(variants, 0, KPI_VALUE_SIZES, iw, True, True, small)
        _compacted(ctx, variants[0][0][0], lines, n, path)
        lh = size * 1.25
        for i, line in enumerate(lines):
            items.append(Label(ix, cy + 32 + i * lh, line, size, bold=True))
        cy += 42 + (len(lines) - 1) * lh
    cmp = variants[0][0][1]
    if cmp:
        small = tuple(s for s in KPI_SMALL_SIZES if s >= ctx.min_text)
        lines, size = _fit_number_text(variants, 1, (12,), iw, False, False, small)
        _compacted(ctx, cmp, lines, n, path)
        for line in lines:
            items.append(Label(ix, cy + 13, line, size, INK))
            cy += size + 5
    if n.source:
        for line in wrap_text(f"Source: {n.source}", 12, iw):
            items.append(Label(ix, cy + 13, line, 12, MUTED))
            cy += 17
    h = cy - y + 12
    return [Rect(x, y, w, h, fill="#f6f8fa", stroke=LINE, rx=8)] + items, h


def _cell(v, decimals=None) -> str:
    if v is None:
        return NULL_CELL
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return format_number(v, decimals)
    s = str(v)
    return s[:-9] if s.endswith("T00:00:00") else s  # a midnight datetime reads as its date


def table_cells(n: Table, ds: DataSnapshot, path: str = ""
                ) -> tuple[list[str], list[str], list[list[str]], int]:
    """(column names, header texts, rows, total row count) - shared with HTML.
    At most ``MAX_TABLE_ROWS`` rows are returned; callers say "Showing N of M"."""
    names = n.columns if n.columns is not None else list(ds.columns)
    heads = [nm + (f" ({ds.columns[nm].unit})" if ds.columns[nm].unit else "") for nm in names]
    total = ds.num_rows
    if total is None:  # validation reports column_length_mismatch; never draw zero rows
        raise RenderError([ValidationIssue(ptr_join(path, "data"), "column_length_mismatch",
                                           f"the columns of dataset {n.data!r} differ in "
                                           "length; the table cannot be drawn.")])
    shown = min(total, MAX_TABLE_ROWS if n.max_rows is None else min(n.max_rows, MAX_TABLE_ROWS))
    rows = [[_cell(ds.columns[nm].values[i]) for nm in names] for i in range(shown)]
    return names, heads, rows, total


#: A table cell or header is measured and truncated on at most this many
#: characters: a column is never wider than the view, so the rest could only
#: be cut off (it gets the "…"). Bounds the work per cell whatever its length.
FIT_CHARS = 1000


def _fit(s: str, size: float, w: float, bold: bool = False) -> str:
    """``s``, or its longest prefix + "…" that fits in ``w`` px. Only the
    first ``FIT_CHARS`` characters are ever measured, and the search
    (:func:`.text.fit_len`) costs O(k log k) in the length k of the prefix
    that fits, so a long string in a narrow column is cheap."""
    if len(s) <= FIT_CHARS and text_width(s, size, bold) <= w:  # cached: column widths
        return s
    t = s[:FIT_CHARS]
    return t[:fit_len(t, size, w, bold, "…")] + "…"


def _title(title: str, x: float, y: float, w: float) -> tuple[list[Item], float]:
    """A table / flow title, wrapped to the slot: ``(items, y below it)``."""
    items: list[Item] = []
    for i, line in enumerate(wrap_text(title, 15, w, bold=True)):
        items.append(Label(x, y + 16 + 20 * i, line, 15, bold=True))
    return items, y + 26 + 20 * (len(items) - 1)


def _table(n: Table, path, x, y, w, ctx: Ctx):
    ds = ctx.view.datasets[n.data]
    names, heads, rows, total = table_cells(n, ds, path)
    items: list[Item] = []
    cy = y
    if n.title:
        sub, cy = _title(n.title, x, cy, w)
        items.extend(sub)
    numeric = [ds.columns[nm].dtype in ("int", "float") for nm in names]
    want = [max([text_width(h[:FIT_CHARS], 13, True)]
                + [text_width(r[i][:FIT_CHARS], 13) for r in rows]) + 20
            for i, h in enumerate(heads)]
    k = min(1.0, w / sum(want)) if want else 1.0
    widths = [wi * k for wi in want]
    w = sum(widths)  # a table is as wide as its content needs, at most the slot
    rh = 26.0
    cut = 0
    for ri, cells in enumerate([heads] + rows):
        if ri == 0:
            items.append(Rect(x, cy, w, rh, fill="#eaeef2"))
        elif ri % 2 == 0:
            items.append(Rect(x, cy, w, rh, fill="#f6f8fa"))
        cx = x
        for ci, s in enumerate(cells):
            right = numeric[ci]
            color = MUTED if s == NULL_CELL else INK
            shown = _fit(s, 13, widths[ci] - 20, ri == 0)
            cut += shown != s
            items.append(Label(cx + widths[ci] - 10 if right else cx + 10, cy + 17,
                               shown, 13, color, bold=ri == 0,
                               anchor="end" if right else "start"))
            cx += widths[ci]
        cy += rh
    items.append(Path([(x, cy), (x + w, cy)], LINE, 1))
    if cut and ctx.min_text:
        from ..render import RenderWarning

        ctx.warnings.append(RenderWarning(
            "table_cells_truncated",
            f"{cut} cell(s) of the table do not fit its {len(heads)} columns at this width and "
            "end in '…'; the HTML and email outputs show them whole.", path))
    if len(rows) < total:
        items.append(Label(x, cy + 16, f"Showing {len(rows)} of {total} rows", 12, MUTED))
        cy += 22
    return items, cy - y


# --------------------------------------------------------------------------
# flow
# --------------------------------------------------------------------------

NODE_FONT, NODE_LH, NODE_MAX_W = 14.0, 18.0, 180.0


@lru_cache(maxsize=1024)
def _node_lines(label: str) -> tuple[str, ...]:
    """Wrapped node label; cached because layout asks for every node's size
    more than once and drawing wraps it again."""
    return tuple(wrap_text(label, NODE_FONT, NODE_MAX_W))


def _node_size(node) -> tuple[float, float]:
    lines = _node_lines(node.label)
    return (max(text_width(s, NODE_FONT) for s in lines) + 28, len(lines) * NODE_LH + 24)


def edge_text(e) -> str | None:
    if e.kind == "causal":
        return f"claim: {e.label}" if e.label else "claim (unlabelled)"
    return e.label


def _arrow(p0, p1, color: str) -> tuple[tuple[float, float], Path]:
    """Arrowhead at p1 pointing along p0->p1; returns (shortened end, head)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    back = (p1[0] - 9 * ux, p1[1] - 9 * uy)
    head = [p1, (back[0] - 4.5 * uy, back[1] + 4.5 * ux), (back[0] + 4.5 * uy, back[1] - 4.5 * ux)]
    return back, Path(head, None, fill=color, closed=True)


def flow_items(f: Flow) -> tuple[list[Item], float, float, list[str]]:
    """Display list of a flow in its own coordinates, its natural size and its
    reading order (so the text version reuses this one layout)."""
    geo = _flow.layout_flow(
        f, _node_size, label_size=lambda s: (text_width(s, 12) + 8, 16),
        lane_label_size=lambda s: (text_width(s, 12, True), 16))
    items: list[Item] = []
    for name, band in geo.lanes:
        items.append(Rect(band.x, band.y, band.w, band.h, fill="#f6f8fa", stroke=LINE, rx=6))
        if name:
            items.append(Label(band.x + 8, band.y + 18, name, 12, MUTED, bold=True))
    for k, (e, pts) in enumerate(zip(f.edges, geo.routes)):
        if not pts:
            continue
        color, dashed = EDGE_STYLE.get(e.kind, EDGE_STYLE["dataflow"])
        end, head = _arrow(pts[-2], pts[-1], color)
        g = Group([Path(pts[:-1] + [end], color, 1.5, dashed), head],
                  attrs={"data-edge": str(k), "data-from": e.source, "data-to": e.target,
                         "data-kind": e.kind})
        text = edge_text(e)
        if text and geo.label_at[k]:
            lx, ly = geo.label_at[k]
            tw = text_width(text, 12)
            if e.source == e.target:  # keep a self-loop's label outside the box
                if f.layout.direction == "TB" and f.layout.algorithm == "layered":
                    lx += tw / 2 + 6
                else:
                    ly -= 10
            g.items.append(Rect(lx - tw / 2 - 4, ly - 10, tw + 8, 18, fill="#ffffff"))
            g.items.append(Label(lx, ly + 4, text, 12, color, italic=e.kind == "causal",
                                 anchor="middle"))
        items.append(g)
    for node in f.nodes:
        b = geo.boxes[node.id]
        fill, stroke, rx, dashed = NODE_STYLE.get(node.component, NODE_STYLE["card"])
        g = Group([Rect(b.x, b.y, b.w, b.h, fill=fill, stroke=stroke, rx=rx, dash=dashed,
                        sw=1.5)], attrs={"data-node": node.id})
        lines = _node_lines(node.label)
        top = b.cy - len(lines) * NODE_LH / 2
        for i, line in enumerate(lines):
            g.items.append(Label(b.cx, top + i * NODE_LH + 14, line, NODE_FONT, anchor="middle"))
        items.append(g)
    return items, geo.width, geo.height, geo.order


#: Smallest text a flow draws (edge and lane labels), in its own px.
FLOW_MIN_FONT = 12.0


def fit_flow(f: Flow, w: float, min_text: float = 0.0, path: str = "",
             warnings: list | None = None):
    """``(flow, items, width, height, order, scale)`` of ``f`` scaled to fit
    ``w`` px. When scaling would take its text below ``min_text`` px, a
    left-to-right layered flow is laid out top to bottom instead (the layout
    is only a hint); if that is still too small, a ``flow_text_too_small``
    warning is appended."""
    sub, gw, gh, order = flow_items(f)
    s = min(1.0, w / gw) if gw else 1.0
    if min_text and FLOW_MIN_FONT * s < min_text and f.layout.algorithm == "layered" \
            and f.layout.direction == "LR":
        tb = replace(f, layout=replace(f.layout, direction="TB"))
        sub2, gw2, gh2, order2 = flow_items(tb)
        s2 = min(1.0, w / gw2) if gw2 else 1.0
        if s2 > s:
            f, sub, gw, gh, order, s = tb, sub2, gw2, gh2, order2, s2
    if min_text and FLOW_MIN_FONT * s < min_text - 1e-9 and warnings is not None:
        from ..render import RenderWarning

        warnings.append(RenderWarning(
            "flow_text_too_small",
            f"the flow is scaled to {s:.0%} to fit {w:.0f} px, so its smallest labels are "
            f"{FLOW_MIN_FONT * s:.1f} px (minimum {min_text:g}); use fewer nodes per layer or "
            "a wider profile.", path))
    return f, sub, gw, gh, order, s


def _flow_component(f: Flow, path, x, y, w, ctx):
    items: list[Item] = []
    cy = y
    if f.title:
        sub, cy = _title(f.title, x, cy, w)
        items.extend(sub)
    f, sub, gw, gh, order, s = fit_flow(f, w, ctx.min_text, path, ctx.warnings)
    desc = "\n".join(([f.description] if f.description else [])
                     + _flow.flow_outline(f, order))
    items.append(Group(sub, attrs={"data-flow": path, "aria-label": f.title or "Flow diagram"},
                       dx=x + (w - gw * s) / 2, dy=cy, scale=s, desc=desc))
    return items, cy - y + gh * s
