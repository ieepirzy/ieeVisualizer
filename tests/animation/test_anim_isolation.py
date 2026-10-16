"""The default isolation="process": a render or export runs in a worker that
is killed at the deadline, so the bound holds even for a step the
cooperative checks cannot interrupt (a blocked ffmpeg pipe, a long draw)."""

import dataclasses
import glob
import os
import shutil
import tempfile
import threading
import time

import pytest
from anim_helpers import SMALL, sine_anim, vector_field_anim, weekly_anim, weekly_data
from test_anim_deadline import HEAVY, _fn, _sweep

from ieeVisualizer.animation import AnimationError, export_animation, render_animation
from ieeVisualizer.animation.isolate import ISOLATION_SLACK_S
from ieeVisualizer.artifacts import MemoryWriter
from ieeVisualizer.spec import DatasetRef, FigureSpec, SpecValidationError

D = 2.0
HAS_FFMPEG = shutil.which("ffmpeg") is not None


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
    """A 0.5 s expression over 120 states (function and template sweeps), as
    MP4 and as a GIF + MP4 export: each ran for minutes before round 3."""
    fn = _sweep(FigureSpec(layers=[_fn(HEAVY)]), 120)
    tpl = vector_field_anim(params={"u": HEAVY.replace("+a)", "+k)"), "v": "x"},
                            values=[i / 40 for i in range(40)])
    cases = [lambda: render_animation(fn, SMALL, deadline=D, isolation="process"),
             lambda: render_animation(tpl, SMALL, deadline=D, isolation="process")]
    if HAS_FFMPEG:
        cases += [lambda: render_animation(fn, dataclasses.replace(SMALL, format="mp4"),
                                           deadline=D, isolation="process"),
                  lambda: export_animation(fn, ["gif", "mp4"], MemoryWriter(), request=SMALL,
                                           deadline=D, isolation="process")]
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


def test_a_blocked_ffmpeg_is_killed_with_the_worker(tmp_path, monkeypatch):
    """An ffmpeg that never reads its input blocks the frame write: inline
    there is nothing to check, the worker and ffmpeg are killed at the
    deadline. The overrun stats name the phase that was running."""
    fake = tmp_path / "ffmpeg"
    pid = tmp_path / "pid"
    fake.write_text(f'#!/bin/sh\ncase "$*" in *-encoders*) exit 0;; esac\necho $$ > {pid}\n'
                    "exec sleep 1000\n")
    fake.chmod(0o755)
    monkeypatch.setenv("IEEVISUALIZER_FFMPEG", str(fake))
    anim = sine_anim(axis_limits="auto", timeline=_sweep(FigureSpec(layers=[_fn()]), 40).timeline)
    anim.figure = FigureSpec(layers=[_fn()])
    req = dataclasses.replace(SMALL, format="mp4", width=640, height=480)
    before = _left()
    elapsed, e = _timed(lambda: render_animation(anim, req, deadline=D, isolation="process"))
    assert D <= elapsed <= D + ISOLATION_SLACK_S, (elapsed, e.stats)
    assert e.stats["phase"] == "draw" and e.stats.get("states_drawn", 0) >= 1, e.stats
    ffmpeg = int(pid.read_text())
    time.sleep(0.2)
    assert not os.path.exists(f"/proc/{ffmpeg}") or "Z" in open(f"/proc/{ffmpeg}/stat").read()
    assert _left() <= before


def test_an_inline_mp4_render_leaves_no_helper_thread():
    """A helper thread kept drawing (and changing rcParams) after an MP4
    render had raised."""
    if not HAS_FFMPEG:
        pytest.skip("no ffmpeg")
    render_animation(sine_anim(), dataclasses.replace(SMALL, format="mp4"), isolation="inline")
    assert not [t for t in threading.enumerate() if t.name.startswith("ieeVisualizer")]
