"""Guards that hold before anything is built (oversized cases run in a child
with an address-space limit and a timeout, so a regression fails, not hangs)."""


import pytest
from anim_helpers import (
    ROOT,
    SMALL,
    codes,
    run_limited,
    sine_anim,
    vector_field_anim,
    weekly_anim,
    weekly_data,
)

from ieeVisualizer.animation import (
    AnimationCapabilities,
    AnimationError,
    AnimationRegistry,
    AnimationRequest,
    ParameterSweep,
    export_animation,
    plan,
    render_animation,
    validate_animation,
)
from ieeVisualizer.animation.frames import sweep_values
from ieeVisualizer.render import Capabilities
from ieeVisualizer.spec import DatasetRef, SpecValidationError

resource = pytest.importorskip("resource", reason="address-space limits need POSIX")


# ---------------------------------------------------------------- HIGH 1


def test_huge_sweep_duration_is_refused_before_any_value_is_built():
    out = run_limited("""
        from anim_helpers import sine_anim
        from ieeVisualizer.animation import ParameterSweep, plan, validate_animation
        from ieeVisualizer.spec import SpecValidationError
        anim = sine_anim(fps=60, timeline=ParameterSweep(parameter="phase", start=0.0,
                                                         end=1.0, duration=1e7))
        codes = [i.code for i in validate_animation(anim)]
        try:
            plan(anim)  # defensive: plan() guards even when validation was skipped
            planned = "built"
        except SpecValidationError as e:
            planned = [i.code for i in e.issues]
        print(json.dumps({"codes": codes, "plan": planned, "s": time.perf_counter() - t0}))
    """)
    assert out["codes"] == ["too_many_frames", "duration_too_long"]
    assert out["plan"] == ["too_many_frames", "duration_too_long"]


def test_hold_frames_of_a_referenced_frame_dataset_are_guarded_at_every_stage():
    out = run_limited("""
        from anim_helpers import SMALL, weekly_anim, weekly_data
        from ieeVisualizer.animation import plan, render_animation, validate_animation
        from ieeVisualizer.spec import DatasetRef, SpecValidationError

        def refd(**kw):
            anim = weekly_anim("current", **kw)
            anim.figure.datasets["b"] = DatasetRef(kind="query", id="w")
            return anim

        res = {}
        for name, anim in (("hold", refd(hold_end=3_000_000_000)),
                           ("fpv", refd())):
            if name == "fpv":
                anim.timeline.frames_per_value = 10 ** 9
            res[name + "_validate"] = [i.code for i in validate_animation(anim)
                                       if i.severity == "error"]
            for stage, call in (("render", lambda: render_animation(
                                    anim, SMALL, resolver=lambda r: weekly_data(None))),
                                ("plan", lambda: plan(anim, lambda r: weekly_data(None)))):
                try:
                    call()
                    res[name + "_" + stage] = "built"
                except SpecValidationError as e:
                    res[name + "_" + stage] = [i.code for i in e.issues]
        print(json.dumps(res))
    """)
    for name in ("hold", "fpv"):
        assert out[name + "_validate"] == ["too_many_frames", "duration_too_long"], out
        assert out[name + "_render"] == ["too_many_frames", "duration_too_long"], out
        assert out[name + "_plan"] == ["too_many_frames", "duration_too_long"], out


def test_many_listed_values_are_counted_not_expanded():
    anim = sine_anim(timeline=ParameterSweep(parameter="phase", values=[0.0] * 100_000))
    assert codes(validate_animation(anim)) == ["too_many_frames", "duration_too_long"]


def test_non_finite_duration_is_an_issue_not_an_exception():
    anim = sine_anim()
    anim.timeline.duration = float("inf")  # bypasses the constructor's check
    assert codes(validate_animation(anim)) == ["invalid_value"]


# ---------------------------------------------------------------- NIT 6


@pytest.mark.parametrize("duration,fps,states", [
    (0.25, 10, 3),   # 2.5 -> 3: half rounds up (round() would give 2)
    (0.35, 10, 4),   # 3.5 -> 4
    (0.45, 10, 5),   # 4.5 -> 5 (round() would give 4)
    (0.5, 10, 5),
    (0.04, 10, 2),   # at least two states
    (4.1, 15, 62),   # 61.5 -> 62; binary 4.1 * 15 + 0.5 = 61.999... floored to 61
])
def test_state_count_rounds_half_up_as_documented(duration, fps, states):
    tl = ParameterSweep(parameter="p", start=0.0, end=1.0, duration=duration)
    assert len(sweep_values(tl, fps)) == states
    assert "rounded half up, computed in decimal" in (ROOT / "docs" / "ANIMATION.md").read_text()


# ---------------------------------------------------------------- HIGH 2


class _RecordingAdapter:
    """A capable adapter that records whether it was asked to render."""

    name, version = "recording", "0"

    def __init__(self):
        self.calls = 0
        self.capabilities = AnimationCapabilities(
            figure=Capabilities(coordinate_systems=frozenset({"cartesian2d"}),
                                mark_types=frozenset({"function"}),
                                output_formats=frozenset({"png"}), animation=True),
            timelines=frozenset({"parameter_sweep:mark"}),
            output_formats=frozenset({"gif", "mp4"}))

    def unmet(self, anim, request):
        return []

    def render(self, anim, request, *, resolver=None, deadline=None):
        self.calls += 1
        raise AssertionError("render must not be reached")


def test_pixel_guard_is_enforced_for_every_adapter_before_it_renders():
    reg = AnimationRegistry()
    adapter = reg.register(_RecordingAdapter())
    anim = sine_anim(fps=60, timeline=ParameterSweep(parameter="phase", start=0.0, end=1.0,
                                                     duration=4.0))
    big = AnimationRequest(format="mp4", width=4000, height=3000, units="px", dpi=100)
    with pytest.raises(AnimationError) as e:
        render_animation(anim, big, registry=reg)
    assert codes(e.value.issues) == ["animation_too_large"]
    assert adapter.calls == 0


def test_pixel_guard_counts_hold_frames():
    # 5 states x 800x800 px = 3.2 M px, but 205 output frames x 640 000 px =
    # 131 M px are encoded: hold frames count.
    anim = sine_anim(hold_end=200)
    req = AnimationRequest(width=800, height=800, units="px", dpi=100)
    with pytest.raises(AnimationError) as e:
        render_animation(anim, req)
    assert codes(e.value.issues) == ["animation_too_large"]


# ---------------------------------------------------------------- MEDIUM 3


def _ref_anim(**kw):
    anim = weekly_anim("current", **kw)
    anim.figure.datasets["b"] = DatasetRef(kind="query", id="w")
    return anim


def test_poster_of_a_referenced_frame_dataset_is_range_checked_after_resolution():
    anim = _ref_anim(poster=99)
    assert "invalid_poster" not in codes(validate_animation(anim))  # count unknown yet
    with pytest.raises(SpecValidationError) as e:
        render_animation(anim, SMALL, resolver=lambda ref: weekly_data(None))
    assert [(i.path, i.code) for i in e.value.issues] == [("/poster", "invalid_poster")]
    assert "0..3" in e.value.issues[0].message
    with pytest.raises(SpecValidationError):
        plan(anim, lambda ref: weekly_data(None))


def test_export_with_a_bad_poster_raises_a_structured_error_and_writes_nothing():
    from ieeVisualizer.artifacts import MemoryWriter

    w = MemoryWriter()
    with pytest.raises(SpecValidationError) as e:
        export_animation(_ref_anim(poster=99), "gif", w, request=SMALL,
                         resolver=lambda ref: weekly_data(None))
    assert codes(e.value.issues) == ["invalid_poster"] and w.files == {}


@pytest.mark.parametrize("anim", [sine_anim(poster=-1), _ref_anim(poster=-1),
                                  weekly_anim("current", poster=-1)])
def test_negative_poster_indices_are_refused(anim):
    bad = [i for i in validate_animation(anim) if i.code == "invalid_poster"]
    assert len(bad) == 1 and "negative" in bad[0].message


# ---------------------------------------------------------------- MEDIUM 5


def _scale_sweep(values):
    anim = vector_field_anim(values=values)
    anim.timeline.parameter = "scale"
    return anim


def test_template_refusal_at_a_later_value_is_reported_at_that_value(monkeypatch):
    """Validation checks the template at its first value; a value build()
    refuses is reported when its state is built (under the deadline), at
    the sweep value and naming the template path."""
    assert not [i for i in validate_animation(_scale_sweep([1.0, -1.0, 1.0]))
                if i.severity == "error"]
    for call in (lambda a: render_animation(a, SMALL), plan):
        with pytest.raises(SpecValidationError) as e:
            call(_scale_sweep([1.0, -1.0, 1.0]))
        assert e.value.issues[0].path == "/timeline/values/1"
        assert "scale = -1" in e.value.issues[0].message


# ------------------------------------------------- round 2: render-work budget


def _layers_anim(n_layers: int, n_states: int):
    from ieeVisualizer.animation import AnimationSpec
    from ieeVisualizer.spec import FigureSpec, FunctionMark

    layers = [FunctionMark(expression="sin(x + phase)", variables=["x"], domain={"x": [0, 1]},
                           samples=2, parameters={"phase": 0.0}) for _ in range(n_layers)]
    return AnimationSpec(timeline=ParameterSweep(parameter="phase",
                                                 values=[i / 10 for i in range(n_states)]),
                         figure=FigureSpec(layers=layers), fps=30)


def test_too_much_work_per_state_is_refused_before_any_state_is_built(monkeypatch):
    """1000 function layers x 300 states (about 40 minutes of drawing) passes
    the frame and pixel guards; the estimate refuses it before any state is
    built, through render_animation and through the adapter called directly."""
    from ieeVisualizer.animation import frames as F
    from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter

    built = []
    monkeypatch.setattr(F, "_labelled", lambda *a: built.append(a))
    anim = _layers_anim(1000, 300)
    req = AnimationRequest(width=64, height=48, units="px", dpi=20)
    for call in (render_animation, MatplotlibAnimationAdapter().render,
                 MatplotlibAnimationAdapter().plan):
        with pytest.raises(AnimationError) as e:
            call(anim, req)
        assert e.value.issues[0].code == "animation_too_expensive"
    assert built == []


def test_work_budget_counts_points_groups_and_frame_size():
    from ieeVisualizer.animation import AnimationRequest
    from ieeVisualizer.animation.cost import MAX_RENDER_WORK_US, matplotlib_work_us

    small = (64, 48)
    few = _layers_anim(1, 50)
    many = _layers_anim(40, 50)
    assert matplotlib_work_us(many, small, 50, many.figure) > \
        matplotlib_work_us(few, small, 50, few.figure) + 39 * 50 * 9_000
    big = AnimationRequest(width=640, height=480, units="px").frame_request().size_px
    assert matplotlib_work_us(few, big, 50, few.figure) > \
        matplotlib_work_us(few, small, 50, few.figure)
    # The examples and the defaults stay well inside the budget.
    assert matplotlib_work_us(sine_anim(), small, 5, sine_anim().figure) < \
        MAX_RENDER_WORK_US / 50


def test_frame_column_work_counts_only_the_rows_each_state_shows():
    from ieeVisualizer.animation.cost import frame_rows, matplotlib_work_us
    from ieeVisualizer.spec import DataSnapshot

    ds = DataSnapshot.from_columns({"t": [0, 0, 1, 2, 2, 2, None], "y": [1] * 7})
    from ieeVisualizer.animation import FrameColumn

    assert frame_rows(ds, FrameColumn(dataset="d", column="t")) == [2, 1, 3]
    assert frame_rows(ds, FrameColumn(dataset="d", column="t", display="cumulative")) == \
        [2, 3, 6]
    assert frame_rows(ds, FrameColumn(dataset="d", column="t", display="window",
                                      window=2)) == [2, 3, 4]
    cur, cum = weekly_anim("current"), weekly_anim("cumulative")
    assert matplotlib_work_us(cur, (64, 48), 4, cur.figure) < \
        matplotlib_work_us(cum, (64, 48), 4, cum.figure)


def test_huge_frame_column_is_refused_for_its_work():
    from ieeVisualizer.animation import AnimationSpec, FrameColumn
    from ieeVisualizer.spec import DataSnapshot, FigureSpec, LineMark

    n = 400_000
    ds = DataSnapshot.from_columns({"t": [i % 100 for i in range(n)], "y": [0.5] * n})
    anim = AnimationSpec(timeline=FrameColumn(dataset="d", column="t", display="cumulative"),
                         figure=FigureSpec(datasets={"d": ds}, layers=[LineMark(
                             data="d", encoding={"x": "t", "y": "y"})]), fps=10)
    with pytest.raises(AnimationError) as e:
        render_animation(anim, SMALL)
    assert e.value.issues[0].code == "animation_too_expensive"


def test_template_sweep_work_is_estimated_before_the_template_runs(monkeypatch):
    """300 states x 6400 arrows: over budget for every adapter at any size.
    Validation refuses it before the template runs at all: no state is
    built, the template is never checked or sampled."""
    from ieeVisualizer import templates
    from ieeVisualizer.animation import frames as F

    anim = vector_field_anim(values=[i / 10 for i in range(300)], params={"grid": 80},
                             fps=30)
    cls = type(templates.get("vector_field_2d", "1.0.0"))
    calls = {"build": 0, "check": 0, "sample": 0}

    def spy(owner, name, key):
        orig = getattr(owner, name)

        def wrapped(*a, **k):
            calls[key] += 1
            return orig(*a, **k)

        monkeypatch.setattr(owner, name, wrapped)

    spy(F, "_build_template", "build")
    spy(cls, "check", "check")
    spy(cls, "_sample", "sample")
    with pytest.raises((SpecValidationError, AnimationError)) as e:
        render_animation(anim, SMALL)
    assert e.value.issues[0].code == "animation_too_expensive"
    assert calls == {"build": 0, "check": 0, "sample": 0}


# ------------------------------------------------- round 2: request overflow


@pytest.mark.parametrize("kw", [
    {"width": 1e300, "height": 1e300, "units": "in", "dpi": 1e300},
    {"width": 1e300, "height": 1.0, "units": "in", "dpi": 1e10},
])
def test_overflowing_request_size_is_a_value_error(kw):
    with pytest.raises(ValueError, match="no representable pixel size"):
        AnimationRequest(**kw)
