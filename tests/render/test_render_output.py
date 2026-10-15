"""Bytes out: determinism, decodable PNG of the requested size, no global state."""

import io
import os
import subprocess
import sys

import matplotlib
import matplotlib.image as mimage
import matplotlib.pyplot as plt
import pytest
from render_examples import heatmap_spec, quiver_spec, revenue_spec, surface_spec

from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.spec import to_json

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))

_SUBPROCESS = """
import sys
sys.path.insert(0, {here!r})
from render_examples import revenue_spec
from ieeVisualizer.render import render, RenderRequest
sys.stdout.buffer.write(render(revenue_spec(), RenderRequest(format={fmt!r})).content)
"""


def _fresh_process_render(fmt: str) -> bytes:
    env = dict(os.environ, PYTHONPATH=ROOT, MPLBACKEND="Agg")
    out = subprocess.run([sys.executable, "-c", _SUBPROCESS.format(here=HERE, fmt=fmt)],
                         capture_output=True, env=env, check=False)
    assert out.returncode == 0, out.stderr.decode()
    return out.stdout


@pytest.mark.parametrize("make", [revenue_spec, quiver_spec, heatmap_spec, surface_spec])
def test_svg_is_byte_identical_across_renders(make):
    a = render(make(), RenderRequest(format="svg")).content
    b = render(make(), RenderRequest(format="svg")).content
    assert a == b
    assert a.startswith(b"<?xml")


def test_svg_and_pdf_identical_in_a_fresh_process():
    for fmt in ("svg", "pdf"):
        here = render(revenue_spec(), RenderRequest(format=fmt)).content
        assert _fresh_process_render(fmt) == here, fmt


def test_outputs_carry_no_timestamps():
    svg = render(revenue_spec(), RenderRequest(format="svg")).content
    assert b"<dc:date>" not in svg
    pdf = render(revenue_spec(), RenderRequest(format="pdf")).content
    assert b"/CreationDate" not in pdf and b"/ModDate" not in pdf
    png = render(revenue_spec(), RenderRequest(format="png")).content
    assert b"ieeVisualizer" in png  # fixed Software tag


@pytest.mark.parametrize("request_", [
    RenderRequest(format="png", width=6.4, height=4.8, dpi=100),
    RenderRequest(format="png", width=1000, height=618, units="px", dpi=96),
    RenderRequest(format="png", width=333, height=211, units="px", dpi=72),
])
def test_png_decodes_with_requested_pixel_size(request_):
    result = render(revenue_spec(), request_)
    assert result.media_type == "image/png" and result.content[:8] == b"\x89PNG\r\n\x1a\n"
    img = mimage.imread(io.BytesIO(result.content), format="png")
    w, h = request_.size_px
    assert img.shape[:2] == (h, w)


def test_result_metadata():
    r = render(revenue_spec(), RenderRequest(format="pdf"))
    assert r.format == "pdf" and r.media_type == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert r.adapter_name == "matplotlib" and r.adapter_version
    assert r.sha256.startswith("sha256:") and r.size_bytes == len(r.content)


def test_rendering_leaves_global_matplotlib_state_alone():
    before = dict(matplotlib.rcParams)
    figs = plt.get_fignums()
    backend = matplotlib.get_backend()
    with matplotlib.rc_context({"lines.linewidth": 7.0, "svg.hashsalt": None}):
        a = render(revenue_spec(), RenderRequest(format="svg")).content
    b = render(revenue_spec(), RenderRequest(format="svg")).content
    assert a == b  # user rc settings do not leak into the output
    assert plt.get_fignums() == figs
    assert matplotlib.get_backend() == backend
    after = dict(matplotlib.rcParams)
    assert {k for k in before if before[k] != after[k]} == set()


def test_render_does_not_mutate_the_spec():
    spec = revenue_spec()
    before = to_json(spec)
    render(spec, RenderRequest(format="svg"))
    assert to_json(spec) == before


def test_importing_render_does_not_import_matplotlib():
    env = dict(os.environ, PYTHONPATH=ROOT)
    code = ("import sys, ieeVisualizer.render, ieeVisualizer.artifacts; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('matplotlib', 'sympy')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
