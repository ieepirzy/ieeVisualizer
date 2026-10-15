"""Accessibility checks for a :class:`~ieeVisualizer.themes.Theme`, as code.

Every check compares what the eye actually sees: a colour drawn with an
opacity is blended over the background first. Thresholds:

==========================  ======================================  ========
check                       rule                                    severity
==========================  ======================================  ========
``text_contrast``           foreground and muted text vs background  error
                            >= 4.5:1 (WCAG 2.x SC 1.4.3, normal text)
``mark_contrast``           palette, signal, semantic, reference     error
                            and forecast-fill colours vs background
                            >= 3:1 (WCAG 2.x SC 1.4.11, graphics)
``palette_indistinct``      every palette pair CIEDE2000 >= 15       error
``palette_cvd_indistinct``  every pair still >= 8 after protanopia   error
                            and deuteranopia simulation
``palette_tritan_close``    same, tritanopia (rare, ~0.01 %)         warning
``diverging_cvd_indistinct`` diverging end stops >= 8 under          error
                            protan/deutan simulation
``colormap_near_background`` an end stop of a colour map < 1.5:1     warning
                            vs background (low/high values look
                            like blank "no data" cells)
``palette_unchecked``       no palette declared (renderer default)   info
==========================  ======================================  ========

WCAG thresholds are the standard's. The CIEDE2000 thresholds (15 for normal
vision, 8 under simulated dichromacy; a just-noticeable difference is about
2.3) are this project's choice for small chart marks, not a standard; the
simulation is Machado et al. (2009) at full severity (see :mod:`.color`).
"""

from __future__ import annotations

from itertools import combinations

from ..spec import ValidationIssue
from .color import blend, contrast_ratio, delta_e2000, simulate_cvd, to_hex
from .theme import Theme

TEXT_MIN = 4.5
MARK_MIN = 3.0
PAIR_MIN = 15.0
CVD_PAIR_MIN = 8.0
COLORMAP_END_MIN = 1.5
#: Renderer defaults assumed when a theme does not declare a colour.
DEFAULT_BACKGROUND = "#ffffff"
DEFAULT_FOREGROUND = "#000000"


def check_theme(theme: Theme) -> list[ValidationIssue]:
    """Every accessibility finding for ``theme`` (empty = all checks pass)."""
    out: list[ValidationIssue] = []
    bg = theme.background or DEFAULT_BACKGROUND

    def add(sev: str, path: str, code: str, msg: str) -> None:
        out.append(ValidationIssue(path, code, msg, sev))

    for key, col in (("foreground", theme.foreground or DEFAULT_FOREGROUND),
                     ("muted", theme.muted)):
        if col is None:
            continue
        r = contrast_ratio(col, bg)
        if r < TEXT_MIN:
            add("error", f"/{key}", "text_contrast",
                f"{key} text {col} on background {bg} has contrast {r:.2f}:1; text needs "
                f">= {TEXT_MIN}:1.")

    marks: list[tuple[str, str, str]] = []  # (path, what, seen colour)
    for i, c in enumerate(theme.palette or ()):
        marks.append((f"/palette/{i}", f"series colour {c}", c))
    if theme.signal is not None:
        marks.append(("/signal", f"signal colour {theme.signal}", theme.signal))
    for k, c in sorted(theme.semantic.items()):
        marks.append((f"/semantic/{k}", f"{k} colour {c}", c))
    ro = theme.roles
    if ro.reference_color is not None:
        for attr in ("reference_alpha", "reference_fill_alpha"):
            a = getattr(ro, attr)
            seen = to_hex(blend(ro.reference_color, bg, a if a is not None else 1.0))
            marks.append((f"/roles/{attr}", f"reference colour {ro.reference_color} at "
                          f"opacity {a}", seen))
    if ro.forecast_fill_alpha is not None:
        for c in theme.palette or ():
            seen = to_hex(blend(c, bg, ro.forecast_fill_alpha))
            marks.append(("/roles/forecast_fill_alpha", f"forecast fill of {c} at opacity "
                          f"{ro.forecast_fill_alpha}", seen))
    for path, what, seen in marks:
        r = contrast_ratio(seen, bg)
        if r < MARK_MIN:
            add("error", path, "mark_contrast",
                f"{what} is seen as {seen} on background {bg}: contrast {r:.2f}:1; graphical "
                f"marks need >= {MARK_MIN}:1.")

    if theme.palette is None:
        add("info", "/palette", "palette_unchecked",
            "no palette is declared; the renderer's default colour cycle is used and is not "
            "checked here.")
    else:
        _pairs(theme.palette, add)

    for which in ("sequential", "diverging"):
        stops = getattr(theme, which)
        if not isinstance(stops, tuple):
            continue
        for end, c in (("0", stops[0]), (str(len(stops) - 1), stops[-1])):
            r = contrast_ratio(c, bg)
            if r < COLORMAP_END_MIN:
                add("warning", f"/{which}/{end}", "colormap_near_background",
                    f"{which} stop {c} has contrast {r:.2f}:1 with the background; cells at "
                    "that end may read as blank (no data).")
        if which == "diverging":
            for kind in ("protanopia", "deuteranopia"):
                d = delta_e2000(simulate_cvd(stops[0], kind), simulate_cvd(stops[-1], kind))
                if d < CVD_PAIR_MIN:
                    add("error", "/diverging", "diverging_cvd_indistinct",
                        f"diverging ends {stops[0]} and {stops[-1]} differ by only "
                        f"{d:.1f} (CIEDE2000) under {kind}; the two directions would look "
                        "the same.")
    return out


def _pairs(palette: tuple[str, ...], add) -> None:
    for (i, a), (j, b) in combinations(enumerate(palette), 2):
        path = f"/palette/{j}"
        d = delta_e2000(a, b)
        if d < PAIR_MIN:
            add("error", path, "palette_indistinct",
                f"series colours {i} ({a}) and {j} ({b}) differ by {d:.1f} (CIEDE2000); "
                f"need >= {PAIR_MIN}.")
        for kind, sev, code in (("protanopia", "error", "palette_cvd_indistinct"),
                                ("deuteranopia", "error", "palette_cvd_indistinct"),
                                ("tritanopia", "warning", "palette_tritan_close")):
            d = delta_e2000(simulate_cvd(a, kind), simulate_cvd(b, kind))
            if d < CVD_PAIR_MIN:
                add(sev, path, code,
                    f"series colours {i} ({a}) and {j} ({b}) differ by only {d:.1f} "
                    f"(CIEDE2000) under simulated {kind}; need >= {CVD_PAIR_MIN}.")


def report(theme: Theme) -> dict:
    """JSON summary: measured contrasts and the minimum pairwise differences,
    plus every issue. For docs and catalogue entries."""
    bg = theme.background or DEFAULT_BACKGROUND
    fg = theme.foreground or DEFAULT_FOREGROUND
    out: dict = {"background": bg,
                 "text_contrast": round(contrast_ratio(fg, bg), 2),
                 "issues": [i.to_dict() for i in check_theme(theme)]}
    if theme.muted:
        out["muted_contrast"] = round(contrast_ratio(theme.muted, bg), 2)
    if theme.palette:
        out["palette_contrast"] = [round(contrast_ratio(c, bg), 2) for c in theme.palette]
        if len(theme.palette) > 1:
            prs = list(combinations(theme.palette, 2))
            out["min_pair_delta_e2000"] = round(min(delta_e2000(a, b) for a, b in prs), 1)
            for kind in ("protanopia", "deuteranopia", "tritanopia"):
                out[f"min_pair_delta_e2000_{kind}"] = round(min(
                    delta_e2000(simulate_cvd(a, kind), simulate_cvd(b, kind)) for a, b in prs), 1)
    return out
