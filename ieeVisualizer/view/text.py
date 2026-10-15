"""Text helpers shared by the SVG/PNG and HTML view renderers.

* :func:`parse_markdown` - the supported subset: paragraphs separated by a
  blank line, ``- `` / ``* `` bullet lines, inline ``**bold**``, ``*italic*``
  / ``_italic_`` and `` `code` ``. Everything else is literal text; there is
  no HTML passthrough and no links (a view never loads or points anywhere).
  Renderers escape every run.
* :func:`text_width` - advance width in px of a string in DejaVu Sans (the
  font matplotlib ships, so it is always present), measured with
  matplotlib's FreeType binding. Used for wrapping and box sizes.
* :func:`format_number` - no invented precision: ``decimals=None`` writes
  integral values without decimals and other values at their stored
  precision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


def run_width(r: Run, size: float) -> float:
    return text_width(r.text, size, r.bold, r.italic, r.code)


def wrap_runs(runs: list[Run], size: float, width: float) -> list[list[Run]]:
    """Greedy word wrap; a single word wider than ``width`` gets its own line."""
    words: list[Run] = []
    for r in runs:
        for i, part in enumerate(re.split(r"(\s+)", r.text)):
            if part and not (i % 2 and words and words[-1].text.endswith(" ")):
                words.append(Run(" " if i % 2 else part, r.bold, r.italic, r.code))
    lines: list[list[Run]] = [[]]
    used = 0.0
    for w in words:
        ww = run_width(w, size)
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
        lines[-1].append(w)
        used += ww
    while lines[-1] and lines[-1][-1].text == " ":
        lines[-1].pop()
    return [ln for ln in lines if ln] or [[]]


def wrap_text(s: str, size: float, width: float, bold: bool = False) -> list[str]:
    return ["".join(r.text for r in ln) for ln in wrap_runs([Run(s, bold)], size, width)]


def format_number(v: float | int | None, decimals: int | None = None) -> str:
    """``None`` -> ``"no data"``; thousands separated by a thin space."""
    if v is None:
        return NO_DATA
    if decimals is not None:
        s = f"{v:,.{decimals}f}"
    elif float(v).is_integer():
        s = f"{int(v):,}"
    else:
        s = f"{v:,}"
    s = s.replace(",", " ")
    return "−" + s[1:] if s.startswith("-") else s  # not an exponent's "-" (1e-05)


def with_unit(s: str, unit: str | None) -> str:
    if not unit or s == NO_DATA:
        return s
    return s + unit if unit == "%" else f"{s} {unit}"
