"""Number, currency and date display conventions of a theme.

Formatting never converts: a value in EUR is shown in EUR, a value with a
unit other than currency or percent is shown as a bare number (the axis
label carries the unit). ``None`` (a null) is never formatted as 0; the
format functions return ``None`` and the caller shows "no data".

``fi-FI`` follows the Finnish conventions: decimal comma, a (no-break)
space as thousands separator, a space before ``%`` and a suffixed currency
symbol, e.g. ``1 234,5``, ``42,1 %``, ``1 234,50 €``.

Rounding for display is half-up on the value as written (its shortest
decimal ``repr``): ``18.25`` -> ``18,3 %`` and ``0.125`` -> ``0,13 €``, the
way a reader rounds by hand, not binary half-even. Only the displayed text is
rounded; callers that record a value (template metadata, datasets) keep the
exact one.

A non-zero percent never displays as zero: a value that would round to
``0,0`` at the display precision is written as a bound, ``< 0,1 %`` (or
``> −0,1 %`` below zero), in the locale's conventions; for the same reason a
value that is not exactly ±100 never displays as ``100,0 %`` (``> 99,9 %``,
``< 100,1 %``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

NBSP = " "
#: Units shown as currency (the value is never converted, only labelled).
CURRENCY_UNITS = {"EUR": "EUR", "€": "EUR", "eur": "EUR"}
PERCENT_UNITS = ("%", "percent")


@dataclass(frozen=True)
class NumberLocale:
    """How numbers and dates are written.

    ``group_min_digits``: an integer part with at least this many digits gets
    group separators (``1234`` -> ``1 234`` with 4). ``date_formats``,
    ``date_zero_formats`` and ``date_offset_formats`` are six strftime
    patterns each (years, months, days, hours, minutes, seconds) for a
    concise date axis; ``None`` keeps the renderer's default.
    """

    tag: str
    decimal: str = "."
    group: str = ","
    group_min_digits: int = 4
    minus: str = "−"
    percent_sep: str = ""
    currency_symbol: str = "€"
    currency_position: str = "suffix"
    currency_sep: str = NBSP
    currency_decimals: int = 2
    max_decimals: int = 2
    date_formats: tuple[str, ...] | None = None
    date_zero_formats: tuple[str, ...] | None = None
    date_offset_formats: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for k in ("date_formats", "date_zero_formats", "date_offset_formats"):
            if isinstance(getattr(self, k), list):
                object.__setattr__(self, k, tuple(getattr(self, k)))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k in self.__dataclass_fields__:
            v = getattr(self, k)
            d[k] = list(v) if isinstance(v, tuple) else v
        return d


#: Used when a theme declares no locale (e.g. ``default``): ``1,234.5`` and
#: ``1,234.50 EUR`` for value labels; axis ticks keep the renderer's format.
NEUTRAL = NumberLocale(tag="und", currency_symbol="EUR")

FI_FI = NumberLocale(
    tag="fi-FI", decimal=",", group=NBSP, group_min_digits=4, percent_sep=NBSP,
    currency_symbol="€", currency_position="suffix", currency_sep=NBSP,
    # Numeric dates (strftime month names would be English); zero-padded
    # because %-d is not portable. Time uses a period (SFS 4175).
    date_formats=("%Y", "%m/%Y", "%d.%m.", "%H.%M", "%H.%M", "%S,%f"),
    date_zero_formats=("", "%Y", "%m/%Y", "%d.%m.", "%H.%M", "%H.%M"),
    date_offset_formats=("", "%Y", "%m/%Y", "%d.%m.%Y", "%d.%m.%Y", "%d.%m.%Y %H.%M"),
)


def localize_decimal(text: str, loc: NumberLocale) -> str:
    """Rewrite a plain ``-1234.5`` / ``−1234.5`` string in ``loc``'s
    conventions. Anything else (exponents, words) is returned unchanged."""
    s = text.strip()
    neg = s[:1] in ("-", "−")
    body = s[1:] if neg else s
    intpart, _, frac = body.partition(".")
    if not intpart.isdigit() or (frac and not frac.isdigit()):
        return text
    if len(intpart) >= loc.group_min_digits:
        rev = intpart[::-1]
        intpart = loc.group.join(rev[i:i + 3] for i in range(0, len(rev), 3))[::-1]
    out = intpart + (loc.decimal + frac if frac else "")
    return (loc.minus + out) if neg else out


def _round_half_up(v: float, decimals: int) -> str:
    """``v`` rounded half-up to ``decimals`` places, as a plain ``-12.34``
    string. The float's shortest ``repr`` is what gets rounded, so a value
    written ``18.25`` rounds to ``18.3`` (binary 18.25 is exact; 0.125 too;
    ``2.675`` is stored as 2.67499.. but written and rounded as 2.675)."""
    with localcontext() as ctx:
        ctx.prec = 400  # any finite float, every digit kept
        d = Decimal(repr(v)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
        if d == 0:
            d = abs(d)  # "-0.00" -> "0.00"
        return format(d, "f")


def format_number(value: Any, loc: NumberLocale | None = None,
                  decimals: int | None = None) -> str | None:
    """``value`` with ``decimals`` places, or (``None``) as few as needed up
    to ``loc.max_decimals``; rounded half-up. ``None`` -> ``None``."""
    if value is None or isinstance(value, bool):
        return None
    loc = loc or NEUTRAL
    v = float(value)
    if not math.isfinite(v):
        return None
    if decimals is None:
        s = _round_half_up(v, loc.max_decimals)
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    else:
        s = _round_half_up(v, decimals)
    return localize_decimal(s, loc)


def format_percent(value: Any, loc: NumberLocale | None = None,
                   decimals: int = 1) -> str | None:
    """``value`` already in percent (42.1 -> ``42,1 %`` in fi-FI).

    A non-zero value that rounds to zero at ``decimals`` places is shown as
    the bound it is below, ``< 0,1 %`` (``> −0,1 %`` when negative), never as
    ``0,0 %``; exactly 0 stays ``0,0 %``. Likewise a value that rounds to
    100 but is not exactly 100 is shown as a bound: ``> 99,9 %`` for 99.97,
    ``< 100,1 %`` for 100.03 (rates above 100 % are possible: growth, share
    of target), and mirrored below zero (``< −99,9 %`` for −99.97, a change
    that is not a total loss); exactly ±100 stays ``100,0 %``."""
    loc = loc or NEUTRAL
    s = format_number(value, loc, decimals)
    if s is None:
        return None
    v = float(value)
    shown = abs(Decimal(_round_half_up(v, decimals)))
    step = 10.0 ** -decimals
    if v != 0 and shown == 0:
        b = format_number(step, loc, decimals)
        bound = f"<{NBSP}{b}" if v > 0 else f">{NBSP}{loc.minus}{b}"
        return f"{bound}{loc.percent_sep}%"
    if shown == 100 and abs(v) != 100:
        # 99.97 is not 100 %: the bound on the side of 100 it is on
        inside = abs(v) < 100
        b = format_number(100 - step if inside else 100 + step, loc, decimals)
        sign = "" if v > 0 else loc.minus
        rel = (">" if inside else "<") if v > 0 else ("<" if inside else ">")
        return f"{rel}{NBSP}{sign}{b}{loc.percent_sep}%"
    return f"{s}{loc.percent_sep}%"


def format_currency(value: Any, loc: NumberLocale | None = None) -> str | None:
    """``1 234,50 €`` (fi-FI) / ``€1,234.50`` (neutral prefix rules apply only
    when the locale says ``prefix``)."""
    loc = loc or NEUTRAL
    s = format_number(value, loc, loc.currency_decimals)
    if s is None:
        return None
    if loc.currency_position == "prefix":
        return f"{loc.currency_symbol}{loc.currency_sep}{s}"
    return f"{s}{loc.currency_sep}{loc.currency_symbol}"


def format_quantity(value: Any, unit: str | None, loc: NumberLocale | None = None) -> str | None:
    """A value label: currency for EUR, ``%`` for percent, otherwise the bare
    number (the axis label names the unit)."""
    if unit in CURRENCY_UNITS:
        return format_currency(value, loc)
    if unit in PERCENT_UNITS:
        return format_percent(value, loc)
    return format_number(value, loc)
