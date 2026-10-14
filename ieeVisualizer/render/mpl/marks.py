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
    FORECAST_SUFFIX,
    REFERENCE_ALPHA,
    fill_kwargs,
    legend_label,
    line_kwargs,
    parse_style,
)


@dataclass
class DrawContext:
    spec: FigureSpec
    fig: Any
    ax: Any
    axes: dict[str, AxisInfo]
    warnings: list[RenderWarning]
    layer_artists: dict[int, list[Any]] = field(default_factory=dict)
    _color_i: int = 0
    _series_colors: dict[str, str] = field(default_factory=dict)

    @property
    def three_d(self) -> bool:
        return self.spec.coordinate_system == "cartesian3d"

    def next_color(self) -> str:
        from matplotlib import rcParams

        cycle = rcParams["axes.prop_cycle"].by_key().get("color", ["C0"])
        c = cycle[self._color_i % len(cycle)]
        self._color_i += 1
        return c

    def series_color(self, key: str, role: str, explicit: str | None) -> str:
        """Observed and forecast layers of the same column share a colour, so
        the forecast reads as the continuation of its observed series."""
        if explicit is not None:
            self._series_colors.setdefault(key, explicit)
            return explicit
        if key in self._series_colors:
            return self._series_colors[key]
        c = self.next_color()
        self._series_colors[key] = c
        return c

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
        kw.setdefault("alpha", 0.6 if m.role == "forecast" else REFERENCE_ALPHA)
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
    ctx.fig.colorbar(mappable, ax=ctx.ax, label=label)


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
        kw = line_kwargs(style, m.role, f"/layers/{i}", ctx.warnings)
        key = f"{m.encoding['y'].field}|{group}"
        kw["color"] = ctx.series_color(key, m.role, kw.get("color")) if m.role != "reference" \
            else kw["color"]
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
            kw["cmap"] = style.get("colormap", "viridis")
            if m.role != "observed":
                _approximate_role(ctx, i, m, kw)
        else:
            color = style.get("color")
            if m.role == "reference":
                color = color or "0.45"
                kw.setdefault("alpha", REFERENCE_ALPHA)
            else:
                color = ctx.series_color(f"{m.encoding['y'].field}|{group}", m.role, color)
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
        kw = fill_kwargs(style, m.role)
        if m.role != "reference":
            kw["color"] = ctx.series_color(f"{m.encoding['y'].field}|{group}", m.role,
                                           kw.get("color"))
        if m.role == "forecast":
            kw.setdefault("edgecolor", kw["color"])
            kw["facecolor"] = "none"
            kw.pop("color")
        kw["label"] = _label(m, group, _title(m, "y"))
        artists.append(ctx.ax.fill_between(
            np.ma.filled(xs.astype(float), np.nan), np.ma.filled(ys.astype(float), np.nan), 0.0,
            where=valid, interpolate=False, **kw))
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
    each = total / len(grp)
    artists = []
    for g, (group, idx) in enumerate(grp):
        p, v = pos[idx], val[idx]
        valid = _mask(p, v)
        _require(valid, i, m, group)
        off = (g - (len(grp) - 1) / 2.0) * each
        kw = fill_kwargs(style, m.role)
        if m.role != "reference":
            kw["color"] = ctx.series_color(f"{m.encoding[val_ch].field}|{group}", m.role,
                                           kw.get("color"))
        if m.role == "forecast":
            kw["edgecolor"] = kw["color"]
        kw["label"] = _label(m, group, _title(m, val_ch))
        pp, vv = np.asarray(p[valid]) + off, np.asarray(v[valid])
        if horizontal:
            artists.append(ctx.ax.barh(pp, vv, height=each, **kw))
        else:
            artists.append(ctx.ax.bar(pp, vv, width=each, **kw))
        # A labelled slot with no bar reads as zero; say "no data" there.
        missing = ~np.ma.getmaskarray(p) & np.ma.getmaskarray(v)
        for q in np.asarray(p[missing].filled(np.nan)) + off:
            xy = (0.0, q) if horizontal else (q, 0.0)
            artists.append(ctx.ax.annotate(
                "no data", xy, xytext=(3, 0) if horizontal else (0, 3),
                textcoords="offset points", ha="left" if horizontal else "center",
                va="center" if horizontal else "bottom", rotation=0 if horizontal else 90,
                fontsize="x-small", color="0.4", style="italic"))
    return artists


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
        kw = fill_kwargs(style, m.role)
        if m.role != "reference":
            kw["color"] = ctx.series_color(f"{m.encoding['x'].field}|{group}", m.role,
                                           kw.get("color"))
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
    kw: dict[str, Any] = {"cmap": style.get("colormap", "viridis")}
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    _approximate_role(ctx, i, m, kw)
    mesh = ctx.ax.pcolormesh(_edges(xu), _edges(yu), grid, shading="flat", **kw)
    _colorbar(ctx, mesh, m)
    return [mesh]


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
    if m.scale is not None:
        # spec: arrow length in data units = scale * |(u, v)|
        kw.update(angles="xy", scale_units="xy", scale=1.0 / m.scale)
    args = [a["x"], a["y"], a["u"], a["v"]]
    if cvals is not None:
        args.append(np.asarray(cvals[valid], dtype=float))
        kw["cmap"] = style.get("colormap", "viridis")
    else:
        kw["color"] = style.get("color", ctx.next_color())
    q = ctx.ax.quiver(*args, **kw)
    if cvals is not None:
        _colorbar(ctx, q, m)
    return [q]


def draw_function(ctx: DrawContext, i: int, m: Mark) -> list[Any]:
    if m.expression is None:  # sampled form: drawn like a line
        return draw_line(ctx, i, m)
    style = _style(ctx, i, m)
    path = f"/layers/{i}"
    keys = ("x", "y") if ctx.three_d else ("x",)
    logs = tuple(ctx.spec.axes[k].scale == "log" if k in ctx.spec.axes else False for k in keys)
    inputs, out = fn_mod.sample(m, path, logs, ctx.warnings)
    if ctx.three_d:
        kw: dict[str, Any] = {"cmap": style.get("colormap", "viridis")}
        if "opacity" in style:
            kw["alpha"] = style["opacity"]
        _approximate_role(ctx, i, m, kw)
        surf = ctx.ax.plot_surface(inputs[0], inputs[1], np.ma.filled(out, np.nan), **kw)
        return [surf]
    for k in ("colormap",):
        if k in style:
            ctx.warn("style_key_ignored", f"a 2D function curve does not use {k!r}.",
                     f"{path}/style/{k}")
    kw = line_kwargs(style, m.role, path, ctx.warnings)
    if m.role != "reference":
        kw["color"] = ctx.series_color(f"function|{m.expression}", m.role, kw.get("color"))
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
