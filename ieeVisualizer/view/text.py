"""Text helpers shared by the SVG/PNG and HTML view renderers.

* :func:`parse_markdown` - the supported subset: paragraphs separated by a
  blank line, ``- `` / ``* `` bullet lines, inline ``**bold**``, ``*italic*``
  / ``_italic_`` and `` `code` ``. Everything else is literal text; there is
  no HTML passthrough and no links (a view never loads or points anywhere).
  Renderers escape every run.
* :func:`text_width` - advance width in px of a string in DejaVu Sans (the
  font matplotlib ships, so it is always present), measured with
  matplotlib's FreeType binding. Used for wrapping and box sizes.
* :func:`format_number` - no invented digits. A float is formatted from its
  shortest decimal form (``Decimal(repr(v))``), never from its binary
  expansion: ``decimals=None`` writes integral values without decimals and
  other values at their stored precision; ``decimals=n`` rounds that decimal
  form half-to-even. From ``1e15`` in magnitude (where a float stops holding
  every integer digit) numbers are written in exponent notation with at most
  17 significant digits, ``decimals`` ignored: ``1e+23``, not
  ``99 999 999 999 999 991 611 392``. A result that rounds to zero is
  written without a minus sign.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import lru_cache

NO_DATA = "no data"
NULL_CELL = "—"  # em dash: a missing cell, never 0

_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\*(.+?)\*|(?<![A-Za-z0-9])_(.+?)_(?![A-Za-z0-9])")
_BULLET = re.compile(r"^\s*[-*]\s+")


@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


def parse_inline(s: str, bold: bool = False) -> list[Run]:
    runs: list[Run] = []
    pos = 0
    for m in _INLINE.finditer(s):
        if m.start() > pos:
            runs.append(Run(s[pos:m.start()], bold=bold))
        b, code, it1, it2 = m.groups()
        if b is not None:
            runs.append(Run(b, bold=True))
        elif code is not None:
            runs.append(Run(code, bold=bold, code=True))
        else:
            runs.append(Run(it1 if it1 is not None else it2, bold=bold, italic=True))
        pos = m.end()
    if pos < len(s):
        runs.append(Run(s[pos:], bold=bold))
    return [r for r in runs if r.text]


def parse_markdown(text: str) -> list[tuple[str, list[Run]]]:
    """``[("p" | "li", runs), ...]``; lines of one paragraph are joined by a space."""
    blocks: list[tuple[str, list[Run]]] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(("p", parse_inline(" ".join(para))))
            para.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            flush()
        elif _BULLET.match(line):
            flush()
            blocks.append(("li", parse_inline(_BULLET.sub("", line).strip())))
        else:
            para.append(line.strip())
    flush()
    return blocks


@lru_cache(maxsize=8)
def _font(bold: bool, italic: bool, mono: bool):
    from matplotlib.font_manager import FontProperties, findfont, get_font

    fp = FontProperties(family="DejaVu Sans Mono" if mono else "DejaVu Sans",
                        weight="bold" if bold else "normal",
                        style="italic" if italic else "normal")
    return get_font(findfont(fp, fallback_to_default=True))


@lru_cache(maxsize=8192)
def text_width(s: str, size: float, bold: bool = False, italic: bool = False,
               mono: bool = False) -> float:
    """Advance width of ``s`` in px at font size ``size`` px."""
    if not s:
        return 0.0
    return _unit_width(s, bold, italic, mono) * size / 1000.0


def measure(s: str, size: float, bold: bool = False, italic: bool = False,
            mono: bool = False) -> float:
    """:func:`text_width` without caching, for one-off probes (truncation)."""
    if not s:
        return 0.0
    return _unit_width.__wrapped__(s, bold, italic, mono) * size / 1000.0


@lru_cache(maxsize=8192)
def _unit_width(s: str, bold: bool, italic: bool, mono: bool) -> float:
    # Measured at 1000 px, where hinting no longer rounds glyph advances, so
    # widths match unhinted rendering (browsers, matplotlib's Agg) at any size.
    font = _font(bold, italic, mono)
    font.set_size(1000, 72)  # at 72 dpi one point is one px
    font.set_text(s, 0.0)
    return font.get_width_height()[0] / 64.0


def _approx(s: str, bold: bool, italic: bool, mono: bool) -> float:
    """Width of ``s`` at size 1 from cached per-glyph advances (no kerning):
    only used to tell that a string is far too wide, never to place it."""
    return sum(_unit_width(ch, bold, italic, mono) for ch in s) / 1000.0


def run_width(r: Run, size: float) -> float:
    return text_width(r.text, size, r.bold, r.italic, r.code)


def wrap_runs(runs: list[Run], size: float, width: float) -> list[list[Run]]:
    """Greedy word wrap. A word wider than ``width`` is broken across lines
    (at least one character per line), so no line is wider than ``width``
    and no label is ever drawn far past its slot."""
    words: list[Run] = []
    for r in runs:
        for i, part in enumerate(re.split(r"(\s+)", r.text)):
            if part and not (i % 2 and words and words[-1].text.endswith(" ")):
                words.append(Run(" " if i % 2 else part, r.bold, r.italic, r.code))
    lines: list[list[Run]] = [[]]
    used = 0.0
    for w in words:
        # a word far wider than the line is broken without measuring all of it
        # (FreeType costs ~10 us per character; a word can be 10 000 long)
        far = len(w.text) > 64 and _approx(w.text, w.bold, w.italic, w.code) * size > 2 * width
        ww = math.inf if far else run_width(w, size)
        if w.text == " ":
            if lines[-1]:
                lines[-1].append(w)
                used += ww
            continue
        if lines[-1] and used + ww > width:
            while lines[-1] and lines[-1][-1].text == " ":
                lines[-1].pop()
            lines.append([])
            used = 0.0
        if ww > width and len(w.text) > 1:  # alone on its line and still too wide
            rest = w.text
            while True:  # O(k log k) per line of k characters; never re-measures the rest
                k = fit_len(rest, size, width, w.bold, italic=w.italic, mono=w.code)
                if k >= len(rest):
                    break
                k = max(1, k)
                lines[-1].append(Run(rest[:k], w.bold, w.italic, w.code))
                lines.append([])
                rest = rest[k:]
            w = Run(rest, w.bold, w.italic, w.code)
            ww = run_width(w, size)
        lines[-1].append(w)
        used += ww
    while lines[-1] and lines[-1][-1].text == " ":
        lines[-1].pop()
    return [ln for ln in lines if ln] or [[]]


def wrap_text(s: str, size: float, width: float, bold: bool = False) -> list[str]:
    return ["".join(r.text for r in ln) for ln in wrap_runs([Run(s, bold)], size, width)]


#: From this magnitude on numbers are written in exponent notation.
SCI_FROM = Decimal("1e15")
SCI_DIGITS = 17  # significant digits kept in exponent notation (a float has <= 17)


def as_decimal(v: float | int | Decimal) -> Decimal:
    """``v`` as written: an int exactly, a float as its shortest decimal form."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int) or (hasattr(v, "dtype") and v.dtype.kind in "iu"):
        return Decimal(int(v))
    return Decimal(repr(float(v)))  # float() first: repr(numpy.float64) is not a number


def _sci(d: Decimal) -> str:
    with localcontext() as ctx:
        ctx.prec = SCI_DIGITS
        d = (+d).normalize()  # unary plus rounds to the context precision
    sign, digits, exp = d.as_tuple()
    m = "".join(map(str, digits))
    e = exp + len(m) - 1
    return ("-" if sign else "") + m[0] + ("." + m[1:] if len(m) > 1 else "") + f"e+{e}"


def is_zero_text(s: str) -> bool:
    """Does the formatted number ``s`` read as zero (no non-zero digit)?"""
    return not re.search("[1-9]", s.split("e")[0])


def format_number(v: float | int | Decimal | None, decimals: int | None = None) -> str:
    """``None`` -> ``"no data"``; thousands separated by a thin space. See the
    module docstring for the rules."""
    if v is None:
        return NO_DATA
    d = as_decimal(v)
    if not d.is_finite():  # validation keeps these out of views; never crash on one
        s = str(float(d))
    elif abs(d) >= SCI_FROM:
        s = _sci(d)
    elif decimals is not None:
        s = f"{d:,.{decimals}f}"
    elif d == d.to_integral_value():
        s = f"{int(d):,}"
    else:
        s = f"{float(d):,}"  # shortest repr: the digits as written, 1e-05 stays 1e-05
    if s.startswith("-") and d.is_finite() and is_zero_text(s):
        s = s[1:]  # -0.04 at one decimal is 0.0, not -0.0
    s = s.replace(",", "\u2009")
    return "\u2212" + s[1:] if s.startswith("-") else s  # not an exponent's "-" (1e-05)


def fit_len(s: str, size: float, w: float, bold: bool = False, suffix: str = "", *,
            italic: bool = False, mono: bool = False) -> int:
    """Largest ``k`` with ``s[:k] + suffix`` at most ``w`` px wide (0 if none).

    The cut is estimated from per-character advances (cached per glyph), then
    settled with exact measurements, galloping away from the estimate and
    bisecting; kerning moves the estimate by a character or two at most, so a
    few measurements of about ``k`` characters settle it. The cost does not
    depend on ``len(s)``: a 10 000-character string in a 100 px slot is
    measured only around its first few dozen characters."""
    def fits(k: int) -> bool:
        return measure(s[:k] + suffix, size, bold, italic, mono) <= w

    budget = w * 1000.0 / size - (_unit_width(suffix, bold, italic, mono) if suffix else 0.0)
    est = 0
    for ch in s:  # stops at the first character past the budget
        budget -= _unit_width(ch, bold, italic, mono)
        if budget < 0:
            break
        est += 1
    if fits(est):
        lo, step = est, 1  # lo fits; look for a hi that does not
        while lo + step <= len(s) and fits(lo + step):
            lo, step = lo + step, step * 2
        hi = min(lo + step, len(s) + 1)
    else:
        hi, step = est, 1  # hi does not fit; look for a lo that does
        while hi - step > 0 and not fits(hi - step):
            hi, step = hi - step, step * 2
        lo = max(hi - step, 0)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid
    return lo


def with_unit(s: str, unit: str | None) -> str:
    if not unit or s == NO_DATA:
        return s
    return s + unit if unit == "%" else f"{s} {unit}"
