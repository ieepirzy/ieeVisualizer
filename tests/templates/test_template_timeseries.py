import json

import pytest

from ieeVisualizer.spec import (
    DataSnapshot,
    ReferenceLine,
    ValidationIssue,
    from_json,
    to_json,
    validate,
)
from ieeVisualizer.spec.data import dataset_to_dict
from ieeVisualizer.templates import TemplateInputError, TimeseriesTemplate, get

TS = get("timeseries")

WEEK_DATES = ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"]
# DataSnapshot stores datetimes in one canonical form (always with seconds).
WEEKS = [d + "T00:00:00" for d in WEEK_DATES]


def snap(columns, units=None, dtypes=None):
    return DataSnapshot.from_columns(columns, units=units, dtypes=dtypes)


def revenue(**extra):
    cols = {"week": WEEK_DATES, "revenue": [100.0, None, 120.0, 130.0, 140.0, 150.0]}
    cols.update(extra)
    return snap(cols, units={"revenue": "EUR"}, dtypes={"week": "datetime"})


def errors(issues):
    return [(i.path, i.code) for i in issues if i.severity == "error"]


def rows(spec, dataset, column="week"):
    return spec.datasets[dataset].columns[column].values


def test_example_builds_and_validates_clean():
    spec = TS.build_payload(TS.example())
    assert validate(spec) == []
    meta = spec.metadata["template"]
    assert (meta["name"], meta["version"]) == ("timeseries", TimeseriesTemplate.version)
    assert meta["params"]["forecast_from"] == "2026-10-05"
    assert meta["params"]["connect_forecast"] is True
    json.dumps(meta)  # serializable provenance
    assert from_json(to_json(spec)).metadata == spec.metadata  # survives the round trip


def test_single_series_layers_axes_and_units():
    spec = TS.build(revenue(), time_column="week", value_columns=["revenue"])
    assert [(m.type, m.data, m.role) for m in spec.layers] == [("line", "observed", "observed")]
    enc = spec.layers[0].encoding
    assert (enc["x"].field, enc["y"].field) == ("week", "revenue")
    assert spec.axes["x"].scale == "time" and spec.axes["x"].label == "week"
    assert (spec.axes["y"].label, spec.axes["y"].unit) == ("revenue", "EUR")
    assert list(spec.datasets) == ["observed"]
    assert "forecast" not in spec.metadata["template"]


def test_axis_labels_come_from_column_descriptions():
    data = revenue()
    data.columns["revenue"].description = "Net revenue"
    data.columns["week"].description = "ISO week"
    spec = TS.build(data, time_column="week", value_columns="revenue")
    assert (spec.axes["x"].label, spec.axes["y"].label) == ("ISO week", "Net revenue")
    spec = TS.build(data, time_column="week", value_columns="revenue", y_label="EUR net")
    assert spec.axes["y"].label == "EUR net"


def test_wide_format_one_layer_per_column():
    data = revenue(costs=[50.0, 60.0, 70.0, 80.0, 90.0, 95.0])
    data.columns["costs"].unit = "EUR"
    spec = TS.build(data, time_column="week", value_columns=["revenue", "costs"])
    assert [m.encoding["y"].field for m in spec.layers] == ["revenue", "costs"]
    assert [m.name for m in spec.layers] == ["revenue", "costs"]
    assert spec.axes["y"].label is None  # the legend names the series
    assert spec.axes["y"].unit == "EUR"


def test_long_format_uses_series_channel():
    data = snap({"t": [0, 1, 0, 1], "channel": ["web", "web", "phone", "phone"],
                 "leads": [3, 4, 1, 2]}, units={"t": "day", "leads": "count"})
    spec = TS.build(data, time_column="t", value_columns=["leads"], series_column="channel")
    (layer,) = spec.layers
    assert layer.encoding["series"].field == "channel"
    assert (spec.axes["x"].unit, spec.axes["x"].scale) == ("day", None)
    # rows were ordered by time (stable), series values travel with their rows
    assert rows(spec, "observed", "t") == [0, 0, 1, 1]
    assert rows(spec, "observed", "channel") == ["web", "phone", "web", "phone"]
    assert rows(spec, "observed", "leads") == [3, 1, 4, 2]


def test_forecast_from_partitions_rows_without_overlap():
    spec = TS.build(revenue(), time_column="week", value_columns=["revenue"],
                    forecast_from="2026-09-28")
    assert rows(spec, "observed") == WEEKS[:3]
    assert rows(spec, "forecast") == WEEKS[3:]  # split point is the first forecast instant
    assert not set(rows(spec, "observed")) & set(rows(spec, "forecast"))
    assert [(m.data, m.role) for m in spec.layers] == [
        ("observed", "observed"), ("forecast", "forecast")]
    assert spec.layers[1].style == {"linestyle": "dashed"}
    assert validate(spec) == []
    fc = spec.metadata["template"]["forecast"]
    assert fc == {"method": "forecast_from", "split": "2026-09-28", "observed_rows": 3,
                  "forecast_rows": 3, "shared_boundary": False}


def test_connect_forecast_shares_exactly_the_last_observed_point():
    spec = TS.build(revenue(), time_column="week", value_columns=["revenue"],
                    forecast_from="2026-09-28", connect_forecast=True)
    assert rows(spec, "observed") == WEEKS[:3]
    assert rows(spec, "forecast") == WEEKS[2:]
    assert set(rows(spec, "observed")) & set(rows(spec, "forecast")) == {WEEKS[2]}
    assert rows(spec, "forecast", "revenue")[0] == 120.0  # the observed value, unchanged
    fc = spec.metadata["template"]["forecast"]
    assert fc["shared_boundary"] is True and fc["forecast_rows"] == 3


def test_connect_forecast_per_series_in_long_format():
    data = snap({"t": [3, 1, 2, 1, 2, 3], "s": ["a", "a", "a", "b", "b", "b"],
                 "y": [30.0, 10.0, 20.0, 1.0, 2.0, 3.0]}, units={"t": "day", "y": "m"})
    spec = TS.build(data, time_column="t", value_columns="y", series_column="s",
                    forecast_from=3, connect_forecast=True)
    obs = list(zip(rows(spec, "observed", "t"), rows(spec, "observed", "s")))
    fc = list(zip(rows(spec, "forecast", "t"), rows(spec, "forecast", "s")))
    assert obs == [(1, "a"), (1, "b"), (2, "a"), (2, "b")]
    assert sorted(fc) == [(2, "a"), (2, "b"), (3, "a"), (3, "b")]
    assert sorted(set(obs) & set(fc)) == [(2, "a"), (2, "b")]


def test_role_column_str_and_bool():
    data = revenue(kind=["observed"] * 4 + ["forecast"] * 2)
    spec = TS.build(data, time_column="week", value_columns="revenue", role_column="kind")
    assert rows(spec, "forecast") == WEEKS[4:]
    assert "kind" not in spec.datasets["observed"].columns  # only drawn columns are copied
    data = revenue(is_fc=[False, False, False, False, True, True])
    spec = TS.build(data, time_column="week", value_columns="revenue", role_column="is_fc")
    assert rows(spec, "observed") == WEEKS[:4]
    assert rows(spec, "forecast") == WEEKS[4:]


def test_bad_role_values_are_reported():
    data = revenue(kind=["observed", "obs", "observed", "forecast", "fcst", "forecast"])
    issues = TS.check(data, time_column="week", value_columns="revenue", role_column="kind")
    assert errors(issues) == [("/data/columns/kind", "invalid_column_values")]
    assert "fcst" in issues[0].message and "obs" in issues[0].message


def test_no_band_layer_unless_bounds_supplied():
    for kwargs in ({}, {"forecast_from": "2026-09-28"}):
        spec = TS.build(revenue(), time_column="week", value_columns="revenue", **kwargs)
        assert all("bound" not in (m.name or "") for m in spec.layers)
        assert all(m.style.get("linestyle") != "dotted" for m in spec.layers)
        assert "bands" not in spec.metadata["template"]
        assert len(spec.layers) == (1 if not kwargs else 2)


def test_bounds_only_where_supplied_and_never_invented():
    lo = [None, None, None, 120.0, 128.0, 135.0]
    hi = [None, None, None, 140.0, 152.0, 165.0]
    data = revenue(lo=lo, hi=hi)
    data.columns["lo"].unit = data.columns["hi"].unit = "EUR"
    spec = TS.build(data, time_column="week", value_columns="revenue",
                    forecast_from="2026-09-28", lower_column="lo", upper_column="hi")
    names = [(m.data, m.role, m.encoding["y"].field) for m in spec.layers]
    # observed rows carry no bounds, so no observed bound lines are drawn
    assert names == [("observed", "observed", "revenue"), ("forecast", "forecast", "revenue"),
                     ("forecast", "forecast", "lo"), ("forecast", "forecast", "hi")]
    assert spec.datasets["forecast"].columns["lo"].values == [120.0, 128.0, 135.0]
    assert spec.datasets["observed"].columns["lo"].values == [None, None, None]
    assert spec.metadata["template"]["bands"] == [
        {"value_column": "revenue", "lower_column": "lo", "upper_column": "hi",
         "role": "forecast", "lower_layer": 2, "upper_layer": 3}]
    assert validate(spec) == []


def test_bounds_need_both_columns_and_same_unit():
    data = revenue(lo=[1.0] * 6, hi=[2.0] * 6)
    issues = TS.check(data, time_column="week", value_columns="revenue", lower_column="lo")
    assert errors(issues) == [("/params/upper_column", "missing_parameter")]
    data.columns["hi"].unit = "EUR"
    issues = TS.check(data, time_column="week", value_columns="revenue", lower_column="lo",
                      upper_column="hi")
    assert errors(issues) == [("/data/columns/lo", "mixed_units")]


def test_nulls_stay_null():
    spec = TS.build(revenue(), time_column="week", value_columns="revenue",
                    forecast_from="2026-10-05")
    assert rows(spec, "observed", "revenue") == [100.0, None, 120.0, 130.0]
    assert rows(spec, "forecast", "revenue") == [140.0, 150.0]


def test_input_snapshot_is_not_mutated():
    data = snap({"time": [1, 2, 3], "y": [1.0, None, 3.0]})
    before = dataset_to_dict(data)
    spec = TS.build(data, value_columns="y", y_unit="kg", forecast_from=2)
    assert dataset_to_dict(data) == before
    assert spec.metadata["template"]["input_hash"] == data.content_hash()


def test_y_unit_fills_a_missing_unit_only():
    data = snap({"time": [1, 2], "y": [1.0, 2.0]}, units={"time": "s"})
    spec = TS.build(data, value_columns="y", y_unit="kg")
    assert spec.axes["y"].unit == "kg"
    assert spec.datasets["observed"].columns["y"].unit == "kg"
    assert "warnings" not in spec.metadata["template"]

    spec = TS.build(revenue(), time_column="week", value_columns="revenue", y_unit="EUR")
    assert spec.axes["y"].unit == "EUR"  # agreeing override is a no-op

    with pytest.raises(TemplateInputError) as exc:
        TS.build(revenue(), time_column="week", value_columns="revenue", y_unit="USD")
    assert errors(exc.value.issues) == [("/params/y_unit", "unit_override_conflict")]
    assert "nothing is converted" in exc.value.issues[0].message


def test_missing_unit_is_a_recorded_warning():
    data = snap({"time": [1, 2], "y": [1.0, 2.0]})
    spec = TS.build(data, value_columns="y")
    warns = [(w["path"], w["code"]) for w in spec.metadata["template"]["warnings"]]
    assert warns == [("/params/y_unit", "missing_unit"), ("/data/columns/time", "missing_unit")]
    assert spec.axes["y"].unit is None


def test_mixed_units_refused():
    data = snap({"time": [1, 2], "a": [1.0, 2.0], "b": [3.0, 4.0]},
                units={"time": "s", "a": "EUR", "b": "USD"})
    assert errors(TS.check(data, value_columns=["a", "b"])) == [("/data/columns/a", "mixed_units")]
    data.columns["b"].unit = None
    assert errors(TS.check(data, value_columns=["a", "b"])) == [("/data/columns/b", "mixed_units")]


def test_structured_errors_missing_column_wrong_dtype_unknown_param():
    data = snap({"week": WEEKS, "revenue": [1.0] * 6, "label": list("abcdef")},
                dtypes={"week": "datetime"})
    with pytest.raises(TemplateInputError) as exc:
        TS.build(data, time_column="week", value_columns=["revenu", "label"], forecats_from=3)
    issues = exc.value.issues
    assert all(isinstance(i, ValidationIssue) for i in issues)
    assert errors(issues) == [
        ("/params/forecats_from", "unknown_parameter"),
        ("/params/value_columns", "missing_column"),
        ("/data/columns/label", "wrong_dtype"),
    ]
    msgs = [i.message for i in issues]
    assert "Did you mean 'forecast_from'?" in msgs[0]
    assert "Did you mean 'revenue'?" in msgs[1]
    assert "'str'" in msgs[2] and "float, int" in msgs[2]
    assert all(i.to_dict()["severity"] == "error" for i in issues)


def test_required_and_invalid_parameters():
    data = revenue()
    assert errors(TS.check(data, time_column="week")) == [
        ("/params/value_columns", "missing_parameter")]
    assert errors(TS.check(data, time_column="week", value_columns="revenue",
                           connect_forecast="yes")) == [
        ("/params/connect_forecast", "invalid_parameter")]
    kinds = revenue(kind=["observed"] * 6)
    assert errors(TS.check(kinds, time_column="week", value_columns="revenue",
                           role_column="kind", forecast_from="2026-01-01")) == [
        ("/params/forecast_from", "conflicting_parameters")]
    assert errors(TS.check(kinds, time_column="week", value_columns="revenue",
                           lower_column="revenue", upper_column="revenue")) == [
        ("/params/lower_column", "conflicting_parameters"),
        ("/params/upper_column", "conflicting_parameters")]
    assert errors(TS.check(data, time_column="week", value_columns="revenue",
                           forecast_from=5)) == [("/params/forecast_from", "invalid_parameter")]


def test_null_time_cannot_be_placed():
    data = snap({"time": [1, None, 3], "y": [1.0, 2.0, 3.0]}, units={"time": "s"})
    assert errors(TS.check(data, value_columns="y")) == [
        ("/data/columns/time", "null_in_key_column")]


def test_all_null_value_column_and_empty_data_are_errors():
    data = snap({"time": [1, 2], "y": [None, None]}, dtypes={"y": "float"})
    assert errors(TS.check(data, value_columns="y")) == [("/data/columns/y", "all_null_column")]
    data = snap({"time": [], "y": []}, dtypes={"time": "int", "y": "float"})
    assert errors(TS.check(data, value_columns="y")) == [("/data", "empty_dataset")]
    assert errors(TS.check(None, value_columns="y")) == [("/data", "missing_data")]


def test_empty_series_and_no_forecast_rows_warn():
    data = snap({"time": [1, 2, 3], "a": [1.0, 2.0, 3.0], "b": [1.0, 2.0, None]},
                units={"time": "s", "a": "m", "b": "m"})
    spec = TS.build(data, value_columns=["a", "b"], forecast_from=3)
    assert [(m.data, m.encoding["y"].field) for m in spec.layers] == [
        ("observed", "a"), ("observed", "b"), ("forecast", "a")]
    warns = [(w["path"], w["code"]) for w in spec.metadata["template"]["warnings"]]
    assert warns == [("/data/columns/b", "empty_series")]

    spec = TS.build(data, value_columns="a", forecast_from=10)
    assert list(spec.datasets) == ["observed"]
    assert [w["code"] for w in spec.metadata["template"]["warnings"]] == ["no_forecast_rows"]


def test_target_reference_line():
    spec = TS.build(revenue(), time_column="week", value_columns="revenue", target=125,
                    target_label="Goal")
    (ann,) = spec.annotations
    assert isinstance(ann, ReferenceLine)
    assert (ann.axis, ann.value, ann.label) == ("y", 125, "Goal")


def test_accepts_snapshot_dict_and_plain_mapping():
    payload = TS.example()
    assert isinstance(payload["data"], dict) and payload["data"]["type"] == "snapshot"
    spec = TS.build({"time": [1.0, 2.0], "y": [3.0, 4.0]}, value_columns="y", y_unit="m")
    assert spec.datasets["observed"].columns["y"].values == [3.0, 4.0]


def test_timezone_mixing_refused_and_z_suffix_accepted():
    data = snap({"t": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00+02:00"], "y": [1.0, 2.0]},
                units={"y": "m"}, dtypes={"t": "datetime"})
    # 2026-01-02T00:00+02:00 is 2026-01-01T22:00Z: compared as instants, not as strings
    spec = TS.build(data, time_column="t", value_columns="y",
                    forecast_from="2026-01-01T21:00:00+00:00")
    assert rows(spec, "forecast", "t") == ["2026-01-02T00:00:00+02:00"]
    spec = TS.build(data, time_column="t", value_columns="y",
                    forecast_from="2026-01-01T23:00:00+00:00")
    assert "forecast" not in spec.datasets
    data.columns["t"].values = ["2026-01-01T00:00:00", "2026-01-02T00:00:00+02:00"]
    # The spec's own datetime rules now refuse the mix when the snapshot is
    # copied; check() must report that as an issue, not raise.
    assert errors(TS.check(data, time_column="t", value_columns="y")) == [
        ("/data/columns/t/values", "invalid_data")]


def test_bounds_are_forecast_styled_and_paired_with_their_value_series():
    lo = [90.0, None, 110.0, 120.0, 128.0, 135.0]
    hi = [110.0, None, 130.0, 140.0, 152.0, 165.0]
    data = revenue(lo=lo, hi=hi, region=["N", "S", "N", "S", "N", "S"])
    data.columns["lo"].unit = data.columns["hi"].unit = "EUR"
    spec = TS.build(data, time_column="week", value_columns="revenue", series_column="region",
                    forecast_from="2026-09-28", lower_column="lo", upper_column="hi")
    by = [(m.data, m.role, m.encoding["y"].field, m.name, m.style.get("color_of"))
          for m in spec.layers]
    rng = "revenue range (lo to hi)"
    assert by == [
        ("observed", "observed", "revenue", "revenue", None),
        ("observed", "observed", "lo", rng, "revenue"),
        ("observed", "observed", "hi", rng, "revenue"),
        ("forecast", "forecast", "revenue", "revenue (forecast)", None),
        ("forecast", "forecast", "lo", rng, "revenue (forecast)"),
        ("forecast", "forecast", "hi", rng, "revenue (forecast)"),
    ]
    # every color_of names exactly one other layer, which draws the same series
    names = [m.name for m in spec.layers]
    for m in spec.layers:
        ref = m.style.get("color_of")
        if ref is not None:
            assert names.count(ref) == 1
            target = spec.layers[names.index(ref)]
            assert (target.data, target.role) == (m.data, m.role)
            assert target.encoding["series"].field == m.encoding["series"].field
    # forecast bounds are forecast-role layers; observed bounds are not
    assert {m.role for m in spec.layers if m.encoding["y"].field in ("lo", "hi")
            and m.data == "forecast"} == {"forecast"}
    assert [b["role"] for b in spec.metadata["template"]["bands"]] == ["observed", "forecast"]
    assert validate(spec) == []


@pytest.mark.parametrize("param", ["target", "forecast_from"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 10 ** 400],
                         ids=["nan", "inf", "-inf", "huge-int"])
def test_non_finite_numbers_are_refused_by_check_and_build(param, bad):
    data = snap({"t": [1.0, 2.0, 3.0], "v": [1.0, 2.0, 3.0]}, units={"t": "d", "v": "m"})
    kwargs = {"time_column": "t", "value_columns": ["v"], param: bad}
    assert errors(TS.check(data, **kwargs)) == [(f"/params/{param}", "invalid_parameter")]
    with pytest.raises(TemplateInputError) as e:
        TS.build(data, **kwargs)
    assert errors(e.value.issues) == [(f"/params/{param}", "invalid_parameter")]
