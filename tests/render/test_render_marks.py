"""Each mark draws exactly the dataset's values (inspected on the Figure, not
by parsing images); nulls are gaps or omissions, never zeros."""

import numpy as np
import pytest
from matplotlib import dates as mdates
from render_examples import (
    REVENUE,
    WEEKS,
    area_spec,
    bar_spec,
    function_spec,
    heatmap_spec,
    histogram_spec,
    line3d_spec,
    one_layer,
    polar_spec,
    quiver_spec,
    revenue_spec,
    scatter_spec,
    surface_spec,
)

from ieeVisualizer.render import RenderError, build_figure
from ieeVisualizer.spec import Axis, BarMark, LineMark, ScatterMark, SpecValidationError


def _dates(strings):
    return mdates.date2num(np.array(strings, dtype="datetime64[D]").astype(object))


def test_line_draws_dataset_values_with_dates_and_a_gap_for_null():
    built = build_figure(revenue_spec())
    (observed,) = built.layer_artists[0]
    x = np.ma.asarray(observed.get_xdata())
    y = np.ma.asarray(observed.get_ydata())
    np.testing.assert_allclose(x, _dates(WEEKS))
    assert list(np.ma.getmaskarray(y)) == [False, True, False, False]
    np.testing.assert_allclose(y.compressed(), [v for v in REVENUE if v is not None])
    # The drawn path has NaN (a break) where the value is null, never 0.
    verts = observed.get_path().vertices
    assert np.isnan(verts[1, 1])
    assert not np.any(verts[:, 1] == 0)
    codes = [w.code for w in built.warnings]
    assert "null_values_omitted" in codes


def test_isolated_point_between_gaps_is_marked_not_invisible():
    built = build_figure(revenue_spec())
    (observed,) = built.layer_artists[0]
    assert observed.get_marker() == "o"
    assert observed.get_markevery() == [0]


def test_time_axis_uses_date_locator_and_formatter():
    built = build_figure(revenue_spec())
    fmt = built.axes.xaxis.get_major_formatter()
    assert isinstance(fmt, mdates.ConciseDateFormatter)


def test_scatter_offsets_sizes_and_colors_equal_data():
    built = build_figure(scatter_spec())
    (coll,) = built.layer_artists[0]
    np.testing.assert_allclose(coll.get_offsets(), [[1, 10], [3, 30], [4, 40]])
    np.testing.assert_allclose(coll.get_array(), [0.1, 0.3, 0.4])
    sizes = coll.get_sizes()
    assert sizes[0] < sizes[1] < sizes[2]
    # colorbar is labelled with the column and its unit
    cbar_axes = [a for a in built.figure.axes if a is not built.axes]
    assert cbar_axes and cbar_axes[0].get_ylabel() == "c (K)"
    assert built.axes.get_ylabel() == "b (kg)"


@pytest.mark.parametrize("horizontal", [False, True])
def test_bar_lengths_equal_data_and_null_is_no_bar(horizontal):
    built = build_figure(bar_spec(horizontal))
    container = built.layer_artists[0][0]
    patches = list(container)
    assert len(patches) == 3  # the null "lost" row draws no bar (not a 0-length bar)
    lengths = [p.get_width() if horizontal else p.get_height() for p in patches]
    assert lengths == [40, 12, 7]
    centres = [p.get_y() + p.get_height() / 2 if horizontal else p.get_x() + p.get_width() / 2
               for p in patches]
    np.testing.assert_allclose(centres, [0, 1, 2])
    ticks = built.axes.get_yticklabels() if horizontal else built.axes.get_xticklabels()
    assert [t.get_text() for t in ticks] == ["lead", "quote", "booked", "lost"]
    # the labelled slot without a bar says so instead of looking like zero
    notes = [a for a in built.layer_artists[0][1:] if hasattr(a, "get_text")]
    assert [n.get_text() for n in notes] == ["no data"]
    value_label = built.axes.get_xlabel() if horizontal else built.axes.get_ylabel()
    assert value_label == "n (count)"


def test_area_polygons_split_at_null():
    built = build_figure(area_spec())
    (coll,) = built.layer_artists[0]
    paths = coll.get_paths()
    assert len(paths) == 2  # one region before the null, one after
    pts = {tuple(v) for p in paths for v in p.vertices}
    for xy in [(0, 1), (1, 2), (3, 3), (4, 1)]:
        assert xy in pts
    assert not any(x == 2.0 for x, _ in pts)  # no vertex at the null x
    assert built.axes.get_xlabel() == "t (s)" and built.axes.get_ylabel() == "v (m)"


def test_histogram_counts_equal_numpy_histogram():
    built = build_figure(histogram_spec(bins=4))
    patches = built.layer_artists[0][0]
    values = [0.1, 0.4, 0.5, 0.9, 1.2, 1.3, 2.0, 2.5]
    counts, edges = np.histogram(values, bins=4)
    assert [p.get_height() for p in patches] == list(counts)
    np.testing.assert_allclose([p.get_x() for p in patches], edges[:-1])
    assert built.axes.get_ylabel() == "Count"
    assert built.axes.get_xlabel() == "lat (s)"


def test_histogram_density_and_explicit_edges():
    built = build_figure(histogram_spec(bin_edges=[0.0, 1.0, 3.0], density=True))
    heights = [p.get_height() for p in built.layer_artists[0][0]]
    expect, _ = np.histogram([0.1, 0.4, 0.5, 0.9, 1.2, 1.3, 2.0, 2.5], bins=[0, 1, 3],
                             density=True)
    np.testing.assert_allclose(heights, expect)
    assert built.axes.get_ylabel() == "Density"


def test_heatmap_grid_equals_data_and_null_cell_is_masked():
    built = build_figure(heatmap_spec())
    (mesh,) = built.layer_artists[0]
    arr = np.ma.asarray(mesh.get_array()).reshape(2, 2)
    assert np.ma.getmaskarray(arr).tolist() == [[False, True], [False, False]]
    assert arr[0, 0] == pytest.approx(0.1)
    assert arr[1, 0] == pytest.approx(0.3) and arr[1, 1] == pytest.approx(0.4)
    cbar = [a for a in built.figure.axes if a is not built.axes][0]
    assert cbar.get_ylabel() == "val (°C)"


def test_quiver_vectors_equal_data_and_units_label_axes():
    built = build_figure(quiver_spec())
    (q,) = built.layer_artists[0]
    np.testing.assert_allclose(q.X, [-1, 0, 1, 0])
    np.testing.assert_allclose(q.Y, [0, -1, 0, 1])
    np.testing.assert_allclose(q.U, [0, 2, 0, -1])
    np.testing.assert_allclose(q.V, [-1, 0, 3, 0])
    assert built.axes.get_xlabel() == "x (m)" and built.axes.get_ylabel() == "y (m)"


def test_quiver_normalize_and_scale():
    built = build_figure(quiver_spec(normalize=True, scale=0.5))
    (q,) = built.layer_artists[0]
    np.testing.assert_allclose(np.hypot(q.U, q.V), 1.0)
    assert q.scale == pytest.approx(2.0) and q.scale_units == "xy"


def test_function_samples_expression_with_parameters():
    built = build_figure(function_spec())
    (line,) = built.layer_artists[0]
    x = np.linspace(0, 10, 50)
    np.testing.assert_allclose(line.get_xdata(), x)
    np.testing.assert_allclose(np.ma.asarray(line.get_ydata()), np.exp(-0.3 * x) * np.sin(x))
    assert built.axes.get_ylabel() == "exp(-a*x)*sin(x)"


def test_function_nonfinite_samples_are_gaps_with_warning():
    from ieeVisualizer.spec import FigureSpec, FunctionMark

    spec = FigureSpec(layers=[FunctionMark(expression="1/x", domain={"x": [-1.0, 1.0]},
                                           samples=5)])
    built = build_figure(spec)
    y = np.ma.asarray(built.layer_artists[0][0].get_ydata())
    assert np.ma.getmaskarray(y).tolist() == [False, False, True, False, False]
    assert "nonfinite_samples_omitted" in [w.code for w in built.warnings]


def test_function_surface_in_3d():
    built = build_figure(surface_spec())
    (surf,) = built.layer_artists[0]
    assert type(surf).__name__ == "Poly3DCollection"
    assert built.axes.name == "3d"


def test_line_in_3d_keeps_null_as_nan():
    built = build_figure(line3d_spec())
    (line,) = built.layer_artists[0]
    xs, ys, zs = line.get_data_3d()
    np.testing.assert_allclose(xs, [0, 1, 2])
    assert np.isnan(zs[1]) and zs[0] == 1 and zs[2] == 3


@pytest.mark.parametrize("unit,expect", [("rad", [0.0, 1.0, 2.0]),
                                         ("deg", [0.0, np.pi / 2, np.pi])])
def test_polar_line_angles(unit, expect):
    built = build_figure(polar_spec(unit))
    assert built.axes.name == "polar"
    (line,) = built.layer_artists[0]
    np.testing.assert_allclose(line.get_xdata(), expect)
    np.testing.assert_allclose(line.get_ydata(), [1, 2, 3])


def test_series_channel_splits_lines_with_labels():
    spec = one_layer(LineMark(encoding={"x": "t", "y": "v", "series": "s"}, name="Load"),
                     {"t": [0, 1, 0, 1], "v": [1.0, 2.0, 3.0, 4.0], "s": ["a", "a", "b", "b"]})
    built = build_figure(spec)
    a, b = built.layer_artists[0]
    assert a.get_label() == "Load: a" and b.get_label() == "Load: b"
    np.testing.assert_allclose(np.ma.asarray(b.get_ydata()), [3, 4])
    assert a.get_color() != b.get_color()
    assert [t.get_text() for t in built.axes.get_legend().get_texts()] == ["Load: a", "Load: b"]


def test_annotations_are_drawn_on_the_right_axes():
    built = build_figure(revenue_spec())
    line, band, text = built.annotation_artists
    assert line.get_ydata()[0] == 1300 and line.get_label() == "Target"
    assert text.get_text() == "peak"
    assert text.xy[0] == pytest.approx(_dates(["2026-09-28"])[0])
    labels = [t.get_text() for t in built.axes.get_legend().get_texts()]
    assert labels == ["Observed", "Forecast", "Target", "Holiday"]


def test_title_caption_limits():
    built = build_figure(revenue_spec())
    assert built.axes.get_title() == "Weekly revenue"
    assert built.caption.get_text().startswith("Forecast is a model output")
    assert built.axes.get_ylim()[0] == 0


def test_category_axis_shares_categories_between_layers():
    from ieeVisualizer.spec import DataSnapshot, FigureSpec

    a = DataSnapshot.from_columns({"k": ["x", "y"], "n": [1, 2]})
    b = DataSnapshot.from_columns({"k": ["y", "z"], "n": [3, 4]})
    spec = FigureSpec(datasets={"a": a, "b": b}, layers=[
        BarMark(data="a", encoding={"x": "k", "y": "n"}),
        ScatterMark(data="b", encoding={"x": "k", "y": "n"})])
    built = build_figure(spec)
    np.testing.assert_allclose(built.layer_artists[1][0].get_offsets()[:, 0], [1, 2])
    assert [t.get_text() for t in built.axes.get_xticklabels()] == ["x", "y", "z"]


def test_axis_kind_conflict_is_refused():
    from ieeVisualizer.spec import DataSnapshot, FigureSpec

    a = DataSnapshot.from_columns({"k": ["x", "y"], "n": [1, 2]})
    b = DataSnapshot.from_columns({"k": [1.0, 2.0], "n": [3, 4]})
    spec = FigureSpec(datasets={"a": a, "b": b}, layers=[
        ScatterMark(data="a", encoding={"x": "k", "y": "n"}),
        ScatterMark(data="b", encoding={"x": "k", "y": "n"})])
    with pytest.raises(RenderError) as e:
        build_figure(spec)
    assert e.value.issues[0].code == "axis_kind_conflict"
    spec.axes["x"] = Axis(scale="category")
    build_figure(spec)  # explicit category scale makes it drawable


def test_series_that_ends_up_empty_is_an_error_not_an_empty_line():
    spec = one_layer(LineMark(encoding={"x": "t", "y": "v"}),
                     {"t": [0.0, None, 2.0], "v": [None, 1.0, None]})
    with pytest.raises(RenderError) as e:
        build_figure(spec)
    assert e.value.issues[0].code == "empty_series"
    assert e.value.issues[0].path == "/layers/0"


def test_one_empty_group_is_an_error():
    spec = one_layer(LineMark(encoding={"x": "t", "y": "v", "series": "s"}),
                     {"t": [0.0, 1.0, 0.0], "v": [1.0, 2.0, None], "s": ["a", "a", "b"]})
    with pytest.raises(RenderError) as e:
        build_figure(spec)
    assert e.value.issues[0].code == "empty_series"
    assert "'b'" in e.value.issues[0].message


def test_log_axis_and_log_sampled_function():
    from ieeVisualizer.spec import FigureSpec, FunctionMark

    spec = FigureSpec(layers=[FunctionMark(expression="x^2", domain={"x": [1.0, 100.0]},
                                           samples=3)],
                      axes={"x": Axis(scale="log"), "y": Axis(scale="log")})
    built = build_figure(spec)
    assert built.axes.get_xscale() == "log"
    np.testing.assert_allclose(built.layer_artists[0][0].get_xdata(), [1, 10, 100])


def test_log_scale_in_3d_is_refused():
    spec = line3d_spec()
    spec.axes["z"] = Axis(scale="log")
    with pytest.raises(RenderError) as e:
        build_figure(spec)
    assert e.value.issues[0].code == "unsupported_scale"


def test_polar_bar_and_3d_quiver_draw_their_data():
    from ieeVisualizer.spec import QuiverMark

    spec = one_layer(BarMark(encoding={"x": "t", "y": "r"}),
                     {"t": [0.0, 1.0, 2.0], "r": [1.0, None, 1.5]},
                     coordinate_system="polar")
    bars = build_figure(spec).layer_artists[0][0]
    assert [p.get_height() for p in bars] == [1.0, 1.5]
    spec = one_layer(QuiverMark(encoding={c: c for c in "xyzuvw"}),
                     {c: [0.0, 1.0] for c in "xyzuvw"}, coordinate_system="cartesian3d")
    built = build_figure(spec)
    assert type(built.layer_artists[0][0]).__name__ == "Line3DCollection"
    assert built.axes.get_xlim3d()[1] >= 2.0  # arrow tip (1 + 1) is in view


def test_scatter_with_categorical_color_groups_points():
    spec = one_layer(ScatterMark(encoding={"x": "a", "y": "b", "color": "g"}),
                     {"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0], "g": ["p", "q", "p"]})
    p, q = build_figure(spec).layer_artists[0]
    np.testing.assert_allclose(p.get_offsets(), [[1, 4], [3, 6]])
    assert p.get_label() == "p" and q.get_label() == "q"


def test_timestamps_with_offsets_are_labelled_in_the_provenance_timezone():
    from ieeVisualizer.spec import Column, DataSnapshot, FigureSpec, Provenance

    def spec_with(tz):
        ds = DataSnapshot(columns={
            "t": Column(["2026-10-14T06:00:00+03:00", "2026-10-14T09:00:00+03:00"],
                        dtype="datetime"),
            "v": Column([1.0, 2.0])}, provenance=Provenance(timezone=tz))
        return FigureSpec(datasets={"d": ds},
                          layers=[LineMark(data="d", encoding={"x": "t", "y": "v"})])

    built = build_figure(spec_with("Europe/Helsinki"))
    line = built.layer_artists[0][0]
    # placed at the true instant (03:00 UTC) ...
    assert mdates.num2date(line.get_xdata()[0]).hour == 3
    # ... and labelled in Helsinki time
    labels = built.axes.xaxis.get_major_formatter().format_ticks(list(line.get_xdata()))
    assert labels[:2] == ["06:00", "09:00"]  # local wall time, as written (UTC would be 03:00)
    assert not built.warnings
    codes = [w.code for w in build_figure(spec_with(None)).warnings]
    assert codes == ["datetime_displayed_in_utc"]


def test_bad_datetime_and_duplicate_heatmap_cell_are_errors():
    from ieeVisualizer.spec import HeatmapMark

    # The spec refuses an invalid datetime at construction ...
    with pytest.raises(ValueError, match="not an ISO-8601"):
        one_layer(LineMark(encoding={"x": "t", "y": "v"}),
                  {"t": ["2026-10-14", "next tuesday"], "v": [1.0, 2.0]},
                  dtypes={"t": "datetime"})
    # ... and one set after construction is refused before anything is drawn:
    # validate() re-normalises columns, so the error is the validator's
    # structured issue (raised by prepare()), not a renderer failure.
    spec = one_layer(LineMark(encoding={"x": "t", "y": "v"}),
                     {"t": ["2026-10-14", "2026-10-15"], "v": [1.0, 2.0]},
                     dtypes={"t": "datetime"})
    spec.datasets["d"].columns["t"].values[1] = "next tuesday"
    with pytest.raises(SpecValidationError) as e:
        build_figure(spec)
    assert [(i.code, i.path, i.severity) for i in e.value.issues] == [
        ("invalid_values", "/datasets/d/columns/t/values", "error")]
    assert "index 1" in e.value.issues[0].message
    assert "not an ISO-8601" in e.value.issues[0].message
    # The renderer keeps its own check as defence in depth (e.g. a column
    # reached without validation); it reports the exact element.
    from ieeVisualizer.render.mpl.data import _datetimes_to_num
    with pytest.raises(RenderError) as e:
        _datetimes_to_num(["2026-10-14", "next tuesday"], "/datasets/d/columns/t")
    assert e.value.issues[0].code == "invalid_datetime"
    assert e.value.issues[0].path == "/datasets/d/columns/t/values/1"
    spec = one_layer(HeatmapMark(encoding={"x": "a", "y": "b", "color": "c"}),
                     {"a": [0, 0], "b": [1, 1], "c": [1.0, 2.0]})
    with pytest.raises(RenderError) as e:
        build_figure(spec)
    assert e.value.issues[0].code == "duplicate_cell"


def test_area_isolated_point_between_gaps_is_marked_not_invisible():
    from ieeVisualizer.spec import AreaMark

    for role in ("observed", "forecast"):
        spec = one_layer(AreaMark(encoding={"x": "t", "y": "v"}, role=role),
                         {"t": [0.0, 1.0, 2.0, 3.0, 4.0], "v": [1.0, None, 5.0, None, 2.0]},
                         units={"t": "s", "v": "m"})
        coll, marks = build_figure(spec).layer_artists[0]
        # every polygon is zero-width (each valid point stands alone) ...
        assert all(np.ptp(p.vertices[:, 0]) == 0 for p in coll.get_paths())
        # ... so each of those points gets a marker in the series colour
        assert marks.get_marker() == "o"
        np.testing.assert_allclose(marks.get_xdata(), [0.0, 2.0, 4.0])
        np.testing.assert_allclose(marks.get_ydata(), [1.0, 5.0, 2.0])
        assert marks.get_label() == "_nolegend_"
    # a contiguous area gets no extra markers
    assert len(build_figure(area_spec()).layer_artists[0]) == 1


def _two_city_spec(**names):
    from ieeVisualizer.spec import DataSnapshot, FigureSpec

    def snap(v):
        return DataSnapshot.from_columns({"day": [1, 2, 3], "temp": v},
                                         units={"temp": "degC", "day": "d"})

    return FigureSpec(
        datasets={"helsinki": snap([1.0, 2.0, 3.0]), "tampere": snap([4.0, 5.0, 6.0])},
        layers=[LineMark(data="helsinki", encoding={"x": "day", "y": "temp"},
                         name=names.get("a")),
                LineMark(data="tampere", encoding={"x": "day", "y": "temp"},
                         name=names.get("b"))],
        axes={"x": Axis(label="Day", unit="d"), "y": Axis(label="Temperature", unit="degC")})


@pytest.mark.parametrize("names", [{"a": "Helsinki", "b": "Tampere"}, {}])
def test_same_column_from_different_datasets_gets_different_colours(names):
    a, b = (arts[0] for arts in build_figure(_two_city_spec(**names)).layer_artists.values())
    assert a.get_color() != b.get_color()


def test_forecast_pairs_with_its_observed_series_by_field_and_group():
    """The timeseries template's shape: datasets ``observed``/``forecast``,
    layers ``v``/``v (forecast)`` with a series column, plus bound lines."""
    from ieeVisualizer.spec import DataSnapshot, FigureSpec

    def snap(days, vals):
        return DataSnapshot.from_columns(
            {"t": days * 2, "s": ["A"] * len(days) + ["B"] * len(days), "v": vals,
             "lo": [0.0] * len(vals)}, units={"v": "m", "lo": "m"})

    obs, fc = snap([1, 2, 3], [1.0, 2, 3, 4, 5, 6]), snap([4, 5], [3.0, 4, 6, 7])

    def line(part, field, name):
        return LineMark(data=part, encoding={"x": "t", "y": field, "series": "s"},
                        role=part, name=name,
                        style={"linestyle": "dashed"} if part == "forecast" else {})

    spec = FigureSpec(
        datasets={"observed": obs, "forecast": fc},
        layers=[line("observed", "v", "v"), line("observed", "lo", "v lower bound (lo)"),
                line("forecast", "v", "v (forecast)"),
                line("forecast", "lo", "v lower bound (lo)")],
        axes={"x": Axis(label="t"), "y": Axis(label="v", unit="m")})
    arts = build_figure(spec).layer_artists
    colours = [[ln.get_color() for ln in arts[i]] for i in range(4)]
    assert colours[2] == colours[0] and colours[3] == colours[1]  # forecast continues observed
    assert len({c for cs in colours[:2] for c in cs}) == 4  # A/B x value/bound all distinct
