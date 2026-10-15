"""Draw a :class:`NetworkSpec` with matplotlib (node-link or adjacency matrix),
export PNG/SVG/PDF, and write its text alternative.

Rendering reuses the FigureSpec adapter's determinism setup
(:func:`ieeVisualizer.render.mpl.adapter.render_context`, fixed metadata,
``Figure`` objects, never ``pyplot``), so the same spec and library versions
give byte-identical SVG/PDF in any process.

Honesty rules: a missing edge weight is drawn thin in a colour the weight
scale never uses, keeping its kind's dash pattern, with its own legend entry
(never at the width or shade of 0); parallel and reciprocal edges bow apart
so none hides another; a missing node size is drawn
hollow at a neutral size; every legend for a measured quantity states its
unit; labels that would overlap are abbreviated or left out with a warning
(the text outline always lists them all).
"""

from __future__ import annotations

import io
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any

from ..render.base import MEDIA_TYPES, RenderRequest, RenderResult, RenderWarning
from ..spec import SpecValidationError
from .layout import LayoutResult, compute_layout
from .model import NetworkSpec
from .validate import validate_network

ADAPTER_NAME = "ieeVisualizer.network"
ADAPTER_VERSION = "1.0.0"
#: Edge line widths (points) for the smallest and largest |weight|.
LW_MIN, LW_MAX = 0.6, 5.0
#: Node marker area (points^2) for the largest size; area is proportional to
#: the value, floored at AREA_FLOOR so a tiny value stays visible.
AREA_MAX, AREA_FLOOR, AREA_DEFAULT = 700.0, 6.0, 140.0
#: Colour of an edge whose weight was not recorded: a hue the unsigned
#: (grey) weight scale never uses; for signed weights (red/blue) a neutral
#: grey, which that scale never uses either.
MISSING_COLOR = "#d95f02"
MISSING_COLOR_SIGNED = "0.45"
#: Bow (fraction of the edge length) between neighbouring edges that join the
#: same two nodes (parallel edges of different kinds, reciprocal edges).
MULTI_BOW = 0.3
UNGROUPED_COLOR = "0.55"
KIND_STYLES = ("-", "--", "-.", (0, (6, 2, 1, 2, 1, 2)))
GROUP_MARKERS = ("o", "s", "^", "D", "v")


@dataclass
class BuiltNetwork:
    """A drawn network before export (inspection hook for tests).

    ``node_xy[i]`` is node ``i``'s data position; ``edge_paths[k]`` the
    polyline drawn for ``spec.edges[k]`` (data coordinates, one segment of
    ``edge_collection``), ``edge_heads[k]`` its arrowhead triangle and
    ``edge_widths[k]`` its width;
    ``labels`` maps node index -> drawn text (possibly abbreviated);
    ``skipped_labels`` lists node indices whose label was left out."""

    figure: Any
    axes: Any
    layout: LayoutResult
    warnings: list[RenderWarning] = field(default_factory=list)
    node_xy: Any = None
    node_artist: Any = None
    node_areas: list[float] = field(default_factory=list)
    edge_paths: dict[int, Any] = field(default_factory=dict)
    edge_heads: dict[int, Any] = field(default_factory=dict)
    edge_collection: Any = None
    edge_widths: dict[int, float] = field(default_factory=dict)
    labels: dict[int, Any] = field(default_factory=dict)
    skipped_labels: list[int] = field(default_factory=list)
    abbreviated_labels: list[int] = field(default_factory=list)
    legends: list = field(default_factory=list)
    matrix: Any = None


def _check(spec: NetworkSpec) -> list[RenderWarning]:
    issues = validate_network(spec)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SpecValidationError(errors)
    return [RenderWarning(i.code, i.message, i.path) for i in issues if i.severity == "warning"]


def _fmt(v: float) -> str:
    return f"{v:.4g}"


def _nice(vmax: float) -> list[float]:
    from matplotlib.ticker import MaxNLocator

    for nbins in (3, 5):  # two to four round reference values within (0, vmax]
        ticks = MaxNLocator(nbins=nbins, steps=[1, 2, 2.5, 5, 10]).tick_values(0.0, vmax)
        out = [float(t) for t in ticks if 0 < t <= vmax * (1 + 1e-9)]
        if len(out) >= 2:
            return out
    return out or [vmax]


def _group_styles(spec: NetworkSpec, warnings: list[RenderWarning]) -> dict[str, tuple]:
    import matplotlib

    palette = matplotlib.rcParams["axes.prop_cycle"].by_key()["color"]
    groups = list(dict.fromkeys(n.group for n in spec.nodes if n.group is not None))
    if len(groups) > len(palette):
        warnings.append(RenderWarning(
            "colour_cycle_wrapped", f"{len(groups)} groups exceed the {len(palette)}-colour "
            "palette; later groups repeat colours with a different marker shape.", "/nodes"))
    return {g: (palette[r % len(palette)], GROUP_MARKERS[(r // len(palette))
                                                        % len(GROUP_MARKERS)])
            for r, g in enumerate(groups)}


def _frame(xy, equal: bool):
    """Scale layout coordinates into [0, 1] (jointly when ``equal``)."""
    import numpy as np

    lo, hi = xy.min(axis=0), xy.max(axis=0)
    span = hi - lo
    if equal:
        span = np.full(2, max(span.max(), 1e-12))
    span = np.where(span < 1e-12, 1.0, span)
    out = (xy - lo) / span
    if equal:
        out += (1 - (hi - lo) / span) / 2
    out[:, (hi - lo) < 1e-12] = 0.5
    return out


def _edge_style(spec: NetworkSpec):
    """Callable weight -> (linewidth, colour) and the |weight| maximum."""
    import matplotlib

    ws = [abs(e.weight) for e in spec.edges if e.weight is not None]
    wmax = max(ws) if ws else 0.0
    signed = spec.edge_weight is not None and spec.edge_weight.signed
    cmap = matplotlib.colormaps["RdBu" if signed else "Greys"]

    def style(w: float) -> tuple[float, Any]:
        frac = abs(w) / wmax if wmax > 0 else 0.0
        lw = LW_MIN + (LW_MAX - LW_MIN) * frac
        if signed:
            return lw, cmap(0.5 + (0.42 * frac if w >= 0 else -0.42 * frac))
        return lw, cmap(0.4 + 0.55 * frac)

    return style, wmax


def edge_geometry(p0, p1, r0: float, r1: float, head: float, bow: float):
    """Polyline (display units) from the rim of the node at ``p0`` (radius
    ``r0``) to the rim at ``p1``, straight or bowed by ``bow`` x length, plus
    an arrowhead triangle of length ``head`` whose tip touches the target rim
    (``None`` when ``head`` is 0). A self-loop (p0 == p1) is a small circle
    sitting on top of the node, ``1 + |bow|`` times larger."""
    import numpy as np

    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    length = float(np.hypot(*d))
    if length < 1e-9:  # bow > 0: a further loop on the same node, drawn larger
        rr = max(r0 * 0.8, 3.0) * (1.0 + abs(bow))
        t = np.linspace(0, 2 * np.pi, 25)
        return np.c_[p0[0] + rr * np.sin(t), p0[1] + r0 + rr - rr * np.cos(t)], None
    u = d / length
    if bow:
        ctrl = (p0 + p1) / 2 + np.array([-u[1], u[0]]) * bow * length
        t = np.linspace(0, 1, 65)[:, None]
        pts = (1 - t) ** 2 * p0 + 2 * (1 - t) * t * ctrl + t ** 2 * p1
        d0, d1 = np.hypot(*(pts - p0).T) - (r0 + 1), np.hypot(*(pts - p1).T) - (r1 + 1)
        keep = np.nonzero((d0 >= 0) & (d1 >= 0))[0]
        if len(keep) >= 2:  # cut exactly at both rims
            i, j = keep[0], keep[-1]
            a = pts[i - 1] + (pts[i] - pts[i - 1]) * (-d0[i - 1] / (d0[i] - d0[i - 1]))
            b = pts[j + 1] + (pts[j] - pts[j + 1]) * (-d1[j + 1] / (d1[j] - d1[j + 1]))
            pts = np.vstack([a, pts[i:j + 1], b])
        else:
            pts = np.array([p0 + u * (r0 + 1), p1 - u * (r1 + 1)])
    else:
        pts = np.array([p0 + u * (r0 + 1), p1 - u * (r1 + 1)])
    if not head:
        return pts, None
    tip = pts[-1].copy()
    v = tip - pts[-2]
    v = v / max(float(np.hypot(*v)), 1e-9)
    base = tip - v * head
    n = np.array([-v[1], v[0]]) * head * 0.42
    pts[-1] = tip - v * head * 0.8  # the line ends inside the head, not at its tip
    return pts, np.array([tip, base + n, base - n])


def build_network(spec: NetworkSpec, request: RenderRequest | None = None) -> BuiltNetwork:
    """Validate, lay out and draw ``spec`` into a new Figure (no export)."""
    from ..render.mpl.adapter import render_context
    from ..render.mpl.style import resolve_theme

    request = request or RenderRequest()
    warnings = _check(spec)
    theme_rc = resolve_theme(request.theme or spec.theme, "/theme", warnings)
    lay = compute_layout(spec)
    with render_context(theme_rc), _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        built = _draw(spec, lay, request, warnings)
    return built


def _new_figure(request: RenderRequest):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from ..render.mpl.adapter import _figsize

    fig = Figure(figsize=_figsize(request), dpi=request.dpi)
    FigureCanvasAgg(fig)
    return fig


def _draw(spec: NetworkSpec, lay: LayoutResult, request: RenderRequest,
          warnings: list[RenderWarning]) -> BuiltNetwork:
    fig = _new_figure(request)
    if spec.layout.algorithm == "matrix":
        from .matrix import draw_matrix

        return draw_matrix(spec, lay, fig, warnings)
    from matplotlib.collections import LineCollection, PolyCollection
    from matplotlib.lines import Line2D

    ax = fig.add_axes([0.02, 0.07, 0.68, 0.84])
    ax.set_axis_off()
    equal = spec.layout.algorithm in ("force", "circular")
    xy = _frame(lay.xy, equal)
    ax.set_xlim(-0.08, 1.08)
    ax.set_ylim(-0.1, 1.08)
    if equal:
        ax.set_aspect("equal", adjustable="box", anchor="W")
    built = BuiltNetwork(fig, ax, lay, warnings, node_xy=xy)
    styles = _group_styles(spec, warnings)

    # ---- nodes
    sizes = [n.size for n in spec.nodes if n.size is not None]
    smax = max(sizes) if sizes else 0.0
    areas, faces, edges_c, markers = [], [], [], []
    for n in spec.nodes:
        color, marker = styles.get(n.group, (UNGROUPED_COLOR, "o"))
        if spec.node_size is None:
            area = AREA_DEFAULT
        elif n.size is None:
            area, color = AREA_DEFAULT * 0.6, "none"
        else:
            area = max(AREA_MAX * n.size / smax, AREA_FLOOR) if smax > 0 else AREA_FLOOR
        areas.append(area)
        faces.append(color)
        edges_c.append("0.25" if color == "none" else "white")
        markers.append(marker)
    built.node_areas = areas
    for m in dict.fromkeys(markers):
        sel = [i for i, mk in enumerate(markers) if mk == m]
        art = ax.scatter(xy[sel, 0], xy[sel, 1], s=[areas[i] for i in sel],
                         c=[faces[i] for i in sel], edgecolors=[edges_c[i] for i in sel],
                         linewidths=0.8, marker=m, zorder=3)
        built.node_artist = art if built.node_artist is None else built.node_artist
    radius_pt = [(a ** 0.5) / 2 for a in areas]

    # ---- edges
    style, wmax = _edge_style(spec)
    kinds = list(dict.fromkeys(e.kind for e in spec.edges if e.kind is not None))
    kind_ls = {k: KIND_STYLES[r % len(KIND_STYLES)] for r, k in enumerate(kinds)}
    idx = {n.id: i for i, n in enumerate(spec.nodes)}
    n_missing = 0
    signed = spec.edge_weight is not None and spec.edge_weight.signed
    missing_color = MISSING_COLOR_SIGNED if signed else MISSING_COLOR
    bows = _multi_bows(spec, idx)
    ax.apply_aspect()
    to_px = ax.transData.transform
    from_px = ax.transData.inverted().transform
    disp = to_px(xy)
    px = fig.dpi / 72.0
    segs, seg_lw, seg_c, seg_ls, heads, head_c = [], [], [], [], [], []
    for k, e in enumerate(spec.edges):
        a, b = idx[e.source], idx[e.target]
        ls = kind_ls.get(e.kind, "-")
        if spec.edge_weight is None:
            lw, color = 1.0, "0.45"
        elif e.weight is None:  # keeps the kind's dash pattern
            lw, color = LW_MIN + 0.4, missing_color
            n_missing += 1
        else:
            lw, color = style(e.weight)
        # an edge spanning several layers bows so it does not run through the
        # nodes of the layers in between
        span = abs(lay.layers[a] - lay.layers[b]) if lay.layers else 1
        pts, head = edge_geometry(disp[a], disp[b], radius_pt[a] * px, radius_pt[b] * px,
                                  (4.0 + 1.3 * lw) * px if spec.is_directed(e) else 0.0,
                                  (0.25 if span > 1 and a != b else 0.0) + bows.get(k, 0.0))
        built.edge_paths[k] = from_px(pts)
        built.edge_widths[k] = lw
        segs.append(built.edge_paths[k])
        seg_lw.append(lw)
        seg_c.append(color)
        seg_ls.append(ls)
        if head is not None:
            built.edge_heads[k] = from_px(head)
            heads.append(built.edge_heads[k])
            head_c.append(color)
    built.edge_collection = ax.add_collection(LineCollection(
        segs, linewidths=seg_lw, colors=seg_c, linestyles=seg_ls, zorder=2,
        capstyle="butt"), autolim=False)
    if heads:
        ax.add_collection(PolyCollection(heads, facecolors=head_c, edgecolors="none",
                                         zorder=2), autolim=False)

    # ---- legend: one stacked section per encoding, in figure coordinates
    sections: list[tuple[str, list[tuple[Any, str]], float]] = []
    if styles:
        rows = [(Line2D([], [], ls="none", marker=m, mfc=c, mec="white", ms=8), g)
                for g, (c, m) in styles.items()]
        if any(n.group is None for n in spec.nodes):
            rows.append((Line2D([], [], ls="none", marker="o", mfc=UNGROUPED_COLOR,
                                mec="white", ms=8), "no group"))
        sections.append(("Group", rows, 0.5))
    if spec.node_size is not None:
        rows, dmax = [], 8.0
        for v in (_nice(smax) if smax > 0 else []):
            d = max(AREA_MAX * v / smax, AREA_FLOOR) ** 0.5
            dmax = max(dmax, d)
            rows.append((Line2D([], [], ls="none", marker="o", mfc="0.6", mec="white", ms=d),
                         f"{_fmt(v)} {spec.node_size.unit}"))
        if any(n.size is None for n in spec.nodes):
            rows.append((Line2D([], [], ls="none", marker="o", mfc="none", mec="0.25",
                                ms=(AREA_DEFAULT * 0.6) ** 0.5), "not recorded"))
        sections.append((f"{spec.node_size.title} ({spec.node_size.unit})", rows,
                         max(0.5, (dmax - 4.5) / 7.5)))
    if spec.edge_weight is not None:
        q = spec.edge_weight
        rows = []
        for v in (_nice(wmax) if wmax > 0 else []):
            lw, c = style(v)
            rows.append((Line2D([], [], lw=lw, color=c), f"{_fmt(v)} {q.unit}"))
        if q.signed:
            rows.append((Line2D([], [], lw=2.5, color=style(wmax or 1)[1]), "positive"))
            rows.append((Line2D([], [], lw=2.5, color=style(-(wmax or 1))[1]), "negative"))
        if n_missing:
            rows.append((Line2D([], [], lw=LW_MIN + 0.4, color=missing_color),
                         f"not recorded ({n_missing})"))
        sections.append((f"{q.title} ({q.unit})" + (", |value|" if q.signed else ""), rows,
                         0.5))
    if kinds:
        sections.append(("Edge kind", [(Line2D([], [], lw=1.4, ls=kind_ls[kd], color="0.3"),
                                        kd) for kd in kinds], 0.5))
    top = 0.93
    renderer = fig.canvas.get_renderer()
    for title, rows, spacing in sections:
        leg = fig.legend([h for h, _ in rows], [t for _, t in rows], loc="upper left",
                         bbox_to_anchor=(0.72, top), frameon=False, fontsize=7.5,
                         handlelength=2.6, borderaxespad=0.0, labelspacing=spacing,
                         title=title, title_fontproperties={"weight": "bold", "size": 7.5},
                         alignment="left")
        built.legends.append(leg)
        top = fig.transFigure.inverted().transform(
            leg.get_window_extent(renderer))[0, 1] - 0.025
    if spec.title:
        ax.set_title(spec.title, fontsize=11, loc="left")
    if spec.caption:
        fig.text(0.02, 0.012, spec.caption, fontsize=7.5, color="0.3", wrap=True)
    _place_labels(spec, built, radius_pt)
    return built


def _multi_bows(spec: NetworkSpec, idx: dict[str, int]) -> dict[int, float]:
    """Extra bow per edge index so edges joining the same two nodes (parallel
    edges of different kinds or directions, reciprocal edges) do not draw on
    top of each other: the m edges of a pair take slots spaced MULTI_BOW
    apart, symmetric about the straight line, measured in one orientation of
    the pair so a -> b and b -> a land on opposite sides. The j-th further
    loop on a node gets bow j (a larger ring)."""
    pairs: dict[tuple[int, int], list[int]] = {}
    for k, e in enumerate(spec.edges):
        a, b = idx[e.source], idx[e.target]
        pairs.setdefault((min(a, b), max(a, b)), []).append(k)
    out: dict[int, float] = {}
    for (lo, hi), ks in pairs.items():
        if len(ks) < 2:
            continue
        for r, k in enumerate(ks):
            if lo == hi:
                out[k] = float(r)
            else:
                o = MULTI_BOW * (r - (len(ks) - 1) / 2)
                out[k] = o if idx[spec.edges[k].source] == lo else -o
    return out


def _place_labels(spec: NetworkSpec, built: BuiltNetwork, radius_pt: list[float]) -> None:
    """Greedy collision avoidance in display space. Nodes in priority order
    (degree, then size, then declaration order) try their full label, then
    abbreviations of 12 and 6 characters; a label that would still overlap a
    placed label or another node is left out. Both outcomes are warned."""
    from matplotlib.transforms import Bbox

    ax, fig = built.axes, built.figure
    ax.apply_aspect()
    renderer = fig.canvas.get_renderer()
    pts = ax.transData.transform(built.node_xy)
    px = fig.dpi / 72.0
    obstacles = [Bbox.from_extents(x - r * px, y - r * px, x + r * px, y + r * px)
                 for (x, y), r in zip(pts, radius_pt)]
    deg = [0] * len(spec.nodes)
    idx = {n.id: i for i, n in enumerate(spec.nodes)}
    for e in spec.edges:
        deg[idx[e.source]] += 1
        deg[idx[e.target]] += 1
    prio = sorted(range(len(spec.nodes)),
                  key=lambda i: (-deg[i], -(spec.nodes[i].size or 0.0), i))
    placed: list[Any] = []
    fontsize = 7.5 if len(spec.nodes) <= 60 else 6.5
    for i in prio:
        full = spec.nodes[i].display_label
        cands = [full] + [full[:c].rstrip() + "…" for c in (12, 6) if len(full) > c + 1]
        below = ((0.0, -(radius_pt[i] + 2)), "center", "top", False)
        spots = [below]
        if spec.layout.algorithm == "layered" and spec.layout.direction == "TB":
            # beside the node first (below it is where the edges to its children
            # start), if that stays inside the axes; else below as elsewhere
            spots = [((radius_pt[i] + 3, 0.0), "left", "center", True), below]
        elif spec.layout.algorithm == "circular":  # radially outwards
            dx, dy = built.node_xy[i] - 0.5
            norm = max((dx * dx + dy * dy) ** 0.5, 1e-9)
            spots = [((dx / norm * (radius_pt[i] + 3), dy / norm * (radius_pt[i] + 3)),
                      "left" if dx > 0.05 else "right" if dx < -0.05 else "center",
                      "bottom" if dy > 0.05 else "top" if dy < -0.05 else "center", False)]
        for text, (off, ha, va, inside) in ((c, sp) for c in cands for sp in spots):
            t = ax.annotate(text, xy=tuple(built.node_xy[i]), xytext=off,
                            textcoords="offset points", ha=ha, va=va,
                            fontsize=fontsize, color="0.15", zorder=4,
                            bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none",
                                  "alpha": 0.8})
            bb = t.get_window_extent(renderer)
            clash = any(bb.overlaps(o) for o in placed) or any(
                bb.overlaps(o) for j, o in enumerate(obstacles) if j != i) or (
                inside and bb.x1 > ax.bbox.x1)
            if not clash:
                placed.append(bb)
                built.labels[i] = t
                if text != full:
                    built.abbreviated_labels.append(i)
                break
            t.remove()
        else:
            built.skipped_labels.append(i)
    for code, which, verb in (("labels_abbreviated", built.abbreviated_labels, "abbreviated"),
                              ("labels_skipped", built.skipped_labels, "left out")):
        if which:
            names = ", ".join(repr(spec.nodes[i].id) for i in sorted(which)[:8])
            built.warnings.append(RenderWarning(
                code, f"{len(which)} node label(s) were {verb} because they would overlap "
                f"other labels or nodes ({names}{', ...' if len(which) > 8 else ''}); the "
                "text outline lists every label in full.", "/nodes"))


def render_network(spec: NetworkSpec, request: RenderRequest | None = None) -> RenderResult:
    """Render to ``request.format`` (png, svg, pdf). The SVG carries the text
    outline as its description (``dc:description``)."""
    from ..render.mpl.adapter import _metadata, render_context
    from ..render.mpl.style import resolve_theme

    request = request or RenderRequest()
    if request.format not in ("png", "svg", "pdf"):
        raise ValueError(f"network rendering supports png, svg and pdf, not {request.format!r}.")
    warnings = _check(spec)
    theme_rc = resolve_theme(request.theme or spec.theme, "/theme", warnings)
    lay = compute_layout(spec)
    buf = io.BytesIO()
    meta = _metadata(request.format)
    if request.format == "svg":
        meta.update({"Title": spec.title or "Network",
                     "Description": network_outline(spec, lay)})
    with render_context(theme_rc), _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        built = _draw(spec, lay, request, warnings)
        built.figure.savefig(buf, format=request.format, dpi=request.dpi, metadata=meta)
    return RenderResult(content=buf.getvalue(), format=request.format,
                        media_type=MEDIA_TYPES[request.format], adapter_name=ADAPTER_NAME,
                        adapter_version=ADAPTER_VERSION, warnings=built.warnings,
                        request=request)


def network_outline(spec: NetworkSpec, lay: LayoutResult | None = None) -> str:
    """Accessible text alternative: summary, every node in reading order with
    group, size and degree, then every edge with its kind and weight."""
    lay = lay or compute_layout(spec)
    idx = {n.id: i for i, n in enumerate(spec.nodes)}
    n_in, n_out, n_und = ([0] * len(spec.nodes) for _ in range(3))
    for e in spec.edges:
        if spec.is_directed(e):
            n_out[idx[e.source]] += 1
            n_in[idx[e.target]] += 1
        else:
            n_und[idx[e.source]] += 1
            n_und[idx[e.target]] += 1
    nd = sum(spec.is_directed(e) for e in spec.edges)
    kind = ("directed" if nd == len(spec.edges) else "undirected" if nd == 0 else "mixed")
    lines = [f"{spec.title or 'Network'}: {len(spec.nodes)} nodes, {len(spec.edges)} "
             f"{kind} edges."]
    if spec.edge_weight is not None:
        q = spec.edge_weight
        miss = sum(e.weight is None for e in spec.edges)
        lines.append(f"Edge weight: {q.title} in {q.unit}" + (
            f"; {miss} edge(s) have no recorded value." if miss else "."))
    lines.append("Nodes:")
    for r, i in enumerate(lay.order, 1):
        n = spec.nodes[i]
        facts = [f"group {n.group}"] if n.group else []
        if spec.node_size is not None:
            facts.append(f"{spec.node_size.title} " + (
                f"{_fmt(n.size)} {spec.node_size.unit}" if n.size is not None
                else "not recorded"))
        if n_in[i] or n_out[i]:
            facts.append(f"in-degree {n_in[i]}, out-degree {n_out[i]}")
        if n_und[i] or not (n_in[i] or n_out[i]):
            facts.append(f"degree {n_und[i]}")
        lines.append(f"{r}. {n.display_label} ({'; '.join(facts)})")
    lines.append("Edges:")
    for e in spec.edges:
        a, b = spec.nodes[idx[e.source]].display_label, spec.nodes[idx[e.target]].display_label
        s = f"{a} {'->' if spec.is_directed(e) else '--'} {b}"
        extra = [e.kind] if e.kind else []
        if e.label:
            extra.append(e.label)
        if spec.edge_weight is not None:
            extra.append(f"{spec.edge_weight.title} " + (
                f"{_fmt(e.weight)} {spec.edge_weight.unit}" if e.weight is not None
                else "not recorded"))
        lines.append(f"- {s}" + (f" ({'; '.join(extra)})" if extra else ""))
    return "\n".join(lines)
