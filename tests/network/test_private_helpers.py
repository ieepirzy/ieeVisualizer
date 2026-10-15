"""The network package imports a few private helpers from other modules
(``render.mpl.adapter``, ``artifacts.export``, ``spec.patch``). Pin their
names and signatures so a refactor there fails here, loudly, rather than in
the middle of a render or a patch."""

import importlib
import inspect

import pytest

# by module path: ``ieeVisualizer.artifacts.export`` the attribute is a function
artifacts_export = importlib.import_module("ieeVisualizer.artifacts.export")
adapter = importlib.import_module("ieeVisualizer.render.mpl.adapter")
patch = importlib.import_module("ieeVisualizer.spec.patch")


@pytest.mark.parametrize("module, name, params", [
    (adapter, "_figsize", ["request"]),
    (adapter, "_metadata", ["fmt"]),
    (artifacts_export, "_file", ["name", "content", "media_type"]),
    (patch, "_apply_one", ["doc", "op", "where"]),
    (patch, "_err", ["where", "code", "message"]),
])
def test_private_helpers_used_by_the_network_package_keep_their_signature(module, name,
                                                                          params):
    fn = getattr(module, name, None)
    assert callable(fn), f"{module.__name__}.{name} is gone; ieeVisualizer.network uses it"
    got = list(inspect.signature(fn).parameters)
    assert got == params, f"{module.__name__}.{name}{got}: update ieeVisualizer.network"


def test_the_helpers_behave_as_the_network_package_expects():
    from ieeVisualizer.artifacts.manifest import FileEntry
    from ieeVisualizer.render import RenderRequest
    from ieeVisualizer.spec import PatchError

    w, h = adapter._figsize(RenderRequest(format="svg", width=4, height=3))
    assert (w, h) == (4, 3)
    assert isinstance(adapter._metadata("svg"), dict)
    entry = artifacts_export._file("figure.png", b"abc", "image/png")
    assert isinstance(entry, FileEntry) and entry.size_bytes == 3
    assert patch._apply_one({"a": 1}, {"op": "replace", "path": "/a", "value": 2},
                            "/ops/0") == {"a": 2}
    assert isinstance(patch._err("/ops", "invalid_op", "x"), PatchError)
