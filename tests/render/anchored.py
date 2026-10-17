"""Every text of a figure or network with what it belongs to (its anchor),
measured from the drawing, for comparing a desktop render with a profile
render: the same words, in the same order, belonging to the same thing.

* figure: title, axis labels, caption, legend title and entries (by the
  swatch drawn beside each text - colour, line style, marker, hatch -
  paired by position in the drawing), category tick labels (by axis and
  position), value labels (by the bar they are drawn on: layer and category,
  from the drawn extents), "no data" notes, and text annotations - by their
  data anchor if the text is drawn within 10 pt of it (else marked as drawn
  away), or for a template's row notes by the category row their drawn text
  sits in;
* network: title, caption, legend sections (by section and swatch),
  and node labels by the node a reader attributes them to: the node whose
  centre is nearest the label box, nearer than every other by ``MARGIN_PX``,
  or ``None`` when that is not clear (a reader model, not the renderer's
  rim-to-rim rule).

Wrapping only changes line breaks, so texts compare as token sequences.
"""

from __future__ import annotations

from collections import Counter

#: A label belongs to a node only if it is this much nearer (device px of a
#: 1920 px desktop image; scaled by dpi) than to any other node.
MARGIN_PX = 6.0


def tokens(text: str) -> tuple:
    return tuple(text.split())


def anchored(spec, request, mutate=None) -> Counter:
    """``mutate(built)``, if given, changes the drawing before it is
    measured (the tests show a wrong drawing is told apart)."""
    from ieeVisualizer.network import NetworkSpec

    if isinstance(spec, NetworkSpec):
        return _network(spec, request, mutate)
    return _figure(spec, request, mutate)


def _figure(spec, request, mutate=None) -> Counter:
    from matplotlib.container import BarContainer
    from matplotlib.text import Annotation

    from ieeVisualizer.render import build_figure
    from ieeVisualizer.render.mpl.adapter import row_notes

    built = build_figure(spec, request)
    if mutate is not None:
        mutate(built)
    ax, ctx, out = built.axes, built.context, Counter()
    fig = built.figure
    px = fig.dpi / 72.0

    def add(key, text):
        if text and text.strip():
            out[(key, tokens(text))] += 1

    add(("title",), ax.title.get_text())
    add(("xlabel",), ax.xaxis.label.get_text())
    add(("ylabel",), ax.yaxis.label.get_text())
    if built.caption is not None:
        add(("caption",), built.caption.get_text())
    legend = ax.get_legend()
    if legend is not None and legend.get_visible():
        add(("legend-title",), legend.get_title().get_text())
        for t, h in legend_pairs(legend):
            add(("legend", _swatch(h)), t.get_text())  # the swatch drawn beside the text
    for key, axis in (("x", ax.xaxis), ("y", ax.yaxis)):
        info = ctx.axes.get(key)
        if info is None or info.kind != "category":
            continue
        lo, hi = sorted(axis.get_view_interval())
        for tick in axis.get_major_ticks():
            if lo - 1e-9 <= tick.get_loc() <= hi + 1e-9 and tick.label1.get_visible():
                add(("tick", key, round(float(tick.get_loc()), 6)), tick.label1.get_text())
    bars = [(layer, rect) for layer, arts in built.layer_artists.items() for c in arts
            if isinstance(c, BarContainer) for rect in c.patches]
    for lab in ctx.value_labels:
        if lab.text.get_visible():
            add(("value",) + _bar_under(lab.text.get_window_extent(), bars, lab.ch == "x"),
                lab.text.get_text())
    for note in ctx.no_data_notes:
        if note.text.get_visible():
            add(("nodata", note.layer, round(note.slot, 6)), note.text.get_text())
    rows = row_notes(spec)
    category_rows = ctx.axes.get("y") is not None and ctx.axes["y"].kind == "category"
    for a in built.annotation_artists:
        if not (isinstance(a, Annotation) and a.get_visible()):
            continue
        i = getattr(a, "_ieev_index", None)
        box = a.get_window_extent()
        anchor = ax.transData.transform((float(ax.xaxis.convert_units(a.xy[0])),
                                         float(ax.yaxis.convert_units(a.xy[1]))))
        # on a category y axis: every row whose band the drawn text reaches
        # into (a text drifting into the row above reads as that row's)
        touched = _rows_touched(ax, box) if category_rows else ()
        if i in rows:
            add(("row-note", i, touched), a.get_text())
        elif _gap(anchor, box) <= 10 * px:  # drawn at its anchor (offset of a few pt)
            add(("annotation", i, touched) + tuple(round(float(v), 6) for v in
                                                   ax.transData.inverted().transform(anchor)),
                a.get_text())
        else:
            add(("annotation", i, "drawn away from its anchor"), a.get_text())
    return out


def _rows_touched(ax, box) -> tuple:
    """Category positions (y) whose band [k - 0.5, k + 0.5] the box overlaps
    by more than 1 px."""
    inv = ax.transData.inverted()
    lo, hi = sorted(float(v) for v in inv.transform([(0, box.y0 + 1), (0, box.y1 - 1)])[:, 1])
    return tuple(range(int(round(lo)), int(round(hi)) + 1))


def legend_pairs(legend) -> list:
    """``[(text, handle)]``: each legend text with the swatch drawn beside
    it - the handle whose box ends nearest left of the text, on the same
    line - measured from the drawing, not taken in list order."""
    handles = getattr(legend, "legend_handles", None) or legend.legendHandles
    renderer = legend.figure._get_renderer()
    boxes = [_handle_box(h, renderer) for h in handles]
    out = []
    for t in legend.get_texts():
        tb = t.get_window_extent(renderer)
        near = [(tb.x0 - b.x1, k) for k, b in enumerate(boxes)
                if b is not None and tb.y0 <= (b.y0 + b.y1) / 2 <= tb.y1 and b.x1 <= tb.x0 + 2]
        out.append((t, handles[min(near)[1]] if near else None))
    return out


def _handle_box(h, renderer):
    """Where a legend handle is drawn: its extent, or for a marker
    collection (a scatter swatch, whose extent matplotlib reports as
    infinite) the box of its marker offsets."""
    import numpy as np
    from matplotlib.transforms import Bbox

    try:
        b = h.get_window_extent(renderer)
    except (AttributeError, TypeError):
        b = None
    if b is not None and np.all(np.isfinite(b.get_points())) and b.width + b.height > 0:
        return b
    offsets = getattr(h, "get_offsets", lambda: [])()
    if len(offsets):
        pts = h.get_offset_transform().transform(np.asarray(offsets, dtype=float))
        if np.all(np.isfinite(pts)):
            return Bbox.from_extents(pts[:, 0].min(), pts[:, 1].min(),
                                     pts[:, 0].max(), pts[:, 1].max())
    return None


def _swatch(handle) -> tuple:
    """What tells a swatch apart: its colour, line style and marker."""
    from matplotlib.colors import to_rgba

    if handle is None:
        return ("no swatch beside it",)
    colour = ()
    for get in ("get_facecolor", "get_color", "get_markerfacecolor"):
        f = getattr(handle, get, None)
        if f is not None:
            c = f()
            if hasattr(c, "__len__") and len(c) and hasattr(c[0], "__len__"):
                c = c[0]
            try:
                colour = tuple(round(v, 3) for v in to_rgba(c))
                break
            except ValueError:
                continue
    style = getattr(handle, "get_linestyle", lambda: None)()
    # the dash pattern itself (every tuple style reads '--'), as given, not
    # scaled by the line width
    dashes = getattr(handle, "_unscaled_dash_pattern", None)
    if dashes is None and hasattr(handle, "get_dashes"):
        dashes = handle.get_dashes()
        dashes = dashes[0] if isinstance(dashes, list) and dashes else dashes
    if dashes is not None and len(dashes) == 2:
        offset, seq = dashes
        style = (round(float(offset or 0), 3),
                 tuple(round(float(v), 3) for v in (seq or ())))
    marker = getattr(handle, "get_marker", lambda: None)()
    hatch = getattr(handle, "get_hatch", lambda: None)()
    return colour + (str(style), str(marker), str(hatch))


def _gap(p, box) -> float:
    dx = max(box.x0 - p[0], 0.0, p[0] - box.x1)
    dy = max(box.y0 - p[1], 0.0, p[1] - box.y1)
    return (dx * dx + dy * dy) ** 0.5


def _bar_under(box, bars, horizontal: bool) -> tuple:
    """The bar a value label is drawn on, from the drawing: the bar whose
    span across the category axis holds the label's middle and whose end is
    nearest the label (layer and the bar's category position)."""
    best = None
    cy, cx = (box.y0 + box.y1) / 2, (box.x0 + box.x1) / 2
    for layer, rect in bars:
        r = rect.get_window_extent()
        if horizontal and r.y0 <= cy <= r.y1:
            d = min(abs(box.x0 - r.x1), abs(box.x1 - r.x0))
            centre = rect.get_y() + rect.get_height() / 2
        elif not horizontal and r.x0 <= cx <= r.x1:
            d = min(abs(box.y0 - r.y1), abs(box.y1 - r.y0))
            centre = rect.get_x() + rect.get_width() / 2
        else:
            continue
        if best is None or d < best[0]:
            best = (d, layer, round(float(centre), 6))
    return ("no bar",) if best is None else best[1:]


def drawn_nodes(built) -> list:
    """``[(centre, radius)]`` in display px per node, read from the drawn
    marker collections (offsets and marker sizes), not from the renderer's
    own bookkeeping; in node order."""
    import numpy as np

    ax = built.axes
    xy = np.asarray(built.node_xy, dtype=float)
    out = [None] * len(xy)
    px = built.figure.dpi / 72.0
    for coll in ax.collections:
        offsets = getattr(coll, "get_offsets", lambda: [])()
        sizes = getattr(coll, "get_sizes", lambda: [])()
        if len(offsets) == 0 or len(sizes) == 0:
            continue
        for k, off in enumerate(np.asarray(offsets, dtype=float)):
            hit = np.flatnonzero(np.all(np.isclose(xy, off), axis=1))
            for j in hit:
                if out[j] is None:
                    r = (float(sizes[k % len(sizes)]) ** 0.5) / 2 * px  # s is area in pt^2
                    out[j] = (ax.transData.transform(off), r)
    return [o if o is not None else (ax.transData.transform(p), 0.0) for o, p in zip(out, xy)]


def owner(box, nodes, margin):
    """The node a reader attributes label ``box`` to: the node whose drawn
    marker edge is nearest the box (nearest box point to the rim; negative
    when the box covers the marker), if it is nearer than every other by
    ``margin``; else None. A reader model, not the renderer's rule restated:
    it reads centres and sizes from the drawn markers, does not clamp a gap
    at zero (a label over its own marker is clearly that marker's, over
    another marker clearly not), and uses its own margin (``MARGIN_PX``, 6 px
    at 192 dpi = 2.25 pt, against the renderer's 3 pt)."""
    d = sorted((_gap(c, box) - r, j) for j, (c, r) in enumerate(nodes))
    if len(d) > 1 and d[1][0] <= d[0][0] + margin:
        return None
    return d[0][1]


def _network(spec, request, mutate=None) -> Counter:
    from ieeVisualizer.network.render import build_network

    built = build_network(spec, request)
    if mutate is not None:
        mutate(built)
    fig, ax, out = built.figure, built.axes, Counter()
    renderer = fig.canvas.get_renderer()
    margin = MARGIN_PX * fig.dpi / 192.0
    nodes = drawn_nodes(built)

    def add(key, text):
        if text and text.strip():
            out[(key, tokens(text))] += 1

    add(("title",), ax.title.get_text())
    for t in fig.texts:
        add(("caption",), t.get_text())
    for k, leg in enumerate(built.legends):
        add(("legend-title", k), leg.get_title().get_text())
        for t, h in legend_pairs(leg):
            add(("legend", k, _swatch(h)), t.get_text())
    for t in built.labels.values():
        box = t.get_window_extent(renderer)
        add(("node", owner(box, nodes, margin)), t.get_text())
    return out
