import importlib.util
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("name", ["function_plot", "vector_field"])
def test_example_writes_pngs(name, tmp_path):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUT = tmp_path
    module.main()
    pngs = sorted(tmp_path.glob("*.png"))
    assert len(pngs) == 2
    assert all(p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs)
