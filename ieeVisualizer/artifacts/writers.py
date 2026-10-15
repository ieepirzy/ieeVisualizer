"""Pluggable storage for artifact files.

A writer stores named byte blobs under an artifact id. It returns an opaque
location string for the caller's convenience; the manifest never uses it as
identity (no URL or path is an identity).
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Protocol, runtime_checkable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")


def check_name(value: str, what: str) -> str:
    """Artifact ids and file names are single, portable path segments."""
    if not isinstance(value, str) or not _SAFE.fullmatch(value) or ".." in value:
        raise ValueError(f"{what} {value!r} must be 1-200 characters of letters, digits, '.', "
                         "'_' or '-', start with a letter or digit and contain no '..'.")
    return value


@runtime_checkable
class ArtifactWriter(Protocol):
    """``write`` is required. A writer may also provide
    ``write_artifact(artifact_id, files) -> str`` (``files`` is a list of
    ``(name, content, media_type)``, manifest last) that replaces everything
    stored under ``artifact_id`` with exactly ``files``; ``export()`` prefers
    it, so a re-export cannot leave files the new manifest does not list."""

    def write(self, artifact_id: str, name: str, content: bytes, media_type: str) -> str:
        """Store ``content`` as ``name`` within ``artifact_id``; return where."""
        ...


class LocalDirectoryWriter:
    """Writes ``<root>/<artifact_id>/<name>``; each file is replaced
    atomically (temp file + ``os.replace``).

    :meth:`write_artifact` (used by ``export()``) stages all files in a
    temporary directory under ``root`` and then swaps it in for
    ``<root>/<artifact_id>``, so the directory holds exactly the files of
    one export: never a mix of two, never a file its manifest does not list.

    Concurrent exports of one id (threads or processes) are serialised by a
    per-id lock file ``<root>/.lock-<artifact_id>`` (``fcntl.flock`` on
    POSIX, ``msvcrt.locking`` on Windows, an ``O_EXCL`` lock file where
    neither exists); the lock file is left in place for reuse. When the
    directory already holds exactly the same files (artifacts are
    content-addressed, so a repeated export usually does) nothing is
    rewritten. On any failure the staging directory is removed and a
    moved-aside earlier version is put back; staging/retired directories of
    this id left by a killed process are removed on the next export.
    Readers may still see ``<root>/<artifact_id>`` briefly missing while
    *different* content replaces it: the standard library has no portable
    atomic directory exchange.
    """

    #: Seconds to wait for another export of the same id (O_EXCL fallback).
    lock_timeout = 60.0

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = os.fspath(root)

    def path_for(self, artifact_id: str, name: str) -> str:
        return os.path.join(self.root, check_name(artifact_id, "artifact id"),
                            check_name(name, "file name"))

    def write(self, artifact_id: str, name: str, content: bytes, media_type: str) -> str:
        target = self.path_for(artifact_id, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(target))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return target

    def write_artifact(self, artifact_id: str, files: list[tuple[str, bytes, str]]) -> str:
        """Replace ``<root>/<artifact_id>`` with exactly ``files``; return
        the directory."""
        target = os.path.join(self.root, check_name(artifact_id, "artifact id"))
        names = [check_name(name, "file name") for name, _, _ in files]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate file names in artifact {artifact_id!r}: {names}")
        os.makedirs(self.root, exist_ok=True)
        with _id_lock(self.root, artifact_id, self.lock_timeout):
            if _holds_exactly(target, names, files):
                return target
            self._remove_leftovers(artifact_id)
            self._swap_in(artifact_id, target, names, files)
        return target

    def _swap_in(self, artifact_id: str, target: str, names: list[str],
                 files: list[tuple[str, bytes, str]]) -> None:
        # '.'-prefixed names can never be artifact ids (check_name), so the
        # staging and retired directories cannot collide with one.
        stage = tempfile.mkdtemp(prefix=f".tmp-{artifact_id}-", dir=self.root)
        old = None
        try:
            for name, (_, content, _) in zip(names, files):
                with open(os.path.join(stage, name), "wb") as f:
                    f.write(content)
            if os.path.lexists(target):
                old = tempfile.mkdtemp(prefix=f".old-{artifact_id}-", dir=self.root)
                os.replace(target, old)  # an empty directory may be replaced
            try:
                os.replace(stage, target)
            except BaseException:
                if old is not None:
                    os.replace(old, target)
                    old = None
                raise
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            if old is not None:
                # Still the empty placeholder unless putting the earlier
                # version back failed; that version is never deleted here.
                with contextlib.suppress(OSError):
                    os.rmdir(old)
            raise
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)

    def _remove_leftovers(self, artifact_id: str) -> None:
        """Staging/retired directories of this id from an export that was
        killed (the lock is held, so no live export owns them)."""
        pattern = re.compile(r"\.(tmp|old)-" + re.escape(artifact_id) + r"-[a-z0-9_]{8}")
        for entry in os.listdir(self.root):
            if pattern.fullmatch(entry):
                shutil.rmtree(os.path.join(self.root, entry), ignore_errors=True)

    def __repr__(self) -> str:
        return f"LocalDirectoryWriter({self.root!r})"


def _holds_exactly(target: str, names: list[str], files: list[tuple[str, bytes, str]]) -> bool:
    """``target`` is a directory holding exactly ``files``, byte for byte."""
    try:
        if not os.path.isdir(target) or sorted(os.listdir(target)) != sorted(names):
            return False
        for name, (_, content, _) in zip(names, files):
            with open(os.path.join(target, name), "rb") as f:
                if f.read() != content:
                    return False
    except OSError:
        return False
    return True


_THREAD_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def _id_lock(root: str, artifact_id: str, timeout: float):
    """Exclusive lock on one artifact id, across threads and processes."""
    key = (os.path.realpath(root), artifact_id)
    with _THREAD_LOCKS_GUARD:
        tlock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with tlock:
        path = os.path.join(root, f".lock-{artifact_id}")
        if fcntl is not None or msvcrt is not None:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                else:  # pragma: no cover - Windows
                    _msvcrt_lock(fd, timeout)
                yield
            finally:
                os.close(fd)  # closing releases the lock
            return
        yield from _exclusive_file(path, timeout)  # pragma: no cover - no fcntl/msvcrt


def _msvcrt_lock(fd: int, timeout: float) -> None:  # pragma: no cover - Windows
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"\0")
    os.lseek(fd, 0, os.SEEK_SET)
    deadline = time.monotonic() + timeout
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"could not lock {fd} within {timeout} s") from None
            time.sleep(0.01)


def _exclusive_file(path: str, timeout: float):  # pragma: no cover - fallback
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path + ".excl", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{path}.excl is held by another export (remove it if no "
                                   "export is running)") from None
            time.sleep(0.01)
    try:
        yield
    finally:
        os.close(fd)
        os.unlink(path + ".excl")


class MemoryWriter:
    """Keeps files in a dict (``files[(artifact_id, name)] = bytes``); for
    tests and for hosts that upload elsewhere."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}
        self.media_types: dict[tuple[str, str], str] = {}

    def write_artifact(self, artifact_id: str, files: list[tuple[str, bytes, str]]) -> str:
        """Replace everything stored under ``artifact_id`` with ``files``."""
        check_name(artifact_id, "artifact id")
        for name, _, _ in files:
            check_name(name, "file name")
        for key in [k for k in self.files if k[0] == artifact_id]:
            del self.files[key]
            del self.media_types[key]
        for name, content, media_type in files:
            self.write(artifact_id, name, content, media_type)
        return f"memory:{artifact_id}"

    def write(self, artifact_id: str, name: str, content: bytes, media_type: str) -> str:
        key = (check_name(artifact_id, "artifact id"), check_name(name, "file name"))
        self.files[key] = bytes(content)
        self.media_types[key] = media_type
        return f"memory:{artifact_id}/{name}"
