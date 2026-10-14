"""Pluggable storage for artifact files.

A writer stores named byte blobs under an artifact id. It returns an opaque
location string for the caller's convenience; the manifest never uses it as
identity (no URL or path is an identity).
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Protocol, runtime_checkable

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def check_name(value: str, what: str) -> str:
    """Artifact ids and file names are single, portable path segments."""
    if not isinstance(value, str) or not _SAFE.match(value) or ".." in value:
        raise ValueError(f"{what} {value!r} must be 1-200 characters of letters, digits, '.', "
                         "'_' or '-', start with a letter or digit and contain no '..'.")
    return value


@runtime_checkable
class ArtifactWriter(Protocol):
    def write(self, artifact_id: str, name: str, content: bytes, media_type: str) -> str:
        """Store ``content`` as ``name`` within ``artifact_id``; return where."""
        ...


class LocalDirectoryWriter:
    """Writes ``<root>/<artifact_id>/<name>``; each file is replaced
    atomically (temp file + ``os.replace``)."""

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

    def __repr__(self) -> str:
        return f"LocalDirectoryWriter({self.root!r})"


class MemoryWriter:
    """Keeps files in a dict (``files[(artifact_id, name)] = bytes``); for
    tests and for hosts that upload elsewhere."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}
        self.media_types: dict[tuple[str, str], str] = {}

    def write(self, artifact_id: str, name: str, content: bytes, media_type: str) -> str:
        key = (check_name(artifact_id, "artifact id"), check_name(name, "file name"))
        self.files[key] = bytes(content)
        self.media_types[key] = media_type
        return f"memory:{artifact_id}/{name}"
