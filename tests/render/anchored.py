"""Every text of a figure or network with what it belongs to (its anchor),
measured from the drawing, for comparing a desktop render with a profile
render: the same words, in the same order, belonging to the same thing.

* figure: title, axis labels, caption, legend title and entries (by entry),
  category tick labels (by axis and position), value labels (by layer and
  bar), "no data" notes, and text annotations - by their data anchor, or for
  a template's row notes (``metadata["template"]["row_notes"]``, whose x is
  only a layout position) by their row;
* network: title, caption, legend sections (by section and entry), and node
  labels by the node a reader attributes them to: the node whose centre the
  label box is nearest, by more than ``MARGIN_PX`` over every other node's
  rim, or ``None`` when that is not clear.

Wrapping only changes line breaks, so texts compare as token sequences.
"""

from __future__ import annotations

from collections import Counter

#: A label belongs to a node only if it is this much nearer (device px of a
#: 1920 px desktop image; scaled by dpi) than to any other node.
MARGIN_PX = 6.0


def tokens(text: str) -> tuple:
    return tuple(text.split())


def anchored(spec, request) -> Counter:
    from ieeVisualizer.network import NetworkSpec

    if isinstance(spec, NetworkSpec):
        return _network(spec, request)
    return _figure(spec, request)


def _figure(spec, request) -> Counter:
    from matplotlib.text import Annotation

    from ieeVisualizer.render import build_figure

    built = build_figure(spec, request)
    ax, ctx, out = built.axes, built.context, Counter()

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
        for k, t in enumerate(legend.get_texts()):
            add(("legend", k), t.get_text())
    for key, axis in (("x", ax.xaxis), ("y", ax.yaxis)):
        info = ctx.axes.get(key)
        if info is None or info.kind != "category":
            continue
        lo, hi = sorted(axis.get_view_interval())
        for tick in axis.get_major_ticks():
            if lo - 1e-9 <= tick.get_loc() <= hi + 1e-9 and tick.label1.get_visible():
                add(("tick", key, round(float(tick.get_loc()), 6)), tick.label1.get_text())
    for lab in ctx.value_labels:
        if lab.text.get_visible():
            add(("value", lab.layer, round(lab.centre, 6)), lab.text.get_text())
    for note in ctx.no_data_notes:
        if note.text.get_visible():
            add(("nodata", note.layer, round(note.slot, 6)), note.text.get_text())
    meta = spec.metadata.get("template") if isinstance(spec.metadata, dict) else None
    rows = set((meta or {}).get("row_notes") or ())
    for a in built.annotation_artists:
        if not (isinstance(a, Annotation) and a.get_visible()):
            continue
        i = getattr(a, "_ieev_index", None)
        y = float(ax.yaxis.convert_units(a.xy[1]))
        if i in rows:
            add(("row-note", i, round(y, 6)), a.get_text())
        else:
            x = float(ax.xaxis.convert_units(a.xy[0]))
            add(("annotation", i, round(x, 6), round(y, 6)), a.get_text())
    return out


def owner(i_hint, box, centres, radii_px, margin):
    """The node a reader attributes ``box`` to, or None if not clear."""
    def dist(c):
        dx = max(box.x0 - c[0], 0.0, c[0] - box.x1)
        dy = max(box.y0 - c[1], 0.0, c[1] - box.y1)
        return (dx * dx + dy * dy) ** 0.5

    d = sorted((dist(c) - (0.0 if j == i_hint else radii_px[j]), j)
               for j, c in enumerate(centres))
    if len(d) > 1 and d[1][0] <= d[0][0] + margin:
        return None
    return d[0][1]


def _network(spec, request) -> Counter:
    from ieeVisualizer.network.render import build_network

    built = build_network(spec, request)
    fig, ax, out = built.figure, built.axes, Counter()
    renderer = fig.canvas.get_renderer()
    margin = MARGIN_PX * fig.dpi / 192.0
    centres = ax.transData.transform(built.node_xy)
    radii = [(a ** 0.5) / 2 * fig.dpi / 72.0 for a in built.node_areas]

    def add(key, text):
        if text and text.strip():
            out[(key, tokens(text))] += 1

    add(("title",), ax.title.get_text())
    for t in fig.texts:
        add(("caption",), t.get_text())
    for k, leg in enumerate(built.legends):
        add(("legend-title", k), leg.get_title().get_text())
        for n, t in enumerate(leg.get_texts()):
            add(("legend", k, n), t.get_text())
    for i, t in built.labels.items():
        box = t.get_window_extent(renderer)
        add(("node", owner(i, box, centres, radii, margin)), t.get_text())
    return out
