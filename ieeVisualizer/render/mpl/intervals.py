"""Intervals: ``band`` and ``errorbar`` marks, and ``y_lower``/``y_upper`` on
line (a band), scatter and bar (error bars) marks.

Bounds are drawn exactly as the data gives them: never computed, widened,
symmetrised or smoothed. A row whose lower or upper bound is null has no
interval there: a band breaks (a gap, never a zero), an error bar is left
out, and an ``interval_bounds_missing`` warning counts the rows. Every
interval's legend label says what it is ("95 % prediction interval", from
:func:`ieeVisualizer.spec.intervals.interval_label`, with the theme's
decimal separator); with the legend hidden, the adapter writes the same
statements into the caption instead (:func:`caption_with_intervals`).

Bands are filled at the theme's ``roles.interval_fill_alpha`` (0.25 when
the theme does not set it) unless the style sets ``opacity``, so nested
bands of one series (a fan chart) darken towards the centre while the line
drawn over them stays readable. A forecast band is hatched and lighter, like any
forecast fill; forecast error bars are dashed with hollow points.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from matplotlib.colors import to_rgb

from ...spec.intervals import MULTIPLIER_KINDS, asymmetric_rows, mark_interval_label
from . import marks as _m
from .data import positions
from .style import forecast_dashes, legend_label

#: Length of an error-bar cap, in points.
CAP_POINTS = 6.0
#: Fraction of a category slot that dodged error-bar groups spread over.
DODGE_SPAN = 0.5


#: Appended to a ±k SE/SD label whose bounds are not value ± one half-width.
ASYMMETRIC_NOTE = " (bounds not symmetric)"


def interval_text(ctx: Any, m: Any, rows: Any = None) -> str:
    """What the layer's interval is, with the theme's decimal separator; a
    ±k SE/SD statement says so when the data's bounds are not symmetric
    about the value in ``rows`` (the rows that entry draws; default all)
    -- validate warns ``interval_not_symmetric``."""
    decimal = ctx.locale.decimal if ctx.locale is not None else "."
    text = mark_interval_label(m, decimal)
    if asymmetric(ctx, m, rows):
        text += ASYMMETRIC_NOTE
    return text


def asymmetric(ctx: Any, m: Any, rows: Any = None) -> bool:
    """A ±k SE/SD layer whose bounds are not symmetric in ``rows``."""
    if m.interval_kind not in MULTIPLIER_KINDS or "y" not in m.encoding:
        return False
    cols = _m._snapshot(ctx, m).columns
    y, lo, hi = (cols[m.encoding[ch].field].values for ch in ("y", "y_lower", "y_upper"))
    if rows is not None:
        keep = [int(r) for r in rows]
        y, lo, hi = ([v[r] for r in keep] for v in (y, lo, hi))
    return bool(asymmetric_rows(y, lo, hi))


#: Where a profile (phone/tablet) render breaks a percentile interval's
#: legend label: before its level, "P10–P90 percentiles\n(80 %)".
PERCENTILE_BREAK = " ("
#: Points kept free for the legend frame's line and rounding.
FRAME_PT = 4.0


def _text_width_pt(text: str, size: float) -> float:
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextToPath

    prop = FontProperties(size=size)
    return max(TextToPath().get_text_width_height_descent(line, prop, ismath=False)[0]
               for line in text.split("\n"))


def narrow_label(label: str, width_pt: float, size: float) -> str:
    """An interval's legend label for a narrow (profile) render, wrapped
    only when it is wider than a one-column legend can be within the figure
    (the figure width less the legend's handle, paddings and frame, from the
    legend rcParams): a percentile interval puts its level on the next line,
    then a long ``"<series>: <statement>"`` puts the series on its own line."""
    from matplotlib import rcParams

    ems = (rcParams["legend.handlelength"] + rcParams["legend.handletextpad"]
           + 2 * rcParams["legend.borderpad"] + 2 * rcParams["legend.borderaxespad"])
    room = width_pt - ems * size - FRAME_PT
    if _text_width_pt(label, size) <= room:
        return label
    head, sep, tail = label.partition(PERCENTILE_BREAK)
    if sep and head.endswith("percentiles"):
        label = f"{head}\n{sep.lstrip()}{tail}"
    name, colon, rest = label.partition(": ")
    if colon and _text_width_pt(label, size) > room:
        label = f"{name}:\n{rest}"
    return label


def record(ctx: Any, label: str | None) -> str | None:
    """Note an interval's legend label (for the caption when the legend is
    hidden); returns it."""
    if label and not label.startswith("_") and label not in ctx.interval_labels:
        ctx.interval_labels.append(label)
    return label


def bounds(ctx: Any, m: Any) -> tuple[Any, Any] | None:
    """The layer's (lower, upper) bounds as masked arrays on the y axis, or
    None when it draws no interval."""
    if "y_lower" not in m.encoding:
        return None
    return tuple(positions(_m._col(ctx, m, ch), ctx.axes["y"], _m._cpath(ctx, m, ch))
                 for ch in ("y_lower", "y_upper"))


def _missing(ctx: Any, i: int, placed: np.ndarray, lo: Any, hi: Any) -> None:
    n = int((placed & ~_m._mask(lo, hi)).sum())
    if n:
        ctx.warn("interval_bounds_missing",
                 f"{n} row(s) of layer {i} have no lower or upper bound, so no interval is "
                 "drawn there (a gap, not a zero-width or zero interval).", f"/layers/{i}")


def connector_rows(ctx: Any, m: Any) -> np.ndarray:
    """Rows of a forecast layer that the timeseries template copied from the
    observed data so the lines join (``connect_forecast``; recorded as
    ``metadata.template.forecast.shared_boundary``): an observation, which
    has no forecast interval, so its missing bounds are not a gap."""
    snap = _m._snapshot(ctx, m)
    n = len(snap.columns[m.encoding["x"].field].values)
    out = np.zeros(n, dtype=bool)
    meta = ctx.spec.metadata.get("template") if isinstance(ctx.spec.metadata, dict) else None
    fc = meta.get("forecast") if isinstance(meta, dict) else None
    if m.role != "forecast" or not (isinstance(fc, dict) and fc.get("shared_boundary")):
        return out
    xname = m.encoding["x"].field
    observed = set()
    for o in ctx.spec.layers:
        ds = ctx.spec.datasets.get(o.data) if o.role != "forecast" else None
        col = getattr(ds, "columns", {}).get(xname) if ds is not None else None
        if col is not None:
            observed |= {v for v in col.values if v is not None}
    for k, v in enumerate(snap.columns[xname].values):
        out[k] = v in observed
    return out


def _qualified(base: str | None, text: str) -> str:
    return f"{base}: {text}" if base else text


def _band_alpha(ctx: Any, style: dict[str, Any], role: str) -> float:
    alpha = style.get("opacity", ctx.roles.interval_alpha)
    return alpha * ctx.roles.forecast_line_alpha if role == "forecast" else alpha


def fill_band(ctx: Any, x: Any, lo: Any, hi: Any, color: Any, role: str, label: str | None,
              style: dict[str, Any]) -> list[Any]:
    """``fill_between`` over the rows where x and both bounds exist; each run
    of such rows is its own polygon through exactly those points. A lone row
    between gaps (which would be a zero-width polygon) is drawn as a
    vertical segment so it stays visible."""
    valid = _m._mask(x, lo, hi)
    fx, flo, fhi = (np.ma.filled(a.astype(float), np.nan) for a in (x, lo, hi))
    kw: dict[str, Any] = {"facecolor": color, "alpha": _band_alpha(ctx, style, role),
                          "linewidth": 0, "label": label}
    if role == "forecast":
        kw.update(hatch=ctx.roles.forecast_hatch, edgecolor=color)
    record(ctx, label)
    band = ctx.ax.fill_between(fx, flo, fhi, where=valid, interpolate=False, **kw)
    out = [band]
    ctx.band_records.append(BandRecord(band, to_rgb(color), kw["alpha"], kw.get("hatch"),
                                       fx[valid], flo[valid], fhi[valid]))
    lone = _m._isolated(valid)
    if lone:
        out.append(ctx.ax.vlines(fx[lone], flo[lone], fhi[lone], colors=color,
                                 alpha=kw["alpha"], linewidth=1.5, label="_nolegend_"))
    return out


def error_segments(ctx: Any, x: np.ndarray, lo: np.ndarray, hi: np.ndarray, color: Any,
                   role: str, label: str | None, style: dict[str, Any],
                   linewidth: float | None = None) -> list[Any]:
    """A vertical segment from each lower to upper bound, with caps at both."""
    lw = style.get("linewidth", linewidth or 1.2)
    kw: dict[str, Any] = {"colors": color, "linewidth": lw, "label": label}
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    if role == "forecast":
        kw["linestyles"] = [forecast_dashes("--")]
        kw["alpha"] = kw.get("alpha", 1.0) * ctx.roles.forecast_line_alpha
    elif role == "reference":
        kw.setdefault("alpha", ctx.roles.reference_alpha)
    record(ctx, label)
    out = [ctx.ax.vlines(x, lo, hi, **kw)]
    for ends in (lo, hi):
        out += ctx.ax.plot(x, ends, linestyle="none", marker="_", markersize=CAP_POINTS,
                           markeredgewidth=lw, color=color, alpha=kw.get("alpha"),
                           label="_nolegend_")
    return out


def point_intervals(ctx: Any, i: int, m: Any, xs: Any, rows: np.ndarray, color: Any,
                    label: str | None, style: dict[str, Any], qualify: bool = True) -> list[Any]:
    """Error bars at ``xs`` for the drawn rows ``rows`` of a scatter or bar
    layer (``xs[k]`` is where row ``rows[k]`` is drawn), where both bounds
    exist. The legend label is ``"<label>: <interval>"`` (``qualify``) or
    ``label`` as given."""
    lo, hi = bounds(ctx, m)
    lo, hi = lo[rows], hi[rows]
    xs = np.ma.masked_array(np.asarray(xs, dtype=float))
    _missing(ctx, i, np.ones(len(rows), dtype=bool), lo, hi)
    ok = _m._mask(xs, lo, hi)
    if not ok.any():
        return []
    if qualify:
        label = _qualified(label, interval_text(ctx, m, np.asarray(rows)[ok]))
    return error_segments(ctx, np.asarray(xs[ok]), np.asarray(lo[ok]), np.asarray(hi[ok]),
                          color, m.role, label, style)


def bar_intervals(ctx: Any, i: int, m: Any, drawn: list[tuple[Any, np.ndarray, Any, Any]],
                  ink: Any) -> list[Any]:
    """Error bars of a bar layer, drawn after all of its bars so the legend
    lists the interval after them. ``drawn`` holds each group's (bar
    centres, rows, bar label, group). Every group's bars are in one ink, so
    one legend entry covers them, naming the groups that have an interval
    when not all do, and which groups' bounds are not symmetric."""
    lo, hi = bounds(ctx, m)
    with_iv = [d for d in drawn if _m._mask(lo[d[1]], hi[d[1]]).any()]
    if not with_iv:
        return []
    grouped = len(drawn) > 1
    text = mark_interval_label(m, ctx.locale.decimal if ctx.locale is not None else ".")
    first = _qualified(m.name if grouped else drawn[0][2], text)
    if grouped and len(with_iv) < len(drawn):  # name the series that have one
        first += f" ({', '.join(str(d[3]) for d in with_iv)})"
    asym = [d[3] for d in with_iv if asymmetric(ctx, m, d[1])]
    if asym:
        first += ASYMMETRIC_NOTE if not grouped or len(asym) == len(with_iv) else \
            f" (bounds not symmetric: {', '.join(str(g) for g in asym)})"
    out: list[Any] = []
    for pp, rows, _, _ in with_iv:
        got = point_intervals(ctx, i, m, pp, rows, ink, first, {}, qualify=False)
        if got and first != "_nolegend_":
            ctx.listed_after[id(got[0])] = i  # legend: after the layer's bars
            first = "_nolegend_"  # the entry is given once, by the first drawn group
        out += got
    return out


def values_missing(ctx: Any, i: int, m: Any, rows: np.ndarray) -> None:
    """Rows of a bar layer with both bounds but no bar value: the slot says
    "no data", and its interval is not drawn alone (a floating interval on
    a bar chart reads as a bar); say so instead of dropping it silently."""
    lo, hi = bounds(ctx, m)
    n = int(_m._mask(lo[rows], hi[rows]).sum())
    if n:
        ctx.warn("interval_without_value",
                 f"{n} row(s) of layer {i} have both bounds but no bar value; their slot says "
                 "\"no data\" and their interval is not drawn. Supply the estimate, or use an "
                 "errorbar mark to show an interval without one.", f"/layers/{i}")


def line_band(ctx: Any, i: int, m: Any, x: Any, idx: np.ndarray, color: Any,
              label: str | None, style: dict[str, Any]) -> list[Any]:
    """The band of a line's interval, for the line's rows ``idx``."""
    lo, hi = bounds(ctx, m)
    sub = x[idx], lo[idx], hi[idx]
    _missing(ctx, i, ~np.ma.getmaskarray(sub[0]), sub[1], sub[2])
    if not _m._mask(*sub).any():
        return []
    text = interval_text(ctx, m, idx[_m._mask(*sub)])
    band_style = {k: v for k, v in style.items() if k != "opacity"}
    return fill_band(ctx, *sub, color, m.role, _qualified(label, text), band_style)


def _series_colour(ctx: Any, i: int, m: Any, group: Any, style: dict[str, Any]) -> Any:
    if m.role == "reference":
        return style.get("color", ctx.roles.reference_color)
    return ctx.layer_color(i, m, group, style, style.get("color"))


def _base_label(m: Any, group: Any) -> str | None:
    parts = [p for p in (m.name, group) if p]
    return ": ".join(parts) if parts else None


def draw_band(ctx: Any, i: int, m: Any) -> list[Any]:
    style = _m._style(ctx, i, m)
    x = _m._pos(ctx, m, "x")
    lo, hi = bounds(ctx, m)
    _m._report_nulls(ctx, i, ~np.ma.getmaskarray(x), "left out of the band")
    _missing(ctx, i, ~np.ma.getmaskarray(x) & ~connector_rows(ctx, m), lo, hi)
    text = interval_text(ctx, m)
    artists: list[Any] = []
    for group, idx in _m._groups(ctx, i, m, len(x)):
        sub = x[idx], lo[idx], hi[idx]
        _m._require(_m._mask(*sub), i, m, group)
        color = _series_colour(ctx, i, m, group, style)
        label = legend_label(_qualified(_base_label(m, group), text), None, m.role)
        artists += fill_band(ctx, *sub, color, m.role, label, style)
    return artists


def draw_errorbar(ctx: Any, i: int, m: Any) -> list[Any]:
    style = _m._style(ctx, i, m)
    x = _m._pos(ctx, m, "x")
    lo, hi = bounds(ctx, m)
    y = _m._pos(ctx, m, "y") if "y" in m.encoding else None
    _m._report_nulls(ctx, i, ~np.ma.getmaskarray(x), "left out")
    _missing(ctx, i, ~np.ma.getmaskarray(x), lo, hi)
    grp = _m._groups(ctx, i, m, len(x))
    step = 0.0
    if ctx.axes["x"].kind == "category":
        if len(grp) > 1:
            step = DODGE_SPAN / len(grp)  # groups side by side within each category slot
        slots = np.asarray(x.compressed(), dtype=float)
        ctx.ax.update_datalim([(c, 0.0) for c in np.concatenate([slots - 0.5, slots + 0.5])],
                              updatey=False)  # a whole slot per category, as for bars
    artists: list[Any] = []
    for g, (group, idx) in enumerate(grp):
        sub = x[idx], lo[idx], hi[idx]
        ok = _m._mask(*sub)
        _m._require(ok, i, m, group)
        dx = (g - (len(grp) - 1) / 2.0) * step
        color = _series_colour(ctx, i, m, group, style)
        label = legend_label(_qualified(_base_label(m, group), interval_text(ctx, m, idx[ok])),
                             None, m.role)
        xs = np.asarray(sub[0][ok], dtype=float) + dx
        artists += error_segments(ctx, xs, np.asarray(sub[1][ok]), np.asarray(sub[2][ok]),
                                  color, m.role, label, style)
        if y is not None:
            ys = y[idx]
            pts = ~np.ma.getmaskarray(sub[0]) & ~np.ma.getmaskarray(ys)
            kw: dict[str, Any] = {"marker": style.get("marker", "o"), "color": color,
                                  "markersize": 5, "linestyle": "none", "label": "_nolegend_"}
            if m.role == "forecast":
                kw["markerfacecolor"] = "none"
            if "opacity" in style:
                kw["alpha"] = style["opacity"]
            artists += ctx.ax.plot(np.asarray(sub[0][pts], dtype=float) + dx,
                                   np.asarray(ys[pts]), **kw)
    return artists


@dataclass
class BandRecord:
    """A drawn band: its artist, colour, opacity, hatch and complete rows."""

    artist: Any
    rgb: tuple
    alpha: float
    hatch: Any
    x: np.ndarray
    lo: np.ndarray
    hi: np.ndarray

    def inside(self, other: BandRecord) -> bool:
        """Most rows of this band lie within ``other`` at the same x (so a
        row or two overshooting an outer band does not change what the
        swatch of the bulk of the region should look like)."""
        if self.rgb != other.rgb or not self.x.size:
            return False
        pos = {float(x): k for k, x in enumerate(other.x)}
        within = 0
        for x, lo, hi in zip(self.x, self.lo, self.hi):
            k = pos.get(float(x))
            within += k is not None and other.lo[k] <= lo and hi <= other.hi[k]
        return within * 2 > self.x.size


def legend_order(ctx: Any, handles: list[Any], labels: list[str]) -> tuple[list, list]:
    """Move each bar layer's interval entry to just after that layer's last
    bar entry (matplotlib lists collections before bars)."""
    h, lab = list(handles), list(labels)
    for key, layer in ctx.listed_after.items():
        at = next((k for k, x in enumerate(h) if id(x) == key), None)
        if at is None:
            continue
        mine = {id(a) for a in ctx.layer_artists.get(layer, ())}
        entry, text = h.pop(at), lab.pop(at)
        last = max((k for k, x in enumerate(h) if id(x) in mine), default=at - 1)
        h.insert(last + 1, entry)
        lab.insert(last + 1, text)
    return h, lab


def legend_handles(ctx: Any, handles: list[Any]) -> list[Any]:
    """Legend swatches of nested bands (a fan chart) show the region they
    name: inside a band, every same-coloured band containing it is drawn
    too, so the swatch takes the composited opacity ``1 - prod(1 - a)``."""
    from matplotlib.patches import Patch

    by_artist = {id(r.artist): r for r in ctx.band_records}
    out = []
    for h in handles:
        r = by_artist.get(id(h))
        stack = [o for o in ctx.band_records if o is r or (r is not None and r.inside(o))]
        if r is None or len(stack) < 2:
            out.append(h)
            continue
        alpha = 1.0 - float(np.prod([1.0 - o.alpha for o in stack]))
        out.append(Patch(facecolor=r.rgb, alpha=alpha, hatch=r.hatch,
                         edgecolor=r.rgb if r.hatch else "none", linewidth=0,
                         label=h.get_label()))
    return out


def caption_with_intervals(caption: str | None, legend_shown: bool, labels: list[str]) -> \
        str | None:
    """With the legend hidden, the caption states every interval drawn, each
    as its legend entry would ("A: 95 % confidence interval")."""
    if legend_shown or not labels:
        return caption
    note = "Intervals: " + "; ".join(labels) + "."
    return f"{caption}\n{note}" if caption else note
