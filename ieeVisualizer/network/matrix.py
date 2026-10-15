"""Adjacency-matrix view of a :class:`NetworkSpec`.

Rows are sources and columns targets (an undirected edge fills both cells).
A cell with no edge is blank; an edge whose weight was not recorded is a
hatched grey cell with its own legend entry, never the colour of 0. The
colour bar is labelled with the weight's title and unit.
"""

from __future__ import annotations

from typing import Any

from ..render.base import RenderWarning
from .layout import LayoutResult
from .model import NetworkSpec


def adjacency(spec: NetworkSpec, order: list[int]):
    """``(weights, missing, present)`` matrices in ``order``: ``weights`` is NaN
    where there is no edge or no recorded weight; ``missing`` marks edges
    without a recorded weight; ``present`` marks every edge."""
    import numpy as np

    n = len(spec.nodes)
    pos = {spec.nodes[i].id: r for r, i in enumerate(order)}
    w = np.full((n, n), np.nan)
    missing = np.zeros((n, n), dtype=bool)
    present = np.zeros((n, n), dtype=bool)
    for e in spec.edges:
        a, b = pos[e.source], pos[e.target]
        cells = [(a, b)] if spec.is_directed(e) else [(a, b), (b, a)]
        for r, c in cells:
            present[r, c] = True
            if e.weight is None:
                missing[r, c] = True
            else:
                w[r, c] = e.weight
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
        img = ax.imshow(np.ma.masked_where(~present, present.astype(float)),
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
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.0, -0.03),
              ncol=3, frameon=False, fontsize=7)
    groups = [spec.nodes[i].group for i in order]
    if spec.layout.order == "group":
        for r in range(1, n):
            if groups[r] != groups[r - 1]:
                ax.axhline(r - 0.5, color="0.3", lw=0.8)
                ax.axvline(r - 0.5, color="0.3", lw=0.8)
    ax.tick_params(length=0)
    if n <= MATRIX_MAX:
        labels = [spec.nodes[i].display_label for i in order]
        labels = [s if len(s) <= 18 else s[:17] + "…" for s in labels]
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
