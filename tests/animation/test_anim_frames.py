"""Frame planning: which FigureSpec each frame shows (renderer-neutral)."""

import pytest
from anim_helpers import TWO_PI, sine_anim, vector_field_anim, weekly_anim, weekly_data

from ieeVisualizer.animation import FrameColumn, ParameterSweep, plan
from ieeVisualizer.animation.frames import frame_keys, sweep_values
from ieeVisualizer.spec import DataSnapshot, FigureSpec, LineMark


def test_linear_sweep_hits_both_endpoints_with_fps_times_duration_states():
    tl = ParameterSweep(parameter="p", start=1.0, end=3.0, duration=0.5)
    assert sweep_values(tl, 10) == pytest.approx([1.0, 1.5, 2.0, 2.5, 3.0])


def test_smooth_easing_keeps_endpoints_and_slows_the_ends():
    v = sweep_values(ParameterSweep(parameter="p", start=0.0, end=1.0, duration=1.0,
                                    easing="smooth"), 10)
    assert v[0] == 0.0 and v[-1] == pytest.approx(1.0)
    assert v[1] - v[0] < v[5] - v[4]


def test_sweep_sets_only_the_swept_parameter_and_labels_the_title():
    p = plan(sine_anim())
    assert [s.spec.layers[0].parameters["phase"] for s in p.states] == pytest.approx(
        [i * TWO_PI / 4 for i in range(5)])
    assert p.states[2].spec.title == "Sine (phase = 3.142)"
    # the base figure is untouched
    assert sine_anim().figure.layers[0].parameters == {"phase": 0.0}


def test_hold_frames_repeat_states_and_poster_indexes_frames():
    p = plan(sine_anim(hold_start=2, hold_end=3, poster="last"))
    assert p.frames == [0, 0, 0, 1, 2, 3, 4, 4, 4, 4]
    assert p.poster == 9 and p.state_of(p.poster).index == 4
    assert plan(sine_anim(poster="first")).poster == 0


def test_template_sweep_rebuilds_the_template_per_value():
    p = plan(vector_field_anim(values=[0.0, 1.0]))
    u = [s.spec.datasets["field"].columns["u"].values for s in p.states]
    x = p.states[0].spec.datasets["field"].columns["x"].values
    y = p.states[0].spec.datasets["field"].columns["y"].values
    assert u[0] == pytest.approx([-yy for yy in y])
    assert u[1] == pytest.approx([-yy + xx for xx, yy in zip(x, y)])
    assert p.states[1].spec.metadata["template"]["params"]["constants"] == {"k": 1.0}


def test_datetime_frames_are_ordered_by_instant_not_by_text():
    snap = DataSnapshot.from_columns(
        {"t": ["2026-01-01T08:30:00+00:00", "2026-01-01T09:30:00+02:00",
               "2026-01-01T10:00:00+03:00", "2026-01-01T09:00:00+02:00"]},
        dtypes={"t": "datetime"})
    keys, values, nulls = frame_keys(snap, "t")
    # 10:00+03:00 and 09:00+02:00 are the same instant (07:00 UTC): one frame.
    assert values == ["2026-01-01T10:00:00+03:00", "2026-01-01T09:30:00+02:00",
                      "2026-01-01T08:30:00+00:00"]
    assert nulls == 0


def test_strings_keep_first_appearance_order_and_numbers_sort():
    snap = DataSnapshot.from_columns({"s": ["b", "a", "b", None, "c"], "n": [3, 1, 2, 1, 3]})
    assert frame_keys(snap, "s")[1:] == (["b", "a", "c"], 1)
    assert frame_keys(snap, "n")[1] == [1, 2, 3]


@pytest.mark.parametrize("display, window, expected", [
    ("current", None, [["2026-06-01"], ["2026-06-08"], ["2026-06-15"], ["2026-06-22"]]),
    ("cumulative", None, [["2026-06-01"], ["2026-06-01", "2026-06-08"],
                          ["2026-06-01", "2026-06-08", "2026-06-15"],
                          ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]]),
    ("window", 2, [["2026-06-01"], ["2026-06-01", "2026-06-08"],
                   ["2026-06-08", "2026-06-15"], ["2026-06-15", "2026-06-22"]]),
])
def test_frame_column_row_selection(display, window, expected):
    p = plan(weekly_anim(display, window, data=weekly_data(None)))
    got = [[w[:10] for w in s.spec.datasets["b"].columns["week"].values] for s in p.states]
    assert got == expected


def test_missing_values_stay_null_in_the_frame():
    p = plan(weekly_anim("cumulative"))
    assert p.states[3].spec.datasets["b"].columns["bookings"].values == [10, 12, None, 11]
    assert p.states[3].omitted_layers == ()


def test_a_layer_without_data_in_a_frame_is_left_out_not_zeroed():
    other = DataSnapshot.from_columns({"x": [0, 1], "y": [5, 6]})
    data = weekly_data(missing_week=2)
    fig = FigureSpec(datasets={"b": data, "o": other}, layers=[
        LineMark(data="b", encoding={"x": "week", "y": "bookings"}),
        LineMark(data="o", encoding={"x": "x", "y": "y"})])
    a = weekly_anim("current", data=data)
    a.figure = fig
    p = plan(a)
    s = p.states[2]
    assert s.omitted_layers == (0,)
    assert [m.data for m in s.spec.layers] == ["o"]
    assert [(i.code, i.path) for i in p.issues] == [("frame_layer_empty", "/figure/layers/0")]


def test_a_frame_with_no_drawable_layer_is_an_explicit_empty_state():
    p = plan(weekly_anim("current"))
    assert p.states[2].spec is None and p.states[2].to_dict()["empty"] is True
    assert p.states[2].label == "week: 2026-06-15"


def test_frames_per_value_repeats_each_state():
    a = weekly_anim("current", data=weekly_data(None))
    a.timeline = FrameColumn(dataset="b", column="week", frames_per_value=2)
    assert plan(a).frames == [0, 0, 1, 1, 2, 2, 3, 3]
