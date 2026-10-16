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
    assert m["poster"]["warnings"][0]["code"] == "poster_frame"
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


# ---------------------------------------------- selection and guards (core)


def test_an_adapter_with_its_own_look_is_never_selected_automatically():
    import dataclasses

    from ieeVisualizer.animation import AnimationRegistry, render_animation
    from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
    from ieeVisualizer.render import CapabilityError

    reg = AnimationRegistry()
    reg.register(MatplotlibAnimationAdapter(ffmpeg_path="/nonexistent/ffmpeg"))
    reg.register(ManimAdapter())
    mp4 = AnimationRequest(format="mp4", width=160, height=120, units="px", dpi=50)
    with pytest.raises(CapabilityError) as e:
        reg.select(sine_anim(), mp4)
    assert "automatic selection" in str(e.value) and "ffmpeg" in str(e.value)
    if manim_available():
        assert reg.select(sine_anim(), dataclasses.replace(mp4, adapter="manim")).name == "manim"
    only = AnimationRegistry()
    only.register(ManimAdapter())
    with pytest.raises(CapabilityError) as e:
        render_animation(weekly_anim(), mp4, registry=only)
    assert "frame_column" in str(e.value)


def test_manim_budget_is_per_output_frame_and_checked_before_starting():
    """A 200,000-sample curve (about 5 s per Manim frame) is refused before
    Manim starts; the same spec is within the Matplotlib budget."""
    from ieeVisualizer.animation import ParameterSweep
    from ieeVisualizer.animation.cost import (
        MAX_RENDER_WORK_US,
        manim_work_us,
        matplotlib_work_us,
    )

    anim = sine_anim(timeline=ParameterSweep(parameter="phase",
                                             values=[i / 10 for i in range(30)]))
    anim.figure.layers[0].samples = 200_000
    assert matplotlib_work_us(anim, (160, 120), 30, anim.figure) < MAX_RENDER_WORK_US
    assert manim_work_us(anim, 30) > MAX_RENDER_WORK_US
    for req in (REQ, AnimationRequest(format="mp4", width=4000, height=2000, units="px")):
        with pytest.raises(AnimationError) as e:
            ManimAdapter().render(anim, req)
        assert e.value.issues[0].code in ("animation_too_expensive", "animation_too_large")


# ---------------------------------------------------------------- round 4


def _two_layers():
    from ieeVisualizer.animation import AnimationSpec, ParameterSweep

    fig = FigureSpec(layers=[
        FunctionMark(expression="a*sin(x)", variables=["x"], domain={"x": [0, 6]},
                     parameters={"a": 1.0}),
        FunctionMark(expression="a*cos(x)", variables=["x"], domain={"x": [0, 6]},
                     parameters={"a": 0.5})])
    return AnimationSpec(timeline=ParameterSweep(parameter="a", values=[1.0, 2.0]), figure=fig,
                         fps=10)


def test_log_axis_is_reported_as_unmet_not_drawn_linear():
    from ieeVisualizer.spec import Axis

    anim = sine_anim()
    anim.figure.axes["y"] = Axis(scale="log")
    assert any("scale 'log'" in u for u in ManimAdapter().unmet(anim, REQ))


@needs_manim
def test_another_layers_parameter_of_the_swept_name_keeps_its_value():
    """Layer 1's own 'a' was dropped: unknown_symbol at /figure/layers/1."""
    res = ManimAdapter().render(_two_layers(), AnimationRequest(
        format="gif", width=160, height=120, units="px", dpi=50))
    assert res.frame_count == 2


@needs_manim
def test_curves_are_cut_at_the_axis_range_not_clamped(monkeypatch):
    """With y limits [-0.5, 0.5] a sine was drawn along the edges."""
    from ieeVisualizer.animation import manim_adapter as MA
    from ieeVisualizer.spec import Axis

    seen = []
    orig = MA._clip
    monkeypatch.setattr(MA, "_clip", lambda run, xr, yr: seen.extend(orig(run, xr, yr)) or
                        orig(run, xr, yr))
    anim = sine_anim()
    anim.figure.axes["y"] = Axis(limits=[-0.5, 0.5])
    ManimAdapter().render(anim, REQ)
    ys = [np.array([y for _, y in piece]) for piece in seen]
    assert ys and max(abs(y).max() for y in ys) <= 0.5 + 1e-9
    # a clamped curve has many points on the edge; a cut one only its ends
    assert max(int(np.isclose(abs(y), 0.5).sum()) for y in ys) <= 2


@needs_manim
def test_theme_colours_are_used():
    from anim_helpers import png_pixels

    from ieeVisualizer import themes

    dark = next(t for t in map(themes.get, (d["name"] for d in themes.list_themes()))
                if t.background and int(t.background[1:3], 16) < 64)
    want = tuple(int(dark.background[i:i + 2], 16) for i in (1, 3, 5))
    res = ManimAdapter().render(sine_anim(), AnimationRequest(
        format="gif", width=160, height=120, units="px", theme=dark.name))
    assert np.abs(png_pixels(res.poster_png)[2, 2].astype(int) - want).max() <= 3
    unknown = ManimAdapter().render(sine_anim(), AnimationRequest(
        format="gif", width=160, height=120, units="px", theme="no-such-theme"))
    assert "unknown_theme" in [w.code for w in unknown.warnings]


@needs_manim
def test_concurrent_renders_are_serialized_and_quiet(capfd):
    """Manim's global config made concurrent renders return the other
    render's size, lose files or fail; progress_bar 'none' was ignored."""
    import threading

    from PIL import Image

    out = {}

    def job(name, n, w, h):
        from ieeVisualizer.animation import ParameterSweep

        anim = sine_anim(timeline=ParameterSweep(parameter="phase",
                                                 values=[i / 10 for i in range(n)]))
        try:
            r = ManimAdapter().render(anim, AnimationRequest(format="gif", width=w, height=h,
                                                             units="px"))
            out[name] = (Image.open(io.BytesIO(r.content)).size, r.frame_count)
        except Exception as e:  # noqa: BLE001 - recorded and asserted below
            out[name] = repr(e)

    ts = [threading.Thread(target=job, args=("A", 20, 320, 240)),
          threading.Thread(target=job, args=("B", 8, 200, 150))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == {"A": ((320, 240), 20), "B": ((200, 150), 8)}
    err = capfd.readouterr().err
    assert "it/s" not in err and "%|" not in err


@needs_manim
def test_manim_render_and_export_stop_at_the_deadline():
    import time

    from ieeVisualizer.animation import ParameterSweep, export_animation
    from ieeVisualizer.artifacts import MemoryWriter

    anim = sine_anim(fps=30, timeline=ParameterSweep(parameter="phase",
                                                     values=[i / 50 for i in range(200)]))
    for call in (lambda: ManimAdapter().render(anim, REQ, deadline=1.0),
                 lambda: export_animation(anim, "mp4", MemoryWriter(), deadline=1.0,
                                          request=AnimationRequest(
                                              format="mp4", width=320, height=180,
                                              units="px", adapter="manim"))):
        t = time.perf_counter()
        with pytest.raises(AnimationError) as e:
            call()
        assert e.value.issues[0].code == "render_deadline_exceeded"
        assert time.perf_counter() - t < 1.0 + 2.0


@needs_manim
def test_manim_export_builds_no_template_state(monkeypatch):
    """Export re-planned every template state outside the guard (238 s for
    a 27 s render); the poster and frame mapping now come from the clip."""
    from ieeVisualizer.animation import export_animation
    from ieeVisualizer.animation import frames as F
    from ieeVisualizer.artifacts import MemoryWriter

    calls = []
    monkeypatch.setattr(F, "_build_template", lambda *a: calls.append(a))
    req = AnimationRequest(format="mp4", width=160, height=120, units="px", adapter="manim")
    art = export_animation(vector_field_anim(), "mp4", MemoryWriter(), request=req)
    assert calls == [] and art.poster.adapter_name == "manim"


# ------------------------------------------------------------ review of #23


def _fm(expr, dom=(-1.0, 1.0), axes=None, samples=50):
    from ieeVisualizer.animation import AnimationSpec, ParameterSweep

    fig = FigureSpec(layers=[FunctionMark(expression=expr, variables=["x"], domain={"x": list(dom)},
                                          samples=samples, parameters={"a": 1.0})], axes=axes or {})
    return AnimationSpec(timeline=ParameterSweep(parameter="a", values=[1.0, 2.0]), figure=fig,
                         fps=4)


GIF = AnimationRequest(format="gif", width=160, height=120, units="px", dpi=50)


@needs_manim
def test_a_curve_cut_into_many_pieces_is_refused_before_manim_starts():
    """a*10*sin(30000*x) in y [-1, 1]: 10 s estimated, 90 s past a 2 s deadline."""
    from ieeVisualizer.spec import Axis

    anim = _fm("a*10*sin(30000*x)", (0.0, 6.28), {"y": Axis(limits=[-1.0, 1.0])}, 100_000)
    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(anim, GIF)
    assert e.value.issues[0].code == "curve_too_fragmented"


@needs_manim
def test_waiting_for_another_manim_render_is_bounded_by_the_deadline():
    import threading
    import time

    from ieeVisualizer.animation import manim_adapter as MA

    MA.RENDER_LOCK.acquire()
    threading.Timer(4.0, MA.RENDER_LOCK.release).start()
    t = time.monotonic()
    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(_fm("a*x"), GIF, deadline=1.0)
    assert e.value.issues[0].code == "render_deadline_exceeded" and time.monotonic() - t < 2.0
    time.sleep(3.2)


@needs_manim
@pytest.mark.parametrize("expr,dom,limits", [("1.7e308*a*x", (-1.0, 1.0), None),
                                             ("a*x", (-1.0, 1.0), [0.0, 5e-324]),
                                             ("a", (-1e308, 1e308), None)])
def test_unrenderable_axis_ranges_are_structured_errors(expr, dom, limits):
    from ieeVisualizer.spec import Axis

    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(_fm(expr, dom, {"y": Axis(limits=limits)} if limits else None), GIF)
    assert e.value.issues[0].code == "axis_range_unrenderable"
    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(vector_field_anim(params={"x_range": [0.0, 1e-320]}), GIF)
    assert e.value.issues[0].code == "axis_range_unrenderable"


@needs_manim
def test_requested_axis_limits_are_drawn_exactly(monkeypatch):
    import manim

    from ieeVisualizer.spec import Axis

    seen = []
    orig = manim.Axes.__init__
    monkeypatch.setattr(manim.Axes, "__init__",
                        lambda self, *a, **k: seen.append(k) or orig(self, *a, **k))
    ManimAdapter().render(_fm("a*x", (0.0, 2.0), {"x": Axis(limits=[0.0, 1.7]),
                                                  "y": Axis(limits=[0.0, 1.03])}), GIF)
    assert seen[0]["x_range"][:2] == [0.0, 1.7] and seen[0]["y_range"][:2] == [0.0, 1.03]


@needs_manim
def test_a_matplotlib_render_does_not_import_manim():
    import subprocess
    import sys

    code = ("import sys; sys.path[:0] = ['tests/animation']\n"
            "from anim_helpers import SMALL, sine_anim\n"
            "from ieeVisualizer.animation import render_animation\n"
            "render_animation(sine_anim(), SMALL, isolation='inline')\n"
            "print('manim' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
