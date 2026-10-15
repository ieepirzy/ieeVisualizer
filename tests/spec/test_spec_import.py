import importlib.util
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(code: str) -> str:
    env = dict(os.environ, PYTHONPATH=ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         check=False)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_importing_spec_does_not_import_matplotlib():
    # Fresh interpreter: other tests in this process may already have loaded it.
    out = _run(
        "import sys, ieeVisualizer.spec as s; s.validate(s.FigureSpec()); "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('matplotlib', 'sympy')))"
    )
    assert out == "[]"


@pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None or importlib.util.find_spec("sympy") is None,
    reason="legacy core needs matplotlib and sympy",
)
def test_legacy_visualizer_import_still_works():
    assert _run("from ieeVisualizer.core import Visualizer; print(Visualizer.__name__)") == (
        "Visualizer"
    )
    assert _run(
        "import ieeVisualizer, ieeVisualizer.core as c; "
        "print(ieeVisualizer.Visualizer is c.Visualizer)"
    ) == "True"
