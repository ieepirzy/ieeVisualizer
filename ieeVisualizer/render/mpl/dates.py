"""Date tick labels that make no claim about a month the ticks are not in.

matplotlib's :class:`~matplotlib.dates.ConciseDateFormatter` writes the
*last* tick's coarser fields as the axis offset: weekly ticks from September
to November read ``08 15 22 29 Oct ... 2026-Nov``, as if every week were in
November. :class:`ExtentOffsetFormatter` keeps matplotlib's tick labels and
changes two things:

* the offset states only what is true of all the data shown (the data's
  extent, clipped to the axis limits, so neither the autoscale margin nor
  limits wider than the data add a year): its minute (``2026-Oct-14 06:10``),
  day (``2026-Oct-14``) or month (``2026-Oct``) when the data lie within
  one, else its year (``2026``) or two years (``2025–2026``); three or more
  years get none. It never states the field the ticks label (no day offset
  over day ticks). Month and year ticks keep matplotlib's rule: no offset
  when a tick already names the year (January);
* a tick whose label leaves out a coarser field that the offset does not
  state for it either (the month of a day tick, the day of an hour or minute
  tick, the hour and minute of a second tick, the year of a tick outside the
  offset's years) is prefixed with it: the leftmost tick, each tick where
  that field changes, and any tick in another month or year than the offset
  states (limits wider than the data) (``Sep 08``, ``Oct-14 23:00 ... Oct-15 00:00``). A label
  whose own format already carries the field is left alone (``Oct``,
  ``14.09.`` in fi-FI).

The offset depends on the data and the limits, not on the ticks, so it is
the same text at every profile however many day or hour ticks fit.
"""

from __future__ import annotations

import math
from typing import Any

from matplotlib import dates as mdates

#: strftime directives naming each field: year, month, day, hour, minute.
_DIRECTIVES = (("%Y", "%y"), ("%m", "%b", "%B"), ("%d", "%e", "%j"), ("%H", "%I"), ("%M",))
#: The value a field has where its unit starts (month 1, day 1, hour 0, ...).
_ZERO = (0, 1, 1, 0, 0, 0)


def _carries(fmt: str) -> set[int]:
    """The fields (0 year ... 4 minute) a strftime format names."""
    return {f for f, ds in enumerate(_DIRECTIVES) if any(d in fmt for d in ds)}


def _level(fields: list[tuple[int, ...]]) -> int:
    """The finest field that varies among the ticks (0 year ... 5 second),
    as matplotlib's concise formatter picks it."""
    for level in range(5, -1, -1):
        if len({f[level] for f in fields}) > 1:
            return level
    return 5


class ExtentOffsetFormatter(mdates.ConciseDateFormatter):
    """Concise date labels whose offset is true of all the data shown (see
    the module docstring)."""

    def __init__(self, locator: Any, tz: Any = None, **kw: Any) -> None:
        super().__init__(locator, tz=tz, **kw)
        self._offset_text = ""

    def format_ticks(self, values: Any) -> list[str]:
        labels = list(super().format_ticks(values))
        values = [float(v) for v in values]
        dates = [mdates.num2date(v, tz=self._tz) for v in values]
        fields = [d.timetuple()[:6] for d in dates]
        level = _level(fields) if len(fields) > 1 else 5
        text, years, stated = self._extent(level)
        lo, hi = self._view()
        shown = [i for i, v in enumerate(values) if lo - 1e-9 <= v <= hi + 1e-9]
        shown.sort(key=lambda i: values[i], reverse=self._inverted())  # left to right
        if level < 2:
            # month and year ticks: matplotlib's rule, no offset once a tick
            # names the year (January); the year tick dates its neighbours
            names_year = any(0 in _carries(self._format(level, fields[i], dates[i]))
                             for i in shown)
            self._offset_text = "" if names_year else text
            return labels
        self._offset_text = text
        needed = set(range(min(level, 5)))  # the fields coarser than the ticks
        previous: dict[int, int] | None = None
        for i in shown:
            f = fields[i]
            known = _carries(self._format(level, f, dates[i]))
            if years is not None and years[0] <= f[0] <= years[1]:
                known.add(0)  # within the offset's year(s)
            if years is not None and years[0] == years[1]:
                for k in range(1, len(stated)):  # the offset's month/day/... where they match
                    if f[k] != stated[k]:
                        break
                    known.add(k)
            missing = needed - known
            # the leftmost tick, a change of context, and any tick the offset
            # would otherwise misdate (another month or year than it states)
            contradicted = any(k < len(stated) and f[k] != stated[k] for k in missing) or (
                0 in missing and years is not None)
            changed = previous is None or any(previous.get(k) != f[k] for k in missing)
            if missing and (changed or contradicted):
                labels[i] = self._prefixed(labels[i], dates[i], missing)
            previous = {k: f[k] for k in needed}
        return labels

    def get_offset(self) -> str:
        return self._offset_text

    def _format(self, level: int, f: tuple[int, ...], d: Any) -> str:
        """The format matplotlib's labeller used for a tick at this level."""
        if level < 5:
            return self.zero_formats[level] if f[level] == _ZERO[level] else self.formats[level]
        zero = d.second == 0 and d.microsecond == 0
        return self.zero_formats[5] if zero else self.formats[5]

    def _prefixed(self, label: str, d: Any, missing: set[int]) -> str:
        """``label`` after the fields in ``missing``, written with the
        locale's own patterns (coarse to fine)."""
        pieces: list[str] = []
        got: set[int] = set()
        if 2 in missing:
            date_fmt = self.zero_formats[3]  # month and day ("Oct-14", "14.10.")
        elif 1 in missing:
            date_fmt = self.zero_formats[2]  # month ("Oct", "10/2026")
        else:
            date_fmt = ""
        got |= _carries(date_fmt)
        time_fmt = self.zero_formats[4] if missing & {3, 4} else ""  # "06:10"
        got |= _carries(time_fmt)
        if 0 in missing and 0 not in got:
            pieces.append(d.strftime(self.offset_formats[1] or "%Y"))
        pieces += [d.strftime(fmt) for fmt in (date_fmt, time_fmt) if fmt]
        return " ".join([p for p in pieces if p] + [label])

    def _view(self) -> tuple[float, float]:
        if self.axis is None:
            return (-math.inf, math.inf)
        lo, hi = sorted(self.axis.get_view_interval())
        return float(lo), float(hi)

    def _inverted(self) -> bool:
        if self.axis is None:
            return False
        a, b = self.axis.get_view_interval()
        return bool(a > b)

    def _extent(self, level: int) -> tuple[str, tuple[int, int] | None, tuple[int, ...]]:
        """``(text, years, stated)``: the offset, the years it covers (None
        when it states none) and the leading fields it states (year, month,
        day, hour, minute) when it names a single year. It states no field the
        ticks label themselves (no day over day ticks), so ``level`` caps it."""
        if self.axis is None:
            return ("", None, ())
        vlo, vhi = self._view()
        dlo, dhi = sorted(float(v) for v in self.axis.get_data_interval())
        lo, hi = max(vlo, dlo), min(vhi, dhi)
        if not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi:
            return ("", None, ())
        a, b = (mdates.num2date(v, tz=self._tz) for v in (lo, hi))
        if b.year - a.year > 1:
            return ("", None, ())
        if a.year != b.year:
            fmt = self.offset_formats[1] or "%Y"
            return (f"{a.strftime(fmt)}–{b.strftime(fmt)}", (a.year, b.year), (a.year,))
        fa, fb = a.timetuple()[:5], b.timetuple()[:5]
        same = 1
        while same < 5 and fa[same] == fb[same]:
            same += 1
        # offset_formats: [1] year, [2] year-month, [3] year-month-day,
        # [5] year-month-day hour:minute; there is none for an hour alone
        same = min(same, {0: 1, 1: 1, 2: 2, 3: 3, 4: 3, 5: 5}[level])
        index = {1: 1, 2: 2, 3: 3, 4: 3, 5: 5}[same]
        stated = fa[: {1: 1, 2: 2, 3: 3, 5: 5}[index]]
        return (a.strftime(self.offset_formats[index] or "%Y"), (a.year, a.year), stated)
