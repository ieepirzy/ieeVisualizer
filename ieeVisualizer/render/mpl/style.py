"""Style hints, role styling and themes for the matplotlib adapter.

Style keys understood (``ieeVisualizer.spec.marks`` lists them as the
renderer-neutral vocabulary): ``color``, ``colormap``, ``linewidth``,
``linestyle``, ``marker``, ``opacity``; bars also ``value_labels``,
``bar_width`` and ``bar_offset``. A ``colormap`` of ``"sequential"`` or
``"diverging"`` names the theme's map. Any other key, a key the mark cannot
use, or a value matplotlib would reject becomes a :class:`RenderWarning`
and is ignored; nothing is dropped silently.

One adapter-specific key pairs colours: ``color_of: "<layer name>"`` draws
the layer (each of its series groups) in the colour of the named layer's
series with the same group, e.g. uncertainty-bound lines in the colour of
the value they bound. The name must match exactly one other layer
(``unknown_color_of`` warning otherwise); an explicit ``color`` wins.

Themes are :class:`ieeVisualizer.themes.Theme` records: :func:`theme_rc`
turns what a theme declares into rcParams (``default`` declares nothing, so
its renders are the plain matplotlib ``default`` style), and
:func:`roles_for` gives the role styling below with the theme's values.

Roles:

* ``forecast`` is always distinguishable from observed data, whatever style
  it asks for. A forecast line's dash pattern is the requested pattern
  (``solid``/unspecified count as the theme's forecast linestyle, dashed by
  default) with every gap doubled -- a pattern no observed ``linestyle``
  produces -- and it is drawn at the theme's ``roles.forecast_line_alpha``
  (default :data:`FORECAST_ALPHA`) times the requested opacity. Markers are
  hollow, fills hatched, and the legend label says "forecast". A style
  asking a forecast for a solid line is overridden with a warning.
* ``reference`` is drawn lighter (reduced opacity, thinner, the theme's
  reference colour - grey by default - unless a colour is given).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...themes import Theme, resolve_for_render
from ...themes.theme import DEFAULT_FORECAST_LINE_ALPHA
from ..base import RenderWarning

KNOWN_STYLE_KEYS = ("color", "colormap", "linewidth", "linestyle", "marker", "opacity",
                    "value_labels", "bar_width", "bar_offset", "color_of")
#: Which style keys each mark type can honour.
APPLICABLE = {
    "line": {"color", "linewidth", "linestyle", "marker", "opacity", "color_of"},
    "function": {"color", "linewidth", "linestyle", "marker", "opacity", "colormap", "color_of"},
    "scatter": {"color", "colormap", "marker", "opacity", "color_of"},
    "area": {"color", "linewidth", "linestyle", "opacity", "color_of"},
    "bar": {"color", "linewidth", "opacity", "value_labels", "bar_width", "bar_offset",
            "color_of"},
    "histogram": {"color", "linewidth", "opacity", "color_of"},
    "heatmap": {"colormap", "opacity"},
    "quiver": {"color", "colormap", "opacity"},
}
ANNOTATION_KEYS = {"color", "linewidth", "linestyle", "opacity"}

_LINESTYLES = {
    "solid": "-", "-": "-", "dashed": "--", "--": "--", "dotted": ":", ":": ":",
    "dashdot": "-.", "-.": "-.",
}
FORECAST_LINESTYLE = "--"
#: Forecast lines are drawn at this fraction of the requested opacity
#: (unless the theme sets ``roles.forecast_line_alpha``).
FORECAST_ALPHA = DEFAULT_FORECAST_LINE_ALPHA
_DASH_RC = {"--": "lines.dashed_pattern", ":": "lines.dotted_pattern",
            "-.": "lines.dashdot_pattern"}
FORECAST_HATCH = "///"
REFERENCE_ALPHA = 0.55
REFERENCE_COLOR = "0.45"
FORECAST_SUFFIX = " (forecast)"

#: Colormap names that refer to the active theme's maps.
THEME_COLORMAPS = ("sequential", "diverging")
DEFAULT_COLORMAP = "viridis"


@dataclass(frozen=True)
class Roles:
    """Effective role styling (theme values, else the adapter's defaults)."""

    forecast_linestyle: str = FORECAST_LINESTYLE
    forecast_hatch: str = FORECAST_HATCH
    forecast_alpha: float = 0.6
    forecast_line_alpha: float = FORECAST_ALPHA
    reference_color: str = REFERENCE_COLOR
    reference_alpha: float = REFERENCE_ALPHA
    reference_fill_alpha: float = REFERENCE_ALPHA * 0.6
    reference_linewidth: float = 1.0


DEFAULT_ROLES = Roles()


def resolve_theme(name: str | None, path: str, warnings: list[RenderWarning]) -> Theme:
    """The registered theme ``name`` names (``name`` or ``name@version``);
    an unknown name renders with ``default`` and an ``unknown_theme``
    warning."""
    theme, problem = resolve_for_render(name)
    if problem is not None:
        warnings.append(RenderWarning(
            "unknown_theme", f"{problem} The default theme was used.", path))
    return theme


def roles_for(theme: Theme | None) -> Roles:
    if theme is None:
        return DEFAULT_ROLES
    r = theme.roles
    kw: dict[str, Any] = {}
    if r.forecast_linestyle is not None:
        kw["forecast_linestyle"] = _LINESTYLES[r.forecast_linestyle]
    for src, dst in (("forecast_hatch", "forecast_hatch"),
                     ("forecast_fill_alpha", "forecast_alpha"),
                     ("forecast_line_alpha", "forecast_line_alpha"),
                     ("reference_color", "reference_color"),
                     ("reference_alpha", "reference_alpha"),
                     ("reference_fill_alpha", "reference_fill_alpha"),
                     ("reference_linewidth", "reference_linewidth")):
        if getattr(r, src) is not None:
            kw[dst] = getattr(r, src)
    return Roles(**kw)


def theme_rc(theme: Theme) -> dict[str, Any]:
    """rcParams for ``theme``: only what it declares (``default`` -> {})."""
    rc: dict[str, Any] = {}
    if theme.palette is not None:
        from cycler import cycler

        rc["axes.prop_cycle"] = cycler(color=list(theme.palette))
    if theme.background is not None:
        for k in ("figure.facecolor", "axes.facecolor", "savefig.facecolor",
                  "legend.facecolor"):
            rc[k] = theme.background
    if theme.foreground is not None:
        for k in ("text.color", "axes.labelcolor", "xtick.color", "ytick.color"):
            rc[k] = theme.foreground
    edge = theme.muted or theme.foreground
    if edge is not None:
        rc["axes.edgecolor"] = edge
    if theme.grid is not None:
        rc["grid.color"] = theme.grid
        rc["legend.edgecolor"] = theme.grid
    t = theme.typography
    if t.family is not None:
        rc["font.family"] = list(t.family)
    for attr, keys in (("size", ("font.size",)), ("title", ("axes.titlesize",)),
                       ("label", ("axes.labelsize",)),
                       ("tick", ("xtick.labelsize", "ytick.labelsize")),
                       ("legend", ("legend.fontsize",))):
        if getattr(t, attr) is not None:
            for k in keys:
                rc[k] = getattr(t, attr)
    if theme.line_width is not None:
        rc["lines.linewidth"] = theme.line_width
    if theme.grid_show is not None:
        rc["axes.grid"] = theme.grid_show
    if theme.grid_alpha is not None:
        rc["grid.alpha"] = theme.grid_alpha
    if theme.grid_width is not None:
        rc["grid.linewidth"] = theme.grid_width
    if theme.grid_below is not None:
        rc["axes.axisbelow"] = theme.grid_below
    if theme.spines is not None:
        for side in ("top", "right", "left", "bottom"):
            if side not in theme.spines:
                rc[f"axes.spines.{side}"] = False
    return rc


def colormap(name: Any, theme: Theme | None) -> Any:
    """A matplotlib colormap (or name) for a style's ``colormap`` value:
    ``"sequential"``/``"diverging"`` (or no value) use the theme's maps."""
    if name is not None and name not in THEME_COLORMAPS:
        return name
    which = name or "sequential"
    value = getattr(theme, which, None) if theme is not None else None
    if value is None:
        return DEFAULT_COLORMAP if which == "sequential" else "RdBu_r"
    if isinstance(value, str):
        return value
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list(f"{theme.name}-{which}", list(value))


def parse_style(
    style: dict[str, Any], keys: set, path: str, what: str, warnings: list[RenderWarning]
) -> dict[str, Any]:
    """Validated subset of ``style`` this mark can honour; warn on the rest."""
    from matplotlib import colormaps
    from matplotlib import colors as mcolors
    from matplotlib.markers import MarkerStyle

    out: dict[str, Any] = {}
    for key in sorted(style):
        value = style[key]
        kp = f"{path}/{key}"
        if key not in KNOWN_STYLE_KEYS:
            warnings.append(RenderWarning(
                "unknown_style_key",
                f"style key {key!r} is not understood and was ignored; known keys: "
                f"{', '.join(KNOWN_STYLE_KEYS)}.", kp))
            continue
        if key not in keys:
            warnings.append(RenderWarning(
                "style_key_ignored", f"{what} does not use style key {key!r}; it was ignored.",
                kp))
            continue
        ok = True
        if key == "color":
            ok = isinstance(value, str) and mcolors.is_color_like(value)
        elif key == "colormap":
            ok = isinstance(value, str) and (value in colormaps or value in THEME_COLORMAPS)
        elif key == "color_of":
            ok = isinstance(value, str) and value != ""
        elif key == "linestyle":
            ok = isinstance(value, str) and value in _LINESTYLES
            value = _LINESTYLES.get(value, value) if ok else value
        elif key == "marker":
            try:
                MarkerStyle(value)
            except (TypeError, ValueError):
                ok = False
        elif key == "linewidth":
            ok = _num(value) and value >= 0
        elif key == "opacity":
            ok = _num(value) and 0 <= value <= 1
        elif key == "value_labels":
            ok = isinstance(value, bool)
        elif key == "bar_width":
            ok = _num(value) and 0 < value <= 1
        elif key == "bar_offset":
            ok = _num(value) and -0.5 <= value <= 0.5
        if not ok:
            warnings.append(RenderWarning(
                "invalid_style_value", f"style {key}={value!r} is not a valid value; it was "
                "ignored.", kp))
            continue
        out[key] = value
    return out


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def legend_label(name: str | None, fallback: str | None, role: str) -> str | None:
    """Legend label; forecasts always say so. A label that already ends in a
    parenthesis takes it inside (``x range (low to high, forecast)``), never
    a second pair."""
    label = name if name is not None else fallback
    if role == "forecast":
        if label is None:
            return "forecast"
        if "forecast" not in label.lower():
            if label.endswith(")") and "(" in label:
                return label[:-1] + ", forecast)"
            return label + FORECAST_SUFFIX
    return label


def line_kwargs(style: dict[str, Any], role: str, path: str,
                warnings: list[RenderWarning], roles: Roles = DEFAULT_ROLES) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if "color" in style:
        kw["color"] = style["color"]
    if "linewidth" in style:
        kw["linewidth"] = style["linewidth"]
    if "linestyle" in style:
        kw["linestyle"] = style["linestyle"]
    if "marker" in style:
        kw["marker"] = style["marker"]
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    if role == "forecast":
        if kw.get("linestyle", roles.forecast_linestyle) == "-":
            warnings.append(RenderWarning(
                "forecast_style_overridden",
                "a forecast layer asked for a solid line; forecasts are always drawn dashed so "
                "they cannot be mistaken for observations.", path + "/style/linestyle"))
            kw["linestyle"] = roles.forecast_linestyle
        kw["linestyle"] = forecast_dashes(kw.get("linestyle", roles.forecast_linestyle))
        kw["alpha"] = kw.get("alpha", 1.0) * roles.forecast_line_alpha
        if "marker" in kw:
            kw["markerfacecolor"] = "none"
    elif role == "reference":
        kw.setdefault("alpha", roles.reference_alpha)
        kw.setdefault("color", roles.reference_color)
        kw.setdefault("linewidth", roles.reference_linewidth)
    return kw


def forecast_dashes(linestyle: str) -> tuple[float, tuple[float, ...]]:
    """The forecast variant of a (non-solid) named linestyle: matplotlib's
    pattern for it with every gap doubled. No observed linestyle uses such a
    pattern, so a forecast never looks like an observed series of any style."""
    from matplotlib import rcParams

    pattern = [float(v) for v in rcParams[_DASH_RC[linestyle]]]
    return (0.0, tuple(v * 2.0 if k % 2 else v for k, v in enumerate(pattern)))


def fill_kwargs(style: dict[str, Any], role: str, roles: Roles = DEFAULT_ROLES) -> dict[str, Any]:
    """For bars, areas and histograms."""
    kw: dict[str, Any] = {}
    if "color" in style:
        kw["color"] = style["color"]
    if "linewidth" in style:
        kw["linewidth"] = style["linewidth"]
    if "linestyle" in style:
        kw["linestyle"] = style["linestyle"]
    if "opacity" in style:
        kw["alpha"] = style["opacity"]
    if role == "forecast":
        kw["hatch"] = roles.forecast_hatch
        kw.setdefault("alpha", roles.forecast_alpha)
    elif role == "reference":
        kw.setdefault("alpha", roles.reference_fill_alpha)
        kw.setdefault("color", roles.reference_color)
    return kw
