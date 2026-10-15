"""Store a rendered animation with a poster frame and a provenance manifest.

``export_animation(anim, ["gif"], writer)`` writes, under a content-derived
artifact id:

    animation.gif / animation.mp4   one per requested format
    poster.png                      the poster frame as a still
    animation.json                  canonical AnimationSpec (hash = animation_hash)
    manifest.json                   AnimationArtifact (written last)

The poster is frame ``poster_frame`` of the first requested format's clip,
recorded with the adapter that drew it: for the Matplotlib adapter, the
static render of the poster state's FigureSpec (with the animation's fixed
axis limits), which equals that frame pixel for pixel; for an adapter with
its own look (Manim), the frame decoded from its clip. Every output is
rendered before anything is written; a failure leaves no partial artifact.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import io
from dataclasses import dataclass, field
from typing import Any, Callable

from ..artifacts.export import _file, _records, artifact_id_for, library_versions
from ..artifacts.manifest import DatasetRecord, FileEntry, RenderVariant
from ..artifacts.writers import ArtifactWriter
from ..render.base import issue as _issue
from ..spec import spec_hash
from ..spec._common import canonical_json
from .base import (
    ANIMATION_MEDIA_TYPES,
    AnimationError,
    AnimationRegistry,
    AnimationRequest,
    render_animation,
)
from .spec import AnimationSpec, animation_hash, timeline_to_dict, to_json

MANIFEST_NAME = "manifest.json"
SPEC_NAME = "animation.json"
POSTER_NAME = "poster.png"
ANIMATION_MANIFEST_VERSION = "1.0"


@dataclass
class AnimationArtifact:
    artifact_id: str
    animation_hash: str
    spec_file: FileEntry
    timeline: dict[str, Any]
    fps: int
    frame_count: int
    state_count: int
    frame_duration_ms: float
    poster_frame: int
    poster: RenderVariant
    variants: list[RenderVariant]
    states: list[dict[str, Any]]
    frames: list[int]
    datasets: dict[str, DatasetRecord]
    library_versions: dict[str, str]
    created_at: str
    figure_spec_hash: str | None = None
    title: str | None = None
    spec_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ANIMATION_MANIFEST_VERSION

    @property
    def duration_s(self) -> float:
        return self.frame_count * self.frame_duration_ms / 1000.0

    def variant(self, fmt: str) -> RenderVariant:
        for v in self.variants:
            if v.format == fmt:
                return v
        raise KeyError(f"no {fmt!r} variant; have {[v.format for v in self.variants]}.")

    def to_dict(self) -> dict[str, Any]:
        d = {"schema_version": self.schema_version, "kind": "animation",
             "artifact_id": self.artifact_id, "animation_hash": self.animation_hash,
             "spec_file": self.spec_file.to_dict(), "timeline": dict(self.timeline),
             "fps": self.fps, "frame_count": self.frame_count, "state_count": self.state_count,
             "frame_duration_ms": self.frame_duration_ms, "duration_s": self.duration_s,
             "poster_frame": self.poster_frame, "poster": self.poster.to_dict(),
             "variants": [v.to_dict() for v in self.variants], "states": list(self.states),
             "frames": list(self.frames),
             "datasets": {k: v.to_dict() for k, v in self.datasets.items()},
             "library_versions": dict(self.library_versions), "created_at": self.created_at,
             "metadata": dict(self.metadata)}
        for k in ("figure_spec_hash", "title", "spec_id"):
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return canonical_json(self.to_dict(), indent=indent)


def export_animation(
    anim: AnimationSpec,
    formats=("gif",),
    writer: ArtifactWriter | None = None,
    *,
    resolver=None,
    clock: Callable[[], _dt.datetime] | None = None,
    request: AnimationRequest | None = None,
    name: str | None = None,
    registry: AnimationRegistry | None = None,
) -> AnimationArtifact:
    from ..render.prepare import prepare
    from .frames import plan
    from .mpl import MatplotlibAnimationAdapter

    if writer is None:
        raise ValueError("export_animation() needs a writer, e.g. LocalDirectoryWriter('out/').")
    fmts = list(dict.fromkeys([formats] if isinstance(formats, str) else formats))
    if not fmts or any(f not in ANIMATION_MEDIA_TYPES for f in fmts):
        raise ValueError(f"formats must be a non-empty subset of {list(ANIMATION_MEDIA_TYPES)}.")
    base = request or AnimationRequest()

    outputs, results = [], []
    for fmt in fmts:
        req = dataclasses.replace(base, format=fmt)
        res = render_animation(anim, req, resolver=resolver, registry=registry)
        results.append(res)
        fname = f"animation.{fmt}"
        outputs.append((fname, res.content, res.media_type, RenderVariant(
            format=fmt, file=_file(fname, res.content, res.media_type),
            adapter_name=res.adapter_name, adapter_version=res.adapter_version,
            request=req.to_dict(), warnings=[w.to_dict() for w in res.warnings])))
    first = results[0]

    mpl = MatplotlibAnimationAdapter()
    if first.poster_png is not None:  # the adapter's own frame, not a Matplotlib still
        fp = plan(anim, resolver)
    else:
        fp, _ = mpl.plan(anim, base, resolver=resolver)
    if fp.poster != first.poster_frame or fp.frames != first.frames:
        raise AnimationError([_issue("", "poster_mismatch",
                                     f"the {first.adapter_name} result maps frames differently "
                                     "from the frame plan; refusing to record a poster for the "
                                     "wrong frame.")])
    state = fp.state_of(fp.poster)
    if first.poster_png is not None:
        poster_bytes = first.poster_png
        poster_warn = [{"code": "poster_from_clip", "path": "/poster",
                        "message": f"frame {fp.poster} decoded from the {first.adapter_name} "
                                   f"{first.format} clip ({state.label})"}]
        poster_adapter = (first.adapter_name, first.adapter_version)
    elif state.spec is None:
        from PIL import Image

        from .mpl import _no_data_frame

        buf = io.BytesIO()
        Image.fromarray(_no_data_frame(state.label, base)).save(buf, format="PNG")
        poster_bytes, poster_warn = buf.getvalue(), [{"code": "frame_no_data", "path": "",
                                                       "message": "poster state has no data"}]
        poster_adapter = (mpl.name, mpl.version)
    else:
        pres = mpl._static.render(state.spec, base.frame_request())
        poster_bytes, poster_warn = pres.content, [w.to_dict() for w in pres.warnings]
        poster_adapter = (pres.adapter_name, pres.adapter_version)
    poster = RenderVariant(format="png", file=_file(POSTER_NAME, poster_bytes, "image/png"),
                           adapter_name=poster_adapter[0], adapter_version=poster_adapter[1],
                           request=base.frame_request().to_dict(), warnings=poster_warn)

    if anim.figure is not None:
        prepared = prepare(anim.figure, resolver)
        records = _records(anim.figure, prepared.resolved)
        fig_hash = spec_hash(anim.figure)
    else:  # template sweep: the data is generated per state; record the poster's
        records = _records(state.spec, {}) if state.spec is not None else {}
        fig_hash = None
    digest = animation_hash(anim)
    artifact_id = artifact_id_for(digest, {k: r.content_hash for k, r in records.items()},
                                  name if name is not None else "anim", formats=fmts,
                                  request=base.to_dict())
    versions = library_versions()
    import PIL

    versions["pillow"] = PIL.__version__
    if "mp4" in fmts and mpl.ffmpeg and any(r.adapter_name == mpl.name for r in results):
        from .encode import ffmpeg_version

        versions["ffmpeg"] = ffmpeg_version(mpl.ffmpeg)
    if any(r.adapter_name == "manim" for r in results):
        import manim

        versions["manim"] = manim.__version__

    spec_bytes = to_json(anim).encode("utf-8")
    created = (clock or (lambda: _dt.datetime.now(_dt.timezone.utc)))()
    artifact = AnimationArtifact(
        artifact_id=artifact_id, animation_hash=digest,
        spec_file=_file(SPEC_NAME, spec_bytes, "application/json"),
        timeline=timeline_to_dict(anim.timeline), fps=first.fps,
        frame_count=first.frame_count, state_count=first.state_count,
        frame_duration_ms=first.frame_duration_ms, poster_frame=first.poster_frame,
        poster=poster,
        variants=[v for *_, v in outputs], states=first.states, frames=first.frames,
        datasets=records, library_versions=versions, created_at=created.isoformat(),
        figure_spec_hash=fig_hash, title=anim.title or (anim.figure.title if anim.figure
                                                         else None),
        spec_id=anim.id, metadata=dict(anim.metadata))
    blobs = [(f, c, mt) for f, c, mt, _ in outputs]
    blobs += [(POSTER_NAME, poster_bytes, "image/png"),
              (SPEC_NAME, spec_bytes, "application/json"),
              (MANIFEST_NAME, (artifact.to_json() + "\n").encode("utf-8"), "application/json")]
    write_all = getattr(writer, "write_artifact", None)
    if callable(write_all):
        write_all(artifact_id, blobs)
    else:
        for fname, content, media in blobs:
            writer.write(artifact_id, fname, content, media)
    return artifact
