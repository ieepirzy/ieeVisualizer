"""Axes, scales, limits, legend, annotations, title and caption."""

from __future__ import annotations

import textwrap
from typing import Any

from ...spec import FigureSpec, ReferenceBand, ReferenceLine, TextAnnotation
from ..base import RenderError, issue
from .data import AxisInfo, scalar_position
from .marks import DrawContext
from .style import ANNOTATION_KEYS, parse_style

LEGEND_LOC = {
    "auto": "best",
    "top": "upper center",
    "bottom": "lower center",
    "left": "center left",
    "right": "center right",
    "upper-left": "upper left",
    "upper-right": "upper right",
    "lower-left": "lower left",
    "lower-right": "lower right",
}
CAPTION_FONTSIZE = 8.0


def locale_formatter(loc: Any) -> Any:
    """A tick formatter writing numbers in ``loc``'s conventions (decimal
    comma, grouped thousands, no offset text); positions are matplotlib's."""
    from matplotlib.ticker import ScalarFormatter

    from ...themes.locale import localize_decimal

    class LocaleFormatter(ScalarFormatter):
        def __init__(self) -> None:
            super().__init__(useOffset=False)
            self.set_powerlimits((-4, 9))  # business figures: no "1e6" multiplier

        def __call__(self, x: float, pos: Any = None) -> str:
            return localize_decimal(super().__call__(x, pos), loc)

    return LocaleFormatter()


def axis_label(spec: FigureSpec, info: AxisInfo) -> str | None:
    """"Label (unit)": the axis' own label/unit first, then the first bound
    column's title/name and unit."""
    axis = spec.axes.get(info.key)
    explicit = axis.label if axis is not None else None
    if info.derived_label is not None and info.label_column is None:
        label, unit = explicit or info.derived_label, None
    else:
        label = explicit or info.label_title or info.label_column
        unit = (axis.unit if axis is not None and axis.unit else None) or info.unit
    if label is None:
        return f"({unit})" if unit else None
    if unit and not label.rstrip().endswith(f"({unit})"):
        return f"{label} ({unit})"
    return label


def _axis_obj(ax: Any, key: str) -> Any:
    return {"x": ax.xaxis, "y": ax.yaxis}.get(key) or ax.zaxis


def apply_axes(ctx: DrawContext) -> None:
    spec, ax = ctx.spec, ctx.ax
    polar = spec.coordinate_system == "polar"
    for key, info in ctx.axes.items():
        label = axis_label(spec, info)
        if label:
            getattr(ax, f"set_{key}label")(label)
        axis = spec.axes.get(key)
        scale = axis.scale if axis is not None else None
        loc = ctx.locale
        if info.kind == "numeric" and scale in ("log", "symlog"):
            if ctx.three_d or (polar and key == "x"):
                raise RenderError([issue(f"/axes/{key}/scale", "unsupported_scale",
                                         f"the matplotlib adapter cannot draw a {scale} scale on "
                                         f"the {spec.coordinate_system} {key} axis faithfully; "
                                         "use a linear scale or a 2D figure.")])
            getattr(ax, f"set_{key}scale")(scale)
        elif info.kind == "numeric" and loc is not None and not polar:
            _axis_obj(ax, key).set_major_formatter(locale_formatter(loc))
        elif info.kind == "time":
            from matplotlib import dates as mdates

            locator = mdates.AutoDateLocator(tz=info.tz)
            obj = _axis_obj(ax, key)
            obj.set_major_locator(locator)
            fmt: dict[str, Any] = {}
            if loc is not None and loc.date_formats is not None:
                fmt = {"formats": list(loc.date_formats),
                       "zero_formats": list(loc.date_zero_formats or loc.date_formats),
                       "offset_formats": list(loc.date_offset_formats or loc.date_formats)}
            obj.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=info.tz, **fmt))
        elif info.kind == "category":
            names = list(info.categories)
            getattr(ax, f"set_{key}ticks")(range(len(names)))
            getattr(ax, f"set_{key}ticklabels")(names)
        if axis is not None and axis.limits is not None:
            _limits(ctx, key, info, axis.limits)
    if spec.title:
        ax.set_title(spec.title)


def _limits(ctx: DrawContext, key: str, info: AxisInfo, limits: list) -> None:
    lo, hi = limits
    vals = []
    for end, v in (("min", lo), ("max", hi)):
        p = scalar_position(v, info) if v is not None else None
        if v is not None and p is None:
            ctx.warn("limits_ignored",
                     f"limit {v!r} cannot be placed on the {info.kind} {key} axis; the {end} "
                     "end is automatic.", f"/axes/{key}/limits")
        vals.append(p)
    if vals == [None, None]:
        return
    getattr(ctx.ax, f"set_{key}lim")(*vals)


def apply_legend(ctx: DrawContext) -> None:
    spec = ctx.spec
    if not spec.legend.show:
        return
    handles, labels = ctx.ax.get_legend_handles_labels()
    if not handles:
        return
    labelled_roles = any(m.role != "observed" for m in spec.layers)
    if len(handles) < 2 and not spec.legend.title and not labelled_roles:
        return  # a single unqualified series: the axis label already names it
    kw: dict[str, Any] = {"title": spec.legend.title}
    if spec.legend.position == "outside-right":
        kw.update(loc="center left", bbox_to_anchor=(1.02, 0.5))
    else:
        kw["loc"] = LEGEND_LOC[spec.legend.position]
    ctx.ax.legend(handles, labels, **kw)


def apply_annotations(ctx: DrawContext) -> list[Any]:
    out = []
    for i, a in enumerate(ctx.spec.annotations):
        p = f"/annotations/{i}"
        style = parse_style(a.style, ANNOTATION_KEYS, p + "/style", f"a {a.type} annotation",
                            ctx.warnings)
        if isinstance(a, TextAnnotation):
            art = _text(ctx, p, a, style)
        elif isinstance(a, (ReferenceLine, ReferenceBand)):
            art = _reference(ctx, p, a, style)
        else:  # pragma: no cover - validate() rejects unknown types
            art = None
        if art is not None:
            out.append(art)
    return out


def _skip(ctx: DrawContext, p: str, why: str) -> None:
    ctx.warn("annotation_skipped", f"annotation not drawn: {why}", p)


def _text(ctx: DrawContext, p: str, a: TextAnnotation, style: dict) -> Any:
    keys = ("x", "y", "z") if ctx.three_d else ("x", "y")
    coords = []
    for k in keys:
        pos = scalar_position(getattr(a, k), ctx.axes[k])
        if pos is None:
            _skip(ctx, p, f"{k}={getattr(a, k)!r} cannot be placed on the "
                          f"{ctx.axes[k].kind} {k} axis.")
            return None
        coords.append(pos)
    kw = {"color": style.get("color", ctx.muted("0.15")), "fontsize": "small"}
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    if ctx.three_d:
        return ctx.ax.text(*coords, a.text, **kw)
    return ctx.ax.annotate(a.text, tuple(coords), xytext=(4, 4), textcoords="offset points", **kw)


def _reference(ctx: DrawContext, p: str, a: Any, style: dict) -> Any:
    if ctx.three_d:
        _skip(ctx, p, f"{a.type} is not supported in cartesian3d by the matplotlib adapter.")
        return None
    info = ctx.axes[a.axis]
    kw: dict[str, Any] = {"color": style.get("color", ctx.roles.reference_color
                                              if ctx.theme is not None
                                              and ctx.theme.roles.reference_color else "0.35")}
    if a.label is not None:
        kw["label"] = a.label
    if isinstance(a, ReferenceLine):
        pos = scalar_position(a.value, info)
        if pos is None:
            _skip(ctx, p, f"value {a.value!r} cannot be placed on the {info.kind} {a.axis} axis.")
            return None
        kw.update(linestyle=style.get("linestyle", "-."), linewidth=style.get("linewidth", 1.0),
                  alpha=style.get("opacity", 0.9))
        return (ctx.ax.axhline if a.axis == "y" else ctx.ax.axvline)(pos, **kw)
    lo, hi = scalar_position(a.start, info), scalar_position(a.end, info)
    if lo is None or hi is None:
        _skip(ctx, p, f"[{a.start!r}, {a.end!r}] cannot be placed on the {info.kind} {a.axis} "
                      "axis.")
        return None
    kw.update(alpha=style.get("opacity", 0.15), linewidth=0)
    if "linestyle" in style or "linewidth" in style:
        ctx.warn("style_key_ignored", "a reference band is a filled span; linestyle/linewidth "
                 "were ignored.", p + "/style")
    return (ctx.ax.axhspan if a.axis == "y" else ctx.ax.axvspan)(lo, hi, **kw)


def apply_caption(fig: Any, caption: str | None, height_in: float, width_in: float,
                  color: str = "0.25", fontsize: float = CAPTION_FONTSIZE) -> Any:
    """Caption under the axes. The constrained-layout rect is shrunk so the
    caption never overlaps tick labels."""
    if not caption:
        return None
    chars = max(20, int(width_in * 72 / (fontsize * 0.55)))
    text = "\n".join(textwrap.fill(par, chars) for par in caption.splitlines() or [""])
    lines = text.count("\n") + 1
    frac = min(0.4, (lines * fontsize * 1.35 + 8) / (height_in * 72))
    fig.get_layout_engine().set(rect=(0, frac, 1, 1 - frac))
    return fig.text(0.01, frac / 2, text, ha="left", va="center", fontsize=fontsize,
                    style="italic", color=color)
