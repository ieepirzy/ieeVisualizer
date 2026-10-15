"""Export a rendered ViewSpec with a provenance manifest.

``export_view(view, ["html", "svg", "png"], writer)`` stores::

    view.<fmt>              one file per rendered format
    view.json               the canonical ViewSpec (hash = view_hash)
    figure-<n>.spec.json    each figure placed in the view, canonical FigureSpec
    manifest.json           ViewArtifact (below), written last

The manifest lists, per figure, its JSON pointer in the view, its spec hash
and the provenance of every dataset it draws (the same
:class:`~ieeVisualizer.artifacts.DatasetRecord` a single-figure export
writes), the view-level datasets (tables, KPI sources), each KPI's label,
value, unit and source, and per format the file hash, size and every fidelity
warning. The artifact id is content-derived, like a figure artifact's.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable

from ..artifacts.export import _file, _records, artifact_id_for, library_versions
from ..artifacts.manifest import DatasetRecord, FileEntry, RenderVariant
from ..artifacts.writers import ArtifactWriter
from ..render import Resolver
from ..render.prepare import prepare
from ..spec import FigureSpec, spec_hash, to_json
from ..spec._common import canonical_json, ptr_join
from .model import FigureComponent, Kpi, ViewSpec, walk
from .render import RENDERER_NAME, RENDERER_VERSION, VIEW_FORMATS, render_view
from .validate import view_hash, view_to_json

VIEW_MANIFEST_SCHEMA_VERSION = "1.0"


@dataclass
class ViewArtifact:
    artifact_id: str
    view_hash: str
    view_schema_version: str
    view_file: FileEntry
    figures: list[dict[str, Any]]
    datasets: dict[str, DatasetRecord]
    kpis: list[dict[str, Any]]
    variants: list[RenderVariant]
    library_versions: dict[str, str]
    created_at: str
    title: str | None = None
    view_id: str | None = None
    view_metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = VIEW_MANIFEST_SCHEMA_VERSION

    def variant(self, fmt: str) -> RenderVariant:
        for v in self.variants:
            if v.format == fmt:
                return v
        raise KeyError(f"no {fmt!r} variant; have {[v.format for v in self.variants]}.")

    def to_dict(self) -> dict[str, Any]:
        d = {"schema_version": self.schema_version, "kind": "view",
             "artifact_id": self.artifact_id, "view_hash": self.view_hash,
             "view_schema_version": self.view_schema_version,
             "view_file": self.view_file.to_dict(),
             "figures": [dict(f) for f in self.figures],
             "datasets": {k: v.to_dict() for k, v in self.datasets.items()},
             "kpis": [dict(k) for k in self.kpis],
             "variants": [v.to_dict() for v in self.variants],
             "library_versions": dict(self.library_versions), "created_at": self.created_at,
             "view_metadata": dict(self.view_metadata)}
        for k in ("title", "view_id"):
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return canonical_json(self.to_dict(), indent=indent)


def placed_figures(view: ViewSpec) -> list[tuple[str, FigureSpec]]:
    """``(pointer, spec)`` of every distinct figure placed, in document order."""
    out: dict[str, FigureSpec] = {}
    for path, n in walk(view.root):
        if isinstance(n, FigureComponent):
            if n.spec is not None:
                out.setdefault(ptr_join(path, "spec"), n.spec)
            elif n.figure in view.figures:
                out.setdefault(ptr_join("/figures", n.figure), view.figures[n.figure])
    return list(out.items())


def export_view(view: ViewSpec, formats=("html", "svg", "png"),
                writer: ArtifactWriter | None = None, *, width: float = 960.0,
                scale: float = 2.0, resolver: Resolver | None = None,
                clock: Callable[[], _dt.datetime] | None = None,
                name: str | None = None) -> ViewArtifact:
    """Render every format, then store all files and the manifest (last)."""
    if writer is None:
        raise ValueError("export_view() needs a writer, e.g. LocalDirectoryWriter('out/').")
    fmts = list(dict.fromkeys([formats] if isinstance(formats, str) else formats))
    if not fmts or any(f not in VIEW_FORMATS for f in fmts):
        raise ValueError(f"formats must be a non-empty subset of {', '.join(VIEW_FORMATS)}.")
    results = [(f, render_view(view, f, width=width, scale=scale, resolver=resolver))
               for f in fmts]

    files: list[tuple[str, bytes, str]] = []
    figures, hashes = [], {}
    for i, (pointer, spec) in enumerate(placed_figures(view)):
        sbytes = to_json(spec).encode("utf-8")
        fname = f"figure-{i}.spec.json"
        files.append((fname, sbytes, "application/json"))
        recs = _records(spec, prepare(spec, resolver).resolved)
        hashes.update({f"{pointer}/datasets/{k}": r.content_hash for k, r in recs.items()})
        figures.append({"pointer": pointer, "spec_hash": spec_hash(spec),
                        "spec_file": _file(fname, sbytes, "application/json").to_dict(),
                        "title": spec.title,
                        "datasets": {k: r.to_dict() for k, r in recs.items()}})
    view_records = _records(FigureSpec(datasets=dict(view.datasets)), {})
    hashes.update({f"/datasets/{k}": r.content_hash for k, r in view_records.items()})
    kpis = [{"pointer": p, "label": n.label, "value": n.value, "unit": n.unit,
             "source": n.source, "dataset": n.dataset}
            for p, n in walk(view.root) if isinstance(n, Kpi)]

    digest = view_hash(view)
    request = {"width": width, "scale": scale, "renderer": RENDERER_NAME}
    artifact_id = artifact_id_for(digest, hashes, name, formats=fmts, request=request)
    variants = []
    for fmt, res in results:
        fname = f"view.{fmt}"
        files.insert(0, (fname, res.content, res.media_type))
        variants.append(RenderVariant(format=fmt, file=_file(fname, res.content, res.media_type),
                                      adapter_name=RENDERER_NAME,
                                      adapter_version=RENDERER_VERSION, request=dict(request),
                                      warnings=[w.to_dict() for w in res.warnings]))
    vbytes = view_to_json(view).encode("utf-8")
    files.append(("view.json", vbytes, "application/json"))
    created = (clock or (lambda: _dt.datetime.now(_dt.timezone.utc)))()
    artifact = ViewArtifact(
        artifact_id=artifact_id, view_hash=digest, view_schema_version=view.schema_version,
        view_file=_file("view.json", vbytes, "application/json"), figures=figures,
        datasets=view_records, kpis=kpis, variants=variants,
        library_versions=library_versions(), created_at=created.isoformat(),
        title=view.title, view_id=view.id, view_metadata=dict(view.metadata))
    files.append(("manifest.json", (artifact.to_json() + "\n").encode("utf-8"),
                  "application/json"))
    write_all = getattr(writer, "write_artifact", None)
    if callable(write_all):
        write_all(artifact_id, files)
    else:
        for fname, content, media in files:
            writer.write(artifact_id, fname, content, media)
    return artifact
