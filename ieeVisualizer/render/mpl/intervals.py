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

from typing import Any

import numpy as np

from ...spec.intervals import mark_interval_label
from . import marks as _m
from .data import positions
from .style import forecast_dashes, legend_label

#: Length of an error-bar cap, in points.
CAP_POINTS = 6.0
#: Fraction of a category slot that dodged error-bar groups spread over.
DODGE_SPAN = 0.5


def interval_text(ctx: Any, m: Any) -> str:
    decimal = ctx.locale.decimal if ctx.locale is not None else "."
    text = mark_interval_label(m, decimal)
    if text not in ctx.interval_labels:
        ctx.interval_labels.append(text)
    return text


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
    out = [ctx.ax.fill_between(fx, flo, fhi, where=valid, interpolate=False, **kw)]
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
    out = [ctx.ax.vlines(x, lo, hi, **kw)]
    for ends in (lo, hi):
        out += ctx.ax.plot(x, ends, linestyle="none", marker="_", markersize=CAP_POINTS,
                           markeredgewidth=lw, color=color, alpha=kw.get("alpha"),
                           label="_nolegend_")
    return out


def point_intervals(ctx: Any, i: int, m: Any, xs: Any, rows: np.ndarray, color: Any,
                    label: str | None, style: dict[str, Any]) -> list[Any]:
    """Error bars at ``xs`` for the drawn rows ``rows`` of a scatter or bar
    layer (``xs[k]`` is where row ``rows[k]`` is drawn), where both bounds
    exist."""
    lo, hi = bounds(ctx, m)
    lo, hi = lo[rows], hi[rows]
    xs = np.ma.masked_array(np.asarray(xs, dtype=float))
    _missing(ctx, i, np.ones(len(rows), dtype=bool), lo, hi)
    ok = _m._mask(xs, lo, hi)
    if not ok.any():
        return []
    text = interval_text(ctx, m)
    return error_segments(ctx, np.asarray(xs[ok]), np.asarray(lo[ok]), np.asarray(hi[ok]),
                          color, m.role, _qualified(label, text), style)


def line_band(ctx: Any, i: int, m: Any, x: Any, idx: np.ndarray, color: Any,
              label: str | None, style: dict[str, Any]) -> list[Any]:
    """The band of a line's interval, for the line's rows ``idx``."""
    lo, hi = bounds(ctx, m)
    sub = x[idx], lo[idx], hi[idx]
    _missing(ctx, i, ~np.ma.getmaskarray(sub[0]), sub[1], sub[2])
    if not _m._mask(*sub).any():
        return []
    text = interval_text(ctx, m)
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
    _missing(ctx, i, ~np.ma.getmaskarray(x), lo, hi)
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
    text = interval_text(ctx, m)
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
        label = legend_label(_qualified(_base_label(m, group), text), None, m.role)
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


def caption_with_intervals(caption: str | None, legend_shown: bool, labels: list[str]) -> \
        str | None:
    """With the legend hidden, the caption states every interval drawn."""
    if legend_shown or not labels:
        return caption
    note = "Intervals: " + "; ".join(labels) + "."
    return f"{caption}\n{note}" if caption else note
