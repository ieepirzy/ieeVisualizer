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


# ------------------------------------------------- distinct frame labels


def test_distinct_values_get_distinct_labels_with_at_most_three_more_digits():
    from ieeVisualizer.animation.frames import state_labels

    assert state_labels("a", [1.0001, 1.0002]) == ["a = 1.0001", "a = 1.0002"]
    assert state_labels("a", [1.001, 1.002, 1.003]) == ["a = 1.001", "a = 1.002", "a = 1.003"]
    # ordinary values keep their short labels, byte for byte
    assert state_labels("phase", [0.0, 1.5707963, 3.14159265]) == \
        ["phase = 0", "phase = 1.571", "phase = 3.142"]


def test_sweep_and_frame_column_labels_tell_close_values_apart():
    from anim_helpers import SMALL

    from ieeVisualizer.animation import (
        AnimationSpec,
        FrameColumn,
        ParameterSweep,
        render_animation,
    )
    from ieeVisualizer.spec import DataSnapshot, FigureSpec, FunctionMark, LineMark

    sweep = AnimationSpec(timeline=ParameterSweep(parameter="a", values=[1.0001, 1.0002]),
                          figure=FigureSpec(layers=[FunctionMark(
                              expression="a*sin(x)", variables=["x"], domain={"x": [0.0, 6.0]},
                              samples=20, parameters={"a": 1.0})]), fps=2)
    res = render_animation(sweep, SMALL)
    assert [s["label"] for s in res.states] == ["a = 1.0001", "a = 1.0002"]
    ds = DataSnapshot.from_columns({"t": [1.0001, 1.0001, 1.0002, 1.0002],
                                    "x": [0.0, 1.0, 0.0, 1.0], "y": [1.0, 2.0, 2.0, 3.0]})
    fc = AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=2,
                       figure=FigureSpec(datasets={"d": ds}, layers=[LineMark(
                           data="d", encoding={"x": "x", "y": "y"})]))
    labels = [s["label"] for s in render_animation(fc, SMALL).states]
    assert labels == ["t: 1.0001", "t: 1.0002"]


def test_labels_that_still_collide_are_reported_not_escalated():
    from anim_helpers import SMALL

    from ieeVisualizer.animation import AnimationSpec, ParameterSweep, render_animation
    from ieeVisualizer.spec import FigureSpec, FunctionMark

    sweep = AnimationSpec(timeline=ParameterSweep(parameter="a",
                                                  values=[1 + 1e-12, 1 + 2e-12, 2.0]),
                          figure=FigureSpec(layers=[FunctionMark(
                              expression="a*sin(x)", variables=["x"], domain={"x": [0.0, 6.0]},
                              samples=20, parameters={"a": 1.0})]), fps=2)
    res = render_animation(sweep, SMALL)
    assert [s["label"] for s in res.states][:2] == ["a = 1", "a = 1"]  # at most .7g
    amb = [w for w in res.warnings if w.code == "frame_labels_ambiguous"]
    assert len(amb) == 1 and "states [0, 1]" in amb[0].message


def test_ordinary_animations_are_unchanged_byte_for_byte():
    """The labels of existing specs did not change, so neither did their
    hashes or bytes (compared with the formatter this replaces)."""
    from anim_helpers import SMALL, sine_anim, weekly_anim

    from ieeVisualizer.animation import frames as F
    from ieeVisualizer.animation import render_animation

    for anim in (sine_anim(), weekly_anim("cumulative")):
        new = render_animation(anim, SMALL)
        old_texts = F.value_texts
        try:  # the old formatter: always .4g, nothing escalated
            F.value_texts = lambda values, numbers=False: (
                [format(v, ".4g") if numbers else F._show(v) for v in values], [])
            old = render_animation(anim, SMALL)
        finally:
            F.value_texts = old_texts
        assert new.content == old.content and new.states == old.states


def test_three_more_digits_is_the_cap():
    """.8g and .10g mutants passed: 1.0000001 and 1.0000002 differ in the 8th
    digit, so they stay 'a = 1' and are reported."""
    from ieeVisualizer.animation.frames import state_labels, value_texts

    assert state_labels("a", [1.0000001, 1.0000002]) == ["a = 1", "a = 1"]
    assert value_texts([1.0000001, 1.0000002], numbers=True)[1] == [[0, 1]]


def test_only_a_colliding_group_is_widened():
    """One collision used to widen every label of the sweep (260 of 300 in
    a smooth-eased sweep)."""
    from ieeVisualizer.animation import ParameterSweep
    from ieeVisualizer.animation.frames import state_labels, sweep_values

    assert state_labels("a", [0.0, 0.33333333, 0.66666667, 1.0001, 1.0002]) == \
        ["a = 0", "a = 0.3333", "a = 0.6667", "a = 1.0001", "a = 1.0002"]
    values = sweep_values(ParameterSweep(parameter="a", start=0.0, end=1.0, duration=10.0,
                                         easing="smooth"), 30)
    short = [format(v, ".4g") for v in values]
    colliding = {t for t in short if len({v for v, s in zip(values, short) if s == t}) > 1}
    labels = state_labels("a", values)
    widened = [i for i, (lab, s) in enumerate(zip(labels, short)) if lab != f"a = {s}"]
    assert colliding and widened  # the sweep has collisions, and they were widened
    assert all(short[i] in colliding for i in widened)  # and nothing else was
    assert len(set(labels)) == len(set(values))


def test_integer_valued_floats_print_in_full_when_that_tells_them_apart():
    from ieeVisualizer.animation.frames import state_labels

    assert state_labels("n", [-12345678.0, -12345679.0, 0.0]) == \
        ["n = -12345678", "n = -12345679", "n = 0"]
    assert state_labels("n", [10000, 10001, 12345678, 12345679])[2:] == \
        ["n = 12345678", "n = 12345679"]


def test_the_ambiguity_warning_is_summarised_and_advises_relative_spacing():
    from ieeVisualizer.animation.frames import ambiguous_labels, value_texts

    values = [v for k in range(7) for v in (k + 1 + 1e-12, k + 1 + 2e-12)]
    clash = value_texts(values, numbers=True)[1]
    (w,) = ambiguous_labels(values, clash)
    assert len(clash) == 7 and "7 groups" in w.message and "first 5" in w.message
    assert w.message.count("states [") == 5 and "relative" in w.message
    assert len(w.message) < 700


#: GIF SHA-256 of ordinary animations on main before the label change, per
#: matplotlib version (other versions draw other bytes anyway).
MAIN_GIFS = {
    "3.11.2": {"sine": "74e776473346d8101ea8f014ba39e8bd7671b48f9c97b47cce9a2ac0e47cfdea",
               "weekly": "8b83b16c715dd034619f5067d93de7f6db2b4d4d5dd69b847bec1bcaec97f004"},
    "3.9.4": {"sine": "41dbf4d1fb5206894b9bc0295d0bee2b435cdd7bf12a7178c05182725dc6750d",
              "weekly": "8b650c13db2148b5048bfec8c8b0623800ca343c7df47098e4c4291df5445580"},
}


def test_ordinary_animations_keep_mains_recorded_bytes():
    import hashlib

    import matplotlib
    import pytest
    from anim_helpers import SMALL, sine_anim, weekly_anim

    from ieeVisualizer.animation import render_animation

    want = MAIN_GIFS.get(matplotlib.__version__)
    if want is None:
        pytest.skip(f"no hashes recorded for matplotlib {matplotlib.__version__}")
    got = {n: hashlib.sha256(render_animation(a, SMALL).content).hexdigest()
           for n, a in (("sine", sine_anim()), ("weekly", weekly_anim("cumulative")))}
    assert got == want


def _amplitude(values, title="Damped oscillator response"):
    from ieeVisualizer.animation import AnimationSpec, ParameterSweep
    from ieeVisualizer.spec import FigureSpec, FunctionMark

    return AnimationSpec(timeline=ParameterSweep(parameter="amplitude", values=values), fps=4,
                         figure=FigureSpec(title=title, layers=[FunctionMark(
                             expression="amplitude*sin(x)", variables=["x"],
                             domain={"x": [0.0, 6.0]}, samples=40,
                             parameters={"amplitude": 1.0})]))


def test_a_clipped_title_that_carries_the_state_label_is_reported():
    """At 360x640 'Damped oscillator response (amplitude = -1.234561e-05)'
    loses its end: two distinct frames looked identical, silently."""
    from ieeVisualizer.animation import AnimationRequest, render_animation

    narrow = AnimationRequest(width=360, height=640, units="px", dpi=100)
    res = render_animation(_amplitude([-1.234561e-5, -1.234562e-5]), narrow)
    (w,) = [w for w in res.warnings if w.code == "frame_label_clipped"]
    assert "every state" in w.message and w.path == "/figure/title"
    wide = AnimationRequest(width=1200, height=400, units="px", dpi=100)
    assert "frame_label_clipped" not in [w.code for w in render_animation(
        _amplitude([-1.234561e-5, -1.234562e-5]), wide).warnings]
