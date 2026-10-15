"""Manim adapter. Capability tests always run; render tests need ``manim``
(skipped when it does not import, e.g. in CI)."""

import io

import numpy as np
import pytest
from anim_helpers import gif_frames, sine_anim, vector_field_anim, weekly_anim

from ieeVisualizer.animation import AnimationError, AnimationRequest
from ieeVisualizer.animation.manim_adapter import CAPABILITIES, ManimAdapter, manim_available
from ieeVisualizer.spec import FigureSpec, FunctionMark, ReferenceLine

REQ = AnimationRequest(format="mp4", width=320, height=180, units="px", dpi=50)
needs_manim = pytest.mark.skipif(not manim_available(), reason="manim is not installed")


def test_capabilities_are_narrow_and_explicit():
    d = CAPABILITIES.to_dict()
    assert d["timelines"] == ["parameter_sweep:mark", "parameter_sweep:template"]
    assert d["figure"]["mark_types"] == ["function"]
    assert d["figure"]["coordinate_systems"] == ["cartesian2d"]
    assert d["figure"]["annotation_types"] == []
    assert d["templates"] == ["vector_field_2d"]


def test_unsupported_specs_are_reported_not_approximated():
    m = ManimAdapter()
    assert any("frame_column" in u for u in m.unmet(weekly_anim(), REQ))
    two_var = sine_anim()
    two_var.figure = FigureSpec(layers=[FunctionMark(
        expression="sin(x*y + phase)", variables=["x", "y"],
        domain={"x": [0, 1], "y": [0, 1]}, parameters={"phase": 0.0})],
        coordinate_system="cartesian3d")
    unmet = m.unmet(two_var, REQ)
    assert any("cartesian3d" in u for u in unmet)
    assert any("one-variable" in u for u in unmet)
    annotated = sine_anim()
    annotated.figure.annotations = [ReferenceLine(axis="y", value=0.5)]
    assert any("annotation" in u for u in m.unmet(annotated, REQ))
    sampled_vf = vector_field_anim()
    sampled_vf.timeline.target.data = {"x": [0], "y": [0], "u": [1], "v": [1]}
    assert any("expression mode" in u for u in m.unmet(sampled_vf, REQ))
    if not manim_available():
        assert any("not importable" in u for u in m.unmet(sine_anim(), REQ))
        with pytest.raises(AnimationError):
            m.render(sine_anim(), REQ)


def _video_frames(content: bytes) -> list[np.ndarray]:
    import av

    with av.open(io.BytesIO(content)) as c:
        return [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]


def _ink(frame: np.ndarray) -> float:
    return float((frame.min(axis=2) < 200).mean())


@needs_manim
def test_function_sweep_renders_one_frame_per_state_plus_holds():
    anim = sine_anim(hold_start=1, hold_end=2)  # 5 states at 10 fps
    res = ManimAdapter().render(anim, REQ)
    assert res.adapter_name == "manim" and res.content[4:8] == b"ftyp"
    frames = _video_frames(res.content)
    assert len(frames) == res.frame_count == 1 + 5 + 2
    assert frames[0].shape == (180, 320, 3)
    assert all(_ink(f) > 0.005 for f in frames)  # never blank
    # phase 0 and phase 2*pi are the same curve; phase pi is its mirror image
    assert np.abs(frames[1].astype(int) - frames[5].astype(int)).mean() < \
        np.abs(frames[1].astype(int) - frames[3].astype(int)).mean()


@needs_manim
def test_gif_output_uses_the_shared_pillow_encoder():
    res = ManimAdapter().render(sine_anim(), AnimationRequest(
        format="gif", width=320, height=180, units="px"))
    frames, delays = gif_frames(res.content)
    assert res.content[:6] == b"GIF89a" and sum(delays) == 5 * 100
    assert frames[0].shape == (180, 320, 3)


@needs_manim
def test_vector_field_arrows_share_one_length_scale_across_frames():
    # k = 0: pure rotation |v| = r; k = 1.5: |v| = r * sqrt(1 + k^2), ~1.8x longer
    res = ManimAdapter().render(vector_field_anim(values=[0.0, 1.5]), REQ)
    frames = _video_frames(res.content)
    assert len(frames) == 2
    assert _ink(frames[-1]) > 1.3 * _ink(frames[0])


@needs_manim
def test_gif_result_reports_the_stored_delay_frame_mapping_and_poster():
    anim = sine_anim(fps=15, hold_start=1, hold_end=2)  # 8 states at 15 fps
    res = ManimAdapter().render(anim, AnimationRequest(format="gif", width=160, height=120,
                                                       units="px", adapter="manim"))
    _, delays = gif_frames(res.content)
    assert res.frame_duration_ms == 70.0 and set(delays) == {70}
    assert "frame_timing_rounded" in [w.code for w in res.warnings]
    assert res.frames == [0] + list(range(8)) + [7, 7] and res.frame_count == len(res.frames)
    assert res.poster_frame == len(res.frames) - 1 and res.poster_png[:4] == b"\x89PNG"
    assert res.duration_s == pytest.approx(len(res.frames) * 0.07)


@needs_manim
def test_mp4_result_reports_timing_and_loop_like_the_matplotlib_adapter():
    res = ManimAdapter().render(sine_anim(poster="first"), REQ)
    assert res.frame_duration_ms == 100.0 and res.poster_frame == 0
    assert res.frames == [0, 1, 2, 3, 4]
    assert "loop_ignored" in [w.code for w in res.warnings]


@needs_manim
def test_export_records_the_manim_frame_as_poster_with_its_adapter(tmp_path):
    import json

    from anim_helpers import png_pixels

    from ieeVisualizer.animation import export_animation
    from ieeVisualizer.artifacts import MemoryWriter

    w = MemoryWriter()
    req = AnimationRequest(format="mp4", width=320, height=180, units="px", dpi=50,
                           adapter="manim")
    art = export_animation(sine_anim(poster=2), "mp4", w, request=req)
    files = {name: c for (aid, name), c in w.files.items() if aid == art.artifact_id}
    m = json.loads(files["manifest.json"])
    assert m["frames"] == [0, 1, 2, 3, 4] and m["frame_count"] == 5 and m["poster_frame"] == 2
    assert m["poster"]["adapter"]["name"] == "manim"
    assert m["poster"]["warnings"][0]["code"] == "poster_from_clip"
    poster = png_pixels(files["poster.png"])
    frame = _video_frames(files["animation.mp4"])[2]
    assert poster.shape == frame.shape and np.array_equal(poster, frame)


@needs_manim
def test_odd_request_size_is_kept_in_gif_and_poster():
    """Manim renders even sizes; frames are cropped back to the request, so
    the GIF and poster are 161x121 as asked; the MP4 says it is padded."""
    from anim_helpers import png_pixels

    odd = dict(width=161, height=121, units="px", adapter="manim")
    gif = ManimAdapter().render(sine_anim(poster=1), AnimationRequest(format="gif", **odd))
    frames, _ = gif_frames(gif.content)
    assert {f.shape for f in frames} == {(121, 161, 3)}
    assert png_pixels(gif.poster_png).shape == (121, 161, 3)
    assert "frame_size_padded" not in [w.code for w in gif.warnings]
    mp4 = ManimAdapter().render(sine_anim(poster=1), AnimationRequest(format="mp4", **odd))
    assert _video_frames(mp4.content)[0].shape == (122, 162, 3)
    assert png_pixels(mp4.poster_png).shape == (121, 161, 3)
    assert "frame_size_padded" in [w.code for w in mp4.warnings]
