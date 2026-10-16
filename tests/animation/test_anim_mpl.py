"""Matplotlib animation adapter: frames are static renders; GIF encoding."""

import dataclasses

import numpy as np
import pytest
from anim_helpers import (
    SMALL,
    TWO_PI,
    gif_frames,
    growth_anim,
    png_pixels,
    sine_anim,
    sine_figure,
    weekly_anim,
)

from ieeVisualizer.animation import (
    render_animation,
)
from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
from ieeVisualizer.render import MatplotlibAdapter, RenderRequest, build_figure
from ieeVisualizer.spec import SpecValidationError

FRAME = RenderRequest(format="png", width=160, height=120, units="px", dpi=50)


def static(spec):
    return png_pixels(MatplotlibAdapter().render(spec, FRAME).content)


def test_frame_k_equals_the_static_render_of_the_spec_at_that_value():
    anim = sine_anim(axis_limits="auto", label_frames=False)
    fp, _, pixels = MatplotlibAnimationAdapter().render_frames(anim, SMALL)
    for k in range(len(fp.states)):
        expected = static(sine_figure(phase=k * TWO_PI / 4))  # built independently
        assert pixels[k].shape == expected.shape
        assert np.array_equal(pixels[k], expected), f"state {k} differs from its still"
    assert not np.array_equal(pixels[0], pixels[1])


def test_with_default_options_each_frame_equals_the_still_of_its_frame_spec():
    anim = sine_anim(hold_end=2)
    adapter = MatplotlibAnimationAdapter()
    specs = adapter.frame_specs(anim, SMALL)
    fp, _, pixels = adapter.render_frames(anim, SMALL)
    assert len(specs) == fp.frame_count == 7
    for k, spec in enumerate(specs):
        assert np.array_equal(pixels[fp.frames[k]], static(spec))
    assert specs[3].title == "Sine (phase = 4.712)"


def test_gif_frames_match_the_stills_within_palette_error():
    anim = sine_anim(axis_limits="auto", label_frames=False)
    res = render_animation(anim, SMALL)
    frames, delays = gif_frames(res.content)
    assert res.format == "gif" and res.media_type == "image/gif"
    assert len(frames) == res.frame_count == 5
    assert sum(delays) == 5 * 100 and res.duration_s == pytest.approx(0.5)
    stills = [static(sine_figure(phase=k * TWO_PI / 4)) for k in range(5)]
    for k, f in enumerate(frames):
        own = np.abs(f.astype(int) - stills[k].astype(int)).mean()
        other = np.abs(f.astype(int) - stills[(k + 2) % 5].astype(int)).mean()
        assert own < 1.0 and other > 3 * own + 1.0, (k, own, other)


def test_gif_bytes_are_deterministic():
    a = render_animation(sine_anim(hold_end=2), SMALL)
    b = render_animation(sine_anim(hold_end=2), SMALL)
    assert a.content == b.content


def test_hold_frames_keep_their_time_even_when_pillow_merges_them():
    res = render_animation(sine_anim(hold_start=2, hold_end=3), SMALL)
    frames, delays = gif_frames(res.content)
    assert res.frame_count == 10 and len(frames) == 5  # identical neighbours merged
    assert delays[0] == 300 and delays[-1] == 400 and sum(delays) == 1000


def test_gif_timing_rounding_is_reported():
    res = render_animation(sine_anim(fps=12), SMALL)
    assert res.frame_duration_ms == 80.0
    assert "frame_timing_rounded" in [w.code for w in res.warnings]


def test_fixed_limits_cover_every_state_and_are_the_same_in_every_frame():
    specs = MatplotlibAnimationAdapter().frame_specs(growth_anim(), SMALL)
    ylims = {tuple(s.axes["y"].limits) for s in specs}
    assert len(ylims) == 1
    lo, hi = ylims.pop()
    assert lo <= -3.0 and hi >= 3.0
    for s in specs:
        ax = build_figure(s, FRAME).axes
        assert ax.get_ylim() == pytest.approx((lo, hi))


def test_auto_limits_let_each_frame_scale_itself():
    specs = MatplotlibAnimationAdapter().frame_specs(growth_anim(axis_limits="auto"), SMALL)
    assert all("y" not in s.axes for s in specs)
    assert build_figure(specs[0], FRAME).axes.get_ylim()[1] < 1.5


def test_figure_limits_are_kept_and_only_open_ends_are_filled():
    anim = growth_anim()
    anim.figure.axes["y"] = dataclasses.replace(anim.figure.axes.get("y") or _axis(),
                                                limits=[-5.0, None])
    specs = MatplotlibAnimationAdapter().frame_specs(anim, SMALL)
    assert specs[0].axes["y"].limits[0] == -5.0 and specs[0].axes["y"].limits[1] >= 3.0


def _axis():
    from ieeVisualizer.spec import Axis

    return Axis()


def test_frame_column_time_axis_is_fixed_to_the_whole_data_range():
    specs = MatplotlibAnimationAdapter().frame_specs(weekly_anim("cumulative"), SMALL)
    xlims = {tuple(s.axes["x"].limits) for s in specs}
    assert len(xlims) == 1
    lo, hi = xlims.pop()
    # a lone first point must not widen the axis to years (matplotlib's
    # single-date autoscale); the range is the data's, with margins
    assert "2026-05-29" <= lo[:10] <= "2026-06-01" and "2026-06-22" <= hi[:10] <= "2026-06-25"


def test_missing_value_stays_a_gap_in_the_frame():
    adapter = MatplotlibAnimationAdapter()
    specs = adapter.frame_specs(weekly_anim("cumulative"), SMALL)
    built = build_figure(specs[-1], FRAME)
    (line,) = built.layer_artists[0]
    y = line.get_ydata()
    assert np.ma.is_masked(y) and np.ma.getmaskarray(y).tolist() == [False, False, True, False]
    res = render_animation(weekly_anim("cumulative"), SMALL)
    assert "null_values_omitted" in [w.code for w in res.warnings]


def test_a_frame_without_data_is_drawn_as_no_data_not_an_empty_chart():
    fp, warnings, pixels = MatplotlibAnimationAdapter().render_frames(
        weekly_anim("current"), SMALL)
    assert fp.states[2].spec is None
    assert "frame_no_data" in [w.code for w in warnings]
    # no axes frame: the no-data image has far less dark ink than a chart
    ink = [(p < 128).mean() for p in pixels]
    assert ink[2] < ink[1] / 3 and ink[2] > 0


def test_referenced_data_is_resolved_once_and_animated():
    from anim_helpers import weekly_data

    from ieeVisualizer.spec import DatasetRef

    anim = weekly_anim("cumulative")
    anim.figure.datasets["b"] = DatasetRef(kind="query", id="weekly_bookings")
    calls = []

    def resolver(ref):
        calls.append(ref.id)
        return weekly_data()

    res = render_animation(anim, SMALL, resolver=resolver)
    assert res.state_count == 4 and calls
    with pytest.raises(SpecValidationError) as e:
        render_animation(anim, SMALL, resolver=lambda ref: weekly_data().__class__.from_columns(
            {"day": [1]}))
    assert e.value.issues[0].code == "unknown_column"
