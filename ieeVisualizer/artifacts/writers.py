"""Pluggable storage for artifact files.

A writer stores named byte blobs under an artifact id. It returns an opaque
location string for the caller's convenience; the manifest never uses it as
identity (no URL or path is an identity).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from typing import Protocol, runtime_checkable

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
    """

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
            raise
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return target

    def __repr__(self) -> str:
        return f"LocalDirectoryWriter({self.root!r})"


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
