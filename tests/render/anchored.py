"""Every text of a figure or network with what it belongs to (its anchor),
measured from the drawing, for comparing a desktop render with a profile
render: the same words, in the same order, belonging to the same thing.

* figure: title, axis labels, caption, legend title and entries (by the
  swatch colour drawn beside each text), category tick labels (by axis and
  position), value labels (by the bar they are drawn on: layer and category,
  from the drawn extents), "no data" notes, and text annotations - by their
  data anchor if the text is drawn within 10 pt of it (else marked as drawn
  away), or for a template's row notes by the category row their drawn text
  sits in;
* network: title, caption, legend sections (by section and swatch colour),
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
        handles = getattr(legend, "legend_handles", None) or legend.legendHandles
        for t, h in zip(legend.get_texts(), handles):
            add(("legend", _swatch(h)), t.get_text())  # the colour drawn beside the text
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
    for a in built.annotation_artists:
        if not (isinstance(a, Annotation) and a.get_visible()):
            continue
        i = getattr(a, "_ieev_index", None)
        box = a.get_window_extent()
        anchor = ax.transData.transform((float(ax.xaxis.convert_units(a.xy[0])),
                                         float(ax.yaxis.convert_units(a.xy[1]))))
        if i in rows:  # its row: the category whose band holds the text's middle
            mid = ax.transData.inverted().transform(((box.x0 + box.x1) / 2,
                                                     (box.y0 + box.y1) / 2))[1]
            add(("row-note", i, round(float(mid))), a.get_text())
        elif _gap(anchor, box) <= 10 * px:  # drawn at its anchor (offset of a few pt)
            add(("annotation", i) + tuple(round(float(v), 6) for v in
                                           ax.transData.inverted().transform(anchor)),
                a.get_text())
        else:
            add(("annotation", i, "drawn away from its anchor"), a.get_text())
    return out


def _swatch(handle) -> tuple:
    from matplotlib.colors import to_rgba

    for get in ("get_facecolor", "get_color", "get_markerfacecolor"):
        f = getattr(handle, get, None)
        if f is not None:
            c = f()
            if hasattr(c, "__len__") and len(c) and hasattr(c[0], "__len__"):
                c = c[0]
            try:
                return tuple(round(v, 3) for v in to_rgba(c))
            except ValueError:
                continue
    return ()


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


def owner(box, centres, margin):
    """The node a reader attributes label ``box`` to: the node whose centre
    is nearest the box (nearest box point), if it is nearer than every other
    by ``margin``; else None. (A reader model, independent of the renderer's
    rule, which measures rim to rim.)"""
    d = sorted((_gap(c, box), j) for j, c in enumerate(centres))
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
    centres = ax.transData.transform(built.node_xy)

    def add(key, text):
        if text and text.strip():
            out[(key, tokens(text))] += 1

    add(("title",), ax.title.get_text())
    for t in fig.texts:
        add(("caption",), t.get_text())
    for k, leg in enumerate(built.legends):
        add(("legend-title", k), leg.get_title().get_text())
        handles = getattr(leg, "legend_handles", None) or leg.legendHandles
        for t, h in zip(leg.get_texts(), handles):
            add(("legend", k, _swatch(h)), t.get_text())
    for t in built.labels.values():
        box = t.get_window_extent(renderer)
        add(("node", owner(box, centres, margin)), t.get_text())
    return out
