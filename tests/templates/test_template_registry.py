import json
import os
import subprocess
import sys

import pytest

from ieeVisualizer import templates
from ieeVisualizer.spec import FigureSpec, LineMark, SpecValidationError, validate
from ieeVisualizer.templates import (
    ComponentTemplate,
    Parameter,
    TemplateRegistry,
    TemplateRegistryError,
    parse_semver,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Dummy(ComponentTemplate):
    name = "dummy"
    data_required = False
    parameters = (Parameter("n", "integer", "a number", default=1),)

    def __init__(self, version):
        self.version = version

    def _build(self, data, params, issues):
        ds = {"d": {"x": [0.0, 1.0], "y": [0.0, float(params["n"])]}}
        from ieeVisualizer.spec import DataSnapshot

        return FigureSpec(datasets={k: DataSnapshot.from_columns(v) for k, v in ds.items()},
                          layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})])

    def example(self):
        return {"data": None, "params": {"n": 2}}


def test_builtins_are_listed():
    names = [t["name"] for t in templates.list_templates()]
    assert names == ["timeseries", "vector_field_2d"]
    entry = templates.list_templates()[0]
    assert set(entry) == {"name", "version", "versions", "title", "description"}
    json.dumps(templates.list_templates())


@pytest.mark.parametrize("name", ["timeseries", "vector_field_2d"])
def test_describe_is_json_and_its_example_builds(name):
    entry = templates.describe(name)
    text = json.dumps(entry)  # strictly JSON-serializable
    entry = json.loads(text)
    assert entry["name"] == name and entry["versions"] == ["1.0.0"]
    assert {p["name"] for p in entry["parameters"]} == {
        p.name for p in templates.get(name).parameters}
    for p in entry["parameters"]:
        assert {"name", "type", "json_type", "required", "default", "description"} <= set(p)
    assert entry["data"]["columns"]
    spec = templates.get(name).build_payload(entry["example"])  # the JSON copy builds
    assert validate(spec) == []
    spec2 = templates.build(name, entry["example"]["data"], entry["example"]["params"])
    assert spec2.to_json() == spec.to_json()  # deterministic


def test_catalog_covers_every_template():
    cat = templates.catalog()
    json.dumps(cat)
    assert [t["name"] for t in cat["templates"]] == ["timeseries", "vector_field_2d"]


def test_latest_version_by_semver_not_by_string_or_insertion():
    reg = TemplateRegistry()
    for v in ("1.2.0", "1.10.0", "1.9.3", "2.0.0-rc.1", "0.9.0"):
        reg.register(Dummy(v))
    assert reg.get("dummy").version == "1.10.0"  # releases preferred over a newer pre-release
    assert reg.get("dummy", "1.9.3").version == "1.9.3"
    assert reg.get("dummy", "2.0.0-rc.1").version == "2.0.0-rc.1"
    assert reg.versions("dummy") == ["0.9.0", "1.2.0", "1.9.3", "1.10.0", "2.0.0-rc.1"]
    reg.register(Dummy("2.0.0"))
    assert reg.get("dummy").version == "2.0.0"
    assert reg.list()[0]["version"] == "2.0.0"
    assert reg.describe("dummy", "1.2.0")["version"] == "1.2.0"


def test_prerelease_only_is_returned_when_nothing_else_exists():
    reg = TemplateRegistry()
    reg.register(Dummy("1.0.0-alpha"))
    reg.register(Dummy("1.0.0-beta"))
    assert reg.get("dummy").version == "1.0.0-beta"


def test_semver_precedence_matches_the_specification():
    ordered = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
               "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0",
               "2.0.0"]
    assert sorted(reversed(ordered), key=parse_semver) == ordered


def test_duplicate_registration_is_refused():
    reg = TemplateRegistry()
    reg.register(Dummy("1.0.0"))
    with pytest.raises(TemplateRegistryError) as exc:
        reg.register(Dummy("1.0.0"))
    assert exc.value.issues[0].code == "duplicate_template"
    with pytest.raises(TemplateRegistryError):  # build metadata does not make a new version
        reg.register(Dummy("1.0.0+rebuild"))
    with pytest.raises(TemplateRegistryError):
        templates.default_registry.register(templates.TimeseriesTemplate())


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0.0", "01.0.0", "", None])
def test_malformed_versions_are_refused(bad):
    with pytest.raises(TemplateRegistryError) as exc:
        TemplateRegistry().register(Dummy(bad))
    assert exc.value.issues[0].code == "invalid_version"


def test_unknown_template_and_version_are_structured():
    with pytest.raises(TemplateRegistryError) as exc:
        templates.get("timeseris")
    issue = exc.value.issues[0]
    assert issue.code == "unknown_template" and "Did you mean 'timeseries'?" in issue.message
    with pytest.raises(TemplateRegistryError) as exc:
        templates.get("timeseries", "9.0.0")
    assert exc.value.issues[0].code == "unknown_template_version"
    assert "1.0.0" in exc.value.issues[0].message


def test_build_records_template_identity_and_resolved_params():
    reg = TemplateRegistry()
    reg.register(Dummy("1.0.0"))
    spec = reg.build("dummy")
    assert spec.metadata["template"] == {"name": "dummy", "version": "1.0.0",
                                         "params": {"n": 1}}


def test_build_refuses_to_return_an_invalid_spec():
    class Broken(Dummy):
        def _build(self, data, params, issues):
            return FigureSpec()  # no layers: validate() reports no_layers

    with pytest.raises(SpecValidationError) as exc:
        Broken("1.0.0").build()
    assert [i.code for i in exc.value.issues] == ["no_layers"]


def test_payload_with_unknown_top_level_key_is_refused():
    with pytest.raises(templates.TemplateInputError) as exc:
        templates.get("vector_field_2d").build_payload({"params": {}, "dat": {}})
    assert exc.value.issues[0].path == "/dat"


def _fresh(code: str) -> str:
    env = dict(os.environ, PYTHONPATH=ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         check=False)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_importing_templates_is_light():
    # Fresh interpreter: this process has matplotlib loaded already (conftest).
    out = _fresh(
        "import sys, json, ieeVisualizer.templates as t; json.dumps(t.catalog()); "
        "t.get('timeseries').build_payload(t.get('timeseries').example()); "
        "print(sorted({m.split('.')[0] for m in sys.modules} & "
        "{'matplotlib', 'sympy', 'numpy'}))"
    )
    assert out == "[]"


def test_vector_field_build_does_not_import_matplotlib():
    out = _fresh(
        "import sys, ieeVisualizer.templates as t; "
        "t.get('vector_field_2d').build(u='-y', v='x', grid=3); "
        "print('matplotlib' in {m.split('.')[0] for m in sys.modules})"
    )
    assert out == "False"
