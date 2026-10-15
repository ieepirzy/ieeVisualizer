"""examples/gallery.py renders every template example and mark type, and
every manifest it writes describes the files actually on disk."""

import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

from ieeVisualizer import templates
from ieeVisualizer.spec import MARK_TYPES, from_json, spec_hash

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _load_gallery():
    spec = importlib.util.spec_from_file_location("gallery", EXAMPLES / "gallery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_gallery_covers_every_mark_type_and_template():
    gallery = _load_gallery()
    specs = gallery.all_specs()
    drawn = {layer.type for s in specs.values() for layer in s.layers}
    assert drawn == set(MARK_TYPES)
    assert {f"mark-{t}" for t in MARK_TYPES} <= set(specs)
    assert {f"template-{t['name']}" for t in templates.list_templates()} <= set(specs)


def test_gallery_manifests_match_the_files_written(tmp_path):
    gallery = _load_gallery()
    clock = lambda: dt.datetime(2026, 10, 14, 6, 0, tzinfo=dt.timezone.utc)  # noqa: E731
    artifacts = gallery.main(tmp_path, clock=clock)
    manifests = sorted(tmp_path.glob("*.manifest.json"))
    assert len(manifests) == len(artifacts) == len(gallery.all_specs())
    for name, artifact in artifacts.items():
        manifest = json.loads(gallery.output_path(tmp_path, name, "manifest.json").read_text())
        assert manifest["artifact_id"] == artifact.artifact_id
        assert manifest["created_at"] == "2026-10-14T06:00:00+00:00"
        assert sorted(v["format"] for v in manifest["variants"]) == ["png", "svg"]
        for v in manifest["variants"]:
            path = gallery.output_path(tmp_path, name, v["file"]["name"])
            assert path.name == f"{name}.{v['format']}"
            assert _sha(path) == v["file"]["sha256"], path
            assert path.stat().st_size == v["file"]["size_bytes"]
        spec_path = gallery.output_path(tmp_path, name, manifest["spec_file"]["name"])
        assert _sha(spec_path) == manifest["spec_file"]["sha256"]
        assert spec_hash(from_json(spec_path.read_text())) == manifest["spec_hash"]
        png = gallery.output_path(tmp_path, name, "figure.png").read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
