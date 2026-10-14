"""Render a spec to several formats and store it as a VisualizationArtifact."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import platform
from typing import Callable

from ..render.base import MEDIA_TYPES, RenderRequest, Resolver
from ..render.prepare import prepare
from ..render.registry import AdapterRegistry, select_adapter
from ..spec import DatasetRef, DataSnapshot, FigureSpec, spec_hash, to_json
from ..spec._common import sha256_of
from ..spec.data import dataset_to_dict
from .manifest import DatasetRecord, FileEntry, RenderVariant, VisualizationArtifact
from .writers import ArtifactWriter, check_name

MANIFEST_NAME = "manifest.json"
SPEC_NAME = "spec.json"
RESOLVED_SPEC_NAME = "spec.resolved.json"


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _file(name: str, content: bytes, media_type: str) -> FileEntry:
    return FileEntry(name=name, media_type=media_type, sha256=_sha(content),
                     size_bytes=len(content))


def library_versions() -> dict[str, str]:
    import matplotlib
    import numpy
    import sympy

    from .. import __version__

    return {"ieeVisualizer": __version__, "matplotlib": matplotlib.__version__,
            "numpy": numpy.__version__, "sympy": sympy.__version__,
            "python": platform.python_version()}


def artifact_id_for(spec_digest: str, dataset_hashes: dict[str, str | None],
                    name: str | None = None) -> str:
    """Content-derived id: ``<name or 'viz'>-<16 hex>`` over the spec hash and
    the hash of every dataset drawn."""
    digest = sha256_of({"spec_hash": spec_digest, "datasets": dataset_hashes})
    prefix = check_name(name, "artifact name") if name is not None else "viz"
    return f"{prefix}-{digest.split(':', 1)[1][:16]}"


def _records(spec: FigureSpec, resolved: dict[str, DataSnapshot]) -> dict[str, DatasetRecord]:
    out: dict[str, DatasetRecord] = {}
    for name, ds in spec.datasets.items():
        if isinstance(ds, DataSnapshot):
            d = dataset_to_dict(ds)
            out[name] = DatasetRecord(type="snapshot", content_hash=ds.content_hash(),
                                      provenance=d.get("provenance", {}),
                                      columns=_column_meta(ds))
        elif isinstance(ds, DatasetRef):
            d = dataset_to_dict(ds)
            snap = resolved.get(name)
            ref = {k: v for k, v in d.items() if k not in ("type", "columns")}
            out[name] = DatasetRecord(
                type="ref",
                content_hash=snap.content_hash() if snap is not None else None,
                provenance=dataset_to_dict(snap).get("provenance", {}) if snap else {},
                columns=_column_meta(snap) if snap else d.get("columns", {}),
                ref=ref, resolved=snap is not None)
    return out


def _column_meta(ds: DataSnapshot) -> dict[str, dict]:
    full = dataset_to_dict(ds)["columns"]
    return {name: {k: v for k, v in col.items() if k != "values"} for name, col in full.items()}


def export(
    spec: FigureSpec,
    formats=("png", "svg"),
    writer: ArtifactWriter | None = None,
    *,
    resolver: Resolver | None = None,
    clock: Callable[[], _dt.datetime] | None = None,
    request: RenderRequest | None = None,
    name: str | None = None,
    registry: AdapterRegistry | None = None,
) -> VisualizationArtifact:
    """Render ``spec`` once per format and store files + ``manifest.json``.

    ``request`` supplies size/dpi/theme for every variant (its format is
    replaced per variant). ``name`` (e.g. a report run id) prefixes the
    content-derived artifact id. ``clock`` returns the ``created_at``
    datetime (inject a fixed one in tests). Every variant is rendered before
    anything is written, so a failure leaves no partial artifact behind;
    the manifest is written last.
    """
    if writer is None:
        raise ValueError("export() needs a writer, e.g. LocalDirectoryWriter('out/').")
    fmts = list(dict.fromkeys([formats] if isinstance(formats, str) else formats))
    if not fmts:
        raise ValueError("export() needs at least one format.")
    for f in fmts:
        if f not in MEDIA_TYPES:
            raise ValueError(f"unknown format {f!r}; use one of {', '.join(MEDIA_TYPES)}.")
    base = request or RenderRequest()

    prepared = prepare(spec, resolver)
    records = _records(spec, prepared.resolved)
    digest = spec_hash(spec)
    artifact_id = artifact_id_for(digest, {k: r.content_hash for k, r in records.items()}, name)

    outputs: list[tuple[str, bytes, str, RenderVariant]] = []
    for fmt in fmts:
        req = dataclasses.replace(base, format=fmt)
        adapter = select_adapter(prepared.spec, req, registry)
        result = adapter.render(prepared.spec, req)
        fname = f"figure.{fmt}"
        outputs.append((fname, result.content, result.media_type, RenderVariant(
            format=fmt, file=_file(fname, result.content, result.media_type),
            adapter_name=result.adapter_name, adapter_version=result.adapter_version,
            request=req.to_dict(), warnings=[w.to_dict() for w in result.warnings])))

    spec_bytes = to_json(spec).encode("utf-8")
    files = [(SPEC_NAME, spec_bytes, "application/json")]
    resolved_entry = None
    if prepared.resolved:
        rbytes = to_json(prepared.spec).encode("utf-8")
        files.append((RESOLVED_SPEC_NAME, rbytes, "application/json"))
        resolved_entry = _file(RESOLVED_SPEC_NAME, rbytes, "application/json")

    created = (clock or (lambda: _dt.datetime.now(_dt.timezone.utc)))()
    artifact = VisualizationArtifact(
        artifact_id=artifact_id,
        spec_hash=digest,
        spec_schema_version=spec.schema_version,
        spec_file=_file(SPEC_NAME, spec_bytes, "application/json"),
        datasets=records,
        variants=[v for *_, v in outputs],
        library_versions=library_versions(),
        created_at=created.isoformat(),
        spec_id=spec.id,
        title=spec.title,
        spec_metadata=dict(spec.metadata),
        resolved_spec_file=resolved_entry,
    )
    for fname, content, media, _ in outputs:
        writer.write(artifact_id, fname, content, media)
    for fname, content, media in files:
        writer.write(artifact_id, fname, content, media)
    writer.write(artifact_id, MANIFEST_NAME, (artifact.to_json() + "\n").encode("utf-8"),
                 "application/json")
    return artifact
