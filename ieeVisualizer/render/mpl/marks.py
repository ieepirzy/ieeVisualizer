"""Draw each FigureSpec mark onto a matplotlib Axes.

One function per mark type. Each returns the artists it created so tests (and
a later inspection API) can compare what was drawn with the dataset.

Shared rules:

* A row with a null in a channel the mark needs is not drawn: line-like marks
  show a gap there (masked arrays), point-like marks leave the point out.
  Either way a ``null_values_omitted`` warning says how many rows.
* A series (a layer, or one ``series``/categorical ``color`` group of it) with
  no drawable row is an error (``empty_series``), never an empty or zero line.
* ``color`` is a grouping channel for line, area, bar and histogram when the
  column is categorical (str/bool/int); scatter, heatmap and quiver map a
  numeric ``color`` to a colormap with a labelled colorbar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ...spec import Column, DataSnapshot, FigureSpec, Mark
from ..base import RenderError, RenderWarning, issue
from . import function as fn_mod
from .data import AxisInfo, column_path, groups, null_group_rows, numbers, positions
from .style import (
    APPLICABLE,
    DEFAULT_ROLES,
    FORECAST_SUFFIX,
    Roles,
    colormap,
    fill_kwargs,
    legend_label,
    line_kwargs,
    parse_style,
    roles_for,
)


@dataclass
class _Series:
    color: str
    field: str
    group: str | None
    paired: bool = False  # already shares its colour across observed/forecast


@dataclass
class DrawContext:
    spec: FigureSpec
    fig: Any
    ax: Any
    axes: dict[str, AxisInfo]
    warnings: list[RenderWarning]
    layer_artists: dict[int, list[Any]] = field(default_factory=dict)
    #: Bar value labels as ``(annotation, value channel, layer index)``;
    #: :func:`fit_value_labels` keeps them inside the axes after layout.
    value_labels: list[tuple[Any, str, int]] = field(default_factory=list)
    #: The resolved :class:`~ieeVisualizer.themes.Theme` (None = adapter defaults).
    theme: Any = None
    _color_i: int = 0
    _series: dict[tuple, _Series] = field(default_factory=dict)

    @property
    def three_d(self) -> bool:
        return self.spec.coordinate_system == "cartesian3d"

    @property
    def roles(self) -> Roles:
        return roles_for(self.theme) if self.theme is not None else DEFAULT_ROLES

    @property
    def locale(self) -> Any:
        return getattr(self.theme, "locale", None)

    def muted(self, fallback: str) -> str:
        """Secondary text colour: the theme's, else the adapter's ``fallback``."""
        return getattr(self.theme, "muted", None) or fallback

    def cmap(self, style: dict[str, Any]) -> Any:
        return colormap(style.get("colormap"), self.theme)

    def next_color(self) -> str:
        from matplotlib import rcParams

        cycle = rcParams["axes.prop_cycle"].by_key().get("color", ["C0"])
        if self._color_i == len(cycle):
            self.warn("palette_exhausted",
                      f"the figure has more series than the theme's {len(cycle)} series "
                      "colours; colours repeat from here on, so the legend (not the colour) "
                      "tells these series apart. Split the figure or use fewer series.", "")
        c = cycle[self._color_i % len(cycle)]
        self._color_i += 1
        return c

    def series_color(self, m: Mark, fld: str, group: str | None, explicit: str | None) -> str:
        """Colour of one series: a layer (or one group of it).

        A series is identified by the layer's ``name`` if set, else by its
        dataset and field, plus the group, so two layers that plot the same
        column name from different datasets get different colours. A
        forecast layer shares the colour of its observed series, so it reads
        as that series' continuation: the observed layer with the same
        identity, else the only not-yet-paired observed series with the same
        field and group (the timeseries template names its layers
        ``revenue`` / ``revenue (forecast)`` over datasets ``observed`` /
        ``forecast``). With several candidates the forecast gets its own
        colour rather than a guessed one.
        """
        forecast = m.role == "forecast"
        ident = m.name if m.name else f"{m.data}/{fld}"
        key = (ident, group, forecast)
        if key in self._series:
            if explicit is not None:
                return explicit
            return self._series[key].color
        partner = self._series.get((ident, group, not forecast))
        if partner is None or partner.paired:
            cands = [s for k, s in self._series.items() if k[2] != forecast
                     and s.field == fld and s.group == group and not s.paired]
            partner = cands[0] if len(cands) == 1 else None
        if partner is not None:
            partner.paired = True
        color = explicit if explicit is not None else (
            partner.color if partner is not None else self.next_color())
        self._series[key] = _Series(color, fld, group, paired=partner is not None)
        return color

    def warn(self, code: str, message: str, path: str) -> None:
        self.warnings.append(RenderWarning(code, message, path))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _snapshot(ctx: DrawContext, m: Mark) -> DataSnapshot:
    return ctx.spec.datasets[m.data]  # prepare() guarantees a resolved snapshot


def _col(ctx: DrawContext, m: Mark, ch: str) -> Column | None:
    enc = m.encoding.get(ch)
    return None if enc is None else _snapshot(ctx, m).columns[enc.field]


def _cpath(ctx: DrawContext, m: Mark, ch: str) -> str:
    return column_path(ctx.spec, m.data, m.encoding[ch].field)


def _pos(ctx: DrawContext, m: Mark, ch: str) -> np.ma.MaskedArray:
    return positions(_col(ctx, m, ch), ctx.axes[ch], _cpath(ctx, m, ch))


def _num(ctx: DrawContext, m: Mark, ch: str) -> np.ma.MaskedArray:
    return numbers(_col(ctx, m, ch), _cpath(ctx, m, ch))


def _style(ctx: DrawContext, i: int, m: Mark) -> dict[str, Any]:
    return parse_style(m.style, APPLICABLE[m.type], f"/layers/{i}/style",
                       f"a {m.type} mark", ctx.warnings)


def _title(m: Mark, ch: str) -> str | None:
    enc = m.encoding.get(ch)
    if enc is None:
        return None
    return enc.title or enc.field


def _group_col(ctx: DrawContext, i: int, m: Mark) -> Column | None:
    """``series`` column, else a categorical ``color`` column, else None."""
    if "series" in m.encoding:
        return _col(ctx, m, "series")
    col = _col(ctx, m, "color")
    if col is None:
        return None
    if col.dtype == "float":
        ctx.warn("color_channel_ignored",
                 f"a {m.type} mark colours by category; the float column "
                 f"{m.encoding['color'].field!r} cannot group rows and was ignored. Use a "
                 "scatter for a continuous colour scale.", f"/layers/{i}/encoding/color")
        return None
    return col


def _group_channel(m: Mark) -> str | None:
    return "series" if "series" in m.encoding else ("color" if "color" in m.encoding else None)


def _groups(ctx: DrawContext, i: int, m: Mark, n: int):
    gcol = _group_col(ctx, i, m)
    dropped = null_group_rows(gcol)
    if dropped:
        ch = _group_channel(m)
        ctx.warn("null_values_omitted",
                 f"{dropped} row(s) have no {ch} value and belong to no series; they were "
                 "left out.", f"/layers/{i}/encoding/{ch}")
    return groups(gcol, n)


def _label(m: Mark, group: str | None, fallback: str | None) -> str | None:
    if group is None:
        return legend_label(m.name, fallback, m.role)
    base = f"{m.name}: {group}" if m.name else group
    return legend_label(base, None, m.role)


def _require(valid: np.ndarray, i: int, m: Mark, group: str | None) -> None:
    if not valid.any():
        what = f"series {group!r} of layer {i}" if group is not None else f"layer {i}"
        raise RenderError([issue(f"/layers/{i}", "empty_series",
                                 f"{what} ({m.type}) has no row where every required channel "
                                 "has a value, so there is nothing to draw. Fix the data or "
                                 "drop the layer; it is not drawn as an empty or zero series.")])


def _report_nulls(ctx: DrawContext, i: int, valid: np.ndarray, how: str) -> None:
    n = int((~valid).sum())
    if n:
        ctx.warn("null_values_omitted",
                 f"{n} row(s) of layer {i} have null values; they are {how}, not drawn as 0.",
                 f"/layers/{i}")


def _mask(*arrays: np.ma.MaskedArray) -> np.ndarray:
    bad = np.zeros(len(arrays[0]), dtype=bool)
    for a in arrays:
        bad |= np.ma.getmaskarray(a)
    return ~bad


def _isolated(valid: np.ndarray) -> list[int]:
    """Indices of valid points with no valid neighbour (drawn as markers)."""
    prev = np.concatenate([[False], valid[:-1]])
    nxt = np.concatenate([valid[1:], [False]])
    return [int(k) for k in np.flatnonzero(valid & ~prev & ~nxt)]


def _approximate_role(ctx: DrawContext, i: int, m: Mark, kw: dict[str, Any]) -> None:
    if m.role in ("forecast", "reference"):
        kw.setdefault("alpha", ctx.roles.forecast_alpha if m.role == "forecast"
                      else ctx.roles.reference_alpha)
        if m.role == "forecast":
            ctx.warn("role_style_approximate",
                     f"a forecast {m.type} can only be distinguished by transparency (and the "
                     "legend label); consider a caption saying which part is forecast.",
                     f"/layers/{i}/role")


def _colorbar(ctx: DrawContext, mappable: Any, m: Mark, ch: str = "color") -> None:
    col = _col(ctx, m, ch)
    label = _title(m, ch) or ""
    if col is not None and col.unit:
        label = f"{label} ({col.unit})"
    cb = ctx.fig.colorbar(mappable, ax=ctx.ax, label=label)
    if ctx.locale is not None:
        from .decorate import locale_formatter

        cb.ax.yaxis.set_major_formatter(locale_formatter(ctx.locale))


# --------------------------------------------------------------------------
# marks
# --------------------------------------------------------------------------


def draw_line(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    x, y = _pos(ctx, m, "x"), _pos(ctx, m, "y")
    z = _pos(ctx, m, "z") if ctx.three_d else None
    chans = [x, y] + ([z] if z is not None else [])
    _report_nulls(ctx, i, _mask(*chans), "gaps in the line")
    artists = []
    for group, idx in _groups(ctx, i, m, len(x)):
        sub = [a[idx] for a in chans]
        _require(_mask(*sub), i, m, group)
        kw = line_kwargs(style, m.role, f"/layers/{i}", ctx.warnings, ctx.roles)
        if m.role != "reference":
            kw["color"] = ctx.series_color(m, m.encoding["y"].field, group, kw.get("color"))
        kw["label"] = _label(m, group, _title(m, "y"))
        lone = _isolated(_mask(*sub))
        if lone and "marker" not in kw:
            # A valid point between two gaps has no segment; mark it so it is
            # not invisible.
            kw.update(marker="o", markevery=lone, markersize=3.5)
        if ctx.three_d:
            xs, ys, zs = (np.ma.filled(a.astype(float), np.nan) for a in sub)
            artists += ctx.ax.plot(xs, ys, zs, **kw)
        else:
            artists += ctx.ax.plot(sub[0], sub[1], **kw)
    return artists


def draw_scatter(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    x, y = _pos(ctx, m, "x"), _pos(ctx, m, "y")
    z = _pos(ctx, m, "z") if ctx.three_d else None
    size = _num(ctx, m, "size") if "size" in m.encoding else None
    ccol = _col(ctx, m, "color")
    cont = ccol is not None and ccol.dtype in ("float", "int") and "series" not in m.encoding
    cvals = _num(ctx, m, "color") if cont else None
    chans = [a for a in (x, y, z, size, cvals) if a is not None]
    _report_nulls(ctx, i, _mask(*chans), "left out")
    s_all = None
    if size is not None:
        sv = size.compressed()
        lo, hi = (float(sv.min()), float(sv.max())) if sv.size else (0.0, 0.0)
        s_all = np.ma.masked_array(
            np.full(len(size), 60.0) if hi == lo else 20.0 + 180.0 * (size.filled(lo) - lo)
            / (hi - lo), mask=np.ma.getmaskarray(size))
    artists = []
    grouping = (lambda: [(None, np.arange(len(x)))]) if cont else (lambda: _groups(ctx, i, m,
                                                                                    len(x)))
    for group, idx in grouping():
        valid = _mask(*[a[idx] for a in chans])
        _require(valid, i, m, group)
        rows = idx[valid]
        kw: dict[str, Any] = {"label": _label(m, group, _title(m, "y"))}
        if "marker" in style:
            kw["marker"] = style["marker"]
        if "opacity" in style:
            kw["alpha"] = style["opacity"]
        if s_all is not None:
            kw["s"] = np.asarray(s_all[rows])
        if cont:
            kw["c"] = np.asarray(cvals[rows])
            kw["cmap"] = ctx.cmap(style)
            if m.role != "observed":
                _approximate_role(ctx, i, m, kw)
        else:
            color = style.get("color")
            if m.role == "reference":
                color = color or ctx.roles.reference_color
                kw.setdefault("alpha", ctx.roles.reference_alpha)
            else:
                color = ctx.series_color(m, m.encoding["y"].field, group, color)
            if m.role == "forecast":  # hollow markers
                kw["facecolors"] = "none"
                kw["edgecolors"] = color
            else:
                kw["color"] = color
        coords = [np.asarray(x[rows]), np.asarray(y[rows])]
        if z is not None:
            coords.append(np.asarray(z[rows]))
        art = ctx.ax.scatter(*coords, **kw)
        artists.append(art)
        if cont:
            _colorbar(ctx, art, m)
    return artists


def draw_area(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    x, y = _pos(ctx, m, "x"), _pos(ctx, m, "y")
    _report_nulls(ctx, i, _mask(x, y), "gaps in the area")
    artists = []
    for group, idx in _groups(ctx, i, m, len(x)):
        xs, ys = x[idx], y[idx]
        valid = _mask(xs, ys)
        _require(valid, i, m, group)
        kw = fill_kwargs(style, m.role, ctx.roles)
        if m.role != "reference":
            kw["color"] = ctx.series_color(m, m.encoding["y"].field, group, kw.get("color"))
        if m.role == "forecast":
            kw.setdefault("edgecolor", kw["color"])
            kw["facecolor"] = "none"
            kw.pop("color")
        kw["label"] = _label(m, group, _title(m, "y"))
        fx, fy = np.ma.filled(xs.astype(float), np.nan), np.ma.filled(ys.astype(float), np.nan)
        artists.append(ctx.ax.fill_between(fx, fy, 0.0, where=valid, interpolate=False, **kw))
        lone = _isolated(valid)
        if lone:
            # A valid point between two gaps fills a zero-width polygon, i.e.
            # nothing; mark it (as the line mark does) so the value is visible.
            c = kw.get("color", kw.get("edgecolor"))
            artists += ctx.ax.plot(fx[lone], fy[lone], linestyle="none", marker="o",
                                   markersize=3.5, color=c, label="_nolegend_",
                                   markerfacecolor="none" if m.role == "forecast" else c,
                                   alpha=kw.get("alpha"))
    return artists


def _width(pos: np.ndarray, info: AxisInfo, polar: bool) -> float:
    if info.kind == "category":
        return 0.8
    u = np.unique(pos)
    if u.size < 2:
        return 0.8 if not polar else 2 * np.pi / 8 * 0.8
    return 0.8 * float(np.min(np.diff(u)))


def draw_bar(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    horizontal = getattr(m, "orientation", "vertical") == "horizontal"
    cat_ch, val_ch = ("y", "x") if horizontal else ("x", "y")
    if ctx.axes[val_ch].kind != "numeric":
        raise RenderError([issue(f"/layers/{i}/encoding/{val_ch}", "unsupported_dtype",
                                 f"bar lengths come from {val_ch}, which must be numeric here "
                                 f"(it is {ctx.axes[val_ch].kind}). For horizontal bars put "
                                 "the categories on y and set orientation 'horizontal'.")])
    pos, val = _pos(ctx, m, cat_ch), _pos(ctx, m, val_ch)
    _report_nulls(ctx, i, _mask(pos, val), "left out (no bar)")
    grp = _groups(ctx, i, m, len(pos))
    polar = ctx.spec.coordinate_system == "polar"
    total = _width(np.asarray(pos.compressed()), ctx.axes[cat_ch], polar)
    shift = 0.0
    if "bar_width" in style or "bar_offset" in style:
        # explicit placement in fractions of the category slot (the default
        # bar group fills 0.8 of it), e.g. side-by-side layers
        slot = total / 0.8
        total = slot * style.get("bar_width", 0.8)
        shift = slot * style.get("bar_offset", 0.0)
    each = total / len(grp)
    artists = []
    for g, (group, idx) in enumerate(grp):
        p, v = pos[idx], val[idx]
        valid = _mask(p, v)
        _require(valid, i, m, group)
        off = (g - (len(grp) - 1) / 2.0) * each + shift
        kw = fill_kwargs(style, m.role, ctx.roles)
        if m.role != "reference":
            kw["color"] = ctx.series_color(m, m.encoding[val_ch].field, group,
                                           kw.get("color"))
        if m.role == "forecast":
            kw["edgecolor"] = kw["color"]
        kw["label"] = _label(m, group, _title(m, val_ch))
        pp, vv = np.asarray(p[valid]) + off, np.asarray(v[valid])
        if horizontal:
            artists.append(ctx.ax.barh(pp, vv, height=each, **kw))
        else:
            artists.append(ctx.ax.bar(pp, vv, width=each, **kw))
        if style.get("value_labels"):
            labels = _value_labels(ctx, m, val_ch, pp, vv, horizontal)
            ctx.value_labels += [(a, val_ch, i) for a in labels]
            artists += labels
            ctx.ax.margins(**{val_ch: 0.12})  # room for the labels inside the axes
        # A labelled slot with no bar reads as zero; say "no data" there.
        missing = ~np.ma.getmaskarray(p) & np.ma.getmaskarray(v)
        # On a log value axis 0 is not a position; anchor the note to the
        # edge of the plot instead, or it is silently not drawn.
        val_axis = ctx.spec.axes.get(val_ch)
        log = val_axis is not None and val_axis.scale == "log"
        coords = "data" if not log else (("axes fraction", "data") if horizontal
                                         else ("data", "axes fraction"))
        for q in np.asarray(p[missing].filled(np.nan)) + off:
            xy = (0.0, q) if horizontal else (q, 0.0)
            artists.append(ctx.ax.annotate(
                "no data", xy, xycoords=coords, xytext=(3, 0) if horizontal else (0, 3),
                textcoords="offset points", ha="left" if horizontal else "center",
                va="center" if horizontal else "bottom", rotation=0 if horizontal else 90,
                fontsize="x-small", color=ctx.muted("0.4"), style="italic"))
    return artists


def _value_labels(ctx: DrawContext, m: Mark, val_ch: str, pos: np.ndarray,
                  vals: np.ndarray, horizontal: bool) -> list[Any]:
    """Each drawn bar's value at its end (outside, so it never sits on the
    fill colour), formatted with the theme's locale; currency and percent
    keep their symbol, other units are on the axis label."""
    from ...themes import NEUTRAL, format_quantity

    unit = _col(ctx, m, val_ch).unit
    out = []
    for q, v in zip(pos, vals):
        text = format_quantity(float(v), unit, ctx.locale or NEUTRAL)
        neg = v < 0
        if horizontal:
            kw = {"xytext": (-3 if neg else 3, 0), "ha": "right" if neg else "left",
                  "va": "center"}
            xy = (float(v), float(q))
        else:
            kw = {"xytext": (0, -3 if neg else 3), "ha": "center",
                  "va": "top" if neg else "bottom"}
            xy = (float(q), float(v))
        out.append(ctx.ax.annotate(text, xy, textcoords="offset points", fontsize="small",
                                   color=ctx.muted("0.25"), **kw))
    return out


#: Space kept between a value label and the axes edge, in points.
VALUE_LABEL_PAD_PT = 2.0


def fit_value_labels(ctx: DrawContext, rounds: int = 4) -> None:
    """Widen the value axis at draw time until every bar value label lies
    inside the axes.

    A label is drawn at a fixed offset (in points) beyond its bar's end, so
    the longest bar's label can run past the axes and off the image; the
    12 % margin set when drawing is a guess at text width. Only after layout
    are the axes' size and the text extents known, so the figure's
    constrained-layout engine is replaced by one that, after laying out,
    measures each label and moves the end of the value axis it overruns out
    just far enough (in the axis' scale space, so log axes work too), then
    lays out again (new limits mean new tick labels); a few rounds at most.
    This runs on every draw at the size and dpi actually drawn. An end the
    spec fixed with ``axes.<ch>.limits`` is not moved; a label that still
    does not fit is reported with a ``value_label_outside_axes`` warning
    instead of silently cut off. When the labels already fit, the layout
    runs exactly once, as before, so the output is unchanged byte for byte.
    """
    if not ctx.value_labels or ctx.three_d or ctx.spec.coordinate_system == "polar":
        return
    from matplotlib.layout_engine import ConstrainedLayoutEngine

    fig = ctx.fig
    old = fig.get_layout_engine()
    if not isinstance(old, ConstrainedLayoutEngine):
        return
    reported: set[int] = set()

    class ValueLabelLayout(ConstrainedLayoutEngine):
        def execute(self, figure: Any) -> Any:
            out = super().execute(figure)
            pad = VALUE_LABEL_PAD_PT * figure.dpi / 72.0
            for _ in range(rounds):
                moved = False
                for ch in sorted({c for _, c, _ in ctx.value_labels}):
                    moved |= _fit_axis(ctx, ch, [a for a, c, _ in ctx.value_labels
                                                 if c == ch], pad)
                if not moved:
                    break
                out = super().execute(figure)
            _report_unfitted(ctx, reported)
            return out

    new = ValueLabelLayout()
    new.set(**old.get())
    fig.set_layout_engine(new)


def _report_unfitted(ctx: DrawContext, reported: set[int]) -> None:
    box = ctx.ax.get_window_extent()
    for a, _, i in ctx.value_labels:
        if not a.get_visible() or id(a) in reported:
            continue
        b = a.get_window_extent()
        if b.x0 < box.x0 - 0.5 or b.x1 > box.x1 + 0.5 or b.y0 < box.y0 - 0.5 \
                or b.y1 > box.y1 + 0.5:
            reported.add(id(a))
            ctx.warn("value_label_outside_axes",
                     f"the value label {a.get_text()!r} of layer {i} does not fit inside the "
                     "plot (fixed axis limits or a very small figure).", f"/layers/{i}/style")


def _fit_axis(ctx: DrawContext, ch: str, labels: list[Any], pad: float) -> bool:
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


def _further(current: float, wanted: float, other: float) -> float:
    """The one of ``current``/``wanted`` further from ``other``."""
    return wanted if abs(wanted - other) > abs(current - other) else current


def draw_histogram(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    if ctx.axes["x"].kind == "category":
        raise RenderError([issue(f"/layers/{i}/encoding/x", "unsupported_dtype",
                                 "a histogram bins numeric or datetime values; for counts per "
                                 "category aggregate upstream and use a bar mark.")])
    x = _pos(ctx, m, "x")
    _report_nulls(ctx, i, _mask(x), "left out of the counts")
    grp = _groups(ctx, i, m, len(x))
    data = []
    for group, idx in grp:
        xs = x[idx]
        _require(_mask(xs), i, m, group)
        data.append((group, np.asarray(xs.compressed(), dtype=float)))
    values = np.concatenate([d for _, d in data])
    nbins = m.bins if m.bins is not None else 10
    if m.bin_edges is not None:
        edges = np.asarray(m.bin_edges, dtype=float)
    elif "x" in ctx.spec.axes and ctx.spec.axes["x"].scale == "log" and values.min() > 0 \
            and values.max() > values.min():
        # equal-width bins on a log axis: geometric edges
        edges = np.geomspace(values.min(), values.max(), nbins + 1)
    else:
        edges = np.histogram_bin_edges(values, bins=nbins)
    outside = sum(int(((d < edges[0]) | (d > edges[-1])).sum()) for _, d in data)
    if outside:
        ctx.warn("values_outside_bins",
                 f"{outside} value(s) fall outside bin_edges [{edges[0]}, {edges[-1]}] and are "
                 "not counted.", f"/layers/{i}/bin_edges")
    artists = []
    for group, d in data:
        kw = fill_kwargs(style, m.role, ctx.roles)
        if m.role != "reference":
            kw["color"] = ctx.series_color(m, m.encoding["x"].field, group, kw.get("color"))
        if m.role == "forecast":
            kw["edgecolor"] = kw["color"]
        if len(data) > 1:
            kw.setdefault("alpha", 0.5)
        kw["label"] = _label(m, group, _title(m, "x"))
        _, _, patches = ctx.ax.hist(d, bins=edges, density=m.density, **kw)
        artists.append(patches)
    return artists


def _edges(centres: np.ndarray) -> np.ndarray:
    if centres.size == 1:
        return np.array([centres[0] - 0.5, centres[0] + 0.5])
    mid = (centres[:-1] + centres[1:]) / 2.0
    return np.concatenate([[centres[0] - (mid[0] - centres[0])], mid,
                           [centres[-1] + (centres[-1] - mid[-1])]])


def draw_heatmap(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    x, y, c = _pos(ctx, m, "x"), _pos(ctx, m, "y"), _num(ctx, m, "color")
    placed = _mask(x, y)
    if (~placed).any():
        ctx.warn("null_values_omitted",
                 f"{int((~placed).sum())} cell(s) of layer {i} have no x or y position and "
                 "were left out.", f"/layers/{i}")
    _require(_mask(x, y, c), i, m, None)
    xs, ys = np.asarray(x[placed]), np.asarray(y[placed])
    cs = c[placed]
    xu, yu = np.unique(xs), np.unique(ys)
    grid = np.ma.masked_all((yu.size, xu.size))
    seen = np.zeros(grid.shape, dtype=bool)
    xi, yi = np.searchsorted(xu, xs), np.searchsorted(yu, ys)
    for k in range(xs.size):
        r, q = yi[k], xi[k]
        if seen[r, q]:
            raise RenderError([issue(f"/layers/{i}", "duplicate_cell",
                                     f"layer {i} has more than one row for cell "
                                     f"(x={xs[k]}, y={ys[k]}); aggregate upstream so each cell "
                                     "has one value.")])
        seen[r, q] = True
        if not np.ma.is_masked(cs[k]):
            grid[r, q] = cs[k]
    blank = int(grid.mask.sum()) if np.ma.is_masked(grid) else 0
    if blank:
        ctx.warn("null_values_omitted",
                 f"{blank} cell(s) of layer {i} have no value (null or absent); they are left "
                 "blank, not coloured as 0.", f"/layers/{i}")
    kw: dict[str, Any] = {"cmap": ctx.cmap(style)}
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    _approximate_role(ctx, i, m, kw)
    mesh = ctx.ax.pcolormesh(_edges(xu), _edges(yu), grid, shading="flat", **kw)
    _colorbar(ctx, mesh, m)
    return [mesh]


def _cell_scale(a: dict[str, np.ndarray]) -> float:
    """Data units per unit of |(u, v)| so the longest arrow spans one grid
    cell: the smaller of the x and y extents divided by (distinct positions
    - 1). Falls back to the other axis, then to 1, when an axis has a single
    position; 1 when every vector is zero."""
    cells = []
    for k in ("x", "y"):
        u = np.unique(a[k])
        if u.size > 1:
            cells.append((u[-1] - u[0]) / (u.size - 1))
    cell = min(cells) if cells else 1.0
    mmax = float(np.max(np.hypot(a["u"], a["v"])))
    return cell / mmax if mmax > 0 else 1.0


def draw_quiver(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    chans = {ch: (_pos(ctx, m, ch) if ch in ("x", "y", "z") else _num(ctx, m, ch))
             for ch in (("x", "y", "z", "u", "v", "w") if ctx.three_d else ("x", "y", "u", "v"))}
    cvals = _num(ctx, m, "color") if "color" in m.encoding else None
    allc = list(chans.values()) + ([cvals] if cvals is not None else [])
    valid = _mask(*allc)
    _report_nulls(ctx, i, valid, "left out (no arrow)")
    _require(valid, i, m, None)
    a = {k: np.asarray(v[valid], dtype=float) for k, v in chans.items()}
    comps = ("u", "v", "w") if ctx.three_d else ("u", "v")
    if m.normalize:
        mag = np.sqrt(sum(a[k] ** 2 for k in comps))
        nz = mag > 0
        for k in comps:
            a[k] = np.where(nz, a[k] / np.where(nz, mag, 1.0), 0.0)
    kw: dict[str, Any] = {}
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    if m.name:
        kw["label"] = legend_label(m.name, None, m.role)
    _approximate_role(ctx, i, m, kw)
    if ctx.three_d:
        if cvals is not None:
            ctx.warn("color_channel_ignored", "3D quiver arrows are drawn in one colour; the "
                     "color channel was ignored.", f"/layers/{i}/encoding/color")
        kw["color"] = style.get("color", ctx.next_color())
        length = m.scale if m.scale is not None else 1.0
        q = ctx.ax.quiver(a["x"], a["y"], a["z"], a["u"], a["v"], a["w"],
                          length=length, normalize=False, **kw)
        # include the arrow tips in the view, not only the tails
        ctx.ax.auto_scale_xyz(*(np.concatenate([a[p], a[p] + length * a[c]])
                                for p, c in (("x", "u"), ("y", "v"), ("z", "w"))),
                              had_data=True)
        return [q]
    # spec: arrow length in data units = scale * |(u, v)|. Without a scale the
    # longest arrow spans one grid cell (the rule visualize_vf uses in 3D), so
    # arrows neither overlap their neighbours nor run out of the plot: the
    # view is widened to include every arrow tip, which matplotlib's own
    # autoscaling (sized at draw time, tails only) does not do.
    length = m.scale if m.scale is not None else _cell_scale(a)
    kw.update(angles="xy", scale_units="xy", scale=1.0 / length)
    tips_x, tips_y = a["x"] + length * a["u"], a["y"] + length * a["v"]
    ctx.ax.update_datalim(np.column_stack([np.concatenate([a["x"], tips_x]),
                                           np.concatenate([a["y"], tips_y])]))
    args = [a["x"], a["y"], a["u"], a["v"]]
    if cvals is not None:
        args.append(np.asarray(cvals[valid], dtype=float))
        kw["cmap"] = ctx.cmap(style)
    else:
        kw["color"] = style.get("color", ctx.next_color())
    q = ctx.ax.quiver(*args, **kw)
    if cvals is not None:
        _colorbar(ctx, q, m)
    return [q]


def _sampled_surface(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    """A sampled ``function`` in 3D is ``z = f(x, y)``: a surface, like the
    symbolic form, not a polyline zig-zagging through the grid. Its rows must
    form a complete rectilinear grid (one row per (x, y) pair, any order;
    unevenly spaced axes are fine). A null z is a hole in the surface; a null
    x or y cannot be placed and is an error."""
    path = f"/layers/{i}"
    if "series" in m.encoding:
        raise RenderError([issue(f"{path}/encoding/series", "unsupported_channel",
                                 "a sampled 3D function is drawn as one surface; split series "
                                 "into separate layers.")])
    style = _style(ctx, i, m)
    x, y, z = _pos(ctx, m, "x"), _pos(ctx, m, "y"), _pos(ctx, m, "z")
    if np.ma.getmaskarray(x).any() or np.ma.getmaskarray(y).any():
        raise RenderError([issue(f"{path}/encoding/x", "null_in_key_column",
                                 f"layer {i} has rows with a null x or y; a surface point "
                                 "cannot be placed without both. Drop those rows upstream.")])
    xs, ys = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    xu, yu = np.unique(xs), np.unique(ys)
    xi, yi = np.searchsorted(xu, xs), np.searchsorted(yu, ys)
    seen = np.zeros((yu.size, xu.size), dtype=int)
    np.add.at(seen, (yi, xi), 1)
    if xu.size < 2 or yu.size < 2 or not (seen == 1).all():
        raise RenderError([issue(path, "not_a_grid",
                                 f"layer {i} is a sampled 3D function, drawn as a surface "
                                 "z = f(x, y), so its rows must cover every (x, y) pair of a "
                                 f"grid exactly once (got {xs.size} rows over {xu.size} x and "
                                 f"{yu.size} y values). For a 3D curve use a line mark.")])
    _require(_mask(z), i, m, None)
    _report_nulls(ctx, i, _mask(z), "holes in the surface")
    grid = np.full(seen.shape, np.nan)
    grid[yi, xi] = np.ma.filled(z.astype(float), np.nan)
    gx, gy = np.meshgrid(xu, yu, indexing="xy")
    kw: dict[str, Any] = {"cmap": ctx.cmap(style)}
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    _approximate_role(ctx, i, m, kw)
    return [ctx.ax.plot_surface(gx, gy, grid, **kw)]


def draw_function(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    if m.expression is None:  # sampled form: a surface in 3D, else drawn like a line
        if ctx.three_d:
            return _sampled_surface(ctx, i, m)
        return draw_line(ctx, i, m)
    style = _style(ctx, i, m)
    path = f"/layers/{i}"
    keys = ("x", "y") if ctx.three_d else ("x",)
    logs = tuple(ctx.spec.axes[k].scale == "log" if k in ctx.spec.axes else False for k in keys)
    inputs, out = fn_mod.sample(m, path, logs, ctx.warnings)
    if ctx.three_d:
        kw: dict[str, Any] = {"cmap": ctx.cmap(style)}
        if "opacity" in style:
            kw["alpha"] = style["opacity"]
        _approximate_role(ctx, i, m, kw)
        surf = ctx.ax.plot_surface(inputs[0], inputs[1], np.ma.filled(out, np.nan), **kw)
        return [surf]
    for k in ("colormap",):
        if k in style:
            ctx.warn("style_key_ignored", f"a 2D function curve does not use {k!r}.",
                     f"{path}/style/{k}")
    kw = line_kwargs(style, m.role, path, ctx.warnings, ctx.roles)
    if m.role != "reference":
        kw["color"] = ctx.series_color(m, f"function|{m.expression}", None,
                                       kw.get("color"))
    kw["label"] = legend_label(m.name, m.expression, m.role)
    return ctx.ax.plot(inputs[0], out, **kw)


DRAW = {
    "line": draw_line,
    "scatter": draw_scatter,
    "area": draw_area,
    "bar": draw_bar,
    "histogram": draw_histogram,
    "heatmap": draw_heatmap,
    "quiver": draw_quiver,
    "function": draw_function,
}

__all__ = ["DRAW", "DrawContext", "FORECAST_SUFFIX"]
