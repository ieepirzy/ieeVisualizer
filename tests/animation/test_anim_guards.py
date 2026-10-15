"""Resource guards and poster/value checks that must hold before anything is
built: frame counts are arithmetic, every adapter is guarded, posters of
referenced frame data are range-checked, template sweeps check every value.

The oversized cases run in a child process with an address-space limit and a
timeout, so a regression (a guard checked after the lists are built) fails
the test instead of exhausting memory or hanging the run.
"""

import dataclasses

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


def test_huge_template_sweep_duration_is_refused_before_the_template_is_checked():
    out = run_limited("""
        from anim_helpers import vector_field_anim
        from ieeVisualizer.animation import validate_animation
        anim = vector_field_anim(values=None)
        tl = anim.timeline
        tl.values, tl.start, tl.end, tl.duration = None, 0.0, 1.0, 1e7
        codes = [i.code for i in validate_animation(anim)]
        print(json.dumps({"codes": codes, "s": time.perf_counter() - t0}))
    """, timeout=20)
    assert out["codes"] == ["too_many_frames", "duration_too_long"]
    assert out["s"] < 10


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


def test_state_count_matches_exact_decimal_rounding_over_a_sweep():
    """Every duration written with up to 3 decimals x every fps: the count is
    the exact decimal product rounded half up (computed here with Fraction,
    independently of the implementation)."""
    from fractions import Fraction

    from ieeVisualizer.animation.limits import sweep_state_count

    wrong = []
    for k in range(1, 6001, 7):  # 0.001 .. 6.0 s
        text = f"{k / 1000:.3f}"
        for fps in range(1, 61):
            exact = Fraction(text) * fps
            want = max(2, int(exact + Fraction(1, 2)))  # floor(x + 1/2), exact
            if exact > 300:  # beyond MAX_FRAMES; not a useful sweep
                break
            tl = ParameterSweep(parameter="p", start=0.0, end=1.0, duration=float(text))
            got = sweep_state_count(tl, fps)
            if got != want:
                wrong.append((text, fps, got, want))
    assert not wrong, wrong[:10]


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

    def render(self, anim, request, *, resolver=None):
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


def test_an_adapter_with_its_own_look_is_never_selected_automatically():
    from ieeVisualizer.animation.manim_adapter import CAPABILITIES, ManimAdapter, manim_available
    from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
    from ieeVisualizer.render import CapabilityError

    reg = AnimationRegistry()
    reg.register(MatplotlibAnimationAdapter(ffmpeg_path="/nonexistent/ffmpeg"))
    reg.register(ManimAdapter())
    mp4 = dataclasses.replace(SMALL, format="mp4")
    with pytest.raises(CapabilityError) as e:
        reg.select(sine_anim(), mp4)
    assert "automatic selection" in str(e.value) and "ffmpeg" in str(e.value)
    pinned = dataclasses.replace(mp4, adapter="manim")
    if manim_available():
        assert reg.select(sine_anim(), pinned).name == "manim"
    assert CAPABILITIES.to_dict()["auto_select"] is False


def test_manim_adapter_refuses_oversized_clips_before_starting():
    out = run_limited("""
        from anim_helpers import sine_anim
        from ieeVisualizer.animation import AnimationError, AnimationRequest, ParameterSweep
        from ieeVisualizer.animation.manim_adapter import ManimAdapter
        anim = sine_anim(fps=60, timeline=ParameterSweep(parameter="phase", start=0.0,
                                                         end=1.0, duration=4.0))
        big = AnimationRequest(format="mp4", width=4000, height=3000, units="px", dpi=100)
        try:
            ManimAdapter().render(anim, big)
            got = "rendered"
        except AnimationError as e:
            got = [i.code for i in e.issues]
        print(json.dumps({"got": got, "s": time.perf_counter() - t0}))
    """, mem_mb=2500, timeout=60)
    assert out["got"] == ["animation_too_large"]


def test_gif_encoder_streams_a_generator_with_the_same_bytes():
    import numpy as np

    from ieeVisualizer.animation.encode import encode_gif

    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 255, (20, 30, 3), dtype=np.uint8) for _ in range(4)]
    assert encode_gif((f.copy() for f in frames), 10, True) == encode_gif(frames, 10, True)


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


def test_template_sweep_checks_every_value_not_only_the_ends():
    issues = [i for i in validate_animation(_scale_sweep([1.0, -1.0, 1.0]))
              if i.severity == "error"]
    assert [(i.path, i.code) for i in issues] == [("/timeline/values/1", "invalid_parameter")]
    assert "scale = -1" in issues[0].message and "/timeline/target/params/scale" in \
        issues[0].message
    with pytest.raises(SpecValidationError) as e:
        render_animation(_scale_sweep([1.0, 0.0, 1.0]), SMALL)
    assert e.value.issues[0].path == "/timeline/values/1"


def test_repeated_failures_are_reported_once_with_a_count():
    issues = validate_animation(_scale_sweep([1.0, 0.0, 2.0, -3.0]))
    assert [i.path for i in issues] == ["/timeline/values/1"]
    assert "1 other value(s)" in issues[0].message


def test_plan_reports_a_template_refusal_at_the_sweep_value(monkeypatch):
    # A value the template's check() misses but build() refuses must still be
    # reported at the sweep value, not as a bare template-internal path.
    from ieeVisualizer import templates

    tpl = templates.get("vector_field_2d", "1.0.0")
    monkeypatch.setattr(type(tpl), "check", lambda self, data=None, **p: [])
    with pytest.raises(SpecValidationError) as e:
        plan(_scale_sweep([1.0, -1.0, 1.0]))
    assert e.value.issues[0].path == "/timeline/values/1"
    assert "scale = -1" in e.value.issues[0].message


# ------------------------------------------------- round 2: MP4 write timeout


def test_mp4_encode_times_out_when_ffmpeg_stops_reading(tmp_path):
    """A fake ffmpeg on PATH that never reads stdin: the frame writes block
    on a full pipe. The encode must give up at its timeout with a structured
    error and leave no child process (running or zombie) behind."""
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n"
                    'case "$*" in *-version*) echo "fake 1"; exit 0;; '
                    "*-encoders*) exit 0;; esac\n"
                    "exec sleep 1000\n")
    fake.chmod(0o755)
    out = run_limited(f"""
        import os
        os.environ["PATH"] = {str(tmp_path)!r} + os.pathsep + os.environ["PATH"]
        import numpy as np
        from ieeVisualizer.animation import AnimationError
        from ieeVisualizer.animation.encode import encode_mp4, find_ffmpeg
        exe = find_ffmpeg()
        frames = [np.zeros((240, 320, 3), np.uint8)] * 20  # 230 kB each > a pipe buffer
        t = time.perf_counter()
        try:
            encode_mp4(frames, 10, exe, timeout=2)
            code = "encoded"
        except AnimationError as e:
            code = e.issues[0].code
        elapsed = time.perf_counter() - t
        me = str(os.getpid())
        kids = []
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                stat = open(f"/proc/{{pid}}/stat").read()
            except OSError:
                continue
            fields = stat.rsplit(")", 1)[1].split()
            if fields[1] == me:
                kids.append((pid, fields[0]))
        print(json.dumps({{"exe": exe, "code": code, "elapsed": elapsed, "kids": kids}}))
    """, timeout=40)
    assert out["exe"] == str(fake)
    assert out["code"] == "encoder_timeout"
    assert out["elapsed"] < 15
    assert out["kids"] == []


# ------------------------------------------------- round 2: render-work budget


def _layers_anim(n_layers: int, n_states: int):
    from ieeVisualizer.animation import AnimationSpec
    from ieeVisualizer.spec import FigureSpec, FunctionMark

    layers = [FunctionMark(expression="sin(x + phase)", variables=["x"], domain={"x": [0, 1]},
                           samples=2, parameters={"phase": 0.0}) for _ in range(n_layers)]
    return AnimationSpec(timeline=ParameterSweep(parameter="phase",
                                                 values=[i / 10 for i in range(n_states)]),
                         figure=FigureSpec(layers=layers), fps=30)


def test_too_much_work_per_state_is_refused_before_any_state_is_built():
    """1000 function layers x 300 states (about 40 minutes of drawing) passes
    the frame and pixel guards; the work guard refuses it in milliseconds,
    through render_animation and through the adapter called directly."""
    out = run_limited("""
        from ieeVisualizer.animation import AnimationError, AnimationRequest, render_animation
        from ieeVisualizer.animation import frames as F
        from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
        from test_anim_guards import _layers_anim
        built = [0]
        orig = F._clone
        def spy(spec):
            built[0] += 1
            return orig(spec)
        F._clone = spy
        anim = _layers_anim(1000, 300)
        req = AnimationRequest(width=64, height=48, units="px", dpi=20)
        got = []
        for call in (lambda: render_animation(anim, req),
                     lambda: MatplotlibAnimationAdapter().render(anim, req),
                     lambda: MatplotlibAnimationAdapter().plan(anim, req)):
            try:
                call()
                got.append("rendered")
            except AnimationError as e:
                got.append(e.issues[0].code)
        print(json.dumps({"codes": got, "built": built[0],
                          "elapsed": time.perf_counter() - t0}))
    """, timeout=60)
    assert out["codes"] == ["animation_too_expensive"] * 3
    assert out["built"] == 0
    assert out["elapsed"] < 20  # interpreter start-up and imports included


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
    Validation refuses it after one template check at the first value and
    before any per-value work: no state is built, the template is checked
    (and sampled) once, not once per value."""
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
    assert calls == {"build": 0, "check": 1, "sample": 1}


def test_manim_has_a_stricter_per_frame_points_budget():
    """A 200,000-sample curve (about 5 s per Manim frame) is refused before
    Manim is imported or started; the same spec is within the Matplotlib
    budget. Works without manim installed."""
    from ieeVisualizer.animation.cost import (
        MAX_RENDER_WORK_US,
        manim_work_us,
        matplotlib_work_us,
    )
    from ieeVisualizer.animation.manim_adapter import ManimAdapter

    anim = sine_anim(timeline=ParameterSweep(parameter="phase",
                                             values=[i / 10 for i in range(30)]))
    anim.figure.layers[0].samples = 200_000
    assert matplotlib_work_us(anim, (160, 120), 30, anim.figure) < MAX_RENDER_WORK_US
    assert manim_work_us(anim, 30) > MAX_RENDER_WORK_US
    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(anim, AnimationRequest(format="mp4", width=160, height=120,
                                                     units="px"))
    assert e.value.issues[0].code == "animation_too_expensive"
    assert "manim" in e.value.issues[0].message


# ------------------------------------- round 2: guards before template states


def test_direct_adapter_render_checks_pixels_before_building_template_states():
    from ieeVisualizer.animation import frames as F
    from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter

    calls = []
    orig = F._build_template
    F._build_template = lambda *a: calls.append(a) or orig(*a)
    try:
        with pytest.raises(AnimationError) as e:
            MatplotlibAnimationAdapter().render(
                vector_field_anim(values=[i / 10 for i in range(200)]),
                AnimationRequest(width=2000, height=2000, units="px"))
    finally:
        F._build_template = orig
    assert e.value.issues[0].code == "animation_too_large" and calls == []


# ------------------------------------------------- round 2: request overflow


@pytest.mark.parametrize("kw", [
    {"width": 1e300, "height": 1e300, "units": "in", "dpi": 1e300},
    {"width": 1e300, "height": 1.0, "units": "in", "dpi": 1e10},
])
def test_overflowing_request_size_is_a_value_error(kw):
    with pytest.raises(ValueError, match="no representable pixel size"):
        AnimationRequest(**kw)
