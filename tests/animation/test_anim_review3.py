"""Review round 3: template validation cost, annotation cost, heatmap frame
columns with fixed limits, cost-model calibration, MP4 deadline."""

import shutil

import numpy as np
import pytest
from anim_helpers import SMALL, codes, minmax_expression, run_limited, vector_field_anim

import ieeVisualizer.animation as A
from ieeVisualizer import templates
from ieeVisualizer.animation import (
    AnimationError,
    AnimationSpec,
    FrameColumn,
    ParameterSweep,
    export_animation,
    render_animation,
    validate_animation,
)
from ieeVisualizer.animation import frames as F
from ieeVisualizer.animation import validate as V
from ieeVisualizer.animation.base import enforce_limits
from ieeVisualizer.animation.cost import MAX_RENDER_WORK_US, matplotlib_work_us
from ieeVisualizer.spec import (
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    HeatmapMark,
    SpecValidationError,
)
from ieeVisualizer.spec import figure as SF

VF = type(templates.get("vector_field_2d", "1.0.0"))


@pytest.fixture
def spies(monkeypatch):
    """Call counters for the template check, its sampling dry-run, state
    builds and whole validations."""
    calls = {"check": 0, "sample": 0, "build": 0, "validate": 0}
    targets = [(VF, "check", "check"), (VF, "_sample", "sample"),
               (F, "_build_template", "build")]
    if hasattr(V, "_validate"):
        targets.append((V, "_validate", "validate"))
    for owner, name, key in targets:
        orig = getattr(owner, name)

        def wrapped(*a, _orig=orig, _key=key, **k):
            calls[_key] += 1
            return _orig(*a, **k)

        monkeypatch.setattr(owner, name, wrapped)
    return calls


# ---------------------------------------------------------------- H1


def test_heavy_template_sweep_is_validated_in_seconds_not_minutes():
    """474-character expressions, a 200 x 200 grid and 300 values took 184 s
    to validate (a SymPy rebuild and full sampling per value) and reported
    nothing. Now it is refused from the work estimate after one check."""
    out = run_limited("""
        from anim_helpers import minmax_expression, vector_field_anim
        from ieeVisualizer.animation import validate_animation
        u = minmax_expression()
        anim = vector_field_anim(params={"u": u, "v": u, "grid": 200},
                                 values=[i / 300 for i in range(300)], fps=15)
        t = time.perf_counter()
        issues = validate_animation(anim)
        print(json.dumps({"codes": [i.code for i in issues],
                          "elapsed": time.perf_counter() - t}))
    """, mem_mb=3000, timeout=40)
    assert out["codes"] == ["animation_too_expensive"]
    assert out["elapsed"] < 3.0


def test_accepted_heavy_sweep_checks_values_numerically(spies):
    """Within budget, every value is still checked, but the template's full
    check (SymPy + sampling) runs only at the first value and the extremes."""
    u = minmax_expression()
    values = [i / 12 for i in range(12)]
    anim = vector_field_anim(params={"u": u, "v": u, "grid": 20}, values=values, fps=15)
    assert [i for i in validate_animation(anim) if i.severity == "error"] == []
    assert spies["check"] <= 3  # first value (= min), max, 0 (= first)
    assert spies["sample"] == spies["check"]
    assert spies["build"] == 0


def _no_fast_path(monkeypatch):
    monkeypatch.setattr(VF, "sweep_checker", lambda self, *a: None)


@pytest.mark.parametrize("params,values,parameter", [
    ({"u": "sqrt(k - 2) + x", "v": "y"}, [3.0, 1.5, 4.0, 1.2, 5.0], "constants.k"),
    ({"u": "log(k - 2 + 0*x)", "v": "y"}, [3.0, 1.0, 4.0, 1.5, 5.0], "constants.k"),
    ({"u": "x/k", "v": "y"}, [1.0, 2.0, 0.0, 3.0], "constants.k"),
    ({}, [1.0, -1.0, 2.0, 0.0], "scale"),
    ({}, [0.0, 0.5, 1.0], "constants.k"),
])
def test_fast_path_reports_what_a_full_check_of_every_value_reports(
        monkeypatch, params, values, parameter):
    anim = vector_field_anim(params=params, values=values)
    anim.timeline.parameter = parameter
    fast = validate_animation(anim)
    _no_fast_path(monkeypatch)
    full = validate_animation(anim)
    assert [(i.path, i.code, i.message, i.severity) for i in fast] == \
        [(i.path, i.code, i.message, i.severity) for i in full]


def test_value_dependent_failure_is_reported_at_its_value():
    anim = vector_field_anim(params={"u": "sqrt(k - 2) + x", "v": "y"},
                             values=[3.0, 1.5, 4.0, 1.2, 5.0])
    issues = validate_animation(anim)
    assert [(i.path, i.code) for i in issues] == [("/timeline/values/1", "all_null_column")]
    assert "1 other value(s)" in issues[0].message


def test_error_at_the_first_value_is_reported_without_checking_every_value(spies):
    anim = vector_field_anim(params={"u": "-y + q*x"}, values=[i / 10 for i in range(50)])
    issues = validate_animation(anim)
    assert issues[0].path == "/timeline/target/params/u" and issues[0].code == "unknown_symbol"
    assert spies["check"] == 2


def test_an_export_validates_once_and_builds_each_state_once(spies, tmp_path):
    from ieeVisualizer.artifacts.writers import LocalDirectoryWriter

    values = [0.0, 0.5, 1.0]
    fmts = ["gif", "mp4"] if shutil.which("ffmpeg") else ["gif"]
    export_animation(vector_field_anim(values=values), fmts, LocalDirectoryWriter(tmp_path),
                     request=SMALL)
    assert spies["build"] == len(values)
    assert spies["check"] <= len(values) + 1
    assert spies["validate"] == 1
    # each build() samples twice (its check dry-run and the build itself)
    assert spies["sample"] <= spies["check"] + 2 * len(values)


def test_outside_a_render_validation_is_not_cached(spies):
    anim = vector_field_anim()
    validate_animation(anim)
    validate_animation(anim)
    assert spies["validate"] == 2


# ---------------------------------------------------------------- H2


def _annotated(annotations, n_states=60):
    fig = FigureSpec(layers=[FunctionMark(expression="p*sin(x)", variables=["x"],
                                          domain={"x": [0, 6.0]}, samples=20,
                                          parameters={"p": 1.0})],
                     annotations=annotations)
    return AnimationSpec(timeline=ParameterSweep(parameter="p",
                                                 values=[1.0 + i for i in range(n_states)]),
                         figure=fig, fps=10)


def _texts(n):
    return [SF.TextAnnotation(text=f"note {i}", x=i % 6, y=0.1) for i in range(n)]


@pytest.mark.parametrize("annotations,per_state_us", [
    (_texts(150), 150 * 7_000),
    ([SF.ReferenceBand(axis="x", start=i * 0.01, end=i * 0.01 + 0.005) for i in range(150)],
     150 * 1_400),
    ([SF.ReferenceLine(axis="y", value=i * 0.01, label=f"l{i}") for i in range(150)],
     150 * 9_000),
])
def test_annotations_are_charged_per_state(annotations, per_state_us):
    """Measured: about 7 ms per text annotation per state, 1.4 ms per band,
    9 ms per labelled reference line (its legend entry)."""
    bare = _annotated([])
    anim = _annotated(annotations)
    size = SMALL.frame_request().size_px
    extra = matplotlib_work_us(anim, size, 60, anim.figure) \
        - matplotlib_work_us(bare, size, 60, bare.figure)
    assert extra >= 60 * per_state_us


def test_many_text_annotations_are_refused_before_any_state_is_built():
    anim = _annotated(_texts(150))
    assert matplotlib_work_us(anim, SMALL.frame_request().size_px, 60, anim.figure) \
        > MAX_RENDER_WORK_US
    with pytest.raises(AnimationError) as e:
        enforce_limits(anim, SMALL, 60)
    assert e.value.issues[0].code == "animation_too_expensive"


def test_annotation_count_is_capped_for_animations(spies):
    anim = _annotated(_texts(A.MAX_ANIMATION_ANNOTATIONS + 1), n_states=2)
    issues = validate_animation(anim)
    assert [(i.path, i.code) for i in issues] == [("/figure/annotations",
                                                   "too_many_annotations")]
    with pytest.raises(SpecValidationError):
        render_animation(anim, SMALL)
    assert codes(validate_animation(_annotated(_texts(A.MAX_ANIMATION_ANNOTATIONS),
                                               n_states=2))) == []


# ---------------------------------------------------------------- M1


def _heat_anim(categorical: bool, axis_limits: str = "fixed"):
    rows = [(t, (f"c{i}" if categorical else i), (f"r{j}" if categorical else j),
             float(i + j + t)) for t in range(3) for i in range(2) for j in range(2)]
    kind = "str" if categorical else "int"
    ds = DataSnapshot.from_columns(
        {"t": [r[0] for r in rows], "x": [r[1] for r in rows], "y": [r[2] for r in rows],
         "z": [r[3] for r in rows]}, dtypes={"t": "int", "x": kind, "y": kind, "z": "float"})
    fig = FigureSpec(datasets={"d": ds}, layers=[HeatmapMark(
        data="d", encoding={"x": "x", "y": "y", "color": "z"})])
    return AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), figure=fig, fps=10,
                         axis_limits=axis_limits)


@pytest.mark.parametrize("categorical", [True, False])
def test_heatmap_frame_column_renders_with_fixed_limits(categorical):
    """Every frame has its own value for each cell, so the all-frames figure
    is not a valid heatmap (duplicate_cell); fixed limits come from the
    frames themselves."""
    res = render_animation(_heat_anim(categorical), SMALL)
    assert res.frame_count == 3 and res.state_count == 3


def test_numeric_heatmap_frames_share_one_range():
    from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter

    specs = MatplotlibAnimationAdapter().frame_specs(_heat_anim(False), SMALL)
    limits = {(tuple(s.axes["x"].limits), tuple(s.axes["y"].limits)) for s in specs}
    assert len(limits) == 1


# ---------------------------------------------------------------- L1


def _fake_ffmpeg(tmp_path, encoders: str, encode: str):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n"
                    f'case "$*" in *-version*) echo "fake 1"; exit 0;; '
                    f"*-encoders*) {encoders};; esac\n"
                    f"{encode}\n")
    fake.chmod(0o755)
    return str(fake)


def _encode_child(exe: str, frames_src: str, timeout: float) -> str:
    return f"""
        import numpy as np
        from ieeVisualizer.animation import AnimationError
        from ieeVisualizer.animation.encode import encode_mp4
        {frames_src}
        t = time.perf_counter()
        try:
            encode_mp4(frames, 10, {exe!r}, timeout={timeout})
            code, msg = "encoded", ""
        except AnimationError as e:
            code, msg = e.issues[0].code, e.issues[0].message
        print(json.dumps({{"code": code, "msg": msg, "elapsed": time.perf_counter() - t}}))
    """


def test_mp4_deadline_includes_the_encoder_probe(tmp_path):
    exe = _fake_ffmpeg(tmp_path, "exec sleep 1000", "exec sleep 1000")
    out = run_limited(_encode_child(exe, "frames = [np.zeros((8, 8, 3), np.uint8)] * 3", 2),
                      timeout=40)
    assert out["code"] == "encoder_timeout"
    assert out["elapsed"] < 6


def test_mp4_timeout_on_a_slow_first_frame_does_not_wait_for_it(tmp_path):
    exe = _fake_ffmpeg(tmp_path, "exit 0", "exec cat > /dev/null")
    src = ("def gen():\n"
           "            time.sleep(30)\n"
           "            yield np.zeros((8, 8, 3), np.uint8)\n"
           "        frames = gen()")
    out = run_limited(_encode_child(exe, src, 1), timeout=40)
    assert out["code"] == "encoder_timeout" and "frame 0 was still being drawn" in out["msg"]
    assert out["elapsed"] < 4


def test_mp4_timeout_while_a_frame_is_drawn_is_bounded_and_says_so(tmp_path):
    exe = _fake_ffmpeg(tmp_path, "exit 0", "exec cat > /dev/null")
    src = ("def gen():\n"
           "            yield np.zeros((8, 8, 3), np.uint8)\n"
           "            time.sleep(30)\n"
           "            yield np.zeros((8, 8, 3), np.uint8)\n"
           "        frames = gen()")
    out = run_limited(_encode_child(exe, src, 2), timeout=40)
    assert out["code"] == "encoder_timeout"
    assert "frame 1 was still being drawn" in out["msg"] and "too slow" in out["msg"]
    assert out["elapsed"] < 6


def test_mp4_timeout_when_ffmpeg_does_not_finish_says_so(tmp_path):
    exe = _fake_ffmpeg(tmp_path, "exit 0", "cat > /dev/null; exec sleep 1000")
    out = run_limited(_encode_child(exe, "frames = [np.zeros((8, 8, 3), np.uint8)] * 3", 2),
                      timeout=40)
    assert out["code"] == "encoder_timeout" and "received every frame" in out["msg"]
    assert out["elapsed"] < 6


def test_numpy_frames_still_encode():
    exe = shutil.which("ffmpeg")
    if exe is None:
        pytest.skip("no ffmpeg")
    from ieeVisualizer.animation.encode import encode_mp4

    frames = [np.full((16, 16, 3), i * 20, np.uint8) for i in range(5)]
    assert len(encode_mp4(frames, 10, exe, timeout=60)) > 0
