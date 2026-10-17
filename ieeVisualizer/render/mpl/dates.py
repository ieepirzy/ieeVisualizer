"""Date tick labels that make no claim about a month the ticks are not in.

matplotlib's :class:`~matplotlib.dates.ConciseDateFormatter` writes the
*last* tick's coarser fields as the axis offset: weekly ticks from September
to November read ``08 15 22 29 Oct ... 2026-Nov``, as if every week were in
November. :class:`ExtentOffsetFormatter` keeps matplotlib's tick labels and
changes two things:

* the offset states only what is true of the whole visible axis: its day
  (``2026-Oct-14``) when the axis lies within one day, its month
  (``2026-Oct``) within one month, else its year (``2026``) or two years
  (``2025–2026``); three or more years get none, since their ticks name the
  years themselves;
* when the offset is coarser than the ticks need, the first tick names what
  is missing: its month on day ticks, its day on hour and minute ticks
  (``Sep 08``, ``Oct-14 06:00``), unless its label already says it
  (``14.09.`` in fi-FI) or it is a tick where the month or day starts.

The offset comes from the axis's view limits, not from the ticks, so it is
the same text at every profile however many ticks fit.
"""

from __future__ import annotations

import math
from typing import Any

from matplotlib import dates as mdates

#: strftime directives that name a month / a day of the month.
_MONTH = ("%m", "%b", "%B")
_DAY = ("%d", "%e", "%j")
#: The value a field has where its unit starts (month 1, day 1, hour 0, ...).
_ZERO = (0, 1, 1, 0, 0, 0)


def _level(dates: list[Any]) -> int:
    """The finest field that varies among the ticks (0 year ... 5 second),
    as matplotlib's concise formatter picks it."""
    fields = [d.timetuple()[:6] for d in dates]
    for level in range(5, -1, -1):
        if len({f[level] for f in fields}) > 1:
            return level
    return 5


class ExtentOffsetFormatter(mdates.ConciseDateFormatter):
    """Concise date labels whose offset is true of the whole visible axis
    (see the module docstring)."""

    def __init__(self, locator: Any, tz: Any = None, **kw: Any) -> None:
        super().__init__(locator, tz=tz, **kw)
        self._extent = ("", 0)

    def format_ticks(self, values: Any) -> list[str]:
        labels = list(super().format_ticks(values))
        self._extent = self._offset()
        if not labels:
            return labels
        dates = [mdates.num2date(v, tz=self._tz) for v in values]
        level = _level(dates) if len(dates) > 1 else 5
        known = self._extent[1]  # fields (year, month, day) the offset states
        lo, hi = self._shown()
        inside = [i for i, v in enumerate(values) if lo - 1e-9 <= v <= hi + 1e-9]
        if level < 2 or known >= min(level, 3) or not inside:
            return labels  # month/year ticks, or the offset already says it
        first = min(inside, key=lambda i: values[i])
        d = dates[first]
        if level < 5 and d.timetuple()[level] == _ZERO[level]:
            return labels  # a tick where the month or day starts names it itself
        if _says(self.formats[level], _MONTH if level == 2 else _DAY):
            return labels  # the label already carries it ("14.09.")
        # day ticks miss the month; hour/minute ticks the day; second ticks
        # the day and the hour
        need = {2: [2], 3: [3], 4: [3], 5: [3, 4]}[level]
        prefix = " ".join(d.strftime(self.zero_formats[k]) for k in need).strip()
        if prefix:
            labels[first] = f"{prefix} {labels[first]}"
        return labels

    def get_offset(self) -> str:
        return self._extent[0]

    def _shown(self) -> tuple[float, float]:
        if self.axis is None:
            return (-math.inf, math.inf)
        lo, hi = sorted(self.axis.get_view_interval())
        return float(lo), float(hi)

    def _offset(self) -> tuple[str, int]:
        """``(text, fields)``: the offset text and how many of year, month,
        day it states (0 when there is none)."""
        lo, hi = self._shown()
        if not (math.isfinite(lo) and math.isfinite(hi)):
            return ("", 0)
        a, b = (mdates.num2date(v, tz=self._tz) for v in (lo, hi))
        if b.year - a.year > 1:
            return ("", 0)
        if a.year != b.year:
            fmt = self.offset_formats[1] or "%Y"
            return (f"{a.strftime(fmt)}–{b.strftime(fmt)}", 1)
        fields = 3 if (a.month, a.day) == (b.month, b.day) else 2 if a.month == b.month else 1
        return (b.strftime(self.offset_formats[fields] or "%Y"), fields)


def _says(fmt: str, directives: tuple[str, ...]) -> bool:
    return any(d in fmt for d in directives)
