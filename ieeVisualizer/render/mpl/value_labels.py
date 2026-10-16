"""Bar value labels and "no data" notes, placed once the layout is known.

Both are drawn with :func:`ieeVisualizer.render.mpl.marks.draw_bar` at a
fixed offset (in points) from a bar end or from the zero line, but whether
they fit -- inside the axes, beside each other -- depends on text extents
and on the axes' size in pixels, which exist only after layout. So
:func:`fit_value_labels` replaces the figure's constrained-layout engine by
one that, after laying out, on every draw at the size and dpi actually
drawn:

1. **Places "no data" notes** (:func:`_place_notes`): a note reads away from
   the zero line, on the side where the axes have room for it -- the
   positive side when it fits there (as before), else the negative side, so
   on an all-negative value axis (whose zero line is the axes' edge) the
   note is inside the plot instead of above it, near the title. When the
   value axis' final limits exclude zero (``axes.<ch>.limits`` of
   ``[50, 100]`` or ``[-100, -50]``) the note starts at the plot edge
   nearest to zero instead, so it is drawn inside the axes rather than
   clipped away (an empty slot must not read as "below 50"). In ``polar``
   the notes are placed along their slot's angle instead
   (:mod:`.polar_notes`).
2. **Arranges value labels** (:func:`_arrange`) so no two overlap and each
   is attributable to its bar: a label must stay inside its bar's *cell*,
   the stretch of the category axis halfway to the neighbouring bar centres
   (any bar layer of that orientation), centred on its own bar. On vertical
   bars, labels wider than their cell are all turned 90 degrees (reading
   upwards, still centred on their bar); labels still thicker than their
   cell (vertical) or than their row (horizontal bars) are all shrunk to one
   common size, down to :data:`MIN_LABEL_PT`. If even that does not fit,
   or labels would still intersect, the value labels are left out with a
   ``value_labels_omitted`` warning rather than drawn on top of each other.
   The warning names the cause: bars too narrow for their labels, or -- when
   a label that does not fit belongs to a layer of a ``bars_overlap``
   warning -- bars drawn over each other.
3. **Widens the value axis** (:func:`_fit_axis`) until every value label lies
   inside the axes; an end the spec fixed with ``axes.<ch>.limits`` is kept
   and a label that still does not fit is a ``value_label_outside_axes``
   warning.

Each step starts from the labels as drawn, so repeated draws (the legend
pass measures the figure before it is saved) reach the same result. When
nothing needs to change, the layout runs exactly once, so the output is
unchanged byte for byte. The warnings these steps give
(``value_labels_omitted``, ``value_label_outside_axes``,
``no_data_unplaceable``) describe the most recent draw only: each draw
withdraws the previous draw's and gives its own, so the warnings of a render
match the figure it saved, not an intermediate layout.

Value labels are drawn on 2D cartesian bars only (``polar`` and 3D give a
``style_key_ignored`` warning; see :func:`ieeVisualizer.render.mpl.marks.draw_bar`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

#: Space kept between a value label and the axes edge, in points.
VALUE_LABEL_PAD_PT = 2.0
#: Smallest font size a value label is shrunk to, in points.
MIN_LABEL_PT = 6.0
#: Free space kept on each side of a label within its cell, in points.
CELL_GAP_PT = 1.0


@dataclass
class ValueLabel:
    """A bar value label: ``text`` (the annotation) of a bar of layer
    ``layer`` centred at ``centre`` on the category axis; ``ch`` is the
    value channel (``"y"`` for vertical bars)."""

    text: Any
    ch: str
    layer: int
    centre: float
    width: float
    neg: bool
    fontsize: float


@dataclass
class NoDataNote:
    """A "no data" note of layer ``layer`` at a slot without a bar; ``ch``
    is the value channel, ``slot`` the slot's centre on the category axis
    (the angle in polar), ``width`` the slot's width and ``where`` the slot
    as the data names it (for warnings)."""

    text: Any
    ch: str
    layer: int = 0
    slot: float = 0.0
    width: float = 0.0
    where: str = ""


def fit_value_labels(ctx: Any, rounds: int = 4) -> None:
    """Install the layout engine described in the module docstring when the
    figure has bar value labels or "no data" notes (2D cartesian; in polar
    only the notes, see :mod:`.polar_notes`)."""
    if not (ctx.value_labels or ctx.no_data_notes) or ctx.three_d:
        return
    from matplotlib.layout_engine import ConstrainedLayoutEngine

    fig = ctx.fig
    old = fig.get_layout_engine()
    if not isinstance(old, ConstrainedLayoutEngine):
        return
    if ctx.min_pt:  # profile renders: the value axis is widened to hold the labels, so
        # constrained layout must not also shrink the axes for them (the two
        # would chase each other and squeeze the bars)
        for lab in ctx.value_labels:
            lab.text.set_in_layout(False)
    issued: list = []  # the warnings the latest draw gave
    start: list = []  # profile renders: the axes' limits before any widening
    polar = ctx.spec.coordinate_system == "polar"

    class ValueLabelLayout(ConstrainedLayoutEngine):
        def execute(self, figure: Any) -> Any:
            if ctx.min_pt:  # profile renders: every draw widens from the spec's limits,
                # not from what a measuring pass at another layout left behind
                if not start:
                    start.extend((ctx.ax.get_xlim(), ctx.ax.get_ylim()))
                ctx.ax.set_xlim(*start[0])
                ctx.ax.set_ylim(*start[1])
            out = super().execute(figure)
            if polar:
                from .polar_notes import place_polar_notes

                _sync(ctx, issued, place_polar_notes(ctx))
                return out
            status: dict = {}
            changed = _place_notes(ctx)
            changed |= _arrange(ctx, status)
            if changed:
                out = super().execute(figure)
            pad = VALUE_LABEL_PAD_PT * figure.dpi / 72.0
            for _ in range(rounds):
                moved = False
                for ch in sorted({lab.ch for lab in ctx.value_labels}):
                    moved |= _fit_axis(ctx, ch, [lab.text for lab in ctx.value_labels
                                                 if lab.ch == ch], pad)
                if not moved:
                    break
                out = super().execute(figure)
                if _place_notes(ctx) | _arrange(ctx, status):
                    out = super().execute(figure)
            if ctx.min_pt and ctx.text_annotations and _clear_texts(ctx):
                out = super().execute(figure)
            found = [status["omitted"]] if status.get("omitted") else []
            _sync(ctx, issued, found + _unfitted(ctx))
            return out

    new = ValueLabelLayout()
    new.set(**old.get())
    fig.set_layout_engine(new)


def _sync(ctx: Any, issued: list, found: list) -> None:
    """Withdraw the warnings the previous draw gave (``issued``) and give
    ``found``, this draw's."""
    keep = [w for w in ctx.warnings if not any(w is old for old in issued)]
    ctx.warnings[:] = keep + found
    issued[:] = found


# ---------------------------------------------------------------- notes


def _place_notes(ctx: Any) -> bool:
    """Point each "no data" note to the side of the zero line where it fits
    inside the axes (positive side preferred), starting at the plot edge
    nearest to zero when the axis limits exclude it; returns whether one
    changed."""
    box = ctx.ax.get_window_extent()
    pad = VALUE_LABEL_PAD_PT * ctx.fig.dpi / 72.0
    changed = False
    for note in ctx.no_data_notes:
        a = note.text
        vertical = note.ch == "y"
        before = (tuple(a.xyann), a.get_ha(), a.get_va(), a.xy, a.xycoords)
        _anchor(ctx, note)
        _note_side(a, vertical, positive=True)
        ext = a.get_window_extent()
        if vertical:
            length = ext.height + abs(a.xyann[1]) * ctx.fig.dpi / 72.0
            anchor = ext.y0 - abs(a.xyann[1]) * ctx.fig.dpi / 72.0
            room_pos, room_neg = box.y1 - anchor, anchor - box.y0
        else:
            length = ext.width + abs(a.xyann[0]) * ctx.fig.dpi / 72.0
            anchor = ext.x0 - abs(a.xyann[0]) * ctx.fig.dpi / 72.0
            room_pos, room_neg = box.x1 - anchor, anchor - box.x0
        if length + pad > room_pos and (length + pad <= room_neg or room_neg > room_pos):
            _note_side(a, vertical, positive=False)
        changed |= (tuple(a.xyann), a.get_ha(), a.get_va(), a.xy, a.xycoords) != before
    return changed


def _anchor(ctx: Any, note: NoDataNote) -> None:
    """Start ``note`` at the zero line when it is in view, else at the edge
    of the plot nearest to zero (in axes-fraction coordinates on the value
    axis). A note on a log value axis is anchored at the edge already."""
    vertical = note.ch == "y"
    axis = ctx.spec.axes.get(note.ch)
    if axis is not None and axis.scale == "log":
        return
    a = note.text
    lo, hi = ctx.ax.get_ylim() if vertical else ctx.ax.get_xlim()
    if min(lo, hi) <= 0.0 <= max(lo, hi):
        if a.xycoords != "data":  # moved to an edge by an earlier draw
            a.xycoords = "data"
            a.xy = (note.slot, 0.0) if vertical else (0.0, note.slot)
        return
    below = 0.0 < min(lo, hi)  # zero lies past the lower limit
    f = 0.0 if below == (lo < hi) else 1.0
    if vertical:
        a.xycoords, a.xy = ("data", "axes fraction"), (note.slot, f)
    else:
        a.xycoords, a.xy = ("axes fraction", "data"), (f, note.slot)


def _note_side(a: Any, vertical: bool, positive: bool) -> None:
    off = 3 if positive else -3
    if vertical:
        a.xyann = (0, off)
        a.set_va("bottom" if positive else "top")
    else:
        a.xyann = (off, 0)
        a.set_ha("left" if positive else "right")


# --------------------------------------------------------- value labels


def _arrange(ctx: Any, status: dict) -> bool:
    """Rotate / shrink / omit value labels so none overlaps another or
    leaves its bar's cell; returns whether anything changed. Sets
    ``status["omitted"]`` to the ``value_labels_omitted`` warning when the
    labels were left out, else to None."""
    labels = ctx.value_labels
    if not labels:
        return False
    min_pt = max(MIN_LABEL_PT, ctx.min_pt)
    before = [_state(lab.text) for lab in labels]
    for lab in labels:  # start from the labels as drawn
        _set_rotation(lab, 0)
        lab.text.set_fontsize(lab.fontsize)
        lab.text.set_visible(True)
    room = [_cell_px(ctx, lab) for lab in labels]
    gap = 2 * CELL_GAP_PT * ctx.fig.dpi / 72.0
    if any(_thickness(lab) > r - gap for lab, r in zip(labels, room) if lab.ch == "y"):
        for lab in labels:
            if lab.ch == "y":
                _set_rotation(lab, 90)
    scale = min((r - gap) / max(_thickness(lab), 1e-9) for lab, r in zip(labels, room))
    culprits: list = []  # labels that cannot fit (or overlap) at the smallest size
    if scale < 1.0:
        smallest = min(lab.fontsize for lab in labels)
        size = smallest * scale * 0.98
        # the labels whose own room would need a size below the minimum
        culprits = [lab for lab, r in zip(labels, room)
                    if (r - gap) / max(_thickness(lab), 1e-9) * smallest * 0.98 < min_pt]
        if size >= min_pt:
            for lab in labels:
                lab.text.set_fontsize(min(lab.fontsize, size))
            culprits = [lab for lab, r in zip(labels, room) if _thickness(lab) > r - gap]
    if not culprits:
        culprits = _overlapping(labels)
    status["omitted"] = None
    ctx.plot_growth.pop("value_labels", None)
    if culprits and ctx.min_pt and all(lab.ch == "x" for lab in labels) \
            and not {lab.layer for lab in culprits} & ctx.overlapping_bar_layers:
        # horizontal bars: a taller plot gives each row room for its label
        ctx.plot_growth["value_labels"] = max(
            (_thickness(lab) * min_pt / lab.text.get_fontsize() + gap) / max(r, 1e-9)
            for lab, r in zip(labels, room)) * 1.04
    if culprits:
        for lab in labels:
            lab.text.set_visible(False)
        status["omitted"] = _omitted_warning(ctx, labels, culprits)
    return [_state(lab.text) for lab in labels] != before


def _omitted_warning(ctx: Any, labels: list, culprits: list) -> Any:
    from ..base import RenderWarning

    layers = sorted({lab.layer for lab in labels})
    overlapping = sorted({lab.layer for lab in culprits} & ctx.overlapping_bar_layers)
    if overlapping:
        why = (f"bars of layer(s) {overlapping} overlap each other (see the bars_overlap "
               "warning), so their value labels cannot sit apart")
        fix = "Place the layers so their bars do not overlap."
    else:
        why = ("the bars are too narrow for their value labels to sit beside each other "
               f"legibly (at least {max(MIN_LABEL_PT, ctx.min_pt):.3g} pt)")
        fix = "Read the values from the axis, use horizontal bars or a larger figure."
    return RenderWarning("value_labels_omitted",
                         f"{why}; the value labels of layer(s) {layers} were left out. {fix}",
                         f"/layers/{layers[0]}/style")


def _overlapping(labels: list) -> list:
    boxes = [lab.text.get_window_extent() for lab in labels]
    out = []
    for k, a in enumerate(boxes):
        for j in range(k + 1, len(boxes)):
            b = boxes[j]
            if a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1:
                out += [labels[k], labels[j]]
    return out


def _state(a: Any) -> tuple:
    return (a.get_rotation(), a.get_fontsize(), a.get_visible(), a.get_ha(), a.get_va())


def _set_rotation(lab: ValueLabel, degrees: int) -> None:
    """Vertical-bar labels sit centred on their bar either way; turned, they
    read upwards and start at the bar end (below it for a negative bar)."""
    lab.text.set_rotation(degrees)


def _thickness(lab: ValueLabel) -> float:
    """A label's extent across the category axis, in pixels."""
    ext = lab.text.get_window_extent()
    return ext.width if lab.ch == "y" else ext.height


def _cell_px(ctx: Any, lab: ValueLabel) -> float:
    """Width in pixels of ``lab``'s cell: twice the distance from its bar's
    centre to the nearer of the midpoints towards the neighbouring bar
    centres (of every bar of the same orientation); with no neighbour on one
    side the other side's distance counts, and a lone bar's cell is its
    category slot (bar width / 0.8)."""
    centres = ctx.bar_centres.get(lab.ch, np.empty(0))
    c = lab.centre
    lower, upper = centres[centres < c - 1e-12], centres[centres > c + 1e-12]
    halves = []
    if lower.size:
        halves.append((c - lower.max()) / 2.0)
    if upper.size:
        halves.append((upper.min() - c) / 2.0)
    half = min(halves) if halves else lab.width / 1.6
    a, b = _cat_px(ctx.ax, lab.ch, [c - half, c + half])
    return abs(b - a)


def _cat_px(ax: Any, val_ch: str, values: list[float]) -> list[float]:
    """Display positions of category-axis ``values`` (the axis other than
    the value channel ``val_ch``)."""
    if val_ch == "y":
        tr = ax.get_xaxis_transform()  # x in data, y in axes fraction
        return [float(tr.transform((v, 0.5))[0]) for v in values]
    tr = ax.get_yaxis_transform()
    return [float(tr.transform((0.5, v))[1]) for v in values]


# ------------------------------------------------------- value-axis fit


def _unfitted(ctx: Any) -> list:
    """A ``value_label_outside_axes`` warning per visible label that does
    not lie inside the axes."""
    from ..base import RenderWarning

    box = ctx.ax.get_window_extent()
    out = []
    for lab in ctx.value_labels:
        a = lab.text
        if not a.get_visible():
            continue
        b = a.get_window_extent()
        if b.x0 < box.x0 - 0.5 or b.x1 > box.x1 + 0.5 or b.y0 < box.y0 - 0.5 \
                or b.y1 > box.y1 + 0.5:
            out.append(RenderWarning(
                "value_label_outside_axes",
                f"the value label {a.get_text()!r} of layer {lab.layer} does not fit inside "
                "the plot (fixed axis limits or a very small figure).",
                f"/layers/{lab.layer}/style"))
    return out


def _fit_axis(ctx: Any, ch: str, labels: list[Any], pad: float) -> bool:
    """One round for the value axis ``ch``: returns whether a limit moved."""
    ax = ctx.ax
    axis = ax.xaxis if ch == "x" else ax.yaxis
    box = ax.get_window_extent()
    start, length = (box.x0, box.width) if ch == "x" else (box.y0, box.height)
    if length <= 0:
        return False
    t = axis.get_transform()
    a, b = ax.get_xlim() if ch == "x" else ax.get_ylim()  # display order: left/bottom first
    ua, ub = (float(u) for u in t.transform([a, b]))
    if ub == ua:
        return False
    axis_spec = ctx.spec.axes.get(ch)
    fixed = list(axis_spec.limits) if axis_spec is not None and axis_spec.limits else [None, None]
    # which data end (0 = min, 1 = max) is drawn at the far (right/top) side
    far_end, near_end = (1, 0) if b > a else (0, 1)
    new_ua, new_ub = ua, ub
    for lab in labels:
        if not lab.get_visible():
            continue
        u = float(t.transform([lab.xy[0] if ch == "x" else lab.xy[1]])[0])
        f = (u - ua) / (ub - ua)
        if not 0.0 <= f <= 1.0:
            continue  # the bar end itself is outside the view
        anchor = start + f * length
        ext = lab.get_window_extent()
        lo_px, hi_px = (ext.x0, ext.x1) if ch == "x" else (ext.y0, ext.y1)
        beyond_far = hi_px - anchor  # px the label reaches past its anchor
        beyond_near = anchor - lo_px
        if hi_px > start + length - pad + 0.5 and fixed[far_end] is None \
                and beyond_far + pad < length:
            new_ub = _further(new_ub, ua + (u - ua) * length / (length - pad - beyond_far), ua)
        if lo_px < start + pad - 0.5 and fixed[near_end] is None \
                and beyond_near + pad < length:
            k = (beyond_near + pad) / length
            new_ua = _further(new_ua, (u - k * ub) / (1.0 - k), ub)
    if (new_ua, new_ub) == (ua, ub):
        return False
    inv = t.inverted()
    lo, hi = (float(v) for v in inv.transform([new_ua, new_ub]))
    (ax.set_xlim if ch == "x" else ax.set_ylim)(lo, hi)
    return True


def _clear_texts(ctx: Any) -> bool:
    """Profile renders: when a text annotation anchored on a row of
    horizontal bars (a funnel's rate column) is drawn over a value label,
    every such annotation moves under its own row's value label, wrapped to
    the room left of the axes' right edge, and the plot is grown
    (``plot_growth``) until each row holds both. Returns whether they moved.
    Words and rows stay; only the position changes."""
    from matplotlib.text import Annotation

    from .legibility import wrap

    labels = [lab for lab in ctx.value_labels if lab.ch == "x" and lab.text.get_visible()]
    ctx.plot_growth.pop("annotations", None)
    rows = []
    for a in ctx.text_annotations:
        if not (isinstance(a, Annotation) and a.get_visible() and a.get_text()):
            continue
        orig = getattr(a, "_ieev_original", None)
        if orig is None:
            orig = a._ieev_original = (a.get_text(), a.xy, tuple(a.xyann), a.get_ha(),
                                       a.get_va(), a.get_in_layout())
        else:  # every layout decides again from the annotation as drawn
            a.set_text(orig[0])
            a.xy, a.xyann = orig[1], orig[2]
            a.set_ha(orig[3])
            a.set_va(orig[4])
            a.set_in_layout(orig[5])
        try:
            y = float(ctx.ax.yaxis.convert_units(orig[1][1]))
        except (TypeError, ValueError):
            continue
        own = [lab for lab in labels if abs(lab.centre - y) < 1e-9]
        if own:
            rows.append((a, own[-1], orig[0]))
    exts = [lab.text.get_window_extent() for lab in labels]
    right = ctx.ax.get_window_extent().x1
    if not any(a.get_window_extent().overlaps(e) or a.get_window_extent().x1 > right + 0.5
               for a, _, _ in rows for e in exts):
        return False
    dpi = ctx.fig.dpi
    need = 1.0
    for a, lab, text in rows:
        lext = lab.text.get_window_extent()
        room_pt = (right - lext.x0) * 72.0 / dpi - VALUE_LABEL_PAD_PT
        a.set_text(wrap(" ".join(text.split()), a.get_fontsize(),
                        max(room_pt, 4 * a.get_fontsize())))
        a.xy = lab.text.xy
        a.xyann = (lab.text.xyann[0], -lext.height / 2 * 72.0 / dpi - CELL_GAP_PT)
        a.set_ha("left")
        a.set_va("top")
        a.set_in_layout(False)
        # the label (centred on the bar) and the text under it fit the row
        need = max(need, 2 * (lext.height / 2 + a.get_window_extent().height
                              + 2 * CELL_GAP_PT * dpi / 72.0) / max(_cell_px(ctx, lab), 1e-9))
    if need > 1.0:
        ctx.plot_growth["annotations"] = need * 1.04
    return True


def _further(current: float, wanted: float, other: float) -> float:
    """The one of ``current``/``wanted`` further from ``other``."""
    return wanted if abs(wanted - other) > abs(current - other) else current
