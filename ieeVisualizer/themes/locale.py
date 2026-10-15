"""Number, currency and date display conventions of a theme.

Formatting never converts: a value in EUR is shown in EUR, a value with a
unit other than currency or percent is shown as a bare number (the axis
label carries the unit). ``None`` (a null) is never formatted as 0; the
format functions return ``None`` and the caller shows "no data".

``fi-FI`` follows the Finnish conventions: decimal comma, a (no-break)
space as thousands separator, a space before ``%`` and a suffixed currency
symbol, e.g. ``1 234,5``, ``42,1 %``, ``1 234,50 €``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
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


def format_number(value: Any, loc: NumberLocale | None = None,
                  decimals: int | None = None) -> str | None:
    """``value`` with ``decimals`` places, or (``None``) as few as needed up
    to ``loc.max_decimals``. ``None`` -> ``None``."""
    if value is None or isinstance(value, bool):
        return None
    loc = loc or NEUTRAL
    v = float(value)
    if not math.isfinite(v):
        return None
    if decimals is None:
        s = f"{v:.{loc.max_decimals}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    else:
        s = f"{v:.{decimals}f}"
    if s in ("-0", "-0." + "0" * (decimals or 0)):
        s = s[1:]
    return localize_decimal(s, loc)


def format_percent(value: Any, loc: NumberLocale | None = None,
                   decimals: int = 1) -> str | None:
    """``value`` already in percent (42.1 -> ``42,1 %`` in fi-FI)."""
    loc = loc or NEUTRAL
    s = format_number(value, loc, decimals)
    return None if s is None else f"{s}{loc.percent_sep}%"


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
