"""AnimationSpec: serialization, hashing and validation (no rendering)."""

import json
import subprocess
import sys

import pytest
from anim_helpers import codes, sine_anim, sine_figure, vector_field_anim, weekly_anim, weekly_data

from ieeVisualizer.animation import (
    MAX_FRAMES,
    AnimationSpec,
    FrameColumn,
    MarkTarget,
    ParameterSweep,
    animation_hash,
    from_dict,
    from_json,
    to_dict,
    to_json,
    validate_animation,
)
from ieeVisualizer.spec import FigureSpec, LineMark, QuiverMark, SpecParseError


def test_round_trip_is_lossless_and_canonical():
    for anim in (sine_anim(hold_end=3, poster=2), weekly_anim("window", 2),
                 vector_field_anim()):
        text = to_json(anim)
        back = from_json(text)
        assert back == anim
        assert to_json(back) == text
        assert animation_hash(back) == animation_hash(anim)


def test_numbers_are_canonical_so_int_and_float_hash_equal():
    a = sine_anim(timeline=ParameterSweep(parameter="phase", start=0, end=3, duration=1))
    b = sine_anim(timeline=ParameterSweep(parameter="phase", start=0.0, end=3.0, duration=1.0))
    assert to_json(a) == to_json(b)


def test_hash_changes_with_the_timeline_and_the_data():
    base = animation_hash(weekly_anim())
    assert animation_hash(weekly_anim(display="current")) != base
    assert animation_hash(weekly_anim(data=weekly_data(missing_week=None))) != base


def test_parse_errors_are_structured():
    good = to_dict(sine_anim())
    with pytest.raises(SpecParseError) as e:
        from_dict({k: v for k, v in good.items() if k != "kind"})
    assert e.value.issues[0].code == "not_an_animation"
    with pytest.raises(SpecParseError) as e:
        from_dict({**good, "schema_version": "9.9"})
    assert e.value.issues[0].code == "unsupported_schema_version"
    with pytest.raises(SpecParseError) as e:
        from_dict({**good, "fsp": 3})
    assert e.value.issues[0].path == "/fsp"
    with pytest.raises(SpecParseError) as e:
        from_dict({**good, "timeline": {**good["timeline"], "type": "parameter_swep"}})
    assert "parameter_sweep" in e.value.issues[0].message
    bad_fig = json.loads(json.dumps(good))
    bad_fig["figure"]["layers"][0]["type"] = "lyne"
    with pytest.raises(SpecParseError) as e:
        from_dict(bad_fig)
    assert e.value.issues[0].path.startswith("/figure/layers/0")
    with pytest.raises(SpecParseError) as e:
        from_json(to_json(sine_anim()).replace('"end":6.283185307179586', '"end":NaN'))
    assert e.value.issues[0].code == "non_finite_number"


def test_valid_examples_have_no_issues():
    assert validate_animation(sine_anim()) == []
    assert validate_animation(vector_field_anim()) == []
    assert codes(validate_animation(weekly_anim())) == []


@pytest.mark.parametrize("timeline, code, path", [
    (ParameterSweep(parameter="phse", start=0, end=1, duration=1), "unknown_parameter",
     "/timeline/parameter"),
    (ParameterSweep(parameter="phase", target=MarkTarget(layer=3), values=[0, 1]),
     "unknown_layer", "/timeline/target/layer"),
    (ParameterSweep(parameter="phase"), "sweep_source_missing", "/timeline"),
    (ParameterSweep(parameter="phase", values=[0, 1], start=0), "sweep_source_conflict",
     "/timeline"),
    (ParameterSweep(parameter="phase", values=[1.0]), "too_few_values", "/timeline/values"),
    (ParameterSweep(parameter="phase", start=0, end=1, duration=0), "invalid_value",
     "/timeline/duration"),
    (ParameterSweep(parameter="phase", start=0, end=1, duration=1, easing="bounce"),
     "invalid_value", "/timeline/easing"),
    (ParameterSweep(parameter="phase", start=0, end=1, duration=MAX_FRAMES),
     "too_many_frames", "/timeline"),
])
def test_sweep_validation(timeline, code, path):
    issues = validate_animation(sine_anim(timeline=timeline))
    assert (code, path) in [(i.code, i.path) for i in issues], issues
    if code == "unknown_parameter":
        assert "Did you mean 'phase'" in issues[0].message


@pytest.mark.parametrize("start,end,duration,bad", [
    ("a", 1.0, 1.0, ["/timeline/start"]),
    (0.0, True, 1.0, ["/timeline/end"]),
    (False, "1", 1.0, ["/timeline/start", "/timeline/end"]),
    (0.0, 1.0, "2", ["/timeline/duration"]),
    (0.0, 1.0, True, ["/timeline/duration"]),
])
def test_non_numeric_sweep_range_is_invalid_type(start, end, duration, bad):
    """Through the Python API (no JSON parser in front): a string or a bool
    is not a number, and is refused instead of reaching the frame builder."""
    issues = validate_animation(sine_anim(timeline=ParameterSweep(
        parameter="phase", start=start, end=end, duration=duration)))
    assert [(i.code, i.path) for i in issues if i.severity == "error"] == \
        [("invalid_type", p) for p in bad]


def test_non_finite_sweep_end_is_invalid_value():
    tl = ParameterSweep(parameter="phase", start=0.0, end=1.0, duration=1.0)
    tl.end = float("inf")  # bypasses the constructor's canonicalisation
    assert ("invalid_value", "/timeline") in [
        (i.code, i.path) for i in validate_animation(sine_anim(timeline=tl))]


def test_duration_guard_is_separate_from_frame_guard():
    issues = validate_animation(sine_anim(fps=1, timeline=ParameterSweep(
        parameter="phase", start=0, end=1, duration=90)))
    assert "duration_too_long" in codes(issues)


def test_sweeping_a_non_function_layer_is_refused():
    fig = FigureSpec(datasets={"b": weekly_data(None)},
                     layers=[LineMark(data="b", encoding={"x": "week", "y": "bookings"})])
    issues = validate_animation(AnimationSpec(
        timeline=ParameterSweep(parameter="a", values=[0, 1]), figure=fig))
    assert codes(issues) == ["not_a_symbolic_function"]


def test_figure_issues_are_reported_under_figure():
    fig = sine_figure()
    fig.layers[0].domain = {"x": [1.0, 0.0]}
    issues = validate_animation(sine_anim(figure=fig))
    assert issues and all(i.path.startswith("/figure/") for i in issues)


def test_figure_presence_rules():
    assert codes(validate_animation(AnimationSpec(
        timeline=ParameterSweep(parameter="phase", values=[0, 1])))) == ["figure_missing"]
    with_fig = vector_field_anim()
    with_fig.figure = sine_figure()
    assert "figure_not_allowed" in codes(validate_animation(with_fig))


def test_template_target_rules():
    assert codes(validate_animation(vector_field_anim(values=[0, 1]))) == []
    bad = vector_field_anim()
    bad.timeline.target.version = "0.0.1"
    assert codes(validate_animation(bad)) == ["unknown_template"]
    bad = vector_field_anim()
    bad.timeline.parameter = "grid"
    assert codes(validate_animation(bad)) == ["unsupported_parameter"]
    bad = vector_field_anim(params={"u": "-y + q*x"})
    issues = validate_animation(bad)
    assert issues[0].path.startswith("/timeline/target/params/u"), issues
    unscaled = vector_field_anim(params={"scale": None})
    assert codes(validate_animation(unscaled)) == ["quiver_rescaled_per_frame"]


def test_frame_column_rules():
    assert codes(validate_animation(weekly_anim("window"))) == ["invalid_value"]
    assert codes(validate_animation(weekly_anim("current", window=2))) == ["invalid_value"]
    a = weekly_anim()
    a.timeline = FrameColumn(dataset="bookngs", column="week")
    assert codes(validate_animation(a)) == ["unknown_dataset"]
    a.timeline = FrameColumn(dataset="b", column="wek")
    issues = validate_animation(a)
    assert codes(issues) == ["unknown_column"] and "'week'" in issues[0].message
    data = weekly_data(None)
    data.columns["week"].values[1] = None
    issues = validate_animation(weekly_anim(data=data))
    assert [(i.code, i.severity) for i in issues] == [("frame_value_missing", "warning")]


def test_quiver_without_scale_warns_in_a_frame_animation():
    data = weekly_data(None)
    snap = type(data).from_columns({"t": [0, 0, 1, 1], "x": [0, 1, 0, 1], "y": [0, 0, 1, 1],
                                    "u": [1, 1, 1, 1], "v": [0, 1, 0, 1]})
    fig = FigureSpec(datasets={"f": snap}, layers=[QuiverMark(
        data="f", encoding={"x": "x", "y": "y", "u": "u", "v": "v"})])
    a = AnimationSpec(timeline=FrameColumn(dataset="f", column="t"), figure=fig)
    assert codes(validate_animation(a)) == ["quiver_rescaled_per_frame"]


def test_poster_must_exist():
    assert codes(validate_animation(sine_anim(poster=99))) == ["invalid_poster"]
    assert codes(validate_animation(sine_anim(poster="middle"))) == ["invalid_poster"]
    assert validate_animation(sine_anim(poster=4, hold_end=0)) == []


def test_importing_the_package_loads_neither_matplotlib_nor_manim():
    code = ("import sys, ieeVisualizer.animation as a; a.validate_animation; "
            "print('matplotlib' in sys.modules, 'manim' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.split() == ["False", "False"]
