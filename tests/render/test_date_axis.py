"""Date axes say which year (and, on the first tick, which month or day)
without claiming a month for ticks that are not in it.

matplotlib's concise offset is the *last* tick's coarser fields: weekly ticks
from September to November read "08 15 22 29 Oct ... 2026-Nov", as if every
week were in November. The offset now states only what is true of all the
data shown (its minute, day, month, year or two years), and a tick whose label
leaves out a field the offset does not give it says it (the leftmost tick and
where the field changes). The offset text is
the same at every profile (the mobile gate compares every word but ticks).
"""

from __future__ import annotations

import re

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


def _days(start, n):
    import datetime as dt

    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


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
    assert ticks[0].count(".") == 2, ticks  # "15.09." - no month prefix added
    assert not any(" " in t for t in ticks), ticks  # nor "09/2026 15.09." on any tick


@pytest.mark.parametrize("theme", ["default", "dashboard-dark"])
@pytest.mark.parametrize("times", [_weeks("2026-09-07", 12), _days("2026-10-01", 31),
                                   _days("2026-10-25", 12), _days("2026-12-01", 31)],
                         ids=["weeks", "october", "month-end", "december"])
def test_no_tick_names_its_month_twice(theme, times):
    _, ticks = _axis_text(_series(times, theme=theme))
    for t in ticks:
        assert sum(t.count(m) for m in MONTHS) <= 1, ticks  # not "Sep Sep" / "Oct Oct"
        assert not re.search(r"\d\d/\d{4} \d\d\.\d\d\.", t), ticks  # not "09/2026 15.09."


def test_seconds_keep_the_hour_and_minute_in_the_offset():
    times = [f"2026-10-14T06:10:{s:02d}" for s in range(3, 33, 2)]
    offset, ticks = _axis_text(_series(times))
    assert offset == "2026-Oct-14 06:10"
    assert ticks and all(len(t) == 2 for t in ticks), ticks


def test_minutes_across_midnight_name_both_days():
    times = [f"2026-10-14T23:{m:02d}:00" for m in range(0, 60, 10)]
    times += [f"2026-10-15T00:{m:02d}:00" for m in range(0, 60, 10)]
    offset, ticks = _axis_text(_series(times))
    assert offset == "2026-Oct"
    assert ticks[0].startswith("Oct-14"), ticks
    assert any(t.startswith("Oct-15") for t in ticks), ticks


def test_the_autoscale_margin_adds_no_year():
    offset, ticks = _axis_text(_series(_weeks("2026-10-05", 13)))  # to Dec 28
    assert offset == "2026"
    late = [t for t in ticks if "Jan" in t]
    assert all("2027" in t for t in late), ticks  # a tick in the margin says its own year


def test_a_calendar_year_of_months_needs_no_offset():
    offset, ticks = _axis_text(_series([f"2026-{m:02d}-01" for m in range(1, 13)]))
    assert offset == ""  # the January tick names the year, as on main
    assert ticks[0] == "2026", ticks


@pytest.mark.parametrize("start", ["2026-01-01", "2026-10-01", "2026-12-01"])
def test_a_whole_month_of_days_reads_as_that_month(start):
    offset, _ = _axis_text(_series(_days(start, 31)))
    assert offset == "2026-" + MONTHS[int(start[5:7]) - 1]


def test_limits_wider_than_the_data_claim_no_other_year():
    spec = _series(_weeks("2026-09-07", 12))
    spec.axes["x"] = Axis(label="Week starting", scale="time", limits=["2026-06-01", "2027-03-01"])
    offset, _ = _axis_text(spec)
    assert "2027" not in offset and "2025" not in offset
    spec = _series(_days("2026-10-03", 10))
    spec.axes["x"] = Axis(label="Day", scale="time", limits=["2026-09-20", "2026-10-20"])
    offset, ticks = _axis_text(spec)
    assert offset == "2026-Oct"
    assert ticks[0].startswith("Sep"), ticks  # a September tick does not read as October


def test_on_a_reversed_axis_the_leftmost_tick_carries_the_month():
    import datetime as dt

    import matplotlib.pyplot as plt
    from matplotlib import dates as mdates

    from ieeVisualizer.render.mpl.dates import ExtentOffsetFormatter

    x = [dt.datetime(2026, 9, 7) + dt.timedelta(weeks=i) for i in range(12)]
    fig, ax = plt.subplots()
    try:
        ax.plot(x, range(12))
        loc = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(ExtentOffsetFormatter(loc))
        ax.invert_xaxis()
        fig.canvas.draw()
        lo, hi = sorted(ax.get_xlim())
        ticks = sorted(((t.get_loc(), t.label1.get_text()) for t in ax.xaxis.get_major_ticks()
                        if lo <= t.get_loc() <= hi), reverse=True)  # left to right
        assert any(m in ticks[0][1] for m in MONTHS), ticks
    finally:
        plt.close(fig)


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


def test_a_tick_outside_the_view_does_not_stand_in_for_the_leftmost_visible_one():
    """matplotlib may pass the formatter ticks beyond the limits (it draws
    only those inside): a hidden "Sep" tick on Sep 1 must not leave the first
    visible tick, Sep 15, without its month."""
    import datetime as dt

    from matplotlib import dates as mdates

    built = build_figure(_series(_weeks("2026-09-07", 12)), RenderRequest())
    built.figure.canvas.draw()
    axis = built.axes.xaxis
    days = [dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 15), dt.datetime(2026, 10, 1)]
    values = [mdates.date2num(d) for d in days]
    assert values[0] < min(axis.get_view_interval()) <= values[1]  # Sep 1 is hidden
    labels = axis.get_major_formatter().format_ticks(values)
    assert labels[1] == "Sep 15", labels


def _dated_ticks(spec):
    from matplotlib import dates as mdates

    built = build_figure(spec, RenderRequest())
    built.figure.canvas.draw()
    axis = built.axes.xaxis
    lo, hi = sorted(axis.get_view_interval())
    return axis.get_offset_text().get_text(), [
        (mdates.num2date(t.get_loc()), t.label1.get_text()) for t in axis.get_major_ticks()
        if lo - 1e-9 <= t.get_loc() <= hi + 1e-9 and t.label1.get_text()]


def test_the_offset_states_no_field_the_ticks_label():
    spec = _series(["2026-10-14T06:00:00", "2026-10-14T18:00:00"])
    spec.axes["x"] = Axis(label="Day", scale="time", limits=["2026-10-01", "2026-10-31"])
    offset, ticks = _dated_ticks(spec)
    assert offset == "2026-Oct", (offset, ticks)  # not "2026-Oct-14" over "05 09 13 ..."


def test_under_wider_limits_every_tick_outside_the_offsets_month_names_its_month():
    """Fixed limits (an animation's, or the caller's) can reach months the
    data are not in: the offset names the data's month, so each tick in
    another month must say which."""
    spec = _series(_weeks("2026-09-07", 4))  # September only
    spec.axes["x"] = Axis(label="Week", scale="time", limits=["2026-09-01", "2026-11-30"])
    offset, ticks = _dated_ticks(spec)
    assert offset in ("2026", "2026-Sep"), offset
    for d, label in ticks:
        if offset == "2026-Sep" and d.month != 9:
            assert MONTHS[d.month - 1] in label, ticks
