"""Review round 4: the wall-clock deadline is the guarantee, the estimate only
an early refusal. Every input here was accepted by the estimate and ran for
minutes (or hours) before; each now stops within its deadline plus the
documented slack, or is refused or made cheap by a per-step bound."""

import dataclasses

import numpy as np
import pytest
from anim_helpers import ROOT, SMALL, codes, run_limited, sine_anim, weekly_anim

from ieeVisualizer.animation import (
    AnimationRequest,
    AnimationSpec,
    FrameColumn,
    ParameterSweep,
    render_animation,
    validate_animation,
)
from ieeVisualizer.animation.cost import matplotlib_work_us
from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
from ieeVisualizer.spec import (
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    HeatmapMark,
    HistogramMark,
    Legend,
    LineMark,
    SpecValidationError,
)
from ieeVisualizer.spec import figure as SF

#: One uninterruptible step (docs/ANIMATION.md, "Deadline"); measured here
#: 0.03-0.4 s on these inputs, with room for a slower CI runner.
SLACK_S = 2.5
DEADLINE_S = 2.0
#: Max(...) of 11 terms: within MAX_MINMAX_COST, about 0.5 s per SymPy build.
HEAVY = "Max(" + ",".join(f"sin({i}*x+a)*cos(x)+exp(-{i}*x)" for i in range(1, 12)) + ")"


def _fn(expr="a*sin(x)", **kw):
    return FunctionMark(expression=expr, variables=["x"], domain={"x": [0.1, 5.0]},
                        samples=kw.pop("samples", 50), parameters={"a": 1.0}, **kw)


def _sweep(fig, n=3):
    return AnimationSpec(timeline=ParameterSweep(parameter="a",
                                                 values=[1.0 + i for i in range(n)]),
                         figure=fig, fps=10)


# ------------------------------------------------------------ the deadline


# ------------------------------------------- per-step bounds the estimate missed


def test_unused_datasets_are_dropped_before_any_state_is_built():
    """A dataset no layer draws was copied and validated in every state
    (400,000 rows x 100 states: 988 s); over 1 M cells it is refused (one
    validation of 2 M cells takes 7.5 s, uninterruptible)."""
    out = run_limited(f"""
        sys.path.insert(0, {str(ROOT / "tests")!r})
        from cost import measure
        from test_anim_deadline import _fn, _sweep
        from anim_helpers import SMALL
        from ieeVisualizer.animation import render_animation
        from ieeVisualizer.spec import DataSnapshot, FigureSpec
        n = 100_000
        big = DataSnapshot.from_columns({{"u": [float(i) for i in range(n)],
                                         "w": [float(i % 7) for i in range(n)]}})
        def timed(fig):  # a deadline that cannot fire first: the work is measured
            with measure() as c:
                render_animation(_sweep(fig, 20), SMALL, deadline=3600)
            return c.seconds
        timed(FigureSpec(layers=[_fn()]))  # warm-up
        base = timed(FigureSpec(layers=[_fn()]))
        print(json.dumps({{"s": timed(FigureSpec(datasets={{"unused": big}}, layers=[_fn()])),
                          "base": base}}))
    """, timeout=120)
    # relative to the same render without the dataset, both in reference
    # seconds (tests/cost.py: CPU work, which a loaded machine does not
    # inflate as it does wall-clock time): copied per state it was ~50x
    assert out["s"] < 3 * out["base"] + 1.0, out
    big = DataSnapshot.from_columns({"u": [0.0] * 500_001, "w": [0.0] * 500_001})
    issues = validate_animation(_sweep(FigureSpec(datasets={"big": big}, layers=[_fn()])))
    assert [(i.path, i.code) for i in issues] == [("/figure/datasets", "data_too_large")]


def test_each_expression_is_built_once_per_render(monkeypatch):
    """With fixed limits every state was drawn twice, building every
    expression twice per state; now the swept layer is built once per state
    and an unswept layer once per render."""
    from ieeVisualizer.render.mpl import function

    seen = []
    orig = function.build_bounded
    monkeypatch.setattr(function, "build_bounded",
                        lambda tree, subs=None: seen.append(str(tree)) or orig(tree, subs))
    fig = FigureSpec(layers=[_fn("a*sin(x)"), FunctionMark(
        expression="cos(x)*b", variables=["x"], domain={"x": [0.0, 5.0]}, parameters={"b": 1.0})])
    render_animation(_sweep(fig, 5), SMALL)
    assert sum("sin" in t for t in seen) == 5
    assert sum("cos" in t for t in seen) == 1


def test_function_expressions_slow_to_build_are_refused():
    """A Max of 59 terms took 5 s per build, twice per state: estimated 57 s,
    about 50 minutes to 20 hours measured."""
    slow = "Max(" + ",".join(f"sin({i}*x+a)" for i in range(1, 60)) + ")"
    issues = validate_animation(_sweep(FigureSpec(layers=[_fn(slow)])))
    assert [(i.path, i.code) for i in issues] == [("/figure/layers/0/expression",
                                                   "expression_too_complex")]
    assert codes(validate_animation(_sweep(FigureSpec(layers=[_fn(HEAVY)])))) == []
    from anim_helpers import vector_field_anim

    tpl = vector_field_anim(params={"u": slow.replace("+a)", "+k)")})
    assert [(i.path, i.code) for i in validate_animation(tpl)] == [
        ("/timeline/target/params/u", "expression_too_complex")]


@pytest.mark.parametrize("where", ["title", "annotation", "axis"])
def test_long_text_is_refused(where):
    """20,000-character titles and 200 annotations of 2,000 characters were
    accepted and drawn in every state."""
    text = "W " * 1001
    fig = FigureSpec(layers=[_fn()])
    if where == "title":
        fig.title, path = text, "/figure/title"
    elif where == "annotation":
        fig.annotations = [SF.TextAnnotation(text=text, x=1, y=0)]
        path = "/figure/annotations/0/text"
    else:
        fig.axes = {"x": SF.Axis(label=text)}
        path = "/figure/axes/x/label"
    assert [(i.path, i.code) for i in validate_animation(_sweep(fig))] == [
        (path, "text_too_long")]


def test_labelled_layers_charge_their_legend_entries():
    """40 labelled function layers with a legend rendered in 23 s against an
    11 s estimate (without a legend: 12 s)."""
    def anim(show):
        return _sweep(FigureSpec(layers=[_fn(f"a*sin(x+{i})") for i in range(40)],
                                 legend=Legend(show=show)), 20)

    with_legend, without = anim(True), anim(False)
    size = SMALL.frame_request().size_px
    extra = matplotlib_work_us(with_legend, size, 20, with_legend.figure) \
        - matplotlib_work_us(without, size, 20, without.figure)
    assert extra >= 40 * 20 * 12_000


def test_datetime_frame_values_are_read_once_not_per_state(monkeypatch):
    """A 'current' frame column re-parsed every row's datetime in every
    state (800,000 rows x 300 states)."""
    import datetime as dt

    from ieeVisualizer.animation import frames as F

    n, states = 2_000, 50
    days = [(dt.date(2026, 1, 1) + dt.timedelta(days=i % states)).isoformat() for i in range(n)]
    ds = DataSnapshot.from_columns({"t": days, "y": [float(i % 13) for i in range(n)]},
                                   dtypes={"t": "datetime", "y": "float"})
    anim = AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=10,
                         figure=FigureSpec(datasets={"d": ds}, layers=[
                             LineMark(data="d", encoding={"x": "y", "y": "y"})]))
    calls = [0]
    orig = F._key
    monkeypatch.setattr(F, "_key", lambda v, d: calls.__setitem__(0, calls[0] + 1) or orig(v, d))
    MatplotlibAnimationAdapter().plan(anim, SMALL)
    assert calls[0] <= 2 * n  # validation + the resolved count; never per state


# ------------------------------------------------------------- memory


def test_frame_area_is_capped():
    big = AnimationRequest(width=7745, height=7745, units="px", dpi=100)
    with pytest.raises(Exception) as e:
        render_animation(_sweep(FigureSpec(layers=[_fn()]), 2), big)
    assert codes(e.value.issues) == ["animation_too_large"]


def test_frames_are_streamed_so_memory_is_bounded():
    """60 frames of 800 x 800 px: every state's RGB pixels were held at once
    besides the GIF's palette frames (+243 MB measured). Now one RGB frame
    exists at a time (+124 MB: palette frames, copied once by Pillow's GIF
    writer, plus one frame being drawn)."""
    out = run_limited("""
        import resource
        from test_anim_deadline import _fn, _sweep
        from ieeVisualizer.animation import AnimationRequest, render_animation
        from ieeVisualizer.spec import FigureSpec
        req = AnimationRequest(width=800, height=800, units="px", dpi=100)
        # a memory test, not a time test: a loaded runner must not hit the deadline
        render_animation(_sweep(FigureSpec(layers=[_fn()]), 2), req, deadline=600)
        base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        render_animation(_sweep(FigureSpec(layers=[_fn()]), 60), req, deadline=600)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps({"mb": (peak - base) / 1024}))
    """, timeout=600, mem_mb=3000)
    assert out["mb"] < 160, out


# ------------------------------------------------------------ correctness


def _heat(axis_limits="fixed"):
    rows = [(0, 0, 0.0), (0, 1, 1.0), (1, 0, 0.0), (1, 1, 100.0)]
    ds = DataSnapshot.from_columns(
        {"t": [r[0] for r in rows], "x": [r[1] for r in rows], "y": [0] * 4,
         "z": [r[2] for r in rows]}, dtypes={"t": "int", "x": "int", "y": "int", "z": "float"})
    return AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=2,
                         axis_limits=axis_limits, figure=FigureSpec(datasets={"d": ds}, layers=[
                             HeatmapMark(data="d", encoding={"x": "x", "y": "y", "color": "z"})]))


def test_heatmap_colour_scale_is_held_across_frames():
    """Frame 0 mapped 1 to the top colour, frame 1 mapped 100 to it."""
    a = MatplotlibAnimationAdapter()
    fp, _ = a.plan(_heat(), SMALL)
    clims = {a._static.build_figure(s.spec, SMALL.frame_request()).axes.collections[0].get_clim()
             for s in fp.states}
    assert clims == {(0.0, 100.0)}
    _, warnings = a.plan(_heat("auto"), SMALL)
    assert "color_scale_per_frame" in codes(warnings)


def test_histogram_fixed_y_range_is_the_union_of_the_frames():
    """With fixed limits the y range came from every frame's rows pooled
    (counts up to 251 where no frame exceeds 30)."""
    rng = np.random.default_rng(1)
    vals = rng.normal(size=2000).tolist()
    ds = DataSnapshot.from_columns({"t": [i // 200 for i in range(2000)], "v": vals},
                                   dtypes={"t": "int", "v": "float"})
    anim = AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=5, figure=FigureSpec(
        datasets={"d": ds}, layers=[HistogramMark(data="d", encoding={"x": "v"}, bins=20)]))
    a = MatplotlibAnimationAdapter()
    fp, _ = a.plan(anim, SMALL)
    tallest = max(a._static.build_figure(dataclasses.replace(s.spec, axes={}),
                                         SMALL.frame_request()).axes.get_ylim()[1]
                  for s in fp.states)
    assert fp.states[0].spec.axes["y"].limits[1] == pytest.approx(tallest)


def test_no_data_frames_and_their_poster_use_the_theme():
    """A dark clip flashed a white 'no data' frame (the poster: test_anim_mp4)."""
    from ieeVisualizer import themes

    dark = next(t for t in map(themes.get, (d["name"] for d in themes.list_themes()))
                if t.background and int(t.background[1:3], 16) < 64)
    want = tuple(int(dark.background[i:i + 2], 16) for i in (1, 3, 5))
    req = dataclasses.replace(SMALL, theme=dark.name)
    anim = weekly_anim("current", poster=2)  # week 2 has no value
    fp, _, pixels = MatplotlibAnimationAdapter().render_frames(anim, req)
    assert fp.states[2].spec is None and tuple(pixels[2][2, 2]) == want


def test_sweep_range_overflow_is_refused_at_the_timeline():
    """start=1e308, end=-1e308: end - start is -inf, every value nan; it
    passed validation and failed later at a figure path."""
    anim = sine_anim(timeline=ParameterSweep(parameter="phase", start=1e308, end=-1e308,
                                             duration=1.5))
    assert [(i.path, i.code) for i in validate_animation(anim)] == [("/timeline",
                                                                    "invalid_value")]
    with pytest.raises(SpecValidationError):
        render_animation(anim, SMALL)




DEEP = "$" + "\\sqrt{" * 280 + "x" + "}" * 280 + "$"


@pytest.mark.parametrize("where", ["name", "unit", "value"])
def test_text_drawn_from_data_is_checked_like_any_other_text(where):
    """A column name is a default axis label, a unit is part of it, a string
    value is a tick or legend entry: each went to the parser unchecked
    (a raw RecursionError from the limit pass)."""
    from ieeVisualizer.spec import DatasetRef

    col = DEEP if where == "name" else "y"
    ds = DataSnapshot.from_columns(
        {"t": [0, 0, 1, 1], "x": [0.0, 1.0, 0.0, 1.0], col: [1.0, 2.0, 2.0, 3.0],
         "c": [DEEP if where == "value" else "a"] * 4},
        units={col: DEEP} if where == "unit" else None)
    layer = LineMark(data="d", encoding={"x": "x", "y": col, "color": "c"})
    anim = AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=2,
                         figure=FigureSpec(datasets={"d": ds}, layers=[layer]))
    assert "text_too_complex" in codes(validate_animation(anim))
    anim.figure.datasets["d"] = DatasetRef(kind="table", id="d")
    with pytest.raises(SpecValidationError) as e:
        render_animation(anim, SMALL, resolver=lambda ref: ds)
    assert "text_too_complex" in codes(e.value.issues)


def test_a_recursion_error_in_the_limit_pass_is_a_structured_error(monkeypatch):
    from ieeVisualizer.animation import AnimationError
    from ieeVisualizer.render.mpl import MatplotlibAdapter

    def deep(*a, **k):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(MatplotlibAdapter, "build_figure", deep)
    with pytest.raises(AnimationError) as e:
        render_animation(sine_anim(), SMALL)
    assert codes(e.value.issues) == ["frame_not_drawable"]
    assert "axis limits" in e.value.issues[0].message  # the limit pass, before any draw
