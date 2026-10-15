"""The VisualizationArtifact manifest: what was rendered, from what, by what.

A manifest pins a figure for later comparison and audit:

* ``artifact_id`` is derived from the content (spec hash + the content hash
  of every dataset drawn, so a re-resolved reference with different data gets
  a different id), optionally prefixed by a caller-supplied name such as a
  report run id. It never comes from a storage URL or path.
* ``spec_hash`` is the hash of the canonical spec JSON (``spec.json`` in the
  artifact, byte for byte). If references were resolved, the spec with the
  resolved data inlined is stored too (``spec.resolved.json``) so the figure
  can be re-rendered from the frozen snapshot after the source changes.
* ``datasets`` carries each dataset's content hash, its provenance copied
  from the spec (source, query, retrieved_at, timezone, run id ...), and the
  column meaning (dtype, unit, denominator, aggregation).
* ``variants`` lists each rendered file with format, media type, SHA-256,
  byte size, adapter name/version, the request used and every fidelity
  warning.
* ``theme`` names the theme drawn with (name, version, content hash) and
  points at ``theme.json``, its full definition, so a historical figure can be
  re-rendered after the registered theme has a new version.
* ``library_versions`` and ``created_at`` record the environment and time;
  ``created_at`` is the only field that changes between identical exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..spec._common import canonical_json

#: Version of the manifest format itself (not of the spec).
MANIFEST_SCHEMA_VERSION = "1.0"


@dataclass
class FileEntry:
    """A stored file of the artifact (name is relative to the artifact)."""

    name: str
    media_type: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "media_type": self.media_type, "sha256": self.sha256,
                "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileEntry:
        return cls(name=d["name"], media_type=d["media_type"], sha256=d["sha256"],
                   size_bytes=d["size_bytes"])


@dataclass
class RenderVariant:
    """One rendered output of the figure."""

    format: str
    file: FileEntry
    adapter_name: str
    adapter_version: str
    request: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def media_type(self) -> str:
        return self.file.media_type

    @property
    def sha256(self) -> str:
        return self.file.sha256

    @property
    def size_bytes(self) -> int:
        return self.file.size_bytes

    def to_dict(self) -> dict[str, Any]:
        return {"format": self.format, "file": self.file.to_dict(),
                "adapter": {"name": self.adapter_name, "version": self.adapter_version},
                "request": dict(self.request), "warnings": [dict(w) for w in self.warnings]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RenderVariant:
        return cls(format=d["format"], file=FileEntry.from_dict(d["file"]),
                   adapter_name=d["adapter"]["name"], adapter_version=d["adapter"]["version"],
                   request=dict(d.get("request", {})),
                   warnings=[dict(w) for w in d.get("warnings", [])])


@dataclass
class DatasetRecord:
    """Provenance of one dataset of the spec."""

    type: str  # "snapshot" | "ref"
    content_hash: str | None  # None for a reference no layer drew (never resolved)
    provenance: dict[str, Any] = field(default_factory=dict)
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)
    ref: dict[str, Any] | None = None
    resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "content_hash": self.content_hash,
                             "provenance": dict(self.provenance),
                             "columns": {k: dict(v) for k, v in self.columns.items()}}
        if self.ref is not None:
            d["ref"] = dict(self.ref)
            d["resolved"] = self.resolved
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DatasetRecord:
        return cls(type=d["type"], content_hash=d.get("content_hash"),
                   provenance=dict(d.get("provenance", {})),
                   columns={k: dict(v) for k, v in d.get("columns", {}).items()},
                   ref=dict(d["ref"]) if d.get("ref") is not None else None,
                   resolved=bool(d.get("resolved", False)))


@dataclass
class VisualizationArtifact:
    artifact_id: str
    spec_hash: str
    spec_schema_version: str
    spec_file: FileEntry
    datasets: dict[str, DatasetRecord]
    variants: list[RenderVariant]
    library_versions: dict[str, str]
    created_at: str
    spec_id: str | None = None
    title: str | None = None
    spec_metadata: dict[str, Any] = field(default_factory=dict)
    resolved_spec_file: FileEntry | None = None
    #: ``{"name", "version", "sha256", "file": FileEntry dict}``; ``None`` in
    #: manifests written before themes were recorded.
    theme: dict[str, Any] | None = None
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def variant(self, fmt: str) -> RenderVariant:
        for v in self.variants:
            if v.format == fmt:
                return v
        raise KeyError(f"no {fmt!r} variant; have {[v.format for v in self.variants]}.")

    @property
    def dataset_hashes(self) -> dict[str, str | None]:
        return {k: v.content_hash for k, v in self.datasets.items()}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "spec_hash": self.spec_hash,
            "spec_schema_version": self.spec_schema_version,
            "spec_file": self.spec_file.to_dict(),
            "datasets": {k: v.to_dict() for k, v in self.datasets.items()},
            "variants": [v.to_dict() for v in self.variants],
            "library_versions": dict(self.library_versions),
            "created_at": self.created_at,
            "spec_metadata": dict(self.spec_metadata),
        }
        for k in ("spec_id", "title"):
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        if self.resolved_spec_file is not None:
            d["resolved_spec_file"] = self.resolved_spec_file.to_dict()
        if self.theme is not None:
            d["theme"] = dict(self.theme)
        return d

    def to_json(self, indent: int | None = 2) -> str:
        """Sorted-key JSON; ``indent=None`` gives the compact canonical form."""
        return canonical_json(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisualizationArtifact:
        if d.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"manifest schema_version {d.get('schema_version')!r} is not "
                             f"supported (expected {MANIFEST_SCHEMA_VERSION!r}).")
        rs = d.get("resolved_spec_file")
        return cls(
            artifact_id=d["artifact_id"], spec_hash=d["spec_hash"],
            spec_schema_version=d["spec_schema_version"],
            spec_file=FileEntry.from_dict(d["spec_file"]),
            datasets={k: DatasetRecord.from_dict(v) for k, v in d["datasets"].items()},
            variants=[RenderVariant.from_dict(v) for v in d["variants"]],
            library_versions=dict(d["library_versions"]), created_at=d["created_at"],
            spec_id=d.get("spec_id"), title=d.get("title"),
            spec_metadata=dict(d.get("spec_metadata", {})),
            resolved_spec_file=FileEntry.from_dict(rs) if rs else None,
            theme=dict(d["theme"]) if d.get("theme") is not None else None,
        )

    @classmethod
    def from_json(cls, s: str) -> VisualizationArtifact:
        import json

        return cls.from_dict(json.loads(s))
