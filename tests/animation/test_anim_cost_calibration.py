"""Render-time estimate vs measured render time, one case per cost term:
measured / estimated must stay within [1 / OVER, UNDER] (a case over the band
is measured once more; one-time costs are paid first). Marked ``slow``:
excluded by default, run with ``python -m pytest -m slow``.
"""

import time

import pytest
from anim_helpers import vector_field_anim

from ieeVisualizer.animation import (
    AnimationRequest,
    AnimationSpec,
    FrameColumn,
    ParameterSweep,
    render_animation,
)
from ieeVisualizer.animation.cost import matplotlib_work_us
from ieeVisualizer.spec import (
    BarMark,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    HeatmapMark,
)
from ieeVisualizer.spec import figure as SF

pytestmark = pytest.mark.slow

#: measured / estimated must stay within [1 / OVER, UNDER].
UNDER = 1.5
OVER = 6.0

N = 3  # states per case


def _fn(**kw):
    return FunctionMark(expression="p*sin(x)", variables=["x"], domain={"x": [0, 6.0]},
                        samples=20, parameters={"p": 1.0}, **kw)


def _sweep(fig):
    return AnimationSpec(timeline=ParameterSweep(parameter="p",
                                                 values=[1.0 + i for i in range(N)]),
                         figure=fig, fps=10)


def _frames(columns, dtypes, layer):
    ds = DataSnapshot.from_columns(columns, dtypes=dtypes)
    return AnimationSpec(timeline=FrameColumn(dataset="d", column="t"),
                         figure=FigureSpec(datasets={"d": ds}, layers=[layer]), fps=10)


def plain():
    return _sweep(FigureSpec(layers=[_fn()]))


def bar_categories():
    rows = [(t, f"category-{i}", float(i + t + 1)) for t in range(N) for i in range(400)]
    return _frames({"t": [r[0] for r in rows], "x": [r[1] for r in rows],
                    "y": [r[2] for r in rows]}, {"t": "int", "x": "str", "y": "float"},
                   BarMark(data="d", encoding={"x": "x", "y": "y"}))


def heatmap(categorical, k):
    rows = [(t, f"c{i}" if categorical else i, f"r{j}" if categorical else j,
             float((i * j + t) % 11)) for t in range(N) for i in range(k) for j in range(k)]
    kind = "str" if categorical else "int"
    return _frames({"t": [r[0] for r in rows], "x": [r[1] for r in rows],
                    "y": [r[2] for r in rows], "z": [r[3] for r in rows]},
                   {"t": "int", "x": kind, "y": kind, "z": "float"},
                   HeatmapMark(data="d", encoding={"x": "x", "y": "y", "color": "z"}))


def function_3d():
    return _sweep(FigureSpec(coordinate_system="cartesian3d", layers=[FunctionMark(
        expression="p*sin(x)*cos(y)", variables=["x", "y"],
        domain={"x": [0, 6.0], "y": [0, 6.0]}, samples=150, parameters={"p": 1.0})]))


def text_annotations():
    return _sweep(FigureSpec(layers=[_fn()], annotations=[
        SF.TextAnnotation(text=f"note number {i} with some words", x=i % 6, y=(i % 7) / 7)
        for i in range(150)]))


def template_field():
    return vector_field_anim(params={"grid": 60}, values=[i / N for i in range(N)], fps=10)


CASES = {
    "plain": plain, "bar_400_categories": bar_categories,
    "heatmap_100x100_categorical": lambda: heatmap(True, 100),
    "function_3d_150": function_3d, "text_annotations_150": text_annotations,
    "vector_field_grid_60": template_field,
}


@pytest.fixture(scope="module", autouse=True)
def warm():
    # one-time costs (imports, fonts, caches; mplot3d on the first 3D figure)
    # are not per-state costs: pay them before measuring
    render_animation(plain(), AnimationRequest())
    render_animation(function_3d(), AnimationRequest())
    render_animation(vector_field_anim(), AnimationRequest())


def _timed(anim, req) -> float:
    t = time.perf_counter()
    render_animation(anim, req)
    return time.perf_counter() - t


@pytest.mark.parametrize("name", list(CASES))
def test_estimate_tracks_measured_render_time(name):
    anim = CASES[name]()
    req = AnimationRequest()
    figure = anim.figure
    est = matplotlib_work_us(anim, req.frame_request().size_px, N, figure) / 1e6
    actual = _timed(anim, req)
    if actual / est > UNDER:  # load on a shared machine only adds time: measure again
        actual = min(actual, _timed(anim, req))
    ratio = actual / est
    print(f"{name}: estimate {est:.2f} s, measured {actual:.2f} s, ratio {ratio:.2f}")
    assert 1 / OVER <= ratio <= UNDER, f"{name}: estimate {est:.2f} s, measured {actual:.2f} s"
