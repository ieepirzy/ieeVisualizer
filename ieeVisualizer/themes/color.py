"""Colour arithmetic for theme checks: parsing, WCAG contrast, CIELAB,
colour differences and colour-vision-deficiency simulation.

Pure Python, no matplotlib or numpy, so themes can be checked anywhere.

* :func:`contrast_ratio` is WCAG 2.x: ``(L1 + 0.05) / (L2 + 0.05)`` over sRGB
  relative luminance. Black on white is 21:1.
* :func:`delta_e76` is Euclidean distance in CIELAB (D65);
  :func:`delta_e2000` is CIEDE2000 (Sharma, Wu & Dalal 2005 formulation).
* :func:`simulate_cvd` applies the Machado, Oliveira & Fernandes (2009)
  full-severity (1.0) matrices for protanopia, deuteranopia and tritanopia in
  linear sRGB, clipping to the gamut. It is a model of dichromat perception,
  good enough to catch pairs that collapse; it does not replace testing with
  people.
* :func:`parse_color` reads ``#rgb``, ``#rrggbb`` and CSS ``oklch(L C H)``
  (L as 0-1 or a percentage), the forms used by the design tokens the
  built-in themes cite. Out-of-gamut OKLCH is clipped to sRGB.
"""

from __future__ import annotations

import math
import re

RGB = tuple  # (r, g, b) floats in 0..1, gamma-encoded sRGB

#: Machado et al. (2009), severity 1.0, applied to linear sRGB.
CVD_MATRICES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "protanopia": ((0.152286, 1.052583, -0.204868),
                   (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968),
                     (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779),
                   (-0.078411, 0.930809, 0.147602),
                   (0.004733, 0.691367, 0.303900)),
}

_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_OKLCH = re.compile(r"^oklch\(\s*([0-9.]+)(%?)\s+([0-9.]+)\s+([0-9.]+)(?:deg)?\s*\)$")


def parse_color(value: str) -> RGB:
    """``#rgb`` / ``#rrggbb`` / ``oklch(L C H)`` -> sRGB floats. Raises
    ``ValueError`` for anything else."""
    if not isinstance(value, str):
        raise ValueError(f"a colour must be a string, got {value!r}")
    s = value.strip()
    m = _HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    m = _OKLCH.match(s)
    if m:
        lightness = float(m.group(1)) / (100.0 if m.group(2) else 1.0)
        return oklch_to_rgb(lightness, float(m.group(3)), float(m.group(4)))
    raise ValueError(f"{value!r} is not a colour; use '#rrggbb' or 'oklch(L C H)'")


def is_color(value: object) -> bool:
    try:
        parse_color(value)  # type: ignore[arg-type]
    except ValueError:
        return False
    return True


def to_hex(rgb: RGB) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_gamma(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def oklch_to_rgb(lightness: float, chroma: float, hue_deg: float) -> RGB:
    """CSS OKLCH -> gamma-encoded sRGB, clipped to the gamut."""
    h = math.radians(hue_deg)
    a, b = chroma * math.cos(h), chroma * math.sin(h)
    lp = lightness + 0.3963377774 * a + 0.2158037573 * b
    mp = lightness - 0.1055613458 * a - 0.0638541728 * b
    sp = lightness - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = lp ** 3, mp ** 3, sp ** 3
    r = 4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc
    g = -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc
    bl = -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc
    return tuple(_to_gamma(c) for c in (r, g, bl))


def relative_luminance(color: str | RGB) -> float:
    """WCAG relative luminance (0 black .. 1 white)."""
    r, g, b = (_to_linear(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str | RGB, bg: str | RGB) -> float:
    """WCAG 2.x contrast ratio, 1.0 .. 21.0 (order of arguments irrelevant)."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg: str | RGB, bg: str | RGB, alpha: float) -> RGB:
    """``fg`` drawn at opacity ``alpha`` over ``bg`` (what the eye sees)."""
    f, b = _rgb(fg), _rgb(bg)
    return tuple(alpha * x + (1 - alpha) * y for x, y in zip(f, b))


def to_lab(color: str | RGB) -> tuple[float, float, float]:
    """CIELAB (D65 white)."""
    r, g, b = (_to_linear(c) for c in _rgb(color))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e76(c1: str | RGB, c2: str | RGB) -> float:
    return math.dist(to_lab(c1), to_lab(c2))


def delta_e2000(c1: str | RGB, c2: str | RGB) -> float:
    """CIEDE2000 colour difference (kL = kC = kH = 1)."""
    return _de2000(to_lab(c1), to_lab(c2))


def _de2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cbar7 = ((c1 + c2) / 2) ** 7
    g = 0.5 * (1 - math.sqrt(cbar7 / (cbar7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dlp, dcp = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dhp_big = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))
    lbp, cbp = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cbp ** 7 / (cbp ** 7 + 25 ** 7))
    sl = 1 + 0.015 * (lbp - 50) ** 2 / math.sqrt(20 + (lbp - 50) ** 2)
    sc = 1 + 0.045 * cbp
    sh = 1 + 0.015 * cbp * t
    rt = -math.sin(math.radians(2 * dtheta)) * rc
    return math.sqrt((dlp / sl) ** 2 + (dcp / sc) ** 2 + (dhp_big / sh) ** 2
                     + rt * (dcp / sc) * (dhp_big / sh))


def simulate_cvd(color: str | RGB, kind: str) -> RGB:
    """How ``color`` appears to a dichromat of ``kind`` (a key of
    :data:`CVD_MATRICES`)."""
    m = CVD_MATRICES[kind]
    lin = [_to_linear(c) for c in _rgb(color)]
    return tuple(_to_gamma(sum(m[i][j] * lin[j] for j in range(3))) for i in range(3))


def _rgb(color: str | RGB) -> RGB:
    return parse_color(color) if isinstance(color, str) else tuple(color)


def rgb_to_oklch(color: str | RGB) -> tuple[float, float, float]:
    """sRGB -> OKLCH (L 0..1, C, H in degrees)."""
    r, g, b = (_to_linear(c) for c in _rgb(color))
    lc = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    mc = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    sc = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lightness = 0.2104542553 * lc + 0.7936177850 * mc - 0.0040720468 * sc
    a = 1.9779984951 * lc - 2.4285922050 * mc + 0.4505937099 * sc
    bb = 0.0259040371 * lc + 0.7827717662 * mc - 0.8086757660 * sc
    return lightness, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def with_lightness(color: str | RGB, lightness: float) -> str:
    """``color`` with its OKLCH lightness replaced (hue and chroma kept, then
    clipped to sRGB), as ``#rrggbb``."""
    _, c, h = rgb_to_oklch(color)
    return to_hex(oklch_to_rgb(lightness, c, h))


def darken_to_contrast(color: str | RGB, background: str | RGB, target: float) -> str:
    """The lightest version of ``color`` (same OKLCH hue and chroma, lower
    lightness) whose contrast against ``background`` is at least ``target``,
    as ``#rrggbb``. Returns ``color`` unchanged (as hex) if it already passes.
    Used to derive print-safe variants of dark-UI token hues; a derivation,
    not a new brand colour."""
    if contrast_ratio(color, background) >= target:
        return to_hex(_rgb(color))
    lightness = rgb_to_oklch(color)[0]
    lo, hi = 0.0, lightness
    for _ in range(40):
        mid = (lo + hi) / 2
        if contrast_ratio(with_lightness(color, mid), background) >= target:
            lo = mid
        else:
            hi = mid
    return with_lightness(color, lo)
