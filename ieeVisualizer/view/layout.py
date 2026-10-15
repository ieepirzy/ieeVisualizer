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
from dataclasses import dataclass, field
from decimal import Decimal

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
    format_number,
    measure,
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


@dataclass
class Ctx:
    view: ViewSpec
    embeds: list[Embed] = field(default_factory=list)


def layout_view(view: ViewSpec, width: float = 960.0) -> tuple[list[Item], float, list[Embed]]:
    """``(items, height, embeds)`` for ``view`` drawn ``width`` px wide."""
    ctx = Ctx(view)
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
    if isinstance(n, Grid):
        cols = max(1, n.columns)
    else:
        cols = len(kids) if n.direction == "horizontal" else 1
    rows = [kids[i:i + max(cols, 1)] for i in range(0, len(kids), max(cols, 1))]
    return _rows(rows, max(cols, 1), x, y, w, ctx)


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
    e = Embed(x, y, w, round(w * n.aspect, 2), spec, pointer, index=len(ctx.embeds))
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


def exact_delta(value: float, ref: float) -> float:
    """``value - ref`` computed on the numbers as written (``0.3 - 0.1`` is
    ``0.2``, not ``0.19999999999999998``): the difference of the shortest
    decimal forms never has more decimal places than its operands."""
    return float(Decimal(repr(value)) - Decimal(repr(ref)))


def kpi_lines(n: Kpi) -> tuple[str, str | None]:
    """(value text, comparison text). Shared with the HTML renderer."""
    value = with_unit(format_number(n.value, n.decimals), n.unit)
    cmp = None
    if n.comparison is not None:
        c = n.comparison
        if c.value is None:
            cmp = f"{c.label}: {NO_DATA}"
        elif n.value is None:
            cmp = f"{c.label}: {with_unit(format_number(c.value, n.decimals), n.unit)}"
        else:
            delta = exact_delta(n.value, c.value)
            arrow = "▲" if delta > 0 else "▼" if delta < 0 else "="
            sign = "+" if delta > 0 else ""
            cmp = (f"{arrow} {sign}{with_unit(format_number(delta, n.decimals), n.unit)} vs "
                   f"{c.label} ({with_unit(format_number(c.value, n.decimals), n.unit)})")
    return value, cmp


def _kpi(n: Kpi, path, x, y, w, ctx):
    items: list[Item] = []
    ix, iw, cy = x + 16, w - 32, y + 14
    for line in wrap_text(n.label, 13, iw):
        items.append(Label(ix, cy + 13, line, 13, MUTED))
        cy += 18
    value, cmp = kpi_lines(n)
    if n.value is None:
        items.append(Label(ix, cy + 30, NO_DATA, 22, MUTED, italic=True))
    else:
        size = 30.0
        while size > 14 and text_width(value, size, bold=True) > iw:
            size -= 2
        items.append(Label(ix, cy + 32, value, size, bold=True))
    cy += 42
    for text, color in ((cmp, INK), (n.source and f"Source: {n.source}", MUTED)):
        if text:
            for line in wrap_text(text, 12, iw):
                items.append(Label(ix, cy + 13, line, 12, color))
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


def _fit(s: str, size: float, w: float, bold: bool = False) -> str:
    """``s``, or its longest prefix + "…" that fits in ``w`` px. Binary search
    on the cut point: O(n log n) instead of re-measuring per removed char."""
    if text_width(s, size, bold) <= w:
        return s
    lo, hi = 0, len(s)  # s[:lo] + "…" fits (or lo == 0); s[:hi] + "…" does not
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if measure(s[:mid] + "…", size, bold) <= w:  # uncached: probes are throwaway
            lo = mid
        else:
            hi = mid
    return s[:lo] + "…"


def _table(n: Table, path, x, y, w, ctx: Ctx):
    ds = ctx.view.datasets[n.data]
    names, heads, rows, total = table_cells(n, ds, path)
    items: list[Item] = []
    cy = y
    if n.title:
        items.append(Label(x, cy + 16, n.title, 15, bold=True))
        cy += 26
    numeric = [ds.columns[nm].dtype in ("int", "float") for nm in names]
    want = [max([text_width(h, 13, True)] + [text_width(r[i], 13) for r in rows]) + 20
            for i, h in enumerate(heads)]
    k = min(1.0, w / sum(want)) if want else 1.0
    widths = [wi * k for wi in want]
    w = sum(widths)  # a table is as wide as its content needs, at most the slot
    rh = 26.0
    for ri, cells in enumerate([heads] + rows):
        if ri == 0:
            items.append(Rect(x, cy, w, rh, fill="#eaeef2"))
        elif ri % 2 == 0:
            items.append(Rect(x, cy, w, rh, fill="#f6f8fa"))
        cx = x
        for ci, s in enumerate(cells):
            right = numeric[ci]
            color = MUTED if s == NULL_CELL else INK
            items.append(Label(cx + widths[ci] - 10 if right else cx + 10, cy + 17,
                               _fit(s, 13, widths[ci] - 20, ri == 0), 13, color, bold=ri == 0,
                               anchor="end" if right else "start"))
            cx += widths[ci]
        cy += rh
    items.append(Path([(x, cy), (x + w, cy)], LINE, 1))
    if len(rows) < total:
        items.append(Label(x, cy + 16, f"Showing {len(rows)} of {total} rows", 12, MUTED))
        cy += 22
    return items, cy - y


# --------------------------------------------------------------------------
# flow
# --------------------------------------------------------------------------

NODE_FONT, NODE_LH, NODE_MAX_W = 14.0, 18.0, 180.0


def _node_lines(label: str) -> list[str]:
    return wrap_text(label, NODE_FONT, NODE_MAX_W)


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


def _flow_component(f: Flow, path, x, y, w, ctx):
    items: list[Item] = []
    cy = y
    if f.title:
        items.append(Label(x, cy + 16, f.title, 15, bold=True))
        cy += 26
    sub, gw, gh, order = flow_items(f)
    s = min(1.0, w / gw) if gw else 1.0
    desc = "\n".join(([f.description] if f.description else [])
                     + _flow.flow_outline(f, order))
    items.append(Group(sub, attrs={"data-flow": path, "aria-label": f.title or "Flow diagram"},
                       dx=x + (w - gw * s) / 2, dy=cy, scale=s, desc=desc))
    return items, cy - y + gh * s
