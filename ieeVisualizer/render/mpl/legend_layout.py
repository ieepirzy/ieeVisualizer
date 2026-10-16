"""Where an ``"auto"`` legend goes when matplotlib's ``"best"`` is not good enough.

``loc="best"`` picks the least-bad of nine spots inside the axes, but it always
picks one: with many entries, or data in every corner, the legend still hides
data. For ``legend.position == "auto"`` the legend is moved outside the axes
when it has more than :data:`MAX_INSIDE_ENTRIES` entries, or when, at the spot
``"best"`` chose, it overlaps plotted data (see :func:`covers_data`). Outside
means to the right of the axes, or below them for a wide figure (or when a
right-hand legend would take too much of the width). The space is reserved by
constrained layout, so neither the legend nor the tick labels are clipped.

Everything here is computed from the figure's own geometry: the same spec and
request place the legend in the same spot every time. Explicit positions are
never moved.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np

#: Most entries an ``"auto"`` legend keeps inside the axes.
MAX_INSIDE_ENTRIES = 5
#: A figure at least this much wider than tall gets its outside legend below.
WIDE_ASPECT = 1.6
#: A right-hand legend wider than this share of the figure goes below instead.
MAX_RIGHT_SHARE = 0.4
#: Gap between the axes' decorations (tick labels, axis label) and the legend.
PAD_IN = 0.08


def place_auto_legend(ctx: Any, legend: Any, handles: list[Any], labels: list[str],
                      kw: dict[str, Any]) -> Any:
    """Keep ``legend`` (drawn at ``loc="best"``) or replace it with one outside
    the axes; returns the legend in use."""
    if len(handles) <= MAX_INSIDE_ENTRIES and (ctx.three_d or not covers_data(ctx.ax, legend)):
        return legend
    legend.remove()
    return _outside(ctx, handles, labels, kw)


def covers_data(ax: Any, legend: Any) -> bool:
    """Whether ``legend``, where it is drawn, overlaps anything plotted on
    ``ax``: a line's drawn segments or markers, a bar or span, a filled area
    (or an unfilled area's outline), a scatter/quiver point, a mesh, or an
    annotation's text."""
    with _measuring(ax.figure):  # display coordinates hold only while laid out
        return _covers(ax, legend.get_window_extent())


def _covers(ax: Any, box: Any) -> bool:
    from matplotlib.collections import PolyCollection, QuadMesh

    for line in ax.lines:
        if line.get_visible() and _line_hits(line, box):
            return True
    for patch in ax.patches:
        if patch.get_visible() and patch.get_window_extent().overlaps(box):
            return True
    for coll in ax.collections:
        if not coll.get_visible():
            continue
        if isinstance(coll, PolyCollection):
            filled = len(coll.get_facecolor()) and np.any(coll.get_facecolor()[:, 3] > 0)
            tr = coll.get_transform()
            if any(tr.transform_path(p).intersects_bbox(box, filled=bool(filled))
                   for p in coll.get_paths()):
                return True
        elif isinstance(coll, QuadMesh):
            if coll.get_datalim(ax.transData).transformed(ax.transData).overlaps(box):
                return True
        else:  # scatter, quiver: the points themselves
            pts = coll.get_offset_transform().transform(np.asarray(coll.get_offsets()))
            if _any_inside(box, pts):
                return True
    return any(t.get_visible() and t.get_text() and t.get_window_extent().overlaps(box)
               for t in ax.texts)


def _line_hits(line: Any, box: Any) -> bool:
    xy = line.get_transform().transform_path(line.get_path()).vertices
    finite = np.isfinite(xy).all(axis=1)
    if _any_inside(box, xy[finite]):
        return True
    if line.get_linestyle() in ("None", "none", "", " "):
        return False  # markers only: no segments are drawn between them
    from matplotlib.path import Path

    # Segments only between consecutive finite points: a NaN is a gap.
    breaks = np.flatnonzero(~finite)
    for run in np.split(xy, breaks):
        run = run[np.isfinite(run).all(axis=1)]
        if len(run) > 1 and Path(run).intersects_bbox(box, filled=False):
            return True
    return False


@contextmanager
def _measuring(fig: Any) -> Iterator[None]:
    """Lay ``fig`` out (resolving ``"best"`` and the axes' final place) for
    measuring, then put every axes back where it was. Constrained layout
    starts from the current positions, so leaving the measured ones in place
    would shift the saved figure by float noise (changing e.g. SVG clip-path
    ids) even when the legend stays where it was. ``set_position`` is public
    but also takes the axes out of the layout; ``set_in_layout`` undoes that."""
    saved = [(a, a.get_position(original=True).frozen(), a.get_in_layout()) for a in fig.axes]
    fig.draw_without_rendering()
    try:
        yield
    finally:
        for a, pos, in_layout in saved:
            a.set_position(pos)
            a.set_in_layout(in_layout)


def _any_inside(box: Any, pts: Any) -> bool:
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    return bool(np.any((pts[:, 0] >= box.x0) & (pts[:, 0] <= box.x1)
                       & (pts[:, 1] >= box.y0) & (pts[:, 1] <= box.y1)))


def _outside(ctx: Any, handles: list[Any], labels: list[str], kw: dict[str, Any]) -> Any:
    from matplotlib.transforms import ScaledTranslation

    fig, ax = ctx.fig, ctx.ax
    with _measuring(fig):
        deco = ax.get_tightbbox().frozen()  # axes, tick labels, axis labels; no legend
        frame = ax.get_window_extent().frozen()  # frozen: positions are restored below
    width_in, height_in = fig.get_size_inches()
    if width_in / height_in < WIDE_ASPECT and not ctx.narrow:  # narrow: width is scarce
        gap = max(0.0, deco.x1 - frame.x1) / fig.dpi + PAD_IN
        shift = ScaledTranslation(gap, 0.0, fig.dpi_scale_trans)
        legend = ax.legend(handles, labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
                           bbox_transform=ax.transAxes + shift, **kw)
        if legend.get_window_extent().width / fig.dpi <= MAX_RIGHT_SHARE * width_in:
            return legend
        legend.remove()
    gap = max(0.0, frame.y0 - deco.y0) / fig.dpi + PAD_IN
    shift = ScaledTranslation(0.0, -gap, fig.dpi_scale_trans)
    probe = ax.legend(handles, labels, **kw)  # one column: the widest entry sets the width
    column_in = probe.get_window_extent().width / fig.dpi
    probe.remove()
    ncols = int(max(1, min(len(handles), (0.95 * width_in) // max(column_in, 1e-9))))
    return ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.0),
                     bbox_transform=ax.transAxes + shift, ncols=ncols, **kw)
