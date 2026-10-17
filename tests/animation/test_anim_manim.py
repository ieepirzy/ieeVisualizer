"""Manim adapter. Capability tests always run; render tests need ``manim``
(skipped when it does not import, e.g. in CI)."""

import dataclasses
import io

import numpy as np
import pytest
from anim_helpers import gif_frames, png_pixels, sine_anim, vector_field_anim, weekly_anim

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
    res = ManimAdapter().render(vector_field_anim(values=[0.0, 1.5], params={"scale": None}), REQ)
    frames = _video_frames(res.content)
    assert len(frames) == 2
    assert _ink(frames[-1]) > 1.3 * _ink(frames[0])


@needs_manim
def test_gif_result_reports_the_stored_delay_frame_mapping_and_poster():
    anim = sine_anim(fps=15, hold_start=1, hold_end=2)  # 8 states at 15 fps
    res = ManimAdapter().render(anim, AnimationRequest(format="gif", width=160, height=120,
                                                       units="px", dpi=50, adapter="manim"))
    _, delays = gif_frames(res.content)  # Pillow merges the held frames, as for Matplotlib
    assert res.frame_duration_ms == 70.0 and sum(delays) == 70 * res.frame_count
    assert delays[0] == 140 and delays[-1] == 210 and set(delays[1:-1]) == {70}
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
    frames = _video_frames(files["animation.mp4"])
    # the poster is the drawn frame; the MP4 holds it lossily (yuv420p): it is
    # nearest to frame 2 of the clip
    diffs = [np.abs(poster.astype(int) - f).mean() for f in frames]
    assert poster.shape == frames[2].shape and int(np.argmin(diffs)) == 2 and diffs[2] < 3


@needs_manim
def test_odd_request_size_is_kept_in_gif_and_poster():
    """Manim renders even sizes; frames are cropped back to the request, so
    the GIF and poster are 161x121 as asked; the MP4 says it is padded."""
    from anim_helpers import png_pixels

    odd = dict(width=161, height=121, units="px", dpi=50, adapter="manim")
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
        render_animation(weekly_anim(), dataclasses.replace(mp4, adapter="manim"), registry=only)
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
    from ieeVisualizer.spec import Legend

    fig = FigureSpec(layers=[
        FunctionMark(expression="a*sin(x)", variables=["x"], domain={"x": [0, 6]},
                     parameters={"a": 1.0}),
        FunctionMark(expression="a*cos(x)", variables=["x"], domain={"x": [0, 6]},
                     parameters={"a": 0.5})], legend=Legend(show=False))
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
    # a clamped curve runs along the edge (34 points); a cut one touches it at
    # its ends (and where a sample lands exactly on it)
    assert max(int(np.isclose(abs(y[1:-1]), 0.5).sum()) for y in ys) <= 1


@needs_manim
def test_theme_colours_are_used():
    from anim_helpers import png_pixels

    from ieeVisualizer import themes

    dark = next(t for t in map(themes.get, (d["name"] for d in themes.list_themes()))
                if t.background and int(t.background[1:3], 16) < 64)
    want = tuple(int(dark.background[i:i + 2], 16) for i in (1, 3, 5))
    res = ManimAdapter().render(sine_anim(), AnimationRequest(
        format="gif", width=160, height=120, units="px", dpi=50, theme=dark.name))
    assert np.abs(png_pixels(res.poster_png)[2, 2].astype(int) - want).max() <= 3
    unknown = ManimAdapter().render(sine_anim(), AnimationRequest(
        format="gif", width=160, height=120, units="px", dpi=50,
        theme="no-such-theme"))
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
                                                             units="px", dpi=50))
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
    req = AnimationRequest(format="mp4", width=160, height=120, units="px", dpi=50,
                           adapter="manim")
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
@pytest.mark.parametrize("expr,dom,limits,code", [
    # at a = 2 every sample overflows (inf, or inf * 0 = nan): that state is
    # refused as Matplotlib refuses it
    ("1.7e308*a*x", (-1.0, 1.0), None, "empty_series"),
    ("a*x", (-1.0, 1.0), [0.0, 5e-324], "axis_range_unrenderable"),
    ("a", (-1e308, 1e308), None, "axis_range_unrenderable")])
def test_unrenderable_axis_ranges_are_structured_errors(expr, dom, limits, code):
    from ieeVisualizer.spec import Axis

    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(_fm(expr, dom, {"y": Axis(limits=limits)} if limits else None), GIF)
    assert e.value.issues[0].code == code
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


# ------------------------------------------------ every field: drawn or unmet


def _base():
    from ieeVisualizer.animation import AnimationSpec, ParameterSweep

    fig = FigureSpec(layers=[FunctionMark(expression="a*sin(x)", variables=["x"],
                                          domain={"x": [0.0, 6.0]}, samples=40,
                                          parameters={"a": 1.0})])
    return AnimationSpec(timeline=ParameterSweep(parameter="a", values=[1.0, 2.0]), figure=fig,
                         fps=4)


def _base2():
    """Two layers (the legend hidden: Manim draws none)."""
    from ieeVisualizer.spec import Legend

    anim = _base()
    anim.figure.layers.append(FunctionMark(expression="b*cos(x)", variables=["x"],
                                           domain={"x": [0.0, 6.0]}, samples=40,
                                           parameters={"a": 1.0, "b": 0.5}))
    anim.figure.legend = Legend(show=False)
    return anim


def _dark():
    from ieeVisualizer import themes

    return next(n for n in (d["name"] for d in themes.list_themes())
                if themes.get(n).background and int(themes.get(n).background[1:3], 16) < 64)


#: Fields that do not change any picture (identity, bookkeeping) or that
#: define the timeline itself; everything else must be drawn or unmet.
NOT_VISUAL = {"animation": {"timeline", "figure", "id", "metadata", "schema_version"},
              "figure": {"layers", "datasets", "id", "metadata", "schema_version"},
              "legend": {"show"},  # one layer: no legend in either renderer
              "function": {"type", "data", "encoding", "variables", "parameters"}}


def _variants(at: int = 0):
    """``(kind, field, edit)``: each field of each spec class set to a value
    other than its default (a layer field on layer ``at``); asserted to
    cover every field."""
    from ieeVisualizer.spec import Axis, Legend, TextAnnotation

    def fig(**kw):
        return lambda a: dataclasses.replace(a, figure=dataclasses.replace(a.figure, **kw))

    def layer(**kw):
        def edit(a):
            layers = list(a.figure.layers)
            layers[at] = dataclasses.replace(layers[at], **kw)
            return dataclasses.replace(a, figure=dataclasses.replace(a.figure, layers=layers))

        return edit

    out = {
        "animation": {"fps": lambda a: dataclasses.replace(a, fps=5),
                      "hold_start": lambda a: dataclasses.replace(a, hold_start=1),
                      "hold_end": lambda a: dataclasses.replace(a, hold_end=1),
                      "axis_limits": lambda a: dataclasses.replace(a, axis_limits="auto"),
                      "label_frames": lambda a: dataclasses.replace(a, label_frames=False),
                      "poster": lambda a: dataclasses.replace(a, poster="first"),
                      "loop": lambda a: dataclasses.replace(a, loop=False),
                      "title": lambda a: dataclasses.replace(a, title="T")},
        "figure": {"title": fig(title="T"), "axes": fig(axes={"x": Axis(label="L")}),
                   "coordinate_system": fig(coordinate_system="polar"),
                   "legend": fig(legend=Legend(position="upper-left")),
                   "annotations": fig(annotations=[TextAnnotation(text="n", x=1, y=0)]),
                   "caption": fig(caption="C"), "theme": fig(theme=_dark())},
        "axis": {"label": fig(axes={"y": Axis(label="L")}), "unit": fig(axes={"y": Axis(
            unit="m")}), "scale": fig(axes={"y": Axis(scale="log")}),
                 "limits": fig(axes={"y": Axis(limits=[-4.0, 4.0])})},
        "legend": {"position": fig(legend=Legend(position="upper-left")),
                   "title": fig(legend=Legend(title="L"))},
        "function": {"role": layer(role="reference"), "style": layer(style={"color": "red"}),
                     "name": layer(name="signal"),
                     "expression": layer(expression="a*cos(x)"),
                     "domain": layer(domain={"x": [0.0, 3.0]}), "samples": layer(samples=20)},
    }
    return out


def _vf_variants():
    from ieeVisualizer import templates

    values = {"u": "-y + k*x + 0.5", "v": "x + k*y + 0.5", "constants": {"k": 0.25},
              "x_range": [-3.0, 3.0], "y_range": [-3.0, 3.0], "grid": 4, "x_column": "a",
              "y_column": "b", "u_column": "c", "v_column": "d", "x_unit": "m",
              "y_unit": "m", "vector_unit": "m/s", "title": "T", "caption": "C",
              "x_label": "east", "y_label": "north", "normalize": True, "scale": 2.0,
              "role": "observed"}
    names = [q.name for q in templates.get("vector_field_2d", "1.0.0").parameters]
    assert set(names) == set(values), "add the new template parameter to this table"
    return values


def _picture(res):
    import hashlib

    return (hashlib.sha256(res.content).hexdigest(), hashlib.sha256(res.poster_png).hexdigest(),
            res.frame_count, sorted(w.code for w in res.warnings))


def _silent(adapter, spec_fields=True, base=_base, at=0, vector_field=True) -> list[str]:
    """Fields set away from their default that are neither unmet nor change
    the clip. Each variant is compared with its base rendered right before
    it (renders are deterministic: test_renders_are_deterministic)."""
    from ieeVisualizer.animation import AnimationSpec
    from ieeVisualizer.spec import Axis, Legend
    from ieeVisualizer.spec import FigureSpec as FS

    silent = []
    if spec_fields:
        classes = {"animation": AnimationSpec, "figure": FS, "axis": Axis, "legend": Legend,
                   "function": FunctionMark}
        variants = _variants(at)
        for kind, cls in classes.items():
            fields = {f.name for f in dataclasses.fields(cls)}
            assert fields == set(variants[kind]) | NOT_VISUAL.get(kind, set()), (kind, fields)
        for kind, edits in variants.items():
            for name, edit in edits.items():
                anim = edit(base())
                if not adapter.unmet(anim, GIF) and _picture(adapter.render(anim, GIF)) == \
                        _picture(adapter.render(base(), GIF)):
                    silent.append(f"{kind}.{name}")
    for name, value in (_vf_variants().items() if vector_field else ()):
        if name == "constants":  # the swept map: its key is replaced in every state
            continue
        anim = vector_field_anim(params={"scale": None, name: value})
        if not adapter.unmet(anim, GIF) and _picture(adapter.render(anim, GIF)) == \
                _picture(adapter.render(vector_field_anim(params={"scale": None}), GIF)):
            silent.append(f"vector_field_2d.{name}")
    return silent


@needs_manim
def test_every_field_set_is_drawn_or_reported_unmet():
    """Support is an allowlist: a field Manim does not draw is unmet, never
    silently ignored (a swept scale, normalize, captions, units and
    label_frames used to be dropped)."""
    from ieeVisualizer.animation import AnimationSpec, ParameterSweep, TemplateTarget

    adapter = ManimAdapter()
    assert _silent(adapter) == []
    # several layers: every field of the second layer too, and the legend
    assert _silent(adapter, base=_base2, at=1, vector_field=False) == []
    swept = AnimationSpec(fps=4, timeline=ParameterSweep(parameter="grid", values=[3.0, 9.0],
                          target=TemplateTarget(name="vector_field_2d", version="1.0.0",
                                                params={"u": "-y", "v": "x"})))
    assert "manim cannot sweep 'grid'" in adapter.unmet(swept, GIF)


@needs_manim
def test_the_field_check_catches_a_parameter_wrongly_allowlisted(monkeypatch):
    """vector_unit is not drawn: allowlisting it must make the check fail
    (it passed while inline renders differed from run to run)."""
    from ieeVisualizer.animation import manim_adapter as MA

    monkeypatch.setitem(MA.DRAWN, "vector_field_2d", MA.DRAWN["vector_field_2d"] | {"vector_unit"})
    assert "vector_field_2d.vector_unit" in _silent(ManimAdapter(), spec_fields=False)


@needs_manim
@pytest.mark.parametrize("fmt", ["gif", "mp4"])
def test_renders_are_deterministic(fmt):
    """Inline renders in one process gave different frames (Manim's movie
    encoder); frames are now drawn and encoded here."""
    req = dataclasses.replace(GIF, format=fmt)
    for anim in (vector_field_anim(params={"scale": None}), _base()):
        got = [ManimAdapter().render(anim, req) for _ in range(8)]
        assert len({r.poster_png for r in got}) == 1
        if fmt == "gif":
            assert len({r.content for r in got}) == 1
        else:
            first = _video_frames(got[0].content)
            assert all(all(np.array_equal(a, b) for a, b in zip(first, _video_frames(r.content)))
                       for r in got[1:])


@needs_manim
def test_frame_labels_are_the_state_labels():
    """1.001 / 1.002 / 1.003 were all drawn as 'a = 1'."""
    from ieeVisualizer.animation import ParameterSweep
    from ieeVisualizer.animation.manim_adapter import _plan

    anim = dataclasses.replace(_base(), timeline=ParameterSweep(parameter="a",
                                                                values=[1.001, 1.002, 1.003]))
    res = ManimAdapter().render(anim, GIF)
    plot = _plan(anim, GIF)[0]
    assert plot.titles == [s["label"] for s in res.states] == ["a = 1.001", "a = 1.002",
                                                               "a = 1.003"]
    frames, _ = gif_frames(res.content)
    assert len(frames) == 3 and not np.array_equal(frames[0], frames[1])


@needs_manim
def test_default_axis_labels_follow_matplotlib():
    from ieeVisualizer.animation.manim_adapter import _label, _plan
    from ieeVisualizer.spec import Axis

    anim = _base()
    assert _plan(anim, GIF)[0].labels == ("x", "a*sin(x)")
    anim.figure.layers[0].name = "signal"
    anim.figure.axes = {"x": Axis(label="time (s)", unit="s")}
    assert _plan(anim, GIF)[0].labels == ("time (s)", "signal")
    assert _label("time", "s") == "time (s)" and _label(None, "s") == "(s)"


@needs_manim
def test_missing_values_are_reported_as_matplotlib_reports_them():
    from ieeVisualizer.animation import ParameterSweep, render_animation

    gap = _fm("sqrt(a - x**2)", (-3.0, 3.0), {}, 301)
    gap = dataclasses.replace(gap, timeline=ParameterSweep(parameter="a", values=[1.0, 4.0]))
    vf = vector_field_anim(params={"u": "1/x + k", "v": "sqrt(y)", "scale": None})
    for anim, code in ((gap, "nonfinite_samples_omitted"), (vf, "null_values_omitted")):
        want = [w for w in render_animation(anim, GIF).warnings if w.code == code]
        got = [w for w in ManimAdapter().render(anim, GIF).warnings if w.code == code]
        assert want and got == want


def _boxes(anim, req):
    """Per state: the bounding boxes of every text, the plot rectangle and
    the marks, from the composed scene (nothing rendered)."""
    import tempfile

    import manim as M

    from ieeVisualizer.animation import manim_adapter as MA

    plot, _, theme, _ = MA._plan(anim, req)
    out = []
    with tempfile.TemporaryDirectory() as tmp, M.tempconfig(MA._config(req, theme, tmp)):
        scene, set_state = MA._scene(plot, req, theme)
        ax = next(m for m in scene.mobjects if isinstance(m, M.Axes))
        (x0, y0), (x1, y1) = ax.c2p(plot.box[0], plot.box[2])[:2], ax.c2p(plot.box[1],
                                                                            plot.box[3])[:2]
        for k in range(len(plot.titles)):
            set_state(k)

            def box(m):
                return (m.get_left()[0], m.get_right()[0], m.get_bottom()[1], m.get_top()[1])

            texts = [box(m) for m in scene.mobjects if isinstance(m, M.Text)]
            marks = [box(m) for m in scene.mobjects if not isinstance(m, (M.Text, M.Axes))
                     and any(f.has_points() for f in m.get_family())]
            # the axes: their lines and tick marks, which may stick out of the
            # plot by one tick length only
            t = MA.GAP / 3 + 1e-6
            assert x0 - t <= box(ax)[0] and box(ax)[1] <= x1 + t and \
                y0 - t <= box(ax)[2] and box(ax)[3] <= y1 + t, (box(ax), (x0, x1, y0, y1))
            out.append((texts, (x0, x1, y0, y1), marks + [box(ax)],
                        (M.config.frame_width, M.config.frame_height)))
    return out


def _overlap(a, b, eps=1e-6):
    return a[0] < b[1] - eps and b[0] < a[1] - eps and a[2] < b[3] - eps and b[2] < a[3] - eps


def _layout_specs(which):
    from ieeVisualizer.spec import Axis

    if which == "function":
        anim = dataclasses.replace(_base(), title="Damping")
        anim.figure.caption = "Source: model, not measured. " * 3
        anim.figure.axes = {"x": Axis(label="time", unit="s"), "y": Axis(unit="mm")}
    elif which == "long_y":  # a long rotated y title, a 3-line caption, a 2-line title
        anim = dataclasses.replace(_fm("a*x", (1e-7, 3.3e-7), {"y": Axis(
            label="a very long y axis label that is long", unit="EUR")}),
            title="Title\nsecond line")
        anim.figure.caption = "line one\nline two\nline three"
    elif which == "vector_field":
        anim = vector_field_anim(params={"title": "Spiral", "caption": "Model field",
                                         "x_unit": "m", "y_unit": "m", "scale": 0.8,
                                         "x_range": [-3.0, 3.0], "y_range": [-2.0, 2.0]})
    else:  # a wide, flat field with long arrows
        anim = vector_field_anim(params={"title": "Spiral\nline2", "caption": "Model field " * 10,
                                         "x_unit": "m", "y_unit": "m", "scale": 2.0,
                                         "x_range": [-300.0, 300.0], "y_range": [-0.001, 0.001]})
    return anim


@needs_manim
@pytest.mark.parametrize("size", [(560, 400, 100), (300, 450, 100), (400, 400, 100),
                                  (320, 180, 100), (800, 200, 100), (1000, 300, 150),
                                  (1920, 1080, 200), (640, 480, 72)])
@pytest.mark.parametrize("which", ["function", "long_y", "vector_field", "flat_field"])
def test_no_text_overlaps_another_or_the_plot_and_arrows_stay_inside(size, which):
    """The caption overlapped the x title; edge arrows ran over the tick
    labels, titles and frame label; portrait frames shrank text; a long y
    title ran off the top of a wide, short frame; a flat field's tick labels
    overprinted. Every frame is either refused (frame_too_small) or clean."""
    req = AnimationRequest(format="gif", width=size[0], height=size[1], units="px", dpi=size[2])
    anim = _layout_specs(which)
    try:
        boxes = _boxes(anim, req)
    except AnimationError as e:
        assert e.issues[0].code == "frame_too_small"
        return
    for texts, plot, marks, (fw, fh) in boxes:
        assert len(texts) >= 6  # title, caption, two axis titles, ticks
        for i, a in enumerate(texts):
            assert -fw / 2 <= a[0] and a[1] <= fw / 2 and -fh / 2 <= a[2] and a[3] <= fh / 2
            assert not _overlap(a, plot), (a, plot)
            assert not any(_overlap(a, b) for b in texts[i + 1:]), (a, texts)
        for m in marks[:-1]:
            assert plot[0] - 1e-6 <= m[0] and m[1] <= plot[1] + 1e-6 and \
                plot[2] - 1e-6 <= m[2] and m[3] <= plot[3] + 1e-6, (m, plot)
        for m in marks:  # nothing drawn, the axes included, runs over a text
            assert not any(_overlap(m, a) for a in texts), (m, texts)


@needs_manim
def test_a_frame_too_small_for_its_text_is_refused():
    req = AnimationRequest(format="gif", width=120, height=90, units="px", dpi=100)
    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(_base(), req)
    assert e.value.issues[0].code == "frame_too_small"


@needs_manim
def test_reference_arrows_are_translucent_and_observed_ones_are_drawn():
    ref = ManimAdapter().render(vector_field_anim(params={"scale": None}), GIF)
    obs = ManimAdapter().render(vector_field_anim(params={"scale": None, "role": "observed"}),
                                GIF)
    assert not ManimAdapter().unmet(vector_field_anim(params={"role": "observed"}), GIF)
    # the same arrows, more ink when opaque
    assert _ink(png_pixels(obs.poster_png)) >= _ink(png_pixels(ref.poster_png))
    assert png_pixels(obs.poster_png).min() < png_pixels(ref.poster_png).min()


def test_unmet_messages_name_what_is_missing(monkeypatch):
    from ieeVisualizer.animation import (
        AnimationSpec,
        ParameterSweep,
        TemplateTarget,
        render_animation,
    )
    from ieeVisualizer.animation import manim_adapter as MA
    from ieeVisualizer.render import CapabilityError

    m = ManimAdapter()
    ts = AnimationSpec(fps=2, timeline=ParameterSweep(parameter="target", values=[1.0, 2.0],
                       target=TemplateTarget(name="timeseries", version="1.0.0")))
    unmet = m.unmet(ts, GIF)
    assert not any("expression mode" in u for u in unmet)
    assert "manim cannot sweep 'target'" in unmet
    assert any("axis_limits 'auto'" in u
               for u in m.unmet(dataclasses.replace(_base(), axis_limits="auto"), GIF))
    monkeypatch.setattr(MA, "manim_available", lambda: False)
    with pytest.raises(CapabilityError) as e:  # not KeyError
        render_animation(_base(), dataclasses.replace(GIF, adapter="manim"))
    assert "manim cannot provide" in str(e.value) and "not installed" in str(e.value)


@needs_manim
def test_a_swept_scale_changes_the_arrows():
    from ieeVisualizer.animation import AnimationSpec, ParameterSweep, TemplateTarget

    anim = AnimationSpec(fps=4, timeline=ParameterSweep(parameter="scale", values=[0.2, 3.0],
                         target=TemplateTarget(name="vector_field_2d", version="1.0.0",
                                               params={"u": "-y", "v": "x", "grid": 5})))
    frames = _video_frames(ManimAdapter().render(anim, REQ).content)
    assert _ink(frames[1]) > 1.5 * _ink(frames[0])


@needs_manim
def test_curve_pieces_are_counted_by_what_is_drawn():
    """Every sample outside y [-1, 1] but every segment crossing it: counted
    0 pieces, drawn N - 1 (100,000 samples: 311 s past a 2 s deadline)."""
    from ieeVisualizer.spec import Axis

    anim = _fm("a*1000*cos(pi*x)", (0.0, 1999.0), {"y": Axis(limits=[-1.0, 1.0])}, 2000)
    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(anim, GIF)
    assert e.value.issues[0].code == "curve_too_fragmented"


@needs_manim
@pytest.mark.parametrize("value,ok", [("tab:red", True), ("C1", True), ("#ff000080", True),
                                      ([1, 0, 0], False), ("notacolor", False)])
def test_colours_are_read_like_matplotlib(value, ok):
    from ieeVisualizer.animation import ParameterSweep

    anim = _base()
    anim = dataclasses.replace(anim, timeline=ParameterSweep(parameter="a", values=[1.0, 2.0]))
    anim.figure.layers[0].style = {"color": value, "linewidth": "thick"}
    res = ManimAdapter().render(anim, GIF)
    got = [w.path for w in res.warnings if w.code == "invalid_style_value"]
    assert got == (["/figure/layers/0/style/linewidth"] if ok else
                   ["/figure/layers/0/style/color", "/figure/layers/0/style/linewidth"])


def test_an_unimportable_manim_is_reported_unmet(monkeypatch):
    from ieeVisualizer.animation import manim_adapter as MA

    monkeypatch.setattr(MA, "manim_available", lambda: True)
    monkeypatch.setattr(MA, "import_problem", lambda: "ImportError: libpango missing")
    assert any("not importable: ImportError: libpango" in u
               for u in ManimAdapter().unmet(_base(), GIF))


@needs_manim
def test_several_layers_need_a_legend_or_are_never_titled_by_one_name():
    """'revenue' a*sin(x) + 'cost' cos(x): no legend, unmet() == [], and the
    y axis titled 'revenue'."""
    from ieeVisualizer.animation.manim_adapter import _plan

    anim = _base2()
    anim.figure.legend.show = True
    anim.figure.layers[0].name, anim.figure.layers[1].name = "revenue", "cost"
    unmet = ManimAdapter().unmet(anim, GIF)
    assert any(u.startswith("/figure/legend") for u in unmet)
    assert any(u.startswith("/figure/layers/0/name") for u in unmet)
    assert _plan(_base2(), GIF)[0].labels == ("x", None)  # no layer's text titles the axis


@needs_manim
def test_a_state_with_no_finite_sample_is_refused_like_matplotlib():
    from ieeVisualizer.animation import ParameterSweep

    anim = dataclasses.replace(_fm("sqrt(a - x**2)", (-3.0, 3.0), {}, 101),
                               timeline=ParameterSweep(parameter="a", values=[-1.0, 4.0]))
    with pytest.raises(AnimationError) as e:
        ManimAdapter().render(anim, GIF)
    assert (e.value.issues[0].code, e.value.issues[0].path) == ("empty_series",
                                                                "/figure/layers/0")


@needs_manim
@pytest.mark.parametrize("lo", [1e-7, 1e-9, 1e-12])
def test_tiny_spans_are_drawn_rescaled_with_their_unit(lo):
    """1e-7: the axis ran 2.2x the frame height; 1e-9: 2,006 ticks, 14.7 s;
    1e-12: until the deadline."""
    import time

    from ieeVisualizer.animation.manim_adapter import _plan

    anim = _fm("a*x", (lo, 3.3 * lo))
    t = time.perf_counter()
    ManimAdapter().render(anim, GIF, deadline=30)
    assert time.perf_counter() - t < 10
    labels = _plan(anim, GIF)[0].labels
    assert "\u00d71e" in labels[0] and "\u00d71e" in labels[1]
    req = AnimationRequest(format="gif", width=320, height=240, units="px", dpi=50)
    for texts, _plot, marks, _ in _boxes(anim, req):  # the axes: inside the plot (_boxes)
        for m in marks:
            assert not any(_overlap(m, a) for a in texts)


@needs_manim
def test_the_layout_checks_the_deadline():
    import tempfile

    import manim as M

    from ieeVisualizer.animation import manim_adapter as MA
    from ieeVisualizer.animation.deadline import Deadline, DeadlineExceeded

    plot, _, theme, _ = MA._plan(_base(), GIF)
    t = [0.0]
    late = Deadline(1.0, clock=lambda: t[0])
    t[0] = 5.0
    with tempfile.TemporaryDirectory() as tmp, M.tempconfig(MA._config(GIF, theme, tmp)):
        with pytest.raises(DeadlineExceeded) as e:
            MA._scene(plot, GIF, theme, late)
    assert e.value.stats["phase"] == "layout"


def test_math_text_is_reported_unmet():
    anim = dataclasses.replace(_base(), title="$\\alpha$ sweep")
    assert any("math text" in u and u.startswith("/title")
               for u in ManimAdapter().unmet(anim, GIF))


@needs_manim
def test_series_colours_follow_matplotlib():
    from ieeVisualizer.animation.manim_adapter import _styles

    def L(expr, **kw):
        return FunctionMark(expression=expr, variables=["x"], domain={"x": [0.0, 6.0]}, **kw)

    pal = ["#111111", "#222222", "#333333"]
    assert [c for c, *_ in _styles([L("sin(x)"), L("cos(x)")], pal, [])] == pal[:2]
    assert [c for c, *_ in _styles([L("sin(x)", name="s"), L("cos(x)", name="s")], pal, [])] \
        == [pal[0], pal[0]]
    # an explicit colour takes nothing from the cycle
    assert [c for c, *_ in _styles([L("sin(x)", style={"color": "#abcdef"}), L("cos(x)")],
                                   pal, [])] == ["#abcdef", pal[0]]
    (c, alpha, _), = _styles([L("sin(x)", style={"color": "#ff000080"})], pal, [])
    assert c == "#ff0000" and abs(alpha - 128 / 255) < 1e-9


@needs_manim
def test_manim_uses_the_shared_distinct_labels():
    from ieeVisualizer.animation import ParameterSweep

    anim = dataclasses.replace(_base(), timeline=ParameterSweep(parameter="a",
                                                                values=[1.0001, 1.0002]))
    assert [s["label"] for s in ManimAdapter().render(anim, GIF).states] == \
        ["a = 1.0001", "a = 1.0002"]
    anim = dataclasses.replace(_base(), timeline=ParameterSweep(parameter="a",
                                                                values=[1 + 1e-12, 1 + 2e-12]))
    assert "frame_labels_ambiguous" in [w.code for w in ManimAdapter().render(anim, GIF).warnings]


@needs_manim
def test_manim_fits_a_long_title_so_its_state_label_is_never_cut_off():
    from test_anim_frames import _amplitude

    req = AnimationRequest(format="gif", width=360, height=640, units="px", dpi=100)
    for texts, _plot, _marks, (fw, _fh) in _boxes(_amplitude([-1.234561e-5, -1.234562e-5]),
                                                  req):
        assert all(-fw / 2 <= a[0] and a[1] <= fw / 2 for a in texts)
