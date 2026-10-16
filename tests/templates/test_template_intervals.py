"""timeseries 2.0.0 (intervals, fan charts) and estimate_comparison: intervals
come only from the caller's columns and always state their kind and level."""

import pytest

from ieeVisualizer import templates
from ieeVisualizer.render.mpl.adapter import build_figure
from ieeVisualizer.spec import validate

WEEKS = ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05"]
DATA = {"week": WEEKS, "orders": [200.0, 210.0, None, 215.0, 220.0],
        "p10": [None, None, None, 190.0, 192.0], "p90": [None, None, None, 236.0, 250.0],
        "p25": [None, None, None, 205.0, 207.0], "p75": [None, None, None, 225.0, 232.0]}
UNITS = {c: "orders" for c in ("orders", "p10", "p90", "p25", "p75")}
P80 = {"lower": "p10", "upper": "p90", "kind": "prediction", "level": 0.8}
P50 = {"lower": "p25", "upper": "p75", "kind": "prediction", "level": 0.5}


def data(**over):
    from ieeVisualizer.spec import DataSnapshot

    cols = dict(DATA, **over)
    return DataSnapshot.from_columns(cols, units={k: v for k, v in UNITS.items() if k in cols},
                                     dtypes={"week": "datetime"})


def ts(**params):
    params = dict({"time_column": "week", "value_columns": ["orders"],
                   "forecast_from": "2026-09-28"}, **params)
    return templates.get("timeseries", "2.0.0").build(data(), **params)


def issues(name, payload_data, **params):
    return [(i.path, i.code) for i in templates.get(name).check(payload_data, **params)
            if i.severity == "error"]


def test_timeseries_latest_is_2_0_and_1_0_is_still_there():
    assert templates.get("timeseries").version == "2.0.0"
    assert templates.default_registry.versions("timeseries") == ["1.0.0", "2.0.0"]
    old = templates.get("timeseries", "1.0.0")
    assert old.parameter("lower_column") is not None and old.parameter("intervals") is None


def test_fan_chart_bands_widest_first_before_the_line_and_forecast_styled():
    spec = ts(intervals=[P50, P80])  # given narrow first
    assert validate(spec) == []
    kinds = [(m.type, m.role, getattr(m, "interval_level", None)) for m in spec.layers]
    assert kinds == [("line", "observed", None), ("band", "forecast", 0.8),
                     ("band", "forecast", 0.5), ("line", "forecast", None)]
    for m in spec.layers[1:3]:
        assert m.interval_kind == "prediction"
        assert m.style == {"color_of": "orders (forecast)"}
    meta = spec.metadata["template"]
    assert meta["version"] == "2.0.0"
    assert [(b["interval"], b["layer"]) for b in meta["intervals"]] == [(1, 1), (0, 2)]
    labels = [t.get_text() for t in build_figure(spec).axes.get_legend().get_texts()]
    assert "80 % prediction interval (forecast)" in labels
    assert "50 % prediction interval (forecast)" in labels


def test_bounds_are_copied_never_computed():
    spec = ts(intervals=[P80])
    fc = spec.datasets["forecast"].columns
    assert fc["p10"].values == [190.0, 192.0] and fc["p90"].values == [236.0, 250.0]
    assert "observed" in spec.datasets and not any(
        m.type == "band" and m.data == "observed" for m in spec.layers)  # no bounds there


@pytest.mark.parametrize("interval, path, code", [
    ({"lower": "p10", "upper": "p90"}, "/params/intervals/0/kind", "missing_interval_kind"),
    ({"lower": "p10", "upper": "p90", "kind": "prediction"}, "/params/intervals/0/level",
     "missing_interval_level"),
    (dict(P80, level=80), "/params/intervals/0/level", "invalid_interval_level"),
    (dict(P80, kind="likely"), "/params/intervals/0/kind", "invalid_interval_kind"),
    (dict(P80, kind="min-max"), "/params/intervals/0/level", "unexpected_interval_level"),
    ({"lower": "p10", "upper": "p90", "kind": "standard-error"},
     "/params/intervals/0/multiplier", "missing_interval_multiplier"),
    ({"upper": "p90", "kind": "min-max"}, "/params/intervals/0/lower", "missing_parameter"),
    (dict(P80, lowr="p10"), "/params/intervals/0/lowr", "invalid_parameter"),
    (dict(P80, lower="p11"), "/params/intervals/0/lower", "missing_column"),
])
def test_timeseries_interval_errors_point_at_the_parameter(interval, path, code):
    found = issues("timeseries", data(), time_column="week", value_columns=["orders"],
                   intervals=[interval])
    assert (path, code) in found, found


def test_timeseries_interval_data_errors():
    common = {"time_column": "week", "value_columns": ["orders"], "intervals": [P80]}
    bad = data(p10=[None, None, None, 240.0, 192.0])
    assert ("/data/columns/p10", "invalid_column_values") in issues("timeseries", bad, **common)
    from ieeVisualizer.spec import DataSnapshot

    snap = DataSnapshot.from_columns(DATA, units=dict(UNITS, p90="kg"),
                                     dtypes={"week": "datetime"})
    assert ("/data/columns/orders", "mixed_units") in issues("timeseries", snap, **common)
    two = issues("timeseries", data(), time_column="week", value_columns=["orders", "p25"],
                 intervals=[P80])
    assert ("/params/intervals", "conflicting_parameters") in two


def test_old_bound_parameters_are_refused_with_a_way_forward():
    (issue,) = [i for i in templates.get("timeseries").check(
        data(), time_column="week", value_columns=["orders"], lower_column="p10",
        upper_column="p90") if i.path == "/params/lower_column"]
    assert issue.code == "unknown_parameter"
    assert "intervals=" in issue.message and "1.0.0" in issue.message


def test_estimate_comparison_example_draws_error_bars_with_their_statement():
    t = templates.get("estimate_comparison")
    spec = t.build_payload(t.example())
    assert validate(spec) == []
    (layer,) = spec.layers
    assert (layer.type, layer.interval_kind, layer.interval_level) == (
        "errorbar", "confidence", 0.95)
    assert spec.axes["x"].scale == "category"
    built = build_figure(spec)
    labels = [x.get_text() for x in built.axes.get_legend().get_texts()]
    assert labels == ["95 % confidence interval", "No difference"]


ARMS = {"arm": ["B", "C"], "d": [1.0, 0.5], "lo": [0.2, -0.4], "hi": [1.8, 1.3]}


def arms(**over):
    from ieeVisualizer.spec import DataSnapshot

    cols = dict(ARMS, **over)
    return DataSnapshot.from_columns(cols, units={"d": "pp", "lo": "pp", "hi": "pp"})


BASE = {"group_column": "arm", "estimate_column": "d", "lower_column": "lo",
        "upper_column": "hi", "interval_kind": "confidence", "interval_level": 0.95}


@pytest.mark.parametrize("over, data_over, path, code", [
    ({"interval_kind": None}, {}, "/params/interval_kind", "missing_parameter"),
    ({"interval_level": None}, {}, "/params/interval_level", "missing_interval_level"),
    ({"interval_level": 95}, {}, "/params/interval_level", "invalid_interval_level"),
    ({"interval_kind": "standard-error", "interval_level": None}, {},
     "/params/interval_multiplier", "missing_interval_multiplier"),
    ({"interval_kind": "sd"}, {}, "/params/interval_kind", "invalid_parameter"),
    ({}, {"lo": [1.9, -0.4]}, "/data/columns/lo", "invalid_column_values"),
    ({}, {"arm": ["B", "B"]}, "/data/columns/arm", "invalid_column_values"),
])
def test_estimate_comparison_errors(over, data_over, path, code):
    params = {k: v for k, v in dict(BASE, **over).items() if v is not None}
    assert (path, code) in issues("estimate_comparison", arms(**data_over), **params)


def test_estimate_comparison_bounds_share_the_estimate_unit():
    from ieeVisualizer.spec import DataSnapshot

    snap = DataSnapshot.from_columns(ARMS, units={"d": "pp", "lo": "%", "hi": "pp"})
    assert ("/data/columns/lo", "mixed_units") in issues("estimate_comparison", snap, **BASE)


def test_timeseries_interval_with_explicit_quantiles():
    iv = {"lower": "p10", "upper": "p75", "kind": "quantile", "quantiles": [0.1, 0.75]}
    spec = ts(intervals=[iv])
    (band,) = [m for m in spec.layers if m.type == "band"]
    assert band.interval_quantiles == [0.1, 0.75] and band.interval_level is None
    bad = issues("timeseries", data(), time_column="week", value_columns=["orders"],
                 intervals=[dict(iv, level=0.65)])
    assert ("/params/intervals/0/quantiles", "conflicting_interval_level") in bad
