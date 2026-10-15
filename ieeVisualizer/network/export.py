"""Store a rendered network as a VisualizationArtifact (same manifest as
FigureSpec exports: files, hashes, library versions, warnings)."""

from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Callable

from ..artifacts.export import (
    MANIFEST_NAME,
    SPEC_NAME,
    _file,
    artifact_id_for,
    library_versions,
)
from ..artifacts.manifest import DatasetRecord, RenderVariant, VisualizationArtifact
from ..artifacts.writers import ArtifactWriter
from ..render.base import RenderRequest
from .model import NetworkSpec, graph_hash, network_hash, network_to_dict, network_to_json
from .render import render_network


def export_network(spec: NetworkSpec, formats=("png", "svg"),
                   writer: ArtifactWriter | None = None, *,
                   clock: Callable[[], _dt.datetime] | None = None,
                   request: RenderRequest | None = None,
                   name: str | None = None) -> VisualizationArtifact:
    """Render every format first, then write ``figure.<fmt>``, ``spec.json``
    (canonical network JSON; its hash is ``spec_hash``) and ``manifest.json``
    last. The graph is recorded as dataset ``graph`` with its
    :func:`graph_hash` and provenance."""
    if writer is None:
        raise ValueError("export_network() needs a writer, e.g. LocalDirectoryWriter('out/').")
    fmts = list(dict.fromkeys([formats] if isinstance(formats, str) else formats))
    base = request or RenderRequest()
    outputs = []
    for fmt in fmts:
        req = dataclasses.replace(base, format=fmt)
        res = render_network(spec, req)
        fname = f"figure.{fmt}"
        outputs.append((fname, res.content, res.media_type, RenderVariant(
            format=fmt, file=_file(fname, res.content, res.media_type),
            adapter_name=res.adapter_name, adapter_version=res.adapter_version,
            request=req.to_dict(), warnings=[w.to_dict() for w in res.warnings])))
    d = network_to_dict(spec)
    records = {"graph": DatasetRecord(type="snapshot", content_hash=graph_hash(spec),
                                      provenance=d.get("provenance", {}), columns={})}
    digest = network_hash(spec)
    artifact_id = artifact_id_for(digest, {"graph": graph_hash(spec)}, name, formats=fmts,
                                  request=base.to_dict())
    spec_bytes = network_to_json(spec).encode("utf-8")
    created = (clock or (lambda: _dt.datetime.now(_dt.timezone.utc)))()
    artifact = VisualizationArtifact(
        artifact_id=artifact_id, spec_hash=digest, spec_schema_version=spec.schema_version,
        spec_file=_file(SPEC_NAME, spec_bytes, "application/json"), datasets=records,
        variants=[v for *_, v in outputs], library_versions=library_versions(),
        created_at=created.isoformat(), spec_id=spec.id, title=spec.title,
        spec_metadata=dict(spec.metadata))
    blobs = [(f, c, mt) for f, c, mt, _ in outputs]
    blobs.append((SPEC_NAME, spec_bytes, "application/json"))
    blobs.append((MANIFEST_NAME, (artifact.to_json() + "\n").encode("utf-8"),
                  "application/json"))
    write_all = getattr(writer, "write_artifact", None)
    if callable(write_all):
        write_all(artifact_id, blobs)
    else:
        for fname, content, media in blobs:
            writer.write(artifact_id, fname, content, media)
    return artifact
