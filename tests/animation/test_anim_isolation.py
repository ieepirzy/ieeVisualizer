"""The default isolation="process": a render runs in a worker that is killed
at the deadline, so the bound holds even for a step the cooperative checks
cannot interrupt (a long draw; a blocked ffmpeg pipe: test_anim_mp4)."""

import dataclasses
import glob
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time

import pytest
from anim_helpers import HERE, ROOT, SMALL, sine_anim, vector_field_anim, weekly_anim, weekly_data
from test_anim_deadline import HEAVY, _fn, _sweep

from ieeVisualizer.animation import AnimationError, render_animation
from ieeVisualizer.animation.isolate import ISOLATION_SLACK_S
from ieeVisualizer.spec import DatasetRef, DataSnapshot, FigureSpec, SpecValidationError

ROOT_DIR, HERE_DIR = str(ROOT), str(HERE)

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


# ------------------------------------------------- review of #22/#24 (round 2)

PROBE = sine_anim()


def _probe(mode, size=0, **kw):
    from worker_probe import registry

    return render_animation(PROBE, dataclasses.replace(SMALL, adapter="probe"),
                            registry=registry(mode, size), isolation="process", **kw)


def test_only_a_worker_can_stop_a_step_that_holds_the_gil():
    """One C call (no bytecode boundary, so no cooperative check) for
    minutes: stopped at the deadline plus the kill slack."""
    elapsed, e = _timed(lambda: _probe("spin", deadline=D))
    assert e.issues[0].code == "render_deadline_exceeded"
    assert D <= elapsed <= D + ISOLATION_SLACK_S, elapsed


def test_the_worker_is_another_process_with_a_minimal_environment(monkeypatch):
    monkeypatch.setenv("SOME_CALLER_SECRET", "x")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([ROOT_DIR, ""]))
    seen = json.loads(next(w for w in _probe("report").warnings if w.code == "probe").message)
    assert seen["pid"] != os.getpid()
    assert "SOME_CALLER_SECRET" not in seen["env"] and "PYTHONPATH" not in seen["env"]
    assert all(os.path.isabs(p) for p in seen["path"]), seen["path"]


@pytest.mark.parametrize("plant", ["worker", "caller"])
def test_a_module_planted_in_the_current_directory_runs_nowhere(tmp_path, plant):
    """A ``pickle.py`` in the cwd ran in the worker (``python -c`` puts the
    cwd first, and so did an empty PYTHONPATH entry); its forged pickled
    answer then ran ``os.system`` in the caller."""
    marker = tmp_path / "RAN"
    body = (f"open({str(marker)!r}, 'a').write('worker')\n" if plant == "worker" else
            "import os, sys\n"
            f"cmd = 'echo caller > {marker}'\n"
            "body = b'cos\\nsystem\\n(S\\'' + cmd.encode() + b'\\'\\ntR.'\n"
            "os.write(int(sys.argv[2]), len(body).to_bytes(8, 'big') + body)\n"
            "os._exit(0)\n")
    cwd, app = tmp_path / "cwd", tmp_path / "app"
    cwd.mkdir()
    app.mkdir()
    (cwd / "pickle.py").write_text(body)
    (app / "main.py").write_text("from anim_helpers import SMALL, sine_anim\n"
            "from ieeVisualizer.animation import render_animation\n"
            "try:\n"
            "    render_animation(sine_anim(), SMALL, isolation='process'); print('ok')\n"
            "except Exception as e:\n"
            "    print(type(e).__name__, e)\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([ROOT_DIR, HERE_DIR]))
    out = subprocess.run([sys.executable, str(app / "main.py")], cwd=cwd, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "ok", (out.stdout, out.stderr)
    assert not marker.exists()


def test_a_forged_or_pickled_answer_is_refused_without_running_it():
    from ieeVisualizer.animation.isolate import _decode_message, _message

    forged = b"cos\nsystem\n(S'true'\ntR."
    for body in (forged, struct.pack(">I", 2) + b"{}", _message("ok", b"x")[8:-1]):
        with pytest.raises((ValueError, KeyError, struct.error)):
            _decode_message(memoryview(body))
    head = {"kind": "ok", "value": {"$c": "Popen", "f": {}}, "blobs": []}
    raw = json.dumps(head).encode()
    with pytest.raises(KeyError):
        _decode_message(memoryview(struct.pack(">I", len(raw)) + raw))


def test_a_large_result_is_received_in_linear_time():
    """``buf += chunk`` on bytes: 100 MB took 51.6 s."""
    t = time.monotonic()
    res = _probe("big", 64_000_000)
    assert len(res.content) == 64_000_000 and time.monotonic() - t < 15


def test_resolved_data_over_the_cap_is_refused_before_any_worker_starts(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", lambda *a, **k: pytest.fail("a worker was started"))
    anim = weekly_anim("cumulative")
    anim.figure.datasets["b"] = DatasetRef(kind="query", id="w")
    big = DataSnapshot.from_columns({"week": [0] * 500_001, "value": [0.0] * 500_001,
                                     "series": ["a"] * 500_001})
    with pytest.raises(SpecValidationError) as e:
        render_animation(anim, SMALL, resolver=lambda ref: big, isolation="process")
    assert {i.code for i in e.value.issues} == {"data_too_large"}
    with pytest.raises(SpecValidationError):  # an invalid spec does not start one either
        render_animation(sine_anim(poster=99), SMALL, isolation="process")


def test_objects_from_main_and_failed_starts_are_structured_errors(monkeypatch):
    from worker_probe import Probe, registry

    class Local(Probe):
        pass

    Local.__module__ = "__main__"
    reg = registry("report")
    reg.register(Local("report"), replace=True)
    before = _left()
    with pytest.raises(AnimationError) as e:
        render_animation(PROBE, dataclasses.replace(SMALL, adapter="probe"), registry=reg,
                         isolation="process")
    assert e.value.issues[0].code == "render_payload_not_importable"
    import subprocess as sp

    def refuse(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(sp, "Popen", refuse)
    fds = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
    with pytest.raises(AnimationError) as e:
        render_animation(PROBE, SMALL, isolation="process")
    assert e.value.issues[0].code == "render_worker_failed"
    assert _left() <= before
    if fds is not None:
        assert len(os.listdir("/proc/self/fd")) == fds


def test_without_a_usable_interpreter_process_isolation_is_refused_or_reported(monkeypatch):
    monkeypatch.setattr(sys, "executable", "")
    with pytest.raises(AnimationError) as e:
        render_animation(PROBE, SMALL, isolation="process")
    assert e.value.issues[0].code == "isolation_unavailable"
    monkeypatch.delenv("IEEVISUALIZER_ANIMATION_ISOLATION")
    res = render_animation(PROBE, SMALL)
    assert [w.code for w in res.warnings][-1] == "deadline_best_effort"


def test_a_worker_failure_keeps_its_output_off_the_callers_stderr(capfd):
    with pytest.raises(AnimationError) as e:
        _probe("fail")
    assert e.value.issues[0].code == "render_worker_failed"
    assert "RuntimeError" in e.value.issues[0].message and "probe failed" in str(e.value)
    assert "probe:" not in capfd.readouterr().err


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_a_killed_caller_leaves_no_worker_and_no_temporary_directory(tmp_path, sig):
    code = ("import sys\n"
            "from test_anim_deadline import HEAVY, _fn, _sweep\n"
            "from anim_helpers import SMALL\n"
            "from ieeVisualizer.animation import render_animation\n"
            "from ieeVisualizer.spec import FigureSpec\n"
            "print('go', flush=True)\n"
            "render_animation(_sweep(FigureSpec(layers=[_fn(HEAVY)]), 120), SMALL, "
            "deadline=60, isolation='process')\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([ROOT_DIR, HERE_DIR]),
               TMPDIR=str(tmp_path))
    caller = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            work = list(tmp_path.glob("ieev-anim-*"))
            if work:
                break
            time.sleep(0.05)
        time.sleep(1.5)  # the worker is drawing
        workers = [int(p) for p in os.listdir("/proc") if p.isdigit()
                   and _ppid(int(p)) == caller.pid]
        assert work and workers
        caller.send_signal(sig)
        caller.wait(10)
        for _ in range(100):
            if not any(os.path.exists(f"/proc/{w}") and _ppid(w) is not None
                       and _state(w) != "Z" for w in workers) and not work[0].exists():
                break
            time.sleep(0.05)
        assert not work[0].exists()
        assert all(not os.path.exists(f"/proc/{w}") or _state(w) == "Z" for w in workers)
    finally:
        caller.kill()
        caller.wait()


def _ppid(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _state(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return "gone"
