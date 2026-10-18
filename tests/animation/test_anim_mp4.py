"""MP4 through ffmpeg, export, and their deadline behaviour (worker kill,
cleanup); the MP4 tests skip without ffmpeg (CI installs it)."""

import dataclasses
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import numpy as np
import pytest
from anim_helpers import (
    HERE,
    ROOT,
    SMALL,
    codes,
    png_pixels,
    sine_anim,
    vector_field_anim,
    weekly_anim,
)
from test_anim_deadline import HEAVY, _fn, _sweep
from test_anim_guards import _ref_anim
from test_anim_isolation import (
    D,
    _assert_stopped_on_time,
    _own_tmp,
    _ppid,
    _state,
    _Stopper,
    _timed,
)

from ieeVisualizer.animation import (
    AnimationError,
    AnimationRegistry,
    export_animation,
    render_animation,
)
from ieeVisualizer.animation import frames as F
from ieeVisualizer.animation import validate as V
from ieeVisualizer.animation.isolate import ISOLATION_SLACK_S
from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
from ieeVisualizer.artifacts import MemoryWriter
from ieeVisualizer.render import CapabilityError
from ieeVisualizer.spec import FigureSpec, SpecValidationError

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")
MP4 = dataclasses.replace(SMALL, format="mp4")


def test_mp4_is_refused_clearly_without_ffmpeg():
    adapter = MatplotlibAnimationAdapter(ffmpeg_path="/nonexistent/ffmpeg")
    caps = adapter.capabilities
    assert caps.output_formats == {"gif"} and "ffmpeg" in caps.notes[0]
    with pytest.raises(AnimationError) as e:
        adapter.render(sine_anim(), MP4)
    assert e.value.issues[0].code == "format_unavailable"
    reg = AnimationRegistry()
    reg.register(adapter)
    with pytest.raises(CapabilityError) as e:
        render_animation(sine_anim(), MP4, registry=reg)
    assert "ffmpeg" in str(e.value)


@needs_ffmpeg
def test_mp4_when_ffmpeg_exists_and_odd_sizes_say_they_are_padded():
    res = render_animation(sine_anim(hold_end=1), MP4)
    assert res.media_type == "video/mp4" and res.content[4:8] == b"ftyp"
    assert res.frame_count == 6 and res.frame_duration_ms == 100.0
    assert "frame_size_padded" not in codes(res.warnings)
    odd = render_animation(sine_anim(), dataclasses.replace(MP4, width=161, height=121))
    assert "frame_size_padded" in codes(odd.warnings)
    assert "161x121" in str([w.message for w in odd.warnings])


def test_ffmpeg_exiting_zero_without_output_is_encoder_failed(tmp_path):
    from ieeVisualizer.animation.encode import encode_mp4

    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\ncat > /dev/null\nexit 0\n")
    fake.chmod(0o755)
    with pytest.raises(AnimationError) as e:
        encode_mp4([np.zeros((8, 8, 3), np.uint8)] * 3, 10, str(fake), 20)
    assert codes(e.value.issues) == ["encoder_failed"]
    assert "without writing" in e.value.issues[0].message


@needs_ffmpeg
def test_mp4_and_export_stop_at_the_deadline_plus_the_kill_slack(tmp_path, monkeypatch):
    fn = _sweep(FigureSpec(layers=[_fn(HEAVY)]), 120)
    left, stop = _own_tmp(monkeypatch, tmp_path), _Stopper(monkeypatch)
    for call in (lambda: render_animation(fn, MP4, deadline=D, isolation="process"),
                 lambda: export_animation(fn, ["gif", "mp4"], MemoryWriter(), request=SMALL,
                                          deadline=D, isolation="process")):
        s, e = stop(call)
        _assert_stopped_on_time(s, e)
    assert not left()


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
    anim = _sweep(FigureSpec(layers=[_fn()]), 40)
    anim.axis_limits = "auto"
    req = dataclasses.replace(MP4, width=640, height=480)
    left = _own_tmp(monkeypatch, tmp_path)  # this test's workers only
    # long enough that a loaded runner reaches ffmpeg (worker start, parse);
    # once ffmpeg blocks the pipe, the phase stays "draw" until the kill
    d = 3 * D
    s, e = _Stopper(monkeypatch)(
        lambda: render_animation(anim, req, deadline=d, isolation="process"))
    _assert_stopped_on_time(s, e)
    assert e.stats["phase"] == "draw" and e.stats.get("states_drawn", 0) >= 1, e.stats
    ffmpeg = int(pid.read_text())
    time.sleep(0.2)
    assert not os.path.exists(f"/proc/{ffmpeg}") or "Z" in open(f"/proc/{ffmpeg}/stat").read()
    assert not left()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_a_killed_caller_leaves_no_worker_no_ffmpeg_and_no_temporary_directory(tmp_path, sig):
    """The caller is killed while its worker feeds an ffmpeg that never reads:
    the worker and ffmpeg outlived it, and its temporary directory leaked."""
    fake, pid = tmp_path / "ffmpeg", tmp_path / "pid"
    fake.write_text(f'#!/bin/sh\ncase "$*" in *-encoders*) exit 0;; esac\necho $$ > {pid}\n'
                    "exec sleep 1000\n")
    fake.chmod(0o755)
    work = tmp_path / "tmp"
    work.mkdir()
    code = ("import dataclasses\n"
            "from test_anim_deadline import _fn, _sweep\n"
            "from test_anim_mp4 import MP4\n"
            "from ieeVisualizer.animation import render_animation\n"
            "from ieeVisualizer.spec import FigureSpec\n"
            "anim = _sweep(FigureSpec(layers=[_fn()]), 40)\n"
            "anim.axis_limits = 'auto'\n"
            "render_animation(anim, dataclasses.replace(MP4, width=640, height=480), "
            "deadline=600, isolation='process')\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(HERE)]),
               TMPDIR=str(work), IEEVISUALIZER_FFMPEG=str(fake))
    caller = subprocess.Popen([sys.executable, "-c", code], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):  # until ffmpeg runs (and blocks the frame pipe)
            if pid.exists() and pid.read_text().strip():
                break
            time.sleep(0.05)
        ffmpeg = int(pid.read_text())
        worker = _ppid(ffmpeg)
        dirs = list(work.glob("ieev-anim-*"))
        assert dirs and worker not in (None, caller.pid)
        caller.send_signal(sig)
        caller.wait(10)
        alive = lambda p: os.path.exists(f"/proc/{p}") and _state(p) != "Z"  # noqa: E731
        for _ in range(100):
            if not alive(ffmpeg) and not alive(worker) and not dirs[0].exists():
                break
            time.sleep(0.05)
        assert not alive(ffmpeg) and not alive(worker) and not dirs[0].exists()
    finally:
        caller.kill()
        caller.wait()
        if pid.exists() and pid.read_text().strip():
            try:
                os.kill(int(pid.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


@needs_ffmpeg
def test_an_inline_mp4_render_leaves_no_helper_thread():
    """A helper thread kept drawing (and changing rcParams) after an MP4
    render had raised."""
    render_animation(sine_anim(), MP4, isolation="inline")
    assert not [t for t in threading.enumerate() if t.name.startswith("ieeVisualizer")]


def test_export_with_a_bad_poster_raises_a_structured_error_and_writes_nothing():
    from anim_helpers import weekly_data

    w = MemoryWriter()
    with pytest.raises(SpecValidationError) as e:
        export_animation(_ref_anim(poster=99), "gif", w, request=SMALL,
                         resolver=lambda ref: weekly_data(None))
    assert codes(e.value.issues) == ["invalid_poster"] and w.files == {}


def test_an_export_validates_once_and_builds_each_state_once(monkeypatch, tmp_path):
    from ieeVisualizer.artifacts.writers import LocalDirectoryWriter

    calls = {"build": 0, "validate": 0}
    for owner, name in ((F, "_build_template"), (V, "_validate")):
        orig = getattr(owner, name)
        key = "build" if owner is F else "validate"
        monkeypatch.setattr(owner, name, lambda *a, _o=orig, _k=key, **k:
                            calls.__setitem__(_k, calls[_k] + 1) or _o(*a, **k))
    values = [0.0, 0.5, 1.0]
    export_animation(vector_field_anim(values=values), ["gif", "mp4"] if HAS_FFMPEG else
                     ["gif"], LocalDirectoryWriter(tmp_path), request=SMALL)
    assert calls == {"build": len(values), "validate": 1}


def test_the_exported_poster_of_a_no_data_frame_uses_the_theme():
    from ieeVisualizer import themes

    dark = next(t for t in map(themes.get, (d["name"] for d in themes.list_themes()))
                if t.background and int(t.background[1:3], 16) < 64)
    want = tuple(int(dark.background[i:i + 2], 16) for i in (1, 3, 5))
    w = MemoryWriter()
    art = export_animation(weekly_anim("current", poster=2), "gif", w,
                           request=dataclasses.replace(SMALL, theme=dark.name))
    assert tuple(png_pixels(w.files[(art.artifact_id, "poster.png")])[2, 2]) == want


def test_an_export_records_where_it_ran_and_does_no_per_cell_work_in_the_caller(monkeypatch):
    import json

    from test_anim_isolation import _big_inline

    from ieeVisualizer.animation import validate as V

    for mode in ("inline", "process"):
        w = MemoryWriter()
        art = export_animation(sine_anim(), "gif", w, request=SMALL, isolation=mode)
        files = {name: c for (aid, name), c in w.files.items() if aid == art.artifact_id}
        assert art.isolation == mode and json.loads(files["manifest.json"])["isolation"] == mode
        notes = [x["code"] for x in art.variants[0].warnings if x["code"] ==
                 "deadline_best_effort"]
        assert notes == (["deadline_best_effort"] if mode == "inline" else [])
    monkeypatch.setattr(V, "validate_animation",
                        lambda *a, **k: pytest.fail("validated in the caller"))
    big = _big_inline()
    elapsed, e = _timed(lambda: export_animation(big, "gif", MemoryWriter(),
                                                 request=SMALL, deadline=1.0,
                                                 isolation="process"))
    assert e.issues[0].code == "render_deadline_exceeded"
    assert elapsed <= 1.0 + ISOLATION_SLACK_S, elapsed
