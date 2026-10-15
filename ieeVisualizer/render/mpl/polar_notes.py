""""no data" notes on polar bars, placed once the layout is known.

On a cartesian bar chart a "no data" note starts at the zero line of its
slot (:mod:`.value_labels`). In ``polar`` the zero line is the centre, where
every slot meets: notes anchored there would be drawn on top of each other.
So each note is drawn along its slot's angle instead, reading outwards from
just beside the centre, inside the empty wedge its missing bar would fill
(turned by 180 degrees on the left half, so it never reads upside down). It
starts far enough from the centre that the text band stays inside that
wedge, so notes of neighbouring slots never touch each other or the bars
beside them.

A note that cannot be placed that way -- its wedge is too narrow for the
text to clear the centre and still end inside the plot circle, or it would
cover a radial tick label -- is not drawn; a ``no_data_unplaceable`` warning
lists those slots per layer, so a missing bar is still never presented as a
0 without saying so. Horizontal bars in polar have no such wedge; their
notes are always listed that way.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: Least distance of a note from the centre, in points.
START_PT = 4.0
#: Space kept between a note and the edges of its wedge, in points.
WEDGE_GAP_PT = 1.0
#: Space kept between a note and the plot circle, in points.
EDGE_PAD_PT = 2.0


def place_polar_notes(ctx: Any) -> list:
    """Place every note of ``ctx.no_data_notes`` (see the module docstring);
    returns the ``no_data_unplaceable`` warnings for the notes left out."""
    from ..base import RenderWarning

    ax, px = ctx.ax, ctx.fig.dpi / 72.0
    ax.apply_aspect()  # as drawing will: the plot is a circle, not the layout's box
    rmin, rmax = ax.get_ylim()
    centre = np.asarray(ax.transData.transform((0.0, rmin)), dtype=float)
    ticks = [t.get_window_extent() for t in ax.get_yticklabels()
             if t.get_visible() and t.get_text()]
    unplaced: dict[int, list[str]] = {}
    for note in ctx.no_data_notes:
        if not (note.ch == "y" and _place(ax, note, centre, rmin, rmax, px, ticks)):
            note.text.set_visible(False)
            unplaced.setdefault(note.layer, []).append(note.where)
    return [RenderWarning(
        "no_data_unplaceable",
        f"layer {i} has no value at {', '.join(where)}; in polar the \"no data\" note for "
        f"{'that slot' if len(where) == 1 else 'those slots'} does not fit beside the centre, "
        "so the slot is empty without a note. It is missing data, not 0. Use a larger "
        "figure or cartesian bars to show it.", f"/layers/{i}")
        for i, where in sorted(unplaced.items())]


def _place(ax: Any, note: Any, centre: np.ndarray, rmin: float, rmax: float, px: float,
           ticks: list) -> bool:
    """Point ``note`` along its slot's angle; returns whether it fits."""
    from matplotlib.path import Path

    a = note.text
    outer = np.asarray(ax.transData.transform((note.slot, rmax)), dtype=float)
    radius = float(np.hypot(*(outer - centre)))
    if radius <= 0:
        return False
    u = (outer - centre) / radius
    phi = float(np.degrees(np.arctan2(u[1], u[0]))) % 360.0
    flip = 90.0 < phi < 270.0
    a.set_visible(True)
    a.set_rotation(0)
    ext = a.get_window_extent()  # the text's own length and height, unturned
    length, height = ext.width, ext.height
    half = note.width / 2.0  # the empty wedge, either side of the slot's angle
    need = (height / 2.0 + WEDGE_GAP_PT * px) / np.sin(half) if 0 < half < np.pi / 2 else 0.0
    d = max(START_PT * px, float(need))
    a.xycoords, a.xy = "data", (note.slot, rmin)
    a.set_rotation_mode("anchor")
    a.set_rotation(phi + 180.0 if flip else phi)
    a.set_ha("right" if flip else "left")
    a.set_va("center")
    a.xyann = (float(d * u[0] / px), float(d * u[1] / px))
    if d + length + EDGE_PAD_PT * px > radius:
        return False
    n = np.array([-u[1], u[0]])
    start, end = centre + d * u, centre + (d + length) * u
    band = Path([start + n * height / 2, end + n * height / 2, end - n * height / 2,
                 start - n * height / 2, start + n * height / 2], closed=True)
    return not any(band.intersects_bbox(t, filled=True) for t in ticks)
