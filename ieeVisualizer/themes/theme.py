"""The :class:`Theme` record: a corporate visual identity as data.

A theme is JSON-serializable (:meth:`Theme.to_dict` / :meth:`Theme.from_dict`)
and content-addressed (:attr:`Theme.sha256`), so an artifact manifest can
name exactly which theme drew a figure and the figure can be re-rendered
from the recorded theme after the registered one has moved on.

``None`` in a field means "the renderer's own default" (for matplotlib:
its ``default`` style). The ``default`` theme sets nothing, which is how
it reproduces the pre-theme look byte for byte; a theme only makes claims
(and is only checked) for what it declares.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any

from ..spec import SpecError, ValidationIssue
from ..spec._common import canonical_json, sha256_of
from .color import is_color
from .locale import NumberLocale

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
FORECAST_LINESTYLES = ("dashed", "dotted", "dashdot")
SPINES = ("left", "right", "top", "bottom")


class ThemeError(SpecError):
    """A theme failed its structural or accessibility checks, or a registry
    lookup failed. ``.issues`` holds structured ``ValidationIssue`` objects."""


@dataclass(frozen=True)
class Typography:
    """Font family preference list and sizes in points (``None`` = default)."""

    family: tuple[str, ...] | None = None
    size: float | None = None
    title: float | None = None
    label: float | None = None
    tick: float | None = None
    legend: float | None = None
    caption: float | None = None


@dataclass(frozen=True)
class RoleStyle:
    """How forecast and reference layers differ from observations.

    A forecast line is never solid (``forecast_linestyle`` is dashed, dotted
    or dashdot); forecast bars/areas are hatched. ``reference_*`` styles
    reference lines and the reference role (lighter, neutral colour).
    """

    forecast_linestyle: str | None = None
    forecast_hatch: str | None = None
    forecast_fill_alpha: float | None = None
    reference_color: str | None = None
    reference_alpha: float | None = None
    reference_fill_alpha: float | None = None
    reference_linewidth: float | None = None


@dataclass(frozen=True)
class FigureTarget:
    """A delivery target's figure size (inches) and raster resolution."""

    width: float
    height: float
    dpi: float
    description: str = ""


@dataclass(frozen=True)
class Theme:
    name: str
    version: str
    title: str = ""
    description: str = ""
    #: Categorical series colours, in order of use.
    palette: tuple[str, ...] | None = None
    #: Sequential / diverging colour maps: colour stops (low -> high) or a
    #: renderer colormap name.
    sequential: tuple[str, ...] | str | None = None
    diverging: tuple[str, ...] | str | None = None
    background: str | None = None
    foreground: str | None = None
    #: Secondary text (captions, "no data", annotations).
    muted: str | None = None
    grid: str | None = None
    #: Reserved emphasis colour (e.g. "needs a person"); never a series colour.
    signal: str | None = None
    #: Status colours by meaning (e.g. positive / negative); not series colours.
    semantic: dict[str, str] = field(default_factory=dict)
    typography: Typography = field(default_factory=Typography)
    line_width: float | None = None
    grid_show: bool | None = None
    grid_alpha: float | None = None
    grid_width: float | None = None
    #: Draw the grid beneath bars and areas (not across them).
    grid_below: bool | None = None
    #: Spines drawn; ``None`` = renderer default (all four).
    spines: tuple[str, ...] | None = None
    roles: RoleStyle = field(default_factory=RoleStyle)
    locale: NumberLocale | None = None
    targets: dict[str, FigureTarget] = field(default_factory=dict)
    #: Where each value came from (paths, token names, derivations).
    provenance: dict[str, Any] = field(default_factory=dict)
    #: Slots whose values are placeholders awaiting an official source.
    pending: tuple[str, ...] = ()

    # ------------------------------------------------------------ identity
    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def sha256(self) -> str:
        return sha256_of(self.to_dict())

    def record(self) -> dict[str, str]:
        """``{"name", "version", "sha256"}`` for manifests."""
        return {"name": self.name, "version": self.version, "sha256": self.sha256}

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)

    def to_json(self, indent: int | None = 2) -> str:
        return canonical_json(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Theme:
        """Inverse of :meth:`to_dict`. Raises :class:`ThemeError` on unknown
        keys or a malformed structure."""
        if not isinstance(d, dict):
            raise ThemeError([ValidationIssue("", "invalid_theme", "a theme must be an object.")])
        _keys(d, cls, "")
        kw = dict(d)
        for k in ("palette", "spines", "pending"):
            if isinstance(kw.get(k), list):
                kw[k] = tuple(kw[k])
        for k in ("sequential", "diverging"):
            if isinstance(kw.get(k), list):
                kw[k] = tuple(kw[k])
        if isinstance(kw.get("typography"), dict):
            _keys(kw["typography"], Typography, "/typography")
            t = dict(kw["typography"])
            if isinstance(t.get("family"), list):
                t["family"] = tuple(t["family"])
            kw["typography"] = Typography(**t)
        if isinstance(kw.get("roles"), dict):
            _keys(kw["roles"], RoleStyle, "/roles")
            kw["roles"] = RoleStyle(**kw["roles"])
        if isinstance(kw.get("locale"), dict):
            _keys(kw["locale"], NumberLocale, "/locale")
            loc = {k: tuple(v) if isinstance(v, list) else v for k, v in kw["locale"].items()}
            kw["locale"] = NumberLocale(**loc)
        if isinstance(kw.get("targets"), dict):
            tg = {}
            for name, t in kw["targets"].items():
                if not isinstance(t, dict):
                    raise ThemeError([ValidationIssue(f"/targets/{name}", "invalid_theme",
                                                      "a target is an object.")])
                _keys(t, FigureTarget, f"/targets/{name}")
                tg[name] = FigureTarget(**t)
            kw["targets"] = tg
        missing = [k for k in ("name", "version") if k not in kw]
        if missing:
            raise ThemeError([ValidationIssue(f"/{missing[0]}", "invalid_theme",
                                              f"a theme needs {missing[0]!r}.")])
        return cls(**kw)

    @classmethod
    def from_json(cls, s: str) -> Theme:
        import json

        return cls.from_dict(json.loads(s))

    def request(self, target: str = "email", format: str = "png"):
        """A ``RenderRequest`` for one of this theme's delivery targets."""
        from ..render.base import RenderRequest

        if target not in self.targets:
            raise ThemeError([ValidationIssue(
                "/targets", "unknown_target",
                f"theme {self.name!r} has no target {target!r}; it has "
                f"{', '.join(sorted(self.targets)) or '(none)'}.")])
        t = self.targets[target]
        return RenderRequest(format=format, width=t.width, height=t.height, dpi=t.dpi,
                             theme=self.ref)


def _plain(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def _keys(d: dict, cls: type, path: str) -> None:
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(d) - known)
    if unknown:
        raise ThemeError([ValidationIssue(f"{path}/{unknown[0]}", "invalid_theme",
                                          f"unknown theme key {unknown[0]!r}; known: "
                                          f"{', '.join(sorted(known))}.")])


def structure_issues(theme: Theme) -> list[ValidationIssue]:
    """Malformed values (bad colour, negative size, solid forecast...)."""
    out: list[ValidationIssue] = []

    def err(path: str, msg: str, code: str = "invalid_theme") -> None:
        out.append(ValidationIssue(path, code, msg, "error"))

    if not isinstance(theme.name, str) or not _NAME.match(theme.name):
        err("/name", f"theme name {theme.name!r} must be lowercase letters, digits and '-'.")
    if not isinstance(theme.version, str) or not _VERSION.match(theme.version):
        err("/version", f"theme version {theme.version!r} must be MAJOR.MINOR.PATCH.")
    colours = {"background": theme.background, "foreground": theme.foreground,
               "muted": theme.muted, "grid": theme.grid, "signal": theme.signal,
               "roles/reference_color": theme.roles.reference_color}
    colours.update({f"semantic/{k}": v for k, v in theme.semantic.items()})
    for i, c in enumerate(theme.palette or ()):
        colours[f"palette/{i}"] = c
    for which in ("sequential", "diverging"):
        stops = getattr(theme, which)
        if isinstance(stops, tuple):
            if len(stops) < 2:
                err(f"/{which}", f"{which} needs at least two colour stops.")
            colours.update({f"{which}/{i}": c for i, c in enumerate(stops)})
        elif stops is not None and not isinstance(stops, str):
            err(f"/{which}", f"{which} is a list of colours or a colormap name.")
    for path, c in colours.items():
        if c is not None and not is_color(c):
            err(f"/{path}", f"{c!r} is not a colour; use '#rrggbb'.")
    if theme.palette is not None and len(theme.palette) == 0:
        err("/palette", "a declared palette needs at least one colour.")
    if theme.signal is not None and theme.signal in (theme.palette or ()):
        err("/signal", "the signal colour is reserved for emphasis and must not also be a "
                       "series colour.", "signal_in_palette")
    t = theme.typography
    for k in ("size", "title", "label", "tick", "legend", "caption"):
        v = getattr(t, k)
        if v is not None and not (_num(v) and v > 0):
            err(f"/typography/{k}", f"font size must be a positive number, got {v!r}.")
    if t.family is not None and (not t.family or not all(isinstance(f, str) and f
                                                          for f in t.family)):
        err("/typography/family", "font family is a non-empty list of names.")
    for k in ("line_width", "grid_width"):
        v = getattr(theme, k)
        if v is not None and not (_num(v) and v > 0):
            err(f"/{k}", f"{k} must be a positive number, got {v!r}.")
    for k, v in (("grid_alpha", theme.grid_alpha),
                 ("roles/forecast_fill_alpha", theme.roles.forecast_fill_alpha),
                 ("roles/reference_alpha", theme.roles.reference_alpha),
                 ("roles/reference_fill_alpha", theme.roles.reference_fill_alpha)):
        if v is not None and not (_num(v) and 0 < v <= 1):
            err(f"/{k}", f"{k} must be in (0, 1], got {v!r}.")
    ls = theme.roles.forecast_linestyle
    if ls is not None and ls not in FORECAST_LINESTYLES:
        err("/roles/forecast_linestyle",
            f"forecast lines must be one of {list(FORECAST_LINESTYLES)} so they cannot be "
            f"mistaken for observations; got {ls!r}.", "forecast_not_distinguishable")
    if theme.roles.forecast_hatch is not None and not theme.roles.forecast_hatch.strip():
        err("/roles/forecast_hatch", "forecast fills need a visible hatch pattern.",
            "forecast_not_distinguishable")
    if theme.spines is not None and not set(theme.spines) <= set(SPINES):
        err("/spines", f"spines are a subset of {list(SPINES)}.")
    for name, tg in theme.targets.items():
        for k in ("width", "height", "dpi"):
            v = getattr(tg, k)
            if not (_num(v) and v > 0):
                err(f"/targets/{name}/{k}", f"{k} must be a positive number, got {v!r}.")
    loc = theme.locale
    if loc is not None:
        if loc.decimal == loc.group:
            err("/locale/group", "decimal and group separators must differ.")
        if loc.currency_position not in ("prefix", "suffix"):
            err("/locale/currency_position", "currency_position is 'prefix' or 'suffix'.")
        for k in ("date_formats", "date_zero_formats", "date_offset_formats"):
            v = getattr(loc, k)
            if v is not None and len(v) != 6:
                err(f"/locale/{k}", f"{k} needs six patterns (years .. seconds).")
    return out


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)
