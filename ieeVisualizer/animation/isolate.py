"""Process isolation: what makes the render deadline a wall-clock bound.

Cooperative deadline checks cannot stop a step that holds the GIL in C code
(one Matplotlib draw, a SymPy build, a GIF write). With
``isolation="process"`` (the default) a render or export runs in a fresh
worker process (no state is shared with the caller, so a render also cannot leave
Matplotlib's global rcParams changed).
The worker is a fresh interpreter running only this module (never the
caller's ``__main__``). The spec, the request and the resolved data are sent in; phase and progress
stream back; the result or a structured error comes back. When the deadline
passes, the worker's whole process group (ffmpeg included) is killed, its
temporary directory is removed and ``render_deadline_exceeded`` is raised
with the last progress the worker reported. The overrun is then the kill and
clean-up, at most :data:`ISOLATION_SLACK_S`; starting a worker costs about
0.9 s (interpreter and imports), counted inside the deadline.

``isolation="inline"`` runs in the calling thread with the cooperative
checks only (best effort: one long step overruns); it is for tests and
debugging. ``$IEEVISUALIZER_ANIMATION_ISOLATION`` sets the default.
"""

from __future__ import annotations

import os
import shutil
import signal
import tempfile
from typing import Any

from ..render.base import issue
from .base import AnimationError
from .deadline import Deadline

ISOLATIONS = ("process", "inline")
#: Upper bound of the overrun of an isolated render: result polling (50 ms),
#: killing the process group and removing the temporary directory.
ISOLATION_SLACK_S = 1.0


def isolation_mode(value: str | None) -> str:
    mode = value or os.environ.get("IEEVISUALIZER_ANIMATION_ISOLATION", "process")
    if mode not in ISOLATIONS:
        raise ValueError(f"isolation must be one of {', '.join(ISOLATIONS)}, got {mode!r}.")
    return mode


def run_isolated(task: str, payload: dict[str, Any], deadline: Deadline) -> Any:
    """Run ``task`` ("render" or "export") in a worker under ``deadline``.

    The worker is ``python -c`` running :func:`_main` in a new session (its
    own process group), never the caller's ``__main__``: the payload goes in
    on stdin, length-prefixed pickled messages come back on a private pipe."""
    import pickle
    import select
    import subprocess
    import sys

    import ieeVisualizer

    tmp = tempfile.mkdtemp(prefix="ieev-anim-")
    root = os.path.dirname(os.path.dirname(os.path.abspath(ieeVisualizer.__file__)))
    env = dict(os.environ, TMPDIR=tmp, MPLBACKEND="Agg",
               PYTHONPATH=os.pathsep.join([root, os.environ.get("PYTHONPATH", "")]))
    rfd, wfd = os.pipe()
    deadline.check("start worker")
    proc = subprocess.Popen(
        [sys.executable, "-c", "from ieeVisualizer.animation.isolate import _main; _main()",
         str(wfd)], stdin=subprocess.PIPE, pass_fds=(wfd,), env=env, start_new_session=True)
    os.close(wfd)
    buf = b""
    try:
        try:
            proc.stdin.write(pickle.dumps((task, payload, deadline.remaining())))
            proc.stdin.close()
        except BrokenPipeError:
            pass  # the worker died at start-up: its exit is reported below
        while True:
            left = deadline.remaining()
            if left <= 0:
                raise deadline.exceeded()
            if not select.select([rfd], [], [], min(left, 0.05))[0]:
                continue
            chunk = os.read(rfd, 1 << 20)
            if not chunk:
                proc.wait(1.0)
                raise AnimationError([issue("", "render_worker_failed",
                                            f"the render worker exited with code "
                                            f"{proc.returncode} without a result.")])
            buf += chunk
            while len(buf) >= 8 and len(buf) >= 8 + int.from_bytes(buf[:8], "big"):
                n = int.from_bytes(buf[:8], "big")
                kind, *rest = pickle.loads(buf[8:8 + n])
                buf = buf[8 + n:]
                if kind == "progress":
                    deadline.phase, deadline.progress = rest
                elif kind == "ok":
                    return rest[0]
                else:
                    raise _rebuild(*rest)
    finally:
        _kill(proc)
        os.close(rfd)
        shutil.rmtree(tmp, ignore_errors=True)


def _kill(proc) -> None:
    """Kill the worker's process group (it and any ffmpeg it started)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def _rebuild(cls: type, args: tuple, state: dict) -> BaseException:
    """The worker's exception, rebuilt without calling its ``__init__``."""
    e = cls.__new__(cls)
    e.args = args
    e.__dict__.update(state)
    return e


def _main() -> None:
    """The worker: read the task from stdin, send messages to the fd in argv."""
    import pickle
    import sys

    out = os.fdopen(int(sys.argv[1]), "wb", buffering=0)

    def send(msg) -> None:
        data = pickle.dumps(msg)
        out.write(len(data).to_bytes(8, "big") + data)

    task, payload, seconds = pickle.load(sys.stdin.buffer)
    deadline = Deadline(max(seconds, 1e-3))
    deadline.report = lambda d: send(("progress", d.phase, dict(d.progress)))
    try:
        from .base import render_animation as run

        send(("ok", run(**payload, deadline=deadline, isolation="inline")))
    except BaseException as e:  # noqa: BLE001 - every failure goes back to the caller
        try:
            send(("error", type(e), e.args, dict(getattr(e, "__dict__", {}))))
        except Exception:  # noqa: BLE001 - unpicklable: report it as a worker failure
            send(("error", AnimationError, (repr(e),),
                  {"issues": [issue("", "render_worker_failed", repr(e))]}))


def resolved_payload(figure, resolver) -> dict[str, Any]:
    """Resolve, in the caller, every reference the figure's layers draw (the
    resolver may not be picklable) and return ``{key: snapshot}`` for
    :func:`table_resolver` in the worker."""
    from ..render.prepare import bound_refs
    from .base import ref_key

    if figure is None or resolver is None:
        return {}
    return {ref_key(ref): resolver(ref) for ref in bound_refs(figure).values()}


class table_resolver:  # noqa: N801 - used like a function
    """A picklable resolver answering from resolved data."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table

    def __call__(self, ref):
        from .base import ref_key

        return self.table[ref_key(ref)]
