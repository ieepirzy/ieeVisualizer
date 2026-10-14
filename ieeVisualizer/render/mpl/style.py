"""Style hints, role styling and themes for the matplotlib adapter.

Style keys understood (``ieeVisualizer.spec.marks`` lists them as the
renderer-neutral vocabulary): ``color``, ``colormap``, ``linewidth``,
``linestyle``, ``marker``, ``opacity``. Any other key, a key the mark cannot
use, or a value matplotlib would reject becomes a :class:`RenderWarning`
and is ignored; nothing is dropped silently.

Roles:

* ``forecast`` is always distinguishable from observed data: dashed lines,
  hollow markers, hatched fills, and the legend label says "forecast". A
  style asking a forecast for a solid line is overridden with a warning.
* ``reference`` is drawn lighter (reduced opacity, thinner, grey unless a
  colour is given).
"""

from __future__ import annotations

from typing import Any

from ..base import RenderWarning

KNOWN_STYLE_KEYS = ("color", "colormap", "linewidth", "linestyle", "marker", "opacity")
#: Which style keys each mark type can honour.
APPLICABLE = {
    "line": {"color", "linewidth", "linestyle", "marker", "opacity"},
    "function": {"color", "linewidth", "linestyle", "marker", "opacity", "colormap"},
    "scatter": {"color", "colormap", "marker", "opacity"},
    "area": {"color", "linewidth", "linestyle", "opacity"},
    "bar": {"color", "linewidth", "opacity"},
    "histogram": {"color", "linewidth", "opacity"},
    "heatmap": {"colormap", "opacity"},
    "quiver": {"color", "colormap", "opacity"},
}
ANNOTATION_KEYS = {"color", "linewidth", "linestyle", "opacity"}

_LINESTYLES = {
    "solid": "-", "-": "-", "dashed": "--", "--": "--", "dotted": ":", ":": ":",
    "dashdot": "-.", "-.": "-.",
}
FORECAST_LINESTYLE = "--"
FORECAST_HATCH = "///"
REFERENCE_ALPHA = 0.55
REFERENCE_COLOR = "0.45"
FORECAST_SUFFIX = " (forecast)"

#: Built-in themes: rcParams applied on top of matplotlib's defaults.
THEMES: dict[str, dict[str, Any]] = {
    "default": {},
    "minimal": {
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "grid.linewidth": 0.6,
    },
    "presentation": {
        "font.size": 13,
        "axes.titlesize": 16,
        "axes.labelsize": 14,
        "lines.linewidth": 2.2,
        "axes.spines.top": False,
        "axes.spines.right": False,
    },
}


def resolve_theme(name: str | None, path: str, warnings: list[RenderWarning]) -> dict:
    if name is None:
        return THEMES["default"]
    if name not in THEMES:
        warnings.append(RenderWarning(
            "unknown_theme",
            f"theme {name!r} is not known to the matplotlib adapter (known: "
            f"{', '.join(THEMES)}); the default theme was used.", path))
        return THEMES["default"]
    return THEMES[name]


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
            ok = isinstance(value, str) and value in colormaps
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
    """Legend label; forecasts always say so."""
    label = name if name is not None else fallback
    if role == "forecast":
        if label is None:
            return "forecast"
        if "forecast" not in label.lower():
            return label + FORECAST_SUFFIX
    return label


def line_kwargs(style: dict[str, Any], role: str, path: str,
                warnings: list[RenderWarning]) -> dict[str, Any]:
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
        if kw.get("linestyle", FORECAST_LINESTYLE) == "-":
            warnings.append(RenderWarning(
                "forecast_style_overridden",
                "a forecast layer asked for a solid line; forecasts are always drawn dashed so "
                "they cannot be mistaken for observations.", path + "/style/linestyle"))
            kw["linestyle"] = FORECAST_LINESTYLE
        kw.setdefault("linestyle", FORECAST_LINESTYLE)
    elif role == "reference":
        kw.setdefault("alpha", REFERENCE_ALPHA)
        kw.setdefault("color", REFERENCE_COLOR)
        kw.setdefault("linewidth", 1.0)
    return kw


def fill_kwargs(style: dict[str, Any], role: str) -> dict[str, Any]:
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
        kw["hatch"] = FORECAST_HATCH
        kw.setdefault("alpha", 0.6)
    elif role == "reference":
        kw.setdefault("alpha", REFERENCE_ALPHA * 0.6)
        kw.setdefault("color", REFERENCE_COLOR)
    return kw
