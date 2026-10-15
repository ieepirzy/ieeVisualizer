"""Shared builders for the animation tests."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np

from ieeVisualizer.animation import (
    AnimationRequest,
    AnimationSpec,
    FrameColumn,
    ParameterSweep,
    TemplateTarget,
)
from ieeVisualizer.spec import Axis, DataSnapshot, FigureSpec, FunctionMark, LineMark, Provenance

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

SMALL = AnimationRequest(width=160, height=120, units="px", dpi=50)
TWO_PI = 6.283185307179586


def sine_figure(phase: float = 0.0, title: str | None = "Sine") -> FigureSpec:
    return FigureSpec(
        title=title,
        layers=[FunctionMark(expression="sin(x + phase)", variables=["x"],
                             domain={"x": [0.0, TWO_PI]}, samples=100,
                             parameters={"phase": phase})],
        axes={"x": Axis(label="x"), "y": Axis(label="y")},
    )


def sine_anim(**kw) -> AnimationSpec:
    tl = kw.pop("timeline", None) or ParameterSweep(parameter="phase", start=0.0, end=TWO_PI,
                                                    duration=0.5)
    return AnimationSpec(timeline=tl, figure=kw.pop("figure", sine_figure()),
                         fps=kw.pop("fps", 10), **kw)


def growth_anim(**kw) -> AnimationSpec:
    """A sweep whose y range grows: amplitude a from 1 to 3."""
    fig = FigureSpec(layers=[FunctionMark(expression="a*sin(x)", variables=["x"],
                                          domain={"x": [0.0, TWO_PI]}, samples=60,
                                          parameters={"a": 1.0})])
    return AnimationSpec(timeline=ParameterSweep(parameter="a", values=[1.0, 2.0, 3.0]),
                         figure=fig, fps=5, **kw)


def weekly_data(missing_week: int | None = 2) -> DataSnapshot:
    weeks = ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]
    values = [10, 12, 15, 11]
    if missing_week is not None:
        values[missing_week] = None
    return DataSnapshot.from_columns(
        {"week": weeks, "bookings": values}, dtypes={"week": "datetime", "bookings": "int"},
        provenance=Provenance(source="synthetic", notes="test data"))


def weekly_anim(display: str = "cumulative", window=None, data=None, **kw) -> AnimationSpec:
    fig = FigureSpec(title="Bookings", datasets={"b": data or weekly_data()},
                     layers=[LineMark(data="b", encoding={"x": "week", "y": "bookings"})])
    return AnimationSpec(timeline=FrameColumn(dataset="b", column="week", display=display,
                                              window=window),
                         figure=fig, fps=4, **kw)


def vector_field_anim(**kw) -> AnimationSpec:
    params = {"u": "-y + k*x", "v": "x + k*y", "constants": {"k": 0.0}, "grid": 5,
              "scale": 1.0}
    params.update(kw.pop("params", {}))
    return AnimationSpec(
        timeline=ParameterSweep(parameter="constants.k", values=kw.pop("values", [0.0, 0.5, 1.0]),
                                target=TemplateTarget(name="vector_field_2d", version="1.0.0",
                                                      params=params)),
        fps=kw.pop("fps", 4), **kw)


def png_pixels(content: bytes) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(content)).convert("RGB"))


def gif_frames(content: bytes) -> tuple[list[np.ndarray], list[int]]:
    """Decoded frames and their stored delays (ms). Pillow merges identical
    consecutive frames when writing, so ``len`` can be below the frame count."""
    from PIL import Image, ImageSequence

    im = Image.open(io.BytesIO(content))
    frames, delays = [], []
    for f in ImageSequence.Iterator(im):
        delays.append(f.info["duration"])
        frames.append(np.asarray(f.convert("RGB")))
    return frames, delays


def codes(issues) -> list[str]:
    return [i.code for i in issues]


def run_limited(body: str, *, mem_mb: int = 1500, timeout: float = 60.0) -> dict:
    """Run ``body`` in a child Python with RLIMIT_AS = ``mem_mb`` and a
    timeout; it must print one JSON line last. A MemoryError, a crash or a
    timeout fails the calling test with the child's output."""
    code = textwrap.dedent(f"""
        import json, sys, time
        sys.path[:0] = [{str(ROOT)!r}, {str(HERE)!r}]
        t0 = time.perf_counter()
    """) + textwrap.dedent(body)

    import resource

    import pytest

    def limit():
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))

    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MPLBACKEND="Agg")
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=timeout, preexec_fn=limit, env=env)
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"child did not finish in {timeout} s (guard not applied before "
                    f"allocation?): {e.stdout!r} {e.stderr!r}")
    assert proc.returncode == 0, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def minmax_expression() -> str:
    """A 474-character u/v expression whose SymPy build is slow (about 0.3 s)."""
    u = "+".join(f"Max(Min(k*x,{i}*y),floor(k*y+{i}))" for i in range(20))
    while len(u) > 500:
        u = u[:u.rfind("+")]
    return u
