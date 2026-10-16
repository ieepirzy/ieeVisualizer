"""The default isolation="process": a render runs in a worker that is killed
at the deadline, so the bound holds even for a step the cooperative checks
cannot interrupt (a long draw; a blocked ffmpeg pipe: test_anim_mp4)."""

import glob
import os
import tempfile
import time

import pytest
from anim_helpers import SMALL, sine_anim, vector_field_anim, weekly_anim, weekly_data
from test_anim_deadline import HEAVY, _fn, _sweep

from ieeVisualizer.animation import AnimationError, render_animation
from ieeVisualizer.animation.isolate import ISOLATION_SLACK_S
from ieeVisualizer.spec import DatasetRef, FigureSpec, SpecValidationError

D = 2.0


def _left():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "ieev-anim-*")))


def _timed(call):
    t = time.monotonic()
    try:
        call()
    except AnimationError as e:
        return time.monotonic() - t, e
    raise AssertionError("finished; it should have been stopped")


def test_a_worker_render_equals_an_inline_one_and_errors_keep_their_structure():
    anim = weekly_anim("cumulative")
    anim.figure.datasets["b"] = DatasetRef(kind="query", id="w")
    resolve = lambda ref: weekly_data()  # noqa: E731 - not picklable: resolved in the caller
    a = render_animation(anim, SMALL, resolver=resolve, isolation="process")
    b = render_animation(anim, SMALL, resolver=resolve, isolation="inline")
    assert a.content == b.content and a.warnings == b.warnings
    with pytest.raises(SpecValidationError) as e:
        render_animation(sine_anim(poster=99), SMALL, isolation="process")
    assert [(i.path, i.code) for i in e.value.issues] == [("/poster", "invalid_poster")]


def test_reviewer_inputs_stop_at_the_deadline_plus_the_kill_slack():
    """A 0.5 s expression over 120 states (function and template sweeps; MP4
    and export cases: test_anim_mp4): each ran for minutes before round 3."""
    fn = _sweep(FigureSpec(layers=[_fn(HEAVY)]), 120)
    tpl = vector_field_anim(params={"u": HEAVY.replace("+a)", "+k)"), "v": "x"},
                            values=[i / 40 for i in range(40)])
    cases = [lambda: render_animation(fn, SMALL, deadline=D, isolation="process"),
             lambda: render_animation(tpl, SMALL, deadline=D, isolation="process")]
    before = _left()
    for call in cases:
        elapsed, e = _timed(call)
        assert e.issues[0].code == "render_deadline_exceeded" and e.issues[0].path == ""
        assert D <= elapsed <= D + ISOLATION_SLACK_S, (elapsed, e.stats)
        # which phase the deadline lands in depends on the machine's speed (a
        # loaded runner can still be starting the worker or validating)
        assert e.stats["deadline_s"] == D and e.stats["phase"] in (
            "start worker", "start", "validate", "prepare", "plan", "parse"), e.stats
    assert _left() <= before
