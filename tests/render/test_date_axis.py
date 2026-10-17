"""Date axes say which year (and, on the first tick, which month or day)
without claiming a month for ticks that are not in it.

matplotlib's concise offset is the *last* tick's coarser fields: weekly ticks
from September to November read "08 15 22 29 Oct ... 2026-Nov", as if every
week were in November. The offset now states only what is true of the whole
visible axis (its day, month, year or two years), and when that is coarser
than the ticks need, the first tick says its month (or day). The offset text is
the same at every profile (the mobile gate compares every word but ticks).
"""

from __future__ import annotations

import pytest

from ieeVisualizer import templates
from ieeVisualizer.render import RenderRequest, list_profiles
from ieeVisualizer.render.mpl.adapter import build_figure
from ieeVisualizer.spec import Axis, DataSnapshot, FigureSpec, LineMark

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _series(times, theme="default"):
    data = DataSnapshot.from_columns({"t": list(times), "v": [float(i) for i in range(len(times))]},
                                     dtypes={"t": "datetime"})
    return FigureSpec(datasets={"d": data}, theme=theme,
                      layers=[LineMark(data="d", encoding={"x": "t", "y": "v"})],
                      axes={"x": Axis(label="Week starting", scale="time")})


def _weeks(start, n):
    import datetime as dt

    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(weeks=i)).isoformat() for i in range(n)]


def _axis_text(spec, request=None):
    built = build_figure(spec, request or RenderRequest())
    built.figure.canvas.draw()
    axis = built.axes.xaxis
    lo, hi = sorted(axis.get_view_interval())
    ticks = [t.label1.get_text() for t in axis.get_major_ticks()
             if lo - 1e-9 <= t.get_loc() <= hi + 1e-9 and t.label1.get_text()]
    return axis.get_offset_text().get_text(), ticks


def test_weeks_across_months_state_the_year_and_name_the_first_month():
    offset, ticks = _axis_text(_series(_weeks("2026-09-07", 12)))
    assert offset == "2026"
    assert not any(m in offset for m in MONTHS)
    assert any(m in ticks[0] for m in MONTHS), ticks  # the first tick says its month


def test_the_timeseries_example_no_longer_reads_as_one_month():
    t = templates.get("timeseries")
    offset, ticks = _axis_text(t.build_payload(t.example()))
    assert offset == "2026"
    assert any(m in ticks[0] for m in MONTHS), ticks


def test_a_span_across_new_year_states_both_years():
    offset, ticks = _axis_text(_series(_weeks("2025-11-03", 14)))
    assert offset == "2025–2026"
    assert any(m in ticks[0] for m in MONTHS), ticks


def test_hours_across_two_days_name_the_first_ticks_day():
    times = [f"2026-10-14T{h:02d}:00:00" for h in range(6, 24, 3)]
    times += [f"2026-10-15T{h:02d}:00:00" for h in range(0, 12, 3)]
    offset, ticks = _axis_text(_series(times))
    assert offset == "2026-Oct"  # true of every tick: both days are in October
    assert "14" in ticks[0], ticks


def test_hours_within_one_day_keep_that_day_as_the_offset():
    offset, ticks = _axis_text(_series([f"2026-10-14T{h:02d}:00:00" for h in range(6, 19, 3)]))
    assert offset == "2026-Oct-14"
    assert ticks[0] == "06:00", ticks


def test_days_within_one_month_keep_that_month_as_the_offset():
    offset, ticks = _axis_text(_series([f"2026-10-{d:02d}" for d in range(5, 27, 3)]))
    assert offset == "2026-Oct"
    assert not any(m in ticks[0] for m in MONTHS), ticks


def test_a_finnish_day_tick_already_carries_its_month():
    offset, ticks = _axis_text(_series(_weeks("2026-09-07", 12), theme="dashboard-dark"))
    assert offset == "2026"
    assert ticks[0].count(".") == 2, ticks  # "14.09." - no month prefix added twice


def test_several_years_need_no_offset():
    offset, _ = _axis_text(_series([f"{y}-06-01" for y in range(2019, 2027)]))
    assert offset == ""


@pytest.mark.parametrize("spec", [_series(_weeks("2026-09-07", 12)),
                                  _series(_weeks("2025-11-03", 14))],
                         ids=["one-year", "new-year"])
def test_the_offset_is_the_same_text_at_every_profile(spec):
    plain, _ = _axis_text(spec)
    for profile in list_profiles():
        assert _axis_text(spec, profile.figure_request("png"))[0] == plain, profile.name
