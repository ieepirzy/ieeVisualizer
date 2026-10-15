"""export_animation: files, poster, manifest; the examples script."""

import datetime as dt
import json
import shutil

import numpy as np
import pytest
from anim_helpers import SMALL, gif_frames, png_pixels, sine_anim, vector_field_anim, weekly_anim

from ieeVisualizer.animation import animation_hash, export_animation, from_json
from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
from ieeVisualizer.artifacts import LocalDirectoryWriter, MemoryWriter
from ieeVisualizer.spec import spec_hash

CLOCK = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)


def _files(writer, artifact_id):
    return {name: content for (aid, name), content in writer.files.items()
            if aid == artifact_id}


def test_export_writes_animation_poster_spec_and_manifest(tmp_path):
    anim = sine_anim(hold_end=2, poster=3)
    art = export_animation(anim, ["gif"], LocalDirectoryWriter(tmp_path), request=SMALL,
                           clock=lambda: CLOCK)
    d = tmp_path / art.artifact_id
    assert sorted(p.name for p in d.iterdir()) == ["animation.gif", "animation.json",
                                                   "manifest.json", "poster.png"]
    m = json.loads((d / "manifest.json").read_text())
    assert m["kind"] == "animation" and m["frame_count"] == 7 and m["state_count"] == 5
    assert m["timeline"] == {"type": "parameter_sweep", "parameter": "phase",
                             "target": {"kind": "mark", "layer": 0}, "easing": "linear",
                             "start": 0.0, "end": 6.283185307179586, "duration": 0.5}
    assert m["frames"] == [0, 1, 2, 3, 4, 4, 4] and m["poster_frame"] == 3
    assert [s["label"] for s in m["states"]][1] == "phase = 1.571"
    assert m["animation_hash"] == animation_hash(anim)
    assert m["figure_spec_hash"] == spec_hash(anim.figure)
    assert m["created_at"] == CLOCK.isoformat()
    assert "pillow" in m["library_versions"]
    assert from_json((d / "animation.json").read_text()) == anim
    gif = (d / "animation.gif").read_bytes()
    assert m["variants"][0]["file"]["sha256"].endswith(__import__("hashlib").sha256(
        gif).hexdigest())


def test_poster_equals_the_poster_frame():
    anim = sine_anim(poster=2)
    w = MemoryWriter()
    art = export_animation(anim, "gif", w, request=SMALL, clock=lambda: CLOCK)
    files = _files(w, art.artifact_id)
    poster = png_pixels(files["poster.png"])
    _, _, pixels = MatplotlibAnimationAdapter().render_frames(anim, SMALL)
    assert np.array_equal(poster, pixels[2])
    frames, _ = gif_frames(files["animation.gif"])
    assert np.abs(frames[2].astype(int) - poster.astype(int)).mean() < 1.0


def test_artifact_id_is_content_derived_and_stable():
    a = export_animation(weekly_anim(), "gif", MemoryWriter(), request=SMALL,
                         clock=lambda: CLOCK)
    b = export_animation(weekly_anim(), "gif", MemoryWriter(), request=SMALL,
                         clock=lambda: dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc))
    c = export_animation(weekly_anim("current"), "gif", MemoryWriter(), request=SMALL,
                         clock=lambda: CLOCK)
    assert a.artifact_id == b.artifact_id != c.artifact_id
    assert a.artifact_id.startswith("anim-")
    assert a.datasets["b"].provenance["source"] == "synthetic"


def test_template_sweep_records_the_poster_states_data():
    art = export_animation(vector_field_anim(), "gif", MemoryWriter(), request=SMALL,
                           clock=lambda: CLOCK)
    assert art.figure_spec_hash is None
    assert "field" in art.datasets and art.state_count == 3


def test_failed_export_writes_nothing():
    w = MemoryWriter()
    with pytest.raises(Exception):  # noqa: B017 - any refusal; nothing may be written
        export_animation(sine_anim(poster=99), "gif", w, request=SMALL)
    assert w.files == {}


# ------------------------------------------------- one resolution per export


def _drifting_resolver():
    """A source that returns different data on every call (a live table)."""
    from ieeVisualizer.spec import DataSnapshot

    calls = []

    def resolve(ref):
        k = len(calls)
        snap = DataSnapshot.from_columns({"week": [0, 1, 2],
                                          "bookings": [k, 5 + 3 * k, 2 * k]})
        calls.append((ref, snap))
        return snap

    return resolve, calls


def _ref_anim(names=("b",)):
    from ieeVisualizer.animation import AnimationSpec, FrameColumn
    from ieeVisualizer.spec import DatasetRef, FigureSpec, LineMark

    fig = FigureSpec(datasets={n: DatasetRef(kind="table", id=f"t_{n}") for n in names},
                     layers=[LineMark(data=n, encoding={"x": "week", "y": "bookings"})
                             for n in names])
    return AnimationSpec(timeline=FrameColumn(dataset=names[0], column="week",
                                              display="cumulative"), figure=fig, fps=4)


def test_export_resolves_each_reference_once_and_records_the_rendered_data():
    resolve, calls = _drifting_resolver()
    w = MemoryWriter()
    art = export_animation(_ref_anim(), "gif", w, request=SMALL, clock=lambda: CLOCK,
                           resolver=resolve)
    assert len(calls) == 1  # every format, the plan, limits, poster and record: one call
    rendered = calls[0][1]
    assert art.datasets["b"].content_hash == rendered.content_hash()
    files = _files(w, art.artifact_id)
    # The clip is exactly the clip of that snapshot, and so is the artifact id.
    frozen = MemoryWriter()
    ref = export_animation(_ref_anim(), "gif", frozen, request=SMALL, clock=lambda: CLOCK,
                           resolver=lambda r: rendered)
    assert ref.artifact_id == art.artifact_id
    assert _files(frozen, ref.artifact_id)["animation.gif"] == files["animation.gif"]
    # The poster is the rendered poster frame, not a re-render of newer data.
    frames, _ = gif_frames(files["animation.gif"])
    poster = png_pixels(files["poster.png"])
    assert art.poster_frame == len(frames) - 1
    assert np.abs(frames[-1].astype(int) - poster.astype(int)).mean() < 1.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_every_format_of_an_export_uses_the_same_resolution():
    resolve, calls = _drifting_resolver()
    art = export_animation(_ref_anim(("b", "c")), ["gif", "mp4"], MemoryWriter(),
                           request=SMALL, clock=lambda: CLOCK, resolver=resolve)
    assert len(calls) == 2  # two distinct references, each resolved once
    by_id = {r.id: s for r, s in calls}
    assert art.datasets["b"].content_hash == by_id["t_b"].content_hash()
    assert art.datasets["c"].content_hash == by_id["t_c"].content_hash()
