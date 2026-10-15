import pytest
from spec_examples import (
    EXAMPLES,
    function_spec,
    histogram_ref_spec,
    misc_spec,
    quiver_spec,
    scatter3d_spec,
    timeseries_spec,
)

from ieeVisualizer.spec import (
    Axis,
    Column,
    DatasetRef,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    HeatmapMark,
    LineMark,
    Mark,
    ReferenceLine,
    validate,
)


def codes(spec, severity=None):
    return [(i.path, i.code) for i in validate(spec) if severity in (None, i.severity)]


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_are_valid(name):
    example_spec = EXAMPLES[name]()
    assert validate(example_spec) == []


def test_issue_shape_is_serializable():
    spec = timeseries_spec()
    spec.layers[0].encoding["y"].field = "revenu"
    (issue,) = validate(spec)
    assert issue.to_dict() == {
        "path": "/layers/0/encoding/y/field",
        "code": "unknown_column",
        "message": issue.message,
        "severity": "error",
    }
    assert "Did you mean 'revenue'?" in issue.message and "Columns: revenue, week" in issue.message


def test_unknown_mark_type_from_python():
    spec = timeseries_spec()
    spec.layers.append(Mark(type="sankey", data="observed"))
    assert codes(spec) == [("/layers/2/type", "unknown_mark_type")]


def test_unknown_and_missing_dataset():
    spec = timeseries_spec()
    spec.layers[1].data = "forecasts"
    spec.layers.append(LineMark(encoding={"x": "week", "y": "revenue"}))
    assert codes(spec) == [
        ("/layers/1/data", "unknown_dataset"),
        ("/layers/2/data", "missing_data"),
    ]
    assert "Did you mean 'forecast'?" in validate(spec)[0].message


def test_required_channels_per_mark():
    spec = quiver_spec()
    del spec.layers[0].encoding["v"]
    assert codes(spec) == [("/layers/0/encoding/v", "missing_channel")]
    spec = misc_spec()
    del spec.layers[0].encoding["color"]  # heatmap needs its value channel
    assert codes(spec) == [("/layers/0/encoding/color", "missing_channel")]


def test_3d_requires_z_and_2d_rejects_z():
    spec = scatter3d_spec()
    del spec.layers[0].encoding["z"]
    assert codes(spec) == [("/layers/0/encoding/z", "missing_channel")]
    spec = timeseries_spec()
    spec.layers[0].encoding["z"] = "revenue"
    assert codes(spec) == [("/layers/0/encoding/z", "unsupported_channel")]


def test_mark_unsupported_in_coordinate_system():
    spec = misc_spec()
    spec.coordinate_system = "polar"
    assert ("/layers/0/type", "unsupported_in_coordinate_system") in codes(spec)


def test_column_length_mismatch_points_at_the_odd_column():
    spec = quiver_spec()
    spec.datasets["field"].columns["u"].values.pop()
    assert codes(spec) == [("/datasets/field/columns/u", "column_length_mismatch")]


def test_empty_dataset_is_an_error():
    spec = timeseries_spec()
    spec.datasets["forecast"] = DataSnapshot.from_columns({"week": [], "revenue": []})
    issues = codes(spec)
    assert ("/datasets/forecast", "empty_dataset") in issues
    spec.datasets["forecast"] = DataSnapshot()
    assert ("/datasets/forecast", "empty_dataset") in codes(spec)


def test_all_null_column_used_by_encoding_is_an_error():
    spec = timeseries_spec()
    spec.datasets["forecast"].columns["revenue"] = Column([None, None], unit="EUR")
    assert codes(spec) == [("/layers/1/encoding/y", "all_null_column")]


def test_partially_null_column_is_fine():
    spec = timeseries_spec()  # observed revenue already contains a null
    assert None in spec.datasets["observed"].columns["revenue"].values
    assert validate(spec) == []


def test_axis_unit_conflict_across_layers():
    spec = timeseries_spec()
    spec.datasets["forecast"].columns["revenue"].unit = "kEUR"
    (issue,) = validate(spec)
    assert (issue.path, issue.code) == ("/layers/1/encoding/y", "axis_unit_conflict")
    assert "'kEUR'" in issue.message and "/axes/y/unit" in issue.message


def test_unit_conflict_without_declared_axis_unit():
    spec = timeseries_spec()
    spec.axes["y"].unit = None
    spec.datasets["forecast"].columns["revenue"].unit = "USD"
    (issue,) = validate(spec)
    assert (issue.path, issue.code) == ("/layers/1/encoding/y", "axis_unit_conflict")
    assert "/layers/0/encoding/y" in issue.message


def test_log_scale_with_nonpositive_inline_values():
    spec = quiver_spec()
    spec.axes["x"].scale = "log"
    issues = validate(spec)
    assert [(i.path, i.code) for i in issues] == [
        ("/layers/0/encoding/x", "log_scale_nonpositive"),
        ("/annotations/0/x", "log_scale_nonpositive"),  # the text sits at x = 0
    ]
    assert "symlog" in issues[0].message and "6 value(s) <= 0" in issues[0].message


def test_symlog_accepts_nonpositive_values():
    spec = quiver_spec()
    spec.axes["x"].scale = "symlog"
    assert validate(spec) == []


def test_log_scale_limits_and_function_domain():
    spec = function_spec()
    spec.axes["x"] = Axis(scale="log", limits=[0, 10])
    assert codes(spec) == [("/axes/x/limits", "log_scale_nonpositive"),
                           ("/layers/0/domain/x", "log_scale_nonpositive")]


def test_scale_dtype_mismatch():
    spec = timeseries_spec()
    spec.axes["x"].scale = "linear"
    # The holiday band's date strings cannot be placed on a linear axis either.
    assert codes(spec) == [("/layers/0/encoding/x", "scale_dtype_mismatch"),
                           ("/layers/1/encoding/x", "scale_dtype_mismatch"),
                           ("/annotations/1/start", "scale_dtype_mismatch"),
                           ("/annotations/1/end", "scale_dtype_mismatch")]


def test_function_mark_shapes():
    spec = function_spec()
    spec.layers[0].data = "anything"
    assert codes(spec) == [("/layers/0", "function_source_conflict")]
    spec = FigureSpec(layers=[FunctionMark()])
    assert codes(spec) == [("/layers/0", "function_source_missing")]
    spec = FigureSpec(layers=[FunctionMark(expression="x**2", domain={"x": [1, 1]}, samples=1)])
    assert codes(spec) == [("/layers/0/domain/x", "invalid_domain"),
                           ("/layers/0/samples", "invalid_samples")]
    spec = FigureSpec(coordinate_system="cartesian3d",
                      layers=[FunctionMark(expression="x*y", domain={"x": [0, 1]})])
    assert codes(spec) == [("/layers/0/variables", "invalid_variables")]


def test_sampled_function_is_checked_like_data():
    spec = timeseries_spec()
    spec.layers.append(FunctionMark(data="observed", encoding={"x": "week"}, role="reference"))
    assert codes(spec) == [("/layers/2/encoding/y", "missing_channel")]


def test_dataset_ref_columns_are_checked_and_undeclared_ref_is_info_only():
    spec = histogram_ref_spec()
    spec.layers[0].encoding["x"].field = "latncy"
    assert codes(spec) == [("/layers/0/encoding/x/field", "unknown_column")]
    spec = histogram_ref_spec()
    spec.datasets["lat"].columns["latency"].unit = "ms"
    assert codes(spec) == [("/layers/0/encoding/x", "axis_unit_conflict")]
    spec = histogram_ref_spec()
    spec.datasets["lat"] = DatasetRef(kind="query", id="bookings.latency")
    assert codes(spec) == [("/layers/0/data", "unverified_reference")]
    assert codes(spec, "error") == []


def test_enum_fields_and_empty_figure():
    spec = timeseries_spec()
    spec.layers[1].role = "projection"
    spec.axes["y"].scale = "logarithmic"
    spec.legend.position = "middle"
    spec.coordinate_system = "cartesian"
    got = codes(spec)
    for expected in [("/coordinate_system", "invalid_value"), ("/axes/y/scale", "invalid_value"),
                     ("/legend/position", "invalid_value"), ("/layers/1/role", "invalid_value")]:
        assert expected in got
    assert codes(FigureSpec()) == [("/layers", "no_layers")]


def test_annotation_checks():
    spec = timeseries_spec()
    spec.annotations.append(ReferenceLine(axis="z", value=None))
    assert codes(spec) == [("/annotations/2/axis", "unknown_axis"),
                           ("/annotations/2/value", "missing_field")]


def test_unknown_axis_key():
    spec = timeseries_spec()
    spec.axes["z"] = Axis(label="depth")
    assert codes(spec) == [("/axes/z", "unknown_axis")]


@pytest.mark.parametrize("bins, edges, path", [(0, None, "/layers/0/bins"),
                                              (None, [1.0, 1.0], "/layers/0/bin_edges")])
def test_histogram_bins(bins, edges, path):
    spec = histogram_ref_spec()
    spec.layers[0].bins = bins
    spec.layers[0].bin_edges = edges
    assert codes(spec) == [(path, "invalid_bins")]


def test_heatmap_is_valid_with_partial_nulls():
    spec = misc_spec()
    assert isinstance(spec.layers[0], HeatmapMark)
    assert None in spec.datasets["grid"].columns["val"].values
    assert validate(spec) == []
