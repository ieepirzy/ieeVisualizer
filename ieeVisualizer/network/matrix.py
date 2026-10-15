"""Adjacency-matrix view of a :class:`NetworkSpec`.

Rows are sources and columns targets (an undirected edge fills both cells).
A cell with no edge is blank; an edge whose weight was not recorded is a
hatched grey cell with its own legend entry, never the colour of 0. A cell
that several edges share (parallel edges of different kinds, a directed and
an undirected edge between the same nodes) shows no single weight: it is
cross-hatched with the number of edges, has its own legend entry and an
``edges_combined_in_cell`` warning (the outline lists each edge). The
colour bar is labelled with the weight's title and unit.
"""

from __future__ import annotations

from typing import Any

from ..render.base import RenderWarning
from .layout import LayoutResult
from .model import NetworkSpec


def _cells(spec: NetworkSpec, order: list[int]):
    pos = {spec.nodes[i].id: r for r, i in enumerate(order)}
    for k, e in enumerate(spec.edges):
        a, b = pos[e.source], pos[e.target]
        cells = [(a, b)] if spec.is_directed(e) or a == b else [(a, b), (b, a)]
        for r, c in cells:
            yield k, e, r, c


def cell_counts(spec: NetworkSpec, order: list[int]):
    """Number of edges drawn in each cell, in ``order`` (int matrix)."""
    import numpy as np

    n = len(spec.nodes)
    counts = np.zeros((n, n), dtype=int)
    for _, _, r, c in _cells(spec, order):
        counts[r, c] += 1
    return counts


def adjacency(spec: NetworkSpec, order: list[int]):
    """``(weights, missing, present)`` matrices in ``order``: ``weights`` is NaN
    where there is no edge, no recorded weight, or several edges share the
    cell (see :func:`cell_counts`; no one of their weights stands for the
    cell); ``missing`` marks edges without a recorded weight; ``present``
    marks every edge."""
    import numpy as np

    n = len(spec.nodes)
    w = np.full((n, n), np.nan)
    missing = np.zeros((n, n), dtype=bool)
    present = np.zeros((n, n), dtype=bool)
    for _, e, r, c in _cells(spec, order):
        present[r, c] = True
        if e.weight is None:
            missing[r, c] = True
        else:
            w[r, c] = e.weight
    w[cell_counts(spec, order) > 1] = np.nan
    return w, missing, present


def draw_matrix(spec: NetworkSpec, lay: LayoutResult, fig: Any,
                warnings: list[RenderWarning]):
    import matplotlib
    import numpy as np
    from matplotlib.colors import ListedColormap, Normalize, TwoSlopeNorm
    from matplotlib.patches import Patch, Rectangle

    from .render import BuiltNetwork, _group_styles

    order = lay.order
    n = len(order)
    ax = fig.add_axes([0.2, 0.1, 0.6, 0.64])
    built = BuiltNetwork(fig, ax, lay, warnings)
    w, missing, present = adjacency(spec, order)
    counts = cell_counts(spec, order)
    combined = counts > 1
    missing = missing & ~combined
    built.matrix = w
    weighted = spec.edge_weight is not None
    if weighted:
        q = spec.edge_weight
        finite = w[np.isfinite(w)]
        hi = float(np.abs(finite).max()) if finite.size else 1.0
        hi = hi if hi > 0 else 1.0
        if q.signed:
            cmap, norm = matplotlib.colormaps["RdBu"], TwoSlopeNorm(0.0, -hi, hi)
        else:
            cmap, norm = matplotlib.colormaps["viridis"], Normalize(0.0, hi)
        img = ax.imshow(np.ma.masked_invalid(w), cmap=cmap, norm=norm, interpolation="nearest",
                        aspect="equal")
        cb = fig.colorbar(img, ax=ax, fraction=0.046, pad=0.03)
        cb.set_label(f"{q.title} ({q.unit})", fontsize=8)
        cb.ax.tick_params(labelsize=7)
    else:
        img = ax.imshow(np.ma.masked_where(~present | combined, present.astype(float)),
                        cmap=ListedColormap(["0.2"]), interpolation="nearest", aspect="equal")
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(n - 0.5, -0.5)
    handles = [Patch(facecolor="white", edgecolor="0.7", label="no edge")]
    if not weighted:
        handles.append(Patch(facecolor="0.2", label="edge"))
    if missing.any():
        for r, c in zip(*np.nonzero(missing)):
            ax.add_patch(Rectangle((c - 0.5, r - 0.5), 1, 1, facecolor=(0.85, 0.85, 0.85),
                                   edgecolor="0.45", hatch="////", lw=0.0))
        handles.append(Patch(facecolor=(0.85, 0.85, 0.85), edgecolor="0.45", hatch="////",
                             label=f"edge, weight not recorded ({int(missing.sum())} cells)"))
    if combined.any():
        cells = list(zip(*np.nonzero(combined)))
        for r, c in cells:
            ax.add_patch(Rectangle((c - 0.5, r - 0.5), 1, 1, facecolor="white",
                                   edgecolor="0.2", hatch="xxx", lw=0.0))
            if n <= 30:
                ax.text(c, r, str(counts[r, c]), ha="center", va="center",
                        fontsize=9 if n <= 12 else 6.5, fontweight="bold", color="0.05",
                        bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "none"})
        handles.append(Patch(facecolor="white", edgecolor="0.2", hatch="xxx",
                             label=f"several edges, count shown ({len(cells)} cell"
                             f"{'' if len(cells) == 1 else 's'})"))
        ids = [spec.nodes[i].id for i in order]
        names = ", ".join(f"{ids[r]!r} -> {ids[c]!r} ({counts[r, c]} edges)"
                          for r, c in cells[:8])
        warnings.append(RenderWarning(
            "edges_combined_in_cell",
            f"{len(cells)} matrix cell(s) hold more than one edge (parallel edges of "
            f"different kinds or directions: {names}{', ...' if len(cells) > 8 else ''}); "
            "they are cross-hatched with the edge count and show no single weight. The text "
            "outline lists every edge.", "/edges"))
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.0, -0.03),
              ncol=3 if len(handles) < 3 else 2, frameon=False, fontsize=7)
    groups = [spec.nodes[i].group for i in order]
    if spec.layout.order == "group":
        for r in range(1, n):
            if groups[r] != groups[r - 1]:
                ax.axhline(r - 0.5, color="0.3", lw=0.8)
                ax.axvline(r - 0.5, color="0.3", lw=0.8)
    ax.tick_params(length=0)
    if n <= MATRIX_MAX:
        full = [spec.nodes[i].display_label for i in order]
        labels = [s if len(s) <= 18 else s[:17] + "…" for s in full]
        cut = sorted(i for i, s in zip(order, full) if len(s) > 18)
        if cut:
            names = ", ".join(repr(spec.nodes[i].id) for i in cut[:8])
            warnings.append(RenderWarning(
                "labels_abbreviated", f"{len(cut)} row/column label(s) were abbreviated to 18 "
                f"characters ({names}{', ...' if len(cut) > 8 else ''}); the text outline "
                "lists every label in full.", "/nodes"))
        fs = 7 if n <= 30 else 5.5
        ax.set_xticks(range(n), labels, rotation=90, fontsize=fs)
        ax.set_yticks(range(n), labels, fontsize=fs)
        styles = _group_styles(spec, warnings)
        for tick_labels in (ax.get_xticklabels(), ax.get_yticklabels()):
            for t, g in zip(tick_labels, groups):
                if g in styles:
                    t.set_color(styles[g][0])
        ax.xaxis.set_ticks_position("top")
        ax.xaxis.set_label_position("top")
    else:
        ax.set_xticks([])
        ax.set_yticks([])
        warnings.append(RenderWarning(
            "matrix_labels_omitted", f"{n} nodes are too many to label rows and columns "
            f"(limit {MATRIX_MAX}); the text outline lists the order.", "/nodes"))
    directed = any(spec.is_directed(e) for e in spec.edges)
    how = {"group": "ordered by group", "seriation": "seriated (reverse Cuthill-McKee)",
           "input": "in input order"}[spec.layout.order]
    fig.text(0.02, 0.985, (spec.title + " - " if spec.title else "") + f"adjacency, {how}"
             + ("\nrows: source, columns: target" if directed else ""),
             fontsize=10, va="top")
    if spec.caption:
        fig.text(0.02, 0.012, spec.caption, fontsize=7.5, color="0.3")
    built.node_artist = img
    return built


MATRIX_MAX = 60
