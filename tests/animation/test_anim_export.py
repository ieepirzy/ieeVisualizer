"""export_animation: files, poster, manifest; the examples script."""

import datetime as dt
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from anim_helpers import SMALL, gif_frames, png_pixels, sine_anim, vector_field_anim, weekly_anim

from ieeVisualizer.animation import animation_hash, export_animation, from_json
from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
from ieeVisualizer.artifacts import LocalDirectoryWriter, MemoryWriter
from ieeVisualizer.spec import spec_hash

CLOCK = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


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


def test_examples_script_writes_every_animation(tmp_path):
    spec = importlib.util.spec_from_file_location("animations", EXAMPLES / "animations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REQUEST = SMALL
    arts = module.main(tmp_path)
    assert set(arts) == {"sine_phase", "damped_oscillator", "weekly_bookings"}
    for name, art in arts.items():
        gif = (tmp_path / f"{name}.animation.gif").read_bytes()
        assert gif[:6] == b"GIF89a"
        assert (tmp_path / f"{name}.poster.png").read_bytes()[:4] == b"\x89PNG"
        m = json.loads((tmp_path / f"{name}.manifest.json").read_text())
        assert m["frame_count"] == art.frame_count > 1
    wb = json.loads((tmp_path / "weekly_bookings.manifest.json").read_text())
    assert wb["datasets"]["bookings"]["provenance"]["source"] == "synthetic"
    assert "SYNTHETIC" in wb["datasets"]["bookings"]["provenance"]["notes"]
    assert wb["timeline"]["type"] == "frame_column" and wb["state_count"] == 12
