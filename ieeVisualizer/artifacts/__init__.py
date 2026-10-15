# ruff: noqa: N999 - the package name predates this module
"""Provenance-carrying exports of rendered figures.

``export(spec, formats=["png", "svg"], writer=LocalDirectoryWriter("out"))``
renders every format, stores the files, the canonical spec and a
``manifest.json`` (:class:`VisualizationArtifact`) under a content-derived
artifact id. See ``docs/RENDERING.md``.
"""

from .export import (
    MANIFEST_NAME,
    RESOLVED_SPEC_NAME,
    SPEC_NAME,
    artifact_id_for,
    export,
    library_versions,
)
from .manifest import (
    MANIFEST_SCHEMA_VERSION,
    DatasetRecord,
    FileEntry,
    RenderVariant,
    VisualizationArtifact,
)
from .writers import ArtifactWriter, LocalDirectoryWriter, MemoryWriter

__all__ = [
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "RESOLVED_SPEC_NAME",
    "SPEC_NAME",
    "ArtifactWriter",
    "DatasetRecord",
    "FileEntry",
    "LocalDirectoryWriter",
    "MemoryWriter",
    "RenderVariant",
    "VisualizationArtifact",
    "artifact_id_for",
    "export",
    "library_versions",
]
