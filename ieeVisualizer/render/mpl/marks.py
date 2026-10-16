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
from .value_labels import NoDataNote, ValueLabel, fit_value_labels  # noqa: F401 (re-export)


@dataclass
class _Series:
    color: str
    field: str
    group: str | None
    paired: bool = False  # already shares its colour across observed/forecast
    cycle_round: int = 0  # how often the colour cycle had wrapped when assigned


@dataclass
class DrawContext:
    spec: FigureSpec
    fig: Any
    ax: Any
    axes: dict[str, AxisInfo]
    warnings: list[RenderWarning]
    layer_artists: dict[int, list[Any]] = field(default_factory=dict)
    #: Bar value labels and "no data" notes; :func:`fit_value_labels`
    #: arranges them after layout (see :mod:`.value_labels`).
    value_labels: list[ValueLabel] = field(default_factory=list)
    no_data_notes: list[NoDataNote] = field(default_factory=list)
    #: Category-axis centres of every bar and bar-less slot, by value channel.
    bar_centres: dict[str, np.ndarray] = field(default_factory=dict)
    #: Every drawn bar group (:func:`report_bar_overlaps`), the layers found
    #: to overlap, and the legend handles of dodged horizontal bars.
    bar_spans: list[Any] = field(default_factory=list)
    overlapping_bar_layers: set = field(default_factory=set)
    dodged_horizontal: set = field(default_factory=set)
    #: The resolved :class:`~ieeVisualizer.themes.Theme` (None = adapter defaults).
    theme: Any = None
    _color_i: int = 0
    _series: dict[tuple, _Series] = field(default_factory=dict)
    _color_of: dict[int, int | None] = field(default_factory=dict)
    #: Colour-cycle round of the series last returned by :meth:`layer_color`
    #: (0 = first pass through the cycle).
    last_round: int = 0
    _wrap_warned: bool = False
    _next_round: int = 0

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

    def next_color(self, path: str = "") -> str:
        """Next colour of the categorical cycle (the theme's palette). When the
        cycle wraps, colours repeat; that is reported once
        (``colour_cycle_wrapped``) and lines and scatter points of later
        rounds are told apart by linestyle / marker (see :func:`_wrap_variant`)."""
        from matplotlib import rcParams

        cycle = rcParams["axes.prop_cycle"].by_key().get("color", ["C0"])
        c = cycle[self._color_i % len(cycle)]
        self._next_round = self._color_i // len(cycle)
        self._color_i += 1
        if self._next_round and not self._wrap_warned:
            self._wrap_warned = True
            self.warn("colour_cycle_wrapped",
                      f"the figure has more than {len(cycle)} series, so series colours repeat; "
                      "lines of later rounds are dashed/dotted and scatter markers change "
                      "shape, other marks repeat colours. Give explicit colours or split the "
                      "figure.", path)
        return c

    def series_color(self, m: Mark, fld: str, group: str | None, explicit: str | None,
                     path: str = "") -> str:
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
            self.last_round = 0 if explicit is not None else self._series[key].cycle_round
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
        rnd = 0
        if explicit is not None:
            color = explicit
        elif partner is not None:
            color, rnd = partner.color, partner.cycle_round
        else:
            color = self.next_color(path)
            rnd = self._next_round
        self._series[key] = _Series(color, fld, group, paired=partner is not None,
                                    cycle_round=rnd)
        self.last_round = rnd
        return color

    def layer_color(self, i: int, m: Mark, group: str | None, style: dict[str, Any],
                    explicit: str | None) -> str:
        """Colour of series ``group`` of layer ``i``: :meth:`series_color`, or
        with a ``color_of`` style hint the colour of the named layer's series
        with the same group (so a bound line matches the value it bounds)."""
        target = self._color_of_target(i, m, style, explicit)
        if target is None:
            return self.series_color(m, _series_field(m), group, explicit, f"/layers/{i}")
        t = self.spec.layers[target]
        tcolor = t.style.get("color")
        tcolor = tcolor if isinstance(tcolor, str) and _color_like(tcolor) else None
        tgroup = group if _group_channel(t) is not None else None
        return self.series_color(t, _series_field(t), tgroup, tcolor, f"/layers/{i}")

    def _color_of_target(self, i: int, m: Mark, style: dict[str, Any],
                         explicit: str | None) -> int | None:
        if "color_of" not in style:
            return None
        if i in self._color_of:
            return self._color_of[i]
        ref, p = style["color_of"], f"/layers/{i}/style/color_of"
        found = [k for k, t in enumerate(self.spec.layers) if t.name == ref and k != i]
        target = None
        if explicit is not None:
            self.warn("style_key_ignored", "the layer gives an explicit color; color_of was "
                      "ignored.", p)
        elif len(found) != 1:
            what = "several layers" if found else "no other layer"
            self.warn("unknown_color_of",
                      f"color_of {ref!r} names {what}; it must match the name of exactly one "
                      "other layer. The layer gets its own colour.", p)
        elif self.spec.layers[found[0]].type not in _SERIES_COLOURED or \
                self.spec.layers[found[0]].role == "reference":
            self.warn("unknown_color_of",
                      f"color_of {ref!r} names a {self.spec.layers[found[0]].type} layer with "
                      f"role {self.spec.layers[found[0]].role!r}, which has no series colour; "
                      "the layer gets its own colour.", p)
        else:
            target = found[0]
        self._color_of[i] = target
        return target

    def warn(self, code: str, message: str, path: str) -> None:
        self.warnings.append(RenderWarning(code, message, path))


_SERIES_COLOURED = ("line", "scatter", "area", "bar", "histogram", "function")


_WRAP_LINESTYLES = ("--", ":", "-.")
_WRAP_MARKERS = ("s", "^", "D", "v", "P")


def _wrap_variant(ctx: DrawContext, m: Mark, style: dict[str, Any], kw: dict[str, Any],
                  key: str) -> None:
    """A series whose colour came from a wrapped colour cycle (round > 0)
    gets a linestyle (``key="linestyle"``) or marker (``key="marker"``) of its
    round, unless the style chose one; forecasts keep their own pattern."""
    rnd = ctx.last_round
    if rnd <= 0 or key in style or m.role != "observed":
        return
    pool = _WRAP_LINESTYLES if key == "linestyle" else _WRAP_MARKERS
    kw[key] = pool[(rnd - 1) % len(pool)]


def _color_like(c: str) -> bool:
    from matplotlib import colors as mcolors

    return mcolors.is_color_like(c)


def _series_field(m: Mark) -> str:
    """The field that identifies a layer's series (with its dataset/name)."""
    if m.type == "function" and getattr(m, "expression", None) is not None:
        return f"function|{m.expression}"
    if m.type == "histogram":
        return m.encoding["x"].field
    if m.type == "bar" and getattr(m, "orientation", "vertical") == "horizontal":
        return m.encoding["x"].field
    return m.encoding["y"].field


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
            kw["color"] = ctx.layer_color(i, m, group, style, kw.get("color"))
            _wrap_variant(ctx, m, style, kw, "linestyle")
        kw["label"] = _label(m, group, _title(m, "y"))
        lone = _isolated(_mask(*sub))
        if lone and "marker" not in kw:
            # A valid point between two gaps has no segment; mark it so it is
            # not invisible.
            kw.update(marker="o", markevery=lone, markersize=3.5)
            if m.role == "forecast":
                kw["markerfacecolor"] = "none"
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
                color = ctx.layer_color(i, m, group, style, color)
                _wrap_variant(ctx, m, style, kw, "marker")
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
            kw["color"] = ctx.layer_color(i, m, group, style, kw.get("color"))
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


def _orientation(m: Mark) -> str:
    return getattr(m, "orientation", "vertical")


def _bar_group_count(ctx: DrawContext, m: Mark, n: int) -> int:
    """Number of bar groups of ``m`` (as :func:`_groups` splits it), without
    emitting its warnings."""
    col = _col(ctx, m, "series") if "series" in m.encoding else _col(ctx, m, "color")
    if "series" not in m.encoding and col is not None and col.dtype == "float":
        col = None
    return len(groups(col, n))


def _explicit_placement(style: dict[str, Any]) -> bool:
    """Whether a bar layer places itself (``bar_width``/``bar_offset``)."""
    return "bar_width" in style or "bar_offset" in style


def _raw_explicit(m: Mark) -> bool:
    """:func:`_explicit_placement` for another layer, judged from its raw
    style with the same validity rules (an invalid value is ignored there
    with a warning, so it does not take the layer out of dodging here)."""
    w, o = m.style.get("bar_width"), m.style.get("bar_offset")
    num = (int, float)
    return (isinstance(w, num) and not isinstance(w, bool) and 0 < w <= 1) or \
        (isinstance(o, num) and not isinstance(o, bool) and -0.5 <= o <= 0.5)


def _bar_slots(ctx: DrawContext, i: int, m: Mark, style: dict[str, Any], cat_ch: str,
               polar: bool) -> tuple[int, int, float, np.ndarray, float]:
    """Where layer ``i``'s bars go among all bar layers of the same orientation.

    Returns ``(first_slot, slots, width, positions, shift)``. Bar layers
    without explicit placement are dodged in *dodge groups*: layers linked
    by a shared category position, directly or through another layer of the
    group (A shares a category with B, B one with C: A, B and C are one
    group). A group's layers sit side by side in layer order (each layer's
    groups in their own order) at every position of the group, across a slot
    width computed from all their positions, and ``positions`` (every slot
    any of them draws at) is where a missing bar is labelled "no data". A
    layer that shares no position with another is a group of its own and is
    laid out exactly as a single bar layer, with no slot (and no "no data"
    note) at the positions of other groups. Grouping transitively keeps the
    slot count of every member the same, so no two members' bars overlap.

    A layer with ``bar_width``/``bar_offset`` is placed explicitly instead:
    its groups fill ``bar_width`` (default 0.8) of the category slot,
    centred ``bar_offset`` slots from the category, and it takes no part in
    the dodging of the other layers (``shift`` is that offset in data units).
    """
    own = np.asarray(_pos(ctx, m, cat_ch).compressed())
    info = ctx.axes[cat_ch]
    if _explicit_placement(style):
        slot = _width(own, info, polar) / 0.8
        n = _bar_group_count(ctx, m, len(_col(ctx, m, cat_ch).values))
        return (0, n, slot * style.get("bar_width", 0.8), np.unique(own),
                slot * style.get("bar_offset", 0.0))
    peers = [(k, b) for k, b in enumerate(ctx.spec.layers)
             if b.type == "bar" and _orientation(b) == _orientation(m)
             and (k == i or not _raw_explicit(b))]
    pos = {k: (own if k == i else np.asarray(_pos(ctx, b, cat_ch).compressed()))
           for k, b in peers}
    group = _dodge_group(i, [k for k, _ in peers], pos)
    if len(group) == 1:
        n = _bar_group_count(ctx, m, len(_col(ctx, m, cat_ch).values))
        return 0, n, _width(own, info, polar), np.unique(own), 0.0
    layers = dict(peers)
    counts = {k: _bar_group_count(ctx, layers[k], len(_col(ctx, layers[k], cat_ch).values))
              for k in group}
    first = sum(c for k, c in counts.items() if k < i)
    union = np.concatenate([pos[k] for k in group])
    return first, sum(counts.values()), _width(union, info, polar), np.unique(union), 0.0


def _dodge_group(i: int, keys: list[int], pos: dict[int, np.ndarray]) -> list[int]:
    """Layers (in layer order) linked to layer ``i`` by shared positions,
    directly or through other layers; ``[i]`` when it shares none."""
    group, todo = {i}, [i]
    while todo:
        a = todo.pop()
        for b in keys:
            if b not in group and np.intersect1d(pos[a], pos[b]).size:
                group.add(b)
                todo.append(b)
    return sorted(group)


def draw_bar(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    style = _style(ctx, i, m)
    horizontal = _orientation(m) == "horizontal"
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
    first, slots, total, everywhere, shift = _bar_slots(ctx, i, m, style, cat_ch, polar)
    each = total / slots
    labelled = bool(style.get("value_labels"))
    if labelled and (polar or ctx.three_d):
        labelled = False
        ctx.warn("style_key_ignored",
                 f"value_labels are drawn on 2D cartesian bars only; in "
                 f"{ctx.spec.coordinate_system} they could not be kept clear of the fill and "
                 "the axis labels, so they were not drawn. Read the values from the axis.",
                 f"/layers/{i}/style/value_labels")
    artists = []
    for g, (group, idx) in enumerate(grp):
        p, v = pos[idx], val[idx]
        valid = _mask(p, v)
        _require(valid, i, m, group)
        off = (first + g - (slots - 1) / 2.0) * each + shift
        kw = fill_kwargs(style, m.role, ctx.roles)
        if m.role != "reference":
            kw["color"] = ctx.layer_color(i, m, group, style, kw.get("color"))
        if m.role == "forecast":
            kw["edgecolor"] = kw["color"]
        kw["label"] = _label(m, group, _title(m, val_ch))
        pp, vv = np.asarray(p[valid]) + off, np.asarray(v[valid])
        if horizontal:
            artists.append(ctx.ax.barh(pp, vv, height=each, **kw))
        else:
            artists.append(ctx.ax.bar(pp, vv, width=each, **kw))
        ctx.bar_spans.append(_BarSpans(i, _explicit_placement(style), _orientation(m), pp,
                                       float(each), vv))
        if horizontal and slots > 1 and not _explicit_placement(style):
            ctx.dodged_horizontal.add(id(artists[-1]))
        _record_centres(ctx, val_ch, pp)
        if labelled:
            labels = _value_labels(ctx, m, val_ch, pp, vv, horizontal)
            ctx.value_labels += [ValueLabel(a, val_ch, i, float(q), float(each), bool(v < 0),
                                            float(a.get_fontsize()))
                                 for a, q, v in zip(labels, pp, vv)]
            artists += labels
            ctx.ax.margins(**{val_ch: 0.12})  # room for the labels inside the axes
        # A labelled slot with no bar reads as zero; say "no data" there: rows
        # whose value is null, and slots (a position some bar of this layer or
        # of a layer dodged with it is drawn at) this group has no row for.
        missing = ~np.ma.getmaskarray(p) & np.ma.getmaskarray(v)
        absent = np.setdiff1d(everywhere, np.asarray(p.compressed()))
        # On a log value axis 0 is not a position; anchor the note to the
        # edge of the plot instead, or it is silently not drawn. (A linear
        # axis whose limits exclude 0 is re-anchored after layout, when the
        # final limits are known: value_labels.py.)
        val_axis = ctx.spec.axes.get(val_ch)
        log = val_axis is not None and val_axis.scale == "log"
        coords = "data" if not log else (("axes fraction", "data") if horizontal
                                         else ("data", "axes fraction"))
        cats = np.concatenate([np.asarray(p[missing].filled(np.nan)), absent])
        slots_without_bar = cats + off
        if slots_without_bar.size:
            # Keep those slots in view: with no bar there, an edge slot would
            # otherwise fall outside the autoscaled limits and its note be
            # clipped away.
            edges = np.concatenate([slots_without_bar - each / 2, slots_without_bar + each / 2])
            ctx.ax.update_datalim([(0.0, e) if horizontal else (e, 0.0) for e in edges],
                                  updatex=not horizontal, updatey=horizontal)
            ctx.ax.autoscale_view()
        _record_centres(ctx, val_ch, slots_without_bar)
        for c, q in zip(cats, slots_without_bar):
            xy = (0.0, q) if horizontal else (q, 0.0)
            # drawn on the positive side of zero; moved to the negative side
            # (or to the plot edge, or along the angle in polar) after layout
            # (value_labels.py)
            note = ctx.ax.annotate(
                "no data", xy, xycoords=coords, xytext=(3, 0) if horizontal else (0, 3),
                textcoords="offset points", ha="left" if horizontal else "center",
                va="center" if horizontal else "bottom", rotation=0 if horizontal else 90,
                fontsize="x-small", color=ctx.muted("0.4"), style="italic")
            ctx.no_data_notes.append(NoDataNote(note, val_ch, i, float(q), float(each),
                                                _where(ctx, cat_ch, float(c))))
            artists.append(note)
    return artists


@dataclass
class _BarSpans:
    """The bars one group of a bar layer drew: category-axis ``centres``,
    common ``width`` and the bar ``values`` (each bar runs from 0)."""

    layer: int
    explicit: bool
    orientation: str
    centres: np.ndarray
    width: float
    values: np.ndarray


def report_bar_overlaps(ctx: DrawContext) -> None:
    """``bars_overlap``: a layer placed with ``bar_width``/``bar_offset``
    whose bars cover bars of automatically dodged layers (one warning per
    explicit layer, naming the layers it covers). Explicitly placed layers
    overlapping each other are left alone: a narrow bar drawn inside a
    wide one is a deliberate design (e.g. target vs actual)."""
    by_layer: dict[int, tuple[set, int]] = {}
    for e in ctx.bar_spans:
        if not e.explicit:
            continue
        for d in ctx.bar_spans:
            if d.explicit or d.layer == e.layer or d.orientation != e.orientation:
                continue
            across = np.abs(e.centres[:, None] - d.centres[None, :]) \
                < (e.width + d.width) / 2.0 - 1e-9
            lo = np.maximum(np.minimum(e.values, 0)[:, None], np.minimum(d.values, 0)[None, :])
            hi = np.minimum(np.maximum(e.values, 0)[:, None], np.maximum(d.values, 0)[None, :])
            n = int((across & (hi > lo)).any(axis=1).sum())
            if n:
                layers, count = by_layer.get(e.layer, (set(), 0))
                by_layer[e.layer] = (layers | {d.layer}, count + n)
    for i, (layers, n) in sorted(by_layer.items()):
        ctx.overlapping_bar_layers |= layers | {i}
        ctx.warn("bars_overlap",
                 f"layer {i} is placed with bar_width/bar_offset and {n} of its bars overlap "
                 f"bars of the automatically dodged layer(s) {sorted(layers)}, so part of a bar "
                 "is hidden behind another. Give every one of these layers bar_width/"
                 f"bar_offset so they sit apart, or remove both from layer {i} so it is dodged "
                 "with the others.", f"/layers/{i}/style")


def _where(ctx: DrawContext, cat_ch: str, c: float) -> str:
    """A category-axis position as the data names it (for warnings)."""
    info = ctx.axes[cat_ch]
    if info.kind == "category":
        names = {v: k for k, v in info.categories.items()}
        if c in names:
            return repr(names[c])
    if info.degrees:
        c = float(np.degrees(c))
    return f"{c:g}"


def _record_centres(ctx: DrawContext, val_ch: str, centres: np.ndarray) -> None:
    """Record bar (or bar-less slot) centres: value labels must stay within
    the cell between them."""
    have = ctx.bar_centres.get(val_ch, np.empty(0))
    ctx.bar_centres[val_ch] = np.union1d(have, np.asarray(centres, dtype=float))


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
            kw["color"] = ctx.layer_color(i, m, group, style, kw.get("color"))
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


def _grid_index(xs: np.ndarray, ys: np.ndarray):
    """Distinct x and y values, each row's index into them, and how many rows
    land in each (y, x) cell. The rows form a complete rectilinear grid when
    both axes have at least two values and every cell count is exactly 1."""
    xu, yu = np.unique(xs), np.unique(ys)
    xi, yi = np.searchsorted(xu, xs), np.searchsorted(yu, ys)
    seen = np.zeros((yu.size, xu.size), dtype=int)
    np.add.at(seen, (yi, xi), 1)
    return xu, yu, xi, yi, seen


def _is_grid(seen: np.ndarray) -> bool:
    return seen.shape[0] >= 2 and seen.shape[1] >= 2 and bool((seen == 1).all())


def _linear_axis(ctx: DrawContext, ch: str) -> bool:
    axis = ctx.spec.axes.get(ch)
    scale = axis.scale if axis is not None else None
    return scale in (None, "linear") and ctx.axes[ch].kind == "numeric"


def _cell_scale(ctx: DrawContext, a: dict[str, np.ndarray]) -> float | None:
    """Data units per unit of |(u, v)| so the longest arrow spans one grid
    cell (the smaller of the x and y spacings), or None when that rule does
    not apply: the rule measures a cell in linear data units, so it needs a
    complete rectilinear grid of positions (the same check as a sampled 3D
    surface) on two linear numeric axes of a cartesian plot. On a log axis
    equal vectors would be drawn at different apparent lengths and tips can
    fall at x <= 0; for scattered positions "a cell" is not a spacing at
    all. Uneven grid spacing uses the mean spacing per axis. 1 when every
    vector is zero."""
    if ctx.spec.coordinate_system != "cartesian2d":
        return None
    if not (_linear_axis(ctx, "x") and _linear_axis(ctx, "y")):
        return None
    xu, yu, _, _, seen = _grid_index(a["x"], a["y"])
    if not _is_grid(seen):
        return None
    cell = min((xu[-1] - xu[0]) / (xu.size - 1), (yu[-1] - yu[0]) / (yu.size - 1))
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
    # 3D drops every row with a null. In 2D only a null position drops a row;
    # a null component or colour keeps its row as a masked arrow
    # (matplotlib's own "no arrow here", quiver.Umask), so a field with
    # undefined points stays a complete grid and keeps its arrow scale.
    keep = valid if ctx.three_d else _mask(chans["x"], chans["y"])
    hole = ~valid[keep]
    a = {k: np.ma.filled(np.ma.asarray(v[keep], dtype=float), 0.0) for k, v in chans.items()}
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
        mappable = None
        if cvals is not None:
            # Axes3D.quiver has no colour array: it emits all shafts, then all
            # left head strokes, then all right head strokes, so each arrow's
            # colour is repeated once per stroke.
            import matplotlib

            c = np.asarray(cvals[valid], dtype=float)
            cmap = ctx.cmap(style)
            cmap = matplotlib.colormaps[cmap] if isinstance(cmap, str) else cmap
            norm = matplotlib.colors.Normalize(vmin=float(c.min()), vmax=float(c.max()))
            kw["colors"] = np.tile(cmap(norm(c)), (3, 1))
            mappable = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
        else:
            kw["color"] = style.get("color", ctx.next_color(f"/layers/{i}"))
        length = m.scale if m.scale is not None else 1.0
        q = ctx.ax.quiver(a["x"], a["y"], a["z"], a["u"], a["v"], a["w"],
                          length=length, normalize=False, **kw)
        if mappable is not None:
            _colorbar(ctx, mappable, m)
        # include the arrow tips in the view, not only the tails
        ctx.ax.auto_scale_xyz(*(np.concatenate([a[p], a[p] + length * a[c]])
                                for p, c in (("x", "u"), ("y", "v"), ("z", "w"))),
                              had_data=True)
        return [q]
    # spec: arrow length in data units = scale * |(u, v)|. Without a scale,
    # on a complete grid over linear axes, the longest arrow spans one grid
    # cell (the rule visualize_vf uses in 3D), so arrows neither overlap their
    # neighbours nor run out of the plot: the view is widened to include
    # every arrow tip, which matplotlib's own autoscaling (sized at draw
    # time, tails only) does not do. Anywhere else (log axes, scattered
    # positions) matplotlib's own arrow autoscaling is used unchanged.
    length = m.scale if m.scale is not None else _cell_scale(ctx, a)
    if length is not None:
        kw.update(angles="xy", scale_units="xy", scale=1.0 / length)
        tips_x, tips_y = a["x"] + length * a["u"], a["y"] + length * a["v"]
        ctx.ax.update_datalim(np.column_stack([np.concatenate([a["x"], tips_x]),
                                               np.concatenate([a["y"], tips_y])]))
    def holed(values: np.ndarray) -> np.ndarray:
        return np.ma.array(values, mask=hole) if hole.any() else values

    args = [a["x"], a["y"], holed(a["u"]), holed(a["v"])]
    if cvals is not None:
        args.append(holed(np.ma.filled(np.ma.asarray(cvals[keep], dtype=float), 0.0)))
        kw["cmap"] = ctx.cmap(style)
    else:
        kw["color"] = style.get("color", ctx.next_color(f"/layers/{i}"))
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
    xu, yu, xi, yi, seen = _grid_index(xs, ys)
    if not _is_grid(seen):
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
        kw["color"] = ctx.layer_color(i, m, None, style, kw.get("color"))
        _wrap_variant(ctx, m, style, kw, "linestyle")
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
