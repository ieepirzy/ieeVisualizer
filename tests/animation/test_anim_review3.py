"""Review round 3: work per export, annotation cost, heatmap frame columns
with fixed limits, MP4 encode deadline."""

import shutil

import pytest
from anim_helpers import SMALL, codes, run_limited, vector_field_anim

import ieeVisualizer.animation as A
from ieeVisualizer import templates
from ieeVisualizer.animation import (
    AnimationSpec,
    FrameColumn,
    ParameterSweep,
    export_animation,
    render_animation,
    validate_animation,
)
from ieeVisualizer.animation import frames as F
from ieeVisualizer.animation import validate as V
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
    """Call counters for whole validations and template state builds."""
    calls = {"build": 0, "validate": 0}
    for owner, name, key in [(F, "_build_template", "build"), (V, "_validate", "validate")]:
        orig = getattr(owner, name)

        def wrapped(*a, _orig=orig, _key=key, **k):
            calls[_key] += 1
            return _orig(*a, **k)

        monkeypatch.setattr(owner, name, wrapped)
    return calls


def test_an_export_validates_once_and_builds_each_state_once(spies, tmp_path):
    from ieeVisualizer.artifacts.writers import LocalDirectoryWriter

    values = [0.0, 0.5, 1.0]
    fmts = ["gif", "mp4"] if shutil.which("ffmpeg") else ["gif"]
    export_animation(vector_field_anim(values=values), fmts, LocalDirectoryWriter(tmp_path),
                     request=SMALL)
    assert spies == {"build": len(values), "validate": 1}


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
            encode_mp4(frames, 10, {exe!r}, deadline={timeout})
            code, msg = "encoded", ""
        except AnimationError as e:
            code, msg = e.issues[0].code, e.issues[0].message
        print(json.dumps({{"code": code, "msg": msg, "elapsed": time.perf_counter() - t}}))
    """


@pytest.mark.parametrize("encoders,encode,frames,said", [
    ("exec sleep 1000", "exec sleep 1000", "frames = [np.zeros((8, 8, 3), np.uint8)] * 3",
     "probing"),
    ("exit 0", "exec cat > /dev/null", "def gen():\n"
     "            time.sleep(30)\n"
     "            yield np.zeros((8, 8, 3), np.uint8)\n"
     "        frames = gen()", "frame 0 was still being drawn"),
    ("exit 0", "exec cat > /dev/null", "def gen():\n"
     "            yield np.zeros((8, 8, 3), np.uint8)\n"
     "            time.sleep(30)\n"
     "            yield np.zeros((8, 8, 3), np.uint8)\n"
     "        frames = gen()", "frame 1 was still being drawn"),
    ("exit 0", "cat > /dev/null; exec sleep 1000",
     "frames = [np.zeros((8, 8, 3), np.uint8)] * 3", "received every frame"),
    ("exit 0", "exec sleep 1000",  # never reads: 230 kB frames fill the pipe
     "frames = [np.zeros((240, 320, 3), np.uint8)] * 20", "stopped reading input"),
])
def test_mp4_encode_stops_at_its_deadline_and_says_where(tmp_path, encoders, encode, frames,
                                                          said):
    """The deadline covers the encoder probe, a slow first or later frame
    (not waited for), an ffmpeg that stops reading or never finishes; ffmpeg
    is killed and reaped (checked in test_anim_deadline)."""
    exe = _fake_ffmpeg(tmp_path, encoders, encode)
    out = run_limited(_encode_child(exe, frames, 2), timeout=40)
    assert out["code"] == "render_deadline_exceeded" and said in out["msg"], out
    assert out["elapsed"] < 6
