"""export() + VisualizationArtifact manifest."""

import datetime as dt
import hashlib
import json
import os

import pytest
from render_examples import latency_snapshot, ref_spec, revenue_spec

from ieeVisualizer.artifacts import (
    MANIFEST_NAME,
    LocalDirectoryWriter,
    MemoryWriter,
    VisualizationArtifact,
    export,
)
from ieeVisualizer.render import CapabilityError, UnresolvedDatasetError
from ieeVisualizer.spec import SpecValidationError, spec_hash


def _clock(day=14):
    return lambda: dt.datetime(2026, 10, day, 6, 0, tzinfo=dt.timezone.utc)


def _sha(path):
    with open(path, "rb") as f:
        data = f.read()
    return "sha256:" + hashlib.sha256(data).hexdigest(), len(data)


def test_manifest_hashes_match_written_files(tmp_path):
    spec = revenue_spec()
    art = export(spec, ["png", "svg", "pdf"], LocalDirectoryWriter(tmp_path), clock=_clock())
    root = tmp_path / art.artifact_id
    assert sorted(os.listdir(root)) == ["figure.pdf", "figure.png", "figure.svg", MANIFEST_NAME,
                                        "spec.json", "theme.json"]
    for v in art.variants:
        assert _sha(root / v.file.name) == (v.sha256, v.size_bytes)
        assert v.adapter_name == "matplotlib"
    assert [v.media_type for v in art.variants] == ["image/png", "image/svg+xml",
                                                    "application/pdf"]
    # spec.json is the canonical spec; its hash is the spec hash
    assert _sha(root / "spec.json")[0] == art.spec_hash == spec_hash(spec)
    on_disk = json.loads((root / MANIFEST_NAME).read_text())
    assert on_disk == art.to_dict()
    assert VisualizationArtifact.from_json((root / MANIFEST_NAME).read_text()).to_dict() == \
        art.to_dict()


def test_manifest_records_datasets_versions_and_warnings(tmp_path):
    spec = revenue_spec()
    art = export(spec, ["svg"], LocalDirectoryWriter(tmp_path), clock=_clock())
    obs = art.datasets["observed"]
    assert obs.content_hash == spec.datasets["observed"].content_hash()
    assert obs.provenance == {"source": "finance-service", "query": "weekly_revenue",
                              "retrieved_at": "2026-10-14T06:00:00+03:00",
                              "timezone": "Europe/Helsinki", "run_id": "report-2026-W42"}
    assert obs.columns["revenue"] == {"dtype": "float", "unit": "EUR",
                                      "aggregation": "sum per ISO week"}
    assert set(art.library_versions) >= {"ieeVisualizer", "matplotlib", "numpy"}
    assert art.spec_metadata == {"template": "timeseries", "template_version": "1"}
    assert art.spec_id == "fig-revenue" and art.title == "Weekly revenue"
    assert art.created_at == "2026-10-14T06:00:00+00:00"
    (v,) = art.variants
    assert {"code": "null_values_omitted", "path": "/layers/0"}.items() <= v.warnings[0].items()
    assert v.request["format"] == "svg"


def test_manifest_is_stable_apart_from_created_at(tmp_path):
    a = export(revenue_spec(), ["png", "svg"], LocalDirectoryWriter(tmp_path / "a"),
               clock=_clock(14))
    b = export(revenue_spec(), ["png", "svg"], LocalDirectoryWriter(tmp_path / "b"),
               clock=_clock(15))
    da, db = a.to_dict(), b.to_dict()
    assert da.pop("created_at") != db.pop("created_at")
    assert da == db
    # same clock -> byte-identical manifest, independent of where it was stored
    c = export(revenue_spec(), ["png", "svg"], MemoryWriter(), clock=_clock(14))
    assert c.to_json() == a.to_json()
    assert (tmp_path / "a" / a.artifact_id / MANIFEST_NAME).read_text() == a.to_json() + "\n"


def test_artifact_id_is_content_derived_not_location_derived(tmp_path):
    a = export(revenue_spec(), ["svg"], LocalDirectoryWriter(tmp_path / "one"), clock=_clock())
    b = export(revenue_spec(), ["svg"], LocalDirectoryWriter(tmp_path / "two"), clock=_clock())
    assert a.artifact_id == b.artifact_id and a.artifact_id.startswith("viz-")
    assert str(tmp_path) not in a.to_json()
    changed = revenue_spec()
    changed.datasets["observed"].columns["revenue"].values[0] = 1201.0
    c = export(changed, ["svg"], MemoryWriter(), clock=_clock())
    assert c.artifact_id != a.artifact_id
    named = export(revenue_spec(), ["svg"], MemoryWriter(), clock=_clock(), name="report-W42")
    assert named.artifact_id == "report-W42-" + a.artifact_id.split("-", 1)[1]
    with pytest.raises(ValueError):
        export(revenue_spec(), ["svg"], MemoryWriter(), name="../escape")


def test_resolved_reference_is_frozen_into_the_artifact():
    w = MemoryWriter()
    art = export(ref_spec(), ["svg"], w, resolver=lambda r: latency_snapshot(), clock=_clock())
    rec = art.datasets["lat"]
    assert rec.type == "ref" and rec.resolved
    assert rec.ref == {"kind": "query", "id": "bookings.latency", "version": "3"}
    assert rec.content_hash == latency_snapshot(unit="s").content_hash()
    assert rec.provenance == {"source": "bookings-db", "query": "latency_v3"}
    files = {name for (_, name) in w.files}
    assert files == {"figure.svg", "spec.json", "spec.resolved.json", "theme.json", MANIFEST_NAME}
    resolved = json.loads(w.files[(art.artifact_id, "spec.resolved.json")])
    assert resolved["datasets"]["lat"]["type"] == "snapshot"
    assert art.resolved_spec_file.sha256 == "sha256:" + hashlib.sha256(
        w.files[(art.artifact_id, "spec.resolved.json")]).hexdigest()
    # different data behind the same reference -> a different artifact
    other = export(ref_spec(), ["svg"], MemoryWriter(),
                   resolver=lambda r: latency_snapshot(values=(9.0, 8.0)), clock=_clock())
    assert other.artifact_id != art.artifact_id
    assert other.spec_hash == art.spec_hash


@pytest.mark.parametrize("kwargs,exc", [
    ({"formats": ["svg", "html"]}, CapabilityError),
    ({"formats": ["svg"], "spec": "invalid"}, SpecValidationError),
    ({"formats": ["svg"], "spec": "ref"}, UnresolvedDatasetError),
])
def test_failed_export_writes_nothing(tmp_path, kwargs, exc):
    from ieeVisualizer.spec import FigureSpec

    spec = {"invalid": FigureSpec(), "ref": ref_spec()}.get(kwargs.pop("spec", None),
                                                            revenue_spec())
    with pytest.raises(exc):
        export(spec, kwargs["formats"], LocalDirectoryWriter(tmp_path), clock=_clock())
    assert os.listdir(tmp_path) == []


def test_export_argument_checks():
    with pytest.raises(ValueError):
        export(revenue_spec(), ["svg"], None)
    with pytest.raises(ValueError):
        export(revenue_spec(), [], MemoryWriter())
    with pytest.raises(ValueError):
        export(revenue_spec(), ["tiff"], MemoryWriter())


def test_artifact_id_covers_formats_and_render_request():
    from ieeVisualizer.render import RenderRequest

    def aid(formats, **req):
        return export(revenue_spec(), formats, MemoryWriter(), clock=_clock(),
                      request=RenderRequest(**req)).artifact_id

    base = aid(["png", "pdf"], dpi=100)
    assert aid(["png", "pdf"], dpi=100) == base
    others = [aid(["png"], dpi=100), aid(["pdf", "png"], dpi=100), aid(["png", "pdf"], dpi=300),
              aid(["png", "pdf"], dpi=100, width=8.0), aid(["png", "pdf"], dpi=100, theme="dark")]
    assert len({base, *others}) == 1 + len(others)
    # the per-variant format of the request does not matter, only the list
    assert aid(["png", "pdf"], dpi=100, format="svg") == base


def test_reexport_under_same_id_leaves_only_manifest_files(tmp_path):
    w = LocalDirectoryWriter(tmp_path)
    a = export(revenue_spec(), ["png", "pdf"], w, clock=_clock())
    root = tmp_path / a.artifact_id
    (root / "figure.svg").write_bytes(b"stale")  # e.g. left by an older export
    b = export(revenue_spec(), ["png", "pdf"], w, clock=_clock(15))
    assert b.artifact_id == a.artifact_id
    listed = {v.file.name for v in b.variants} | {"spec.json", "theme.json", MANIFEST_NAME}
    assert set(os.listdir(root)) == listed
    assert json.loads((root / MANIFEST_NAME).read_text()) == b.to_dict()
    assert sorted(os.listdir(tmp_path)) == [a.artifact_id]  # no staging dirs left behind


def test_memory_writer_reexport_replaces_artifact_files():
    w = MemoryWriter()
    a = export(revenue_spec(), ["svg"], w, clock=_clock())
    w.write(a.artifact_id, "stale.txt", b"x", "text/plain")
    export(revenue_spec(), ["svg"], w, clock=_clock())
    assert {n for (_, n) in w.files} == {"figure.svg", "spec.json", "theme.json", MANIFEST_NAME}


@pytest.mark.parametrize("bad", ["viz-abc\n", "figure.png\n", "a\nb"])
def test_names_with_newlines_are_refused(bad):
    from ieeVisualizer.artifacts.writers import check_name

    with pytest.raises(ValueError):
        check_name(bad, "artifact id")
    with pytest.raises(ValueError):
        MemoryWriter().write("viz-1", bad, b"", "text/plain")
