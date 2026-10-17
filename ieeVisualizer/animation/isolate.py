"""Process isolation: what makes the render deadline a wall-clock bound.

Cooperative deadline checks cannot stop a step that holds the GIL in C code
(one Matplotlib draw, a SymPy build, a GIF write). With
``isolation="process"`` a render or export runs in a fresh worker process
(no state is shared with the caller, so a render also cannot leave
Matplotlib's global rcParams changed). When the deadline passes, the
worker's whole process group (ffmpeg included) is killed, its temporary
directory is removed and ``render_deadline_exceeded`` is raised with the last
progress the worker reported. The overrun is then the kill and clean-up, at
most :data:`ISOLATION_SLACK_S`.

**Where it runs.** Process isolation needs a POSIX system and a real Python
interpreter as ``sys.executable`` (not a frozen application). It is the
default there; elsewhere the default is ``"inline"`` and the result carries a
``deadline_best_effort`` warning, and asking for ``"process"`` explicitly
raises ``isolation_unavailable``. ``isolation="inline"`` runs in the calling
thread with the cooperative checks only (one long step overruns).
``$IEEVISUALIZER_ANIMATION_ISOLATION`` sets the default.

**Costs.** Starting a worker takes about 0.9 s (interpreter and imports),
inside the deadline. At most :func:`worker_slots` workers run at once per
process (``$IEEVISUALIZER_ANIMATION_WORKERS``, default the CPU count); a
render waiting for a slot waits inside its deadline too.

**Trust.** The worker is ``python -I`` (no current directory, no
``PYTHON*`` variables, no user site) with ``sys.path`` set to the caller's
absolute entries, and a minimal environment. The caller sends the request
pickled (the worker must rebuild the caller's adapters and registry, and the
caller is the trusted side); the worker answers in a format that cannot run
code in the caller: length-framed JSON with raw byte blobs, decoded only into
an allow-list of result classes, and errors rebuilt from an allow-list of
exception types and issue fields. Adapters, registries and resolvers must be
importable by module name (not defined in ``__main__``). A watchdog forked by
the worker before any import kills the worker's process group and removes
its temporary directory as soon as the caller goes away (killed, crashed or
finished), so no worker or ffmpeg outlives its caller.
"""

from __future__ import annotations

import dataclasses
import json
import numbers
import os
import re
import shutil
import signal
import struct
import sys
import tempfile
import threading
from typing import Any

from ..render.base import RenderWarning, issue
from .base import AnimationError
from .deadline import Deadline

ISOLATIONS = ("process", "inline")
#: Upper bound of the overrun of an isolated render: result polling (50 ms),
#: killing the process group and removing the temporary directory.
ISOLATION_SLACK_S = 1.0
#: Largest message a worker may send (the result's bytes included).
MAX_MESSAGE_BYTES = 1 << 34
_ENV_KEEP = ("PATH", "HOME", "LANG", "LANGUAGE", "TZ", "MPLCONFIGDIR", "XDG_CACHE_HOME",
             "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "XDG_CONFIG_HOME", "FONTCONFIG_FILE", "FONTCONFIG_PATH", "SOURCE_DATE_EPOCH")
#: The worker's bootstrap. The watchdog is forked before anything is
#: imported (no threads yet): it waits for EOF on the control pipe, which only
#: the caller holds open, then kills the worker's process group and removes
#: the temporary directory.
_BOOT = """\
import json, os, signal, sys
out, ctl = int(sys.argv[2]), int(sys.argv[3])
if os.fork() == 0:
    os.close(out)
    os.close(0)
    try:
        group = os.getpgid(0)
        os.setpgid(0, 0)
        while os.read(ctl, 4096):
            pass
        os.killpg(group, signal.SIGKILL)
    except OSError:
        pass
    import shutil
    shutil.rmtree(os.environ["TMPDIR"], ignore_errors=True)
    os._exit(0)
os.close(ctl)
sys.path[:] = json.loads(sys.argv[1])
from ieeVisualizer.animation.isolate import _main
_main(out)
"""


def isolation_problem() -> str | None:
    """Why process isolation cannot run here, or None."""
    if os.name != "posix":
        return f"process isolation needs a POSIX system (os.name is {os.name!r})"
    if getattr(sys, "frozen", False):
        return "this is a frozen application (sys.executable is the application, not Python)"
    exe = sys.executable
    if not exe or not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        return f"sys.executable ({exe!r}) is not a runnable Python interpreter"
    return None


def isolation_mode(value: str | None) -> tuple[str, list[RenderWarning]]:
    """``(mode, warnings)``: ``value``, else the environment, else
    ``"process"`` where it can run. Inline, for any reason, comes with a
    ``deadline_best_effort`` warning saying why."""
    explicit = value or os.environ.get("IEEVISUALIZER_ANIMATION_ISOLATION")
    mode = explicit or "process"
    if mode not in ISOLATIONS:
        raise ValueError(f"isolation must be one of {', '.join(ISOLATIONS)}, got {mode!r}.")
    if mode == "inline":
        how = "isolation='inline'" if value else "$IEEVISUALIZER_ANIMATION_ISOLATION=inline"
        return mode, [RenderWarning(
            "deadline_best_effort", f"{how}: the render ran inline, so the deadline is "
            "checked between steps (best effort) and one long step can overrun it.", "")]
    problem = isolation_problem()
    if problem is None:
        return mode, []
    if explicit:
        raise AnimationError([issue("", "isolation_unavailable",
                                    f"isolation='process' cannot run: {problem}. Use "
                                    "isolation='inline' (the deadline is then best effort).")])
    return "inline", [RenderWarning(
        "deadline_best_effort", f"{problem}, so the render ran inline: the deadline is checked "
        "between steps (best effort) and one long step can overrun it.", "")]


_SLOTS_LOCK = threading.Lock()
_SLOTS: list[threading.BoundedSemaphore] = []


def worker_slots() -> threading.BoundedSemaphore:
    """Bounds the workers this process runs at once (each holds an
    interpreter with Matplotlib, about 100 MB, and a CPU)."""
    with _SLOTS_LOCK:
        if not _SLOTS:
            raw = os.environ.get("IEEVISUALIZER_ANIMATION_WORKERS")
            n = os.cpu_count() or 2
            if raw:
                try:
                    n = int(raw)
                except ValueError:
                    n = 0
                if n < 1:
                    raise AnimationError([issue("", "invalid_setting",
                                                "$IEEVISUALIZER_ANIMATION_WORKERS must be a "
                                                f"positive integer, got {raw[:40]!r}.")])
            _SLOTS.append(threading.BoundedSemaphore(n))
        return _SLOTS[0]


def run_isolated(task: str, payload: dict[str, Any], deadline: Deadline) -> Any:
    """Run ``task`` ("render" or "export") in a worker under ``deadline``."""
    import subprocess

    deadline.check("start worker")
    data = _pickle(payload, task)
    slots = worker_slots()
    if not slots.acquire(timeout=deadline.remaining()):
        deadline.phase = "wait for a worker slot"
        raise deadline.exceeded()
    proc = writer = None
    fds: list[int] = []
    tmp = tempfile.mkdtemp(prefix="ieev-anim-")
    try:
        rfd, wfd = os.pipe()
        ctl_r, ctl_w = os.pipe()
        fds = [rfd, wfd, ctl_r, ctl_w]
        log = os.path.join(tmp, "worker.log")
        try:
            with open(log, "wb") as err:
                proc = subprocess.Popen(
                    [sys.executable, "-I", "-c", _BOOT, json.dumps(_paths()), str(wfd),
                     str(ctl_r)], stdin=subprocess.PIPE, stdout=err, stderr=err,
                    pass_fds=(wfd, ctl_r), env=_env(tmp), start_new_session=True)
        except OSError as e:
            raise AnimationError([issue("", "render_worker_failed",
                                        f"the render worker could not start ({e}).")]) from None
        for fd in (wfd, ctl_r):
            os.close(fd)
            fds.remove(fd)
        writer = threading.Thread(target=_feed, args=(proc.stdin.fileno(), data, deadline),
                                  daemon=True)
        writer.start()
        return _receive(rfd, proc, deadline, log)
    finally:
        if proc is not None:
            _kill(proc)
            if writer is not None:
                writer.join(1.0)
            proc.stdin.close()
        for fd in fds:  # closing ctl_w releases the watchdog
            os.close(fd)
        _remove(tmp)
        slots.release()


def _remove(tmp: str, tries: int = 40, pause: float = 0.025) -> None:
    """Remove the worker's temporary directory. A process of the killed
    group (ffmpeg) can still be finishing a write when the first removal
    lists the directory, which then is not empty: retried for up to about
    1 s (within :data:`ISOLATION_SLACK_S`), the watchdog removing it after
    that."""
    import time

    for _ in range(tries):
        shutil.rmtree(tmp, ignore_errors=True)
        if not os.path.exists(tmp):
            return
        time.sleep(pause)


def _pickle(payload: dict[str, Any], task: str) -> bytes:
    """The request, pickled; refused when it refers to ``__main__`` (a
    script's or notebook's objects, which the worker cannot import)."""
    import io
    import pickle
    import types

    class Pickler(pickle.Pickler):
        def reducer_override(self, obj):
            named = isinstance(obj, (type, types.FunctionType))
            if (obj.__module__ if named else type(obj).__module__) == "__main__":
                what = obj.__qualname__ if named else type(obj).__qualname__
                raise _NotImportable(f"{what} is defined in __main__")
            return NotImplemented

    buf = io.BytesIO()
    try:
        Pickler(buf, protocol=pickle.HIGHEST_PROTOCOL).dump((task, payload))
    except Exception as e:  # noqa: BLE001 - every reason is reported the same way
        raise AnimationError([issue("", "render_payload_not_importable",
                                    f"the request cannot be sent to the render worker: {e}. "
                                    "Adapters, registries and resolvers must be defined in an "
                                    "importable module (not a script's or notebook's "
                                    "__main__), or render with isolation='inline'.")]) from None
    return buf.getvalue()


class _NotImportable(Exception):
    pass


def _paths() -> list[str]:
    import ieeVisualizer

    root = os.path.dirname(os.path.dirname(os.path.abspath(ieeVisualizer.__file__)))
    return [root, *(p for p in sys.path if isinstance(p, str) and os.path.isabs(p)
                    and p != root)]


def _env(tmp: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k in _ENV_KEEP or k.startswith(("LC_", "IEEVISUALIZER_"))}
    return {**env, "TMPDIR": tmp, "MPLBACKEND": "Agg"}


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _read_exact(fd: int, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = os.read(fd, min(n - len(buf), 1 << 22))
        if not chunk:
            raise EOFError("the request ended early")
        buf += chunk
    return bytes(buf)


def _feed(fd: int, data: bytes, deadline: Deadline) -> None:
    """Send the request (from a thread: a large one cannot block the caller
    past its deadline)."""
    try:
        _write_all(fd, struct.pack(">Qd", len(data), deadline.remaining()) + data)
    except OSError:
        pass  # the worker died or was killed: reported by the receiving side


def _receive(rfd: int, proc, deadline: Deadline, log: str) -> Any:
    import select

    buf = bytearray()
    while True:
        left = deadline.remaining()
        if left <= 0:
            raise deadline.exceeded()
        if not select.select([rfd], [], [], min(left, 0.05))[0]:
            continue
        chunk = os.read(rfd, 1 << 22)
        if not chunk:
            try:
                proc.wait(1.0)
            except Exception:  # noqa: BLE001 - killed in the caller's finally
                pass
            raise _failed(f"the render worker exited with code {proc.returncode} without a "
                          "result", log)
        buf += chunk
        while len(buf) >= 8:
            n = int.from_bytes(buf[:8], "big")
            if n > MAX_MESSAGE_BYTES:
                raise _failed("the render worker sent a malformed message", log)
            if len(buf) < 8 + n:
                break
            body = memoryview(buf)[8:8 + n]
            try:
                kind, value = _decode_message(body)
            except (ValueError, TypeError, KeyError, IndexError, RecursionError) as e:
                raise _failed(f"the render worker sent a malformed message ({e})", log) from None
            finally:
                body.release()
            del buf[:8 + n]
            if kind == "progress":
                deadline.phase, deadline.progress = value
            elif kind == "ok":
                return value
            else:
                raise value


def _failed(why: str, log: str) -> AnimationError:
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 2000))
            tail = f.read().decode("utf-8", "replace").strip()
    except OSError:
        tail = ""
    return AnimationError([issue("", "render_worker_failed",
                                 f"{why}." + (f" Its output ended with: {tail}" if tail else ""))])


def _kill(proc) -> None:
    """Kill the worker's process group (it and any ffmpeg it started)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


# -------------------------------------------------------------- the format


def _types() -> dict[str, type]:
    """The classes a worker's answer may contain."""
    from ..artifacts.manifest import DatasetRecord, FileEntry, RenderVariant
    from ..spec import FigureSpec, ValidationIssue
    from .base import AnimationRequest, AnimationResult
    from .export import AnimationArtifact

    return {c.__name__: c for c in (AnimationResult, AnimationRequest, RenderWarning,
                                    ValidationIssue, FigureSpec, AnimationArtifact,
                                    FileEntry, RenderVariant, DatasetRecord)}


def _encode(obj: Any, blobs: list[bytes], types: dict[str, type]) -> Any:
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, numbers.Integral):
        return int(obj)
    if isinstance(obj, numbers.Real):
        return float(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        blobs.append(bytes(obj))
        return {"$b": len(blobs) - 1}
    if isinstance(obj, (list, tuple)):
        items = [_encode(v, blobs, types) for v in obj]
        return items if isinstance(obj, list) else {"$t": items}
    if isinstance(obj, dict):
        return {"$m": [[_encode(k, blobs, types), _encode(v, blobs, types)]
                       for k, v in obj.items()]}
    name = type(obj).__name__
    if types.get(name) is not type(obj):
        raise TypeError(f"a {name} cannot be sent from the render worker")
    if name == "FigureSpec":
        return {"$s": obj.to_dict()}
    return {"$c": name, "f": {f.name: _encode(getattr(obj, f.name), blobs, types)
                              for f in dataclasses.fields(obj)}}


def _decode(v: Any, blobs: list[bytes], types: dict[str, type]) -> Any:
    if isinstance(v, list):
        return [_decode(x, blobs, types) for x in v]
    if not isinstance(v, dict):
        return v
    if v.keys() == {"$c", "f"}:
        return types[v["$c"]](**{str(k): _decode(x, blobs, types) for k, x in v["f"].items()})
    if len(v) != 1:
        raise ValueError(f"unexpected keys {sorted(v)}")
    ((tag, inner),) = v.items()
    if tag == "$b":
        return blobs[int(inner)]
    if tag == "$t":
        return tuple(_decode(inner, blobs, types))
    if tag == "$m":
        return {_decode(k, blobs, types): _decode(x, blobs, types) for k, x in inner}
    if tag == "$s":
        return types["FigureSpec"].from_dict(inner)
    raise ValueError(f"unknown tag {tag!r}")


def _message(kind: str, value: Any = None, **extra: Any) -> bytes:
    blobs: list[bytes] = []
    head = json.dumps({"kind": kind, "value": _encode(value, blobs, _types()),
                       "blobs": [len(b) for b in blobs], **extra}).encode("utf-8")
    body = b"".join([struct.pack(">I", len(head)), head, *blobs])
    return len(body).to_bytes(8, "big") + body


def _decode_message(body: memoryview) -> tuple[str, Any]:
    (h,) = struct.unpack(">I", body[:4])
    head = json.loads(bytes(body[4:4 + h]).decode("utf-8"))
    blobs, at = [], 4 + h
    for n in head["blobs"]:
        if not isinstance(n, int) or n < 0 or at + n > len(body):
            raise ValueError("blob lengths do not match the message")
        blobs.append(bytes(body[at:at + n]))
        at += n
    kind = head["kind"]
    if kind == "progress":
        phase, counts = head["phase"], head["counts"]
        if not isinstance(phase, str) or not all(isinstance(k, str) and isinstance(n, int)
                                                 for k, n in counts.items()):
            raise ValueError("bad progress")
        return kind, (phase[:200], dict(counts))
    if kind == "ok":
        return kind, _decode(head["value"], blobs, _types())
    if kind == "error":
        return kind, _rebuild(head["error"])
    raise ValueError(f"unknown kind {kind!r}")


_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _error_types() -> dict[str, type]:
    from ..render.base import CapabilityError, RenderError, UnresolvedDatasetError
    from ..spec import SpecValidationError
    from .deadline import DeadlineExceeded

    return {c.__name__: c for c in (SpecValidationError, AnimationError, CapabilityError,
                                    DeadlineExceeded, RenderError, UnresolvedDatasetError,
                                    ValueError, TypeError, KeyError)}


def _strs(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
        raise ValueError("expected a list of strings")
    return list(v)


def _rebuild(e: dict[str, Any]) -> BaseException:
    """The worker's exception, rebuilt from allow-listed types and fields."""
    from ..spec import ValidationIssue

    cls = _error_types().get(e["type"])
    message = str(e["message"])[:4000]
    if cls is None:
        return AnimationError([issue("", "render_worker_failed",
                                     f"the render worker raised {str(e['type'])[:100]}: "
                                     f"{message}")])
    if cls in (ValueError, TypeError, KeyError):
        return cls(message)
    issues = []
    for i in e["issues"]:
        f = [i["path"], i["code"], i["message"], i["severity"]]
        if not all(isinstance(s, str) for s in f) or not _CODE.fullmatch(f[1]) \
                or f[3] not in ("error", "warning", "info"):
            raise ValueError("bad issue")
        issues.append(ValidationIssue(*f))
    if cls.__name__ == "DeadlineExceeded":
        stats = {str(k): v for k, v in e["stats"].items()
                 if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
        return cls(stats, issues[0].message)
    if cls.__name__ == "CapabilityError":
        return cls(issues, _strs(e["requirements"]),
                   {str(k): _strs(v) for k, v in e["unmet"].items()})
    return cls(issues)


def _error_dict(e: BaseException) -> dict[str, Any]:
    """``e`` as data: its nearest allow-listed class and its structured fields."""
    listed = set(_error_types().values())
    cls = next((c for c in type(e).__mro__ if c in listed), None)
    name = cls.__name__ if cls is not None else f"{type(e).__module__}.{type(e).__qualname__}"
    message = str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)
    out: dict[str, Any] = {"type": name, "message": message,
                           "issues": [i.to_dict() for i in getattr(e, "issues", [])]}
    for k in ("stats", "requirements", "unmet"):
        if hasattr(e, k):
            out[k] = getattr(e, k)
    return out


# -------------------------------------------------------------- the worker


def _main(out: int) -> None:
    """The worker: read the request from stdin, answer on ``out``."""
    import pickle

    def send(msg: bytes) -> None:
        try:
            _write_all(out, msg)
        except OSError:  # the caller is gone: exit quietly (the watchdog cleans up)
            os._exit(1)

    try:
        n, seconds = struct.unpack(">Qd", _read_exact(0, 16))
        task, payload = pickle.loads(_read_exact(0, n))
    except Exception as e:  # noqa: BLE001 - e.g. a module the worker cannot import
        send(_message("error", error=_error_dict(AnimationError([issue(
            "", "render_payload_not_importable",
            f"the render worker could not rebuild the request ({type(e).__name__}: {e}). "
            "Adapters, registries and resolvers must be importable from the caller's "
            "sys.path, or render with isolation='inline'.")]))))
        return
    deadline = Deadline(max(seconds, 1e-3))
    deadline.report = lambda d: send(_message("progress", phase=d.phase,
                                              counts=dict(d.progress)))
    try:
        if task == "render":
            from .base import render_inline as run
        else:
            from .export import build as run
        msg = _message("ok", run(**payload, deadline=deadline))
    except BaseException as e:  # noqa: BLE001 - every failure goes back to the caller
        msg = _message("error", error=_error_dict(e))
    send(msg)


def resolved_payload(figure, resolver, deadline: Deadline | None = None) -> dict[str, Any]:
    """Resolve, in the caller, every reference the figure's layers draw (the
    resolver may not be picklable), check the cell cap on everything sent
    (the inline datasets and the resolved data, from column lengths only:
    O(columns)) before anything is serialised, and return ``{key: snapshot}``
    for :func:`table_resolver` in the worker. Everything else, the full
    data checks included, is validated once, in the worker."""
    from ..render.prepare import bound_refs
    from ..spec import SpecValidationError
    from .base import ref_key
    from .frames import drawn_only
    from .validate import cell_issues

    if figure is None:
        return {}
    refs = bound_refs(drawn_only(figure)) if resolver is not None else {}
    resolved = {k: resolver(r) for k, r in refs.items()}
    if deadline is not None:
        deadline.check("resolve")
    datasets = figure.datasets if isinstance(figure.datasets, dict) else {}
    errors = cell_issues([*datasets.values(), *resolved.values()])
    if errors:
        raise SpecValidationError(errors)
    return {ref_key(r): resolved[k] for k, r in refs.items()}


class table_resolver:  # noqa: N801 - used like a function
    """A picklable resolver answering from resolved data."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table

    def __call__(self, ref):
        from .base import ref_key

        return self.table[ref_key(ref)]
