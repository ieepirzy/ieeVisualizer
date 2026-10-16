"""Review of #22: refusals up front for inputs whose single steps were slow,
and fixed limits / colour ranges taken only from what is drawn."""

import dataclasses

import pytest
from anim_helpers import SMALL, codes, vector_field_anim
from test_anim_deadline import _fn, _sweep

from ieeVisualizer.animation import (
    AnimationSpec,
    Deadline,
    FrameColumn,
    ParameterSweep,
    TemplateTarget,
    render_animation,
    validate_animation,
)
from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter
from ieeVisualizer.spec import (
    DatasetRef,
    DataSnapshot,
    FigureSpec,
    HeatmapMark,
    LineMark,
    ScatterMark,
    SpecValidationError,
)


def _paths(anim):
    return [(i.path, i.code) for i in validate_animation(anim) if i.severity == "error"]


def test_hairline_dash_patterns_are_refused():
    """linewidth 1e-7 dotted: 0.6 s estimated, 231 s in one draw."""
    anim = _sweep(FigureSpec(layers=[_fn(style={"linewidth": 1e-7, "linestyle": "dotted"})]))
    assert _paths(anim) == [("/figure/layers/0/style/linewidth", "invalid_value")]


@pytest.mark.parametrize("text", ["$" + "\\sqrt{" * 280 + "x" + "}" * 280 + "$",
                                  "$" + "\\frac{\\sqrt{a}}{\\int b}" * 80 + "$"])
def test_deep_or_long_math_text_is_refused(text):
    """Nested mathtext raised a bare RecursionError; wide mathtext was slow."""
    assert _paths(_sweep(FigureSpec(title=text, layers=[_fn()]))) == [
        ("/figure/title", "text_too_complex")]


def _diagonal_heatmap(n):
    rows = [(t, i) for t in range(2) for i in range(n)]
    ds = DataSnapshot.from_columns({"t": [r[0] for r in rows], "x": [r[1] for r in rows],
                                    "y": [r[1] for r in rows], "z": [float(r[1]) for r in rows]},
                                   dtypes={"t": "int", "x": "int", "y": "int", "z": "float"})
    return AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=2, figure=FigureSpec(
        datasets={"d": ds}, layers=[HeatmapMark(data="d", encoding={"x": "x", "y": "y",
                                                                    "color": "z"})]))


def test_heatmap_grid_cells_are_capped_and_charged():
    """6000 rows of a diagonal is a 3000 x 3000 grid: 1 s estimated, 4.5 s."""
    assert _paths(_diagonal_heatmap(3000)) == [("/figure/layers/0", "data_too_large")]
    from ieeVisualizer.animation.cost import matplotlib_work_us

    anim = _diagonal_heatmap(400)
    assert matplotlib_work_us(anim, (64, 48), 2, anim.figure) > 2 * 400 * 400 * 100


def test_resolved_data_is_capped_before_it_is_validated():
    """A reference resolving to 5.1 M cells was validated for seconds."""
    n = 400_000
    big = DataSnapshot.from_columns({"t": [i % 2 for i in range(n)], "x": [0.0] * n,
                                     "y": [0.0] * n})
    anim = AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=2, figure=FigureSpec(
        datasets={"d": DatasetRef(kind="table", id="big")},
        layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})]))
    with pytest.raises(SpecValidationError) as e:
        render_animation(anim, SMALL, resolver=lambda r: big)
    assert codes(e.value.issues) == ["data_too_large"]


def test_fixed_limits_and_colours_come_only_from_drawn_rows():
    """A row in no frame (null frame value) set the y range to 1000; a value
    whose position is null set the colour range to 1000."""
    ds = DataSnapshot.from_columns({"t": [0, 0, 1, 1, None], "x": [0.0, 1.0, 0.0, 1.0, 50.0],
                                    "y": [1.0, 2.0, 2.0, 3.0, 1000.0]},
                                   dtypes={"t": "int", "x": "float", "y": "float"})
    anim = AnimationSpec(timeline=FrameColumn(dataset="d", column="t"), fps=2, figure=FigureSpec(
        datasets={"d": ds}, layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})]))
    fp, _ = MatplotlibAnimationAdapter().plan(anim, SMALL)
    assert fp.states[0].spec.axes["y"].limits[1] < 4 and fp.states[0].spec.axes["x"].limits[1] < 2
    ds = DataSnapshot.from_columns({"t": [0, 0, 1, 1, 1], "x": [0.0, 1.0, 0.0, 1.0, None],
                                    "y": [1.0, 2.0, 2.0, 3.0, 2.5],
                                    "c": [0.0, 1.0, 0.5, 2.0, 1000.0]},
                                   dtypes={"t": "int", "x": "float", "y": "float", "c": "float"})
    anim = dataclasses.replace(anim, figure=FigureSpec(datasets={"d": ds}, layers=[ScatterMark(
        data="d", encoding={"x": "x", "y": "y", "color": "c"})]))
    fp, _ = MatplotlibAnimationAdapter().plan(anim, SMALL)
    assert fp.states[1].spec.layers[0].style["color_range"] == [0.0, 2.0]


def test_a_shared_deadline_validates_a_mutated_spec_again():
    anim = _sweep(FigureSpec(layers=[_fn()]))
    d = Deadline(60)
    render_animation(anim, SMALL, deadline=d)
    anim.figure.title = "W " * 10000
    with pytest.raises(SpecValidationError) as e:
        render_animation(anim, SMALL, deadline=d)
    assert codes(e.value.issues) == ["text_too_long"]


def test_template_data_columns_are_charged():
    """5000 value columns x 4 rows: 0.6 s estimated, 31 s in the limits pass."""
    k, r = 5000, 4
    cols = {"t": [f"2026-01-0{i + 1}T00:00:00" for i in range(r)]}
    cols.update({f"c{j}": [float(i) for i in range(r)] for j in range(k)})
    ds = DataSnapshot.from_columns(cols, dtypes={"t": "datetime"})
    anim = AnimationSpec(fps=2, timeline=ParameterSweep(
        parameter="target", values=[1.0, 2.0, 3.0], target=TemplateTarget(
            name="timeseries", version="1.0.0", data=ds,
            params={"time_column": "t", "value_columns": list(cols)[1:], "target": 1.0})))
    assert codes(validate_animation(anim)) == ["animation_too_expensive"]


def test_malformed_targets_and_ranges_are_structured_issues():
    tl = ParameterSweep(parameter="a", values=[1.0, 2.0], target=None)
    assert _paths(_sweep(FigureSpec(layers=[_fn()])).__class__(
        timeline=tl, figure=FigureSpec(layers=[_fn()]))) == [("/timeline/target", "invalid_type")]
    huge = vector_field_anim(params={"x_range": [-1e308, 1e308]})
    assert [c for _, c in _paths(huge)]  # an issue, not a raised ValueError
