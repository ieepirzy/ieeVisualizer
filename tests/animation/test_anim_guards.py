"""Resource guards and poster/value checks that must hold before anything is
built: frame counts are arithmetic, every adapter is guarded, posters of
referenced frame data are range-checked, template sweeps check every value.

The oversized cases run in a child process with an address-space limit and a
timeout, so a regression (a guard checked after the lists are built) fails
the test instead of exhausting memory or hanging the run.
"""

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from anim_helpers import SMALL, codes, sine_anim, vector_field_anim, weekly_anim, weekly_data

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

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

resource = pytest.importorskip("resource", reason="address-space limits need POSIX")


def run_limited(body: str, *, mem_mb: int = 1500, timeout: float = 60.0) -> dict:
    """Run ``body`` in a child Python with RLIMIT_AS = ``mem_mb`` and a
    timeout; it must print one JSON line last. A MemoryError, a crash or a
    timeout fails the calling test with the child's output."""
    code = textwrap.dedent(f"""
        import json, sys, time
        sys.path[:0] = [{str(ROOT)!r}, {str(HERE)!r}]
        t0 = time.perf_counter()
    """) + textwrap.dedent(body)

    def limit():
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))

    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MPLBACKEND="Agg")
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=timeout, preexec_fn=limit, env=env)
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"child did not finish in {timeout} s (guard not applied before "
                    f"allocation?): {e.stdout!r} {e.stderr!r}")
    assert proc.returncode == 0, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


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
])
def test_state_count_rounds_half_up_as_documented(duration, fps, states):
    tl = ParameterSweep(parameter="p", start=0.0, end=1.0, duration=duration)
    assert len(sweep_values(tl, fps)) == states
    assert "floor(duration * fps + 0.5)" in (ROOT / "docs" / "ANIMATION.md").read_text()


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
