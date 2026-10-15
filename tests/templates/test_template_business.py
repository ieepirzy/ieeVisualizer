"""bar_comparison and funnel: inputs checked, nothing fabricated, derived
rates transparent."""

import json

import pytest

from ieeVisualizer.render import build_figure
from ieeVisualizer.spec import DataSnapshot, validate
from ieeVisualizer.templates import TemplateInputError, get

BAR = get("bar_comparison")
FUNNEL = get("funnel")
S = " "


def errors(issues):
    return [(i.path, i.code) for i in issues if i.severity == "error"]


def warnings(issues):
    return [(i.path, i.code) for i in issues if i.severity == "warning"]


def channels(**extra):
    cols = {"channel": ["A", "B", "C"], "rev": [300.0, None, -50.0], "prev": [250.0, 80, 10]}
    cols.update(extra)
    return DataSnapshot.from_columns(cols, units={"rev": "EUR", "prev": "EUR"})


def funnel_data(counts, stages=None):
    stages = stages or [f"s{i}" for i in range(len(counts))]
    return DataSnapshot.from_columns({"stage": stages, "n": counts}, units={"n": "sessions"})


@pytest.mark.parametrize("t", [BAR, FUNNEL])
def test_examples_build_and_validate(t):
    spec = t.build_payload(json.loads(json.dumps(t.example())))
    assert validate(spec) == []
    assert spec.metadata["template"]["name"] == t.name
    assert spec.metadata["template"]["input_hash"].startswith("sha256:")


# ------------------------------------------------------------- bar_comparison


def test_bar_previous_period_is_a_distinct_labelled_reference_layer():
    spec = BAR.build(channels(), category_column="channel", value_columns=["rev"],
                     previous_column="prev", previous_label="September", orientation="vertical")
    cur, prev = spec.layers
    assert (cur.role, prev.role) == ("observed", "reference")
    assert prev.name == "rev, September" and cur.name == "rev"
    assert prev.style["bar_offset"] < 0 < cur.style["bar_offset"]
    assert spec.metadata["template"]["previous"] == {"column": "prev", "label": "September",
                                                     "role": "reference"}
    built = build_figure(spec)
    (cur_bars,) = [a for a in built.layer_artists[0] if hasattr(a, "patches")]
    (prev_bars,) = [a for a in built.layer_artists[1] if hasattr(a, "patches")]
    assert cur_bars.patches[0].get_facecolor() != prev_bars.patches[0].get_facecolor()
    assert prev_bars.patches[0].get_alpha() is not None
    labels = [t.get_text() for t in built.axes.get_legend().get_texts()]
    assert labels == ["rev", "rev, September"]


def test_bar_nulls_stay_null_and_negative_values_get_a_zero_line():
    spec = BAR.build(channels(), category_column="channel", value_columns=["rev"])
    assert spec.datasets["values"].columns["rev"].values == [300.0, None, -50.0]
    assert [a.value for a in spec.annotations] == [0.0]
    texts = [a.get_text() for a in build_figure(spec).layer_artists[0] if hasattr(a, "get_text")]
    assert "no data" in texts and f"−50.00{S}EUR" in texts


def test_bar_sort_orientation_and_reading_order():
    data = channels()
    spec = BAR.build(data, category_column="channel", value_columns=["rev"], sort="descending")
    assert spec.metadata["template"]["category_order"] == ["A", "C", "B"]  # null last
    assert spec.datasets["values"].columns["channel"].values == ["A", "C", "B"]
    spec = BAR.build(data, category_column="channel", value_columns=["rev"],
                     orientation="horizontal", sort="descending")
    assert spec.layers[0].orientation == "horizontal"
    assert spec.datasets["values"].columns["channel"].values == ["B", "C", "A"]  # A on top
    long = DataSnapshot.from_columns({"c": ["Organic search engines", "B"], "v": [1.0, 2.0]},
                                     units={"v": "EUR"})
    assert BAR.build(long, category_column="c", value_columns=["v"]).layers[0].orientation \
        == "horizontal"


def test_bar_several_measures_are_side_by_side():
    spec = BAR.build(channels(), category_column="channel", value_columns=["rev", "prev"])
    assert [m.style["bar_offset"] for m in spec.layers] == [-0.2, 0.2]
    assert all(m.role == "observed" for m in spec.layers)


@pytest.mark.parametrize("data, params, expected", [
    (channels(), {"value_columns": ["rev", "prev"], "previous_column": "prev"},
     ("/params/value_columns", "conflicting_parameters")),
    (channels(), {"value_columns": ["rev", "prev"], "previous_column": "rev"},
     ("/params/value_columns", "conflicting_parameters")),
    (channels(channel=["A", "A", "C"]), {"value_columns": ["rev"]},
     ("/data/columns/channel", "invalid_column_values")),
    (channels(channel=["A", None, "C"]), {"value_columns": ["rev"]},
     ("/data/columns/channel", "null_in_key_column")),
    (DataSnapshot.from_columns({"channel": ["A"], "rev": [1.0], "prev": [2.0]},
                               units={"rev": "EUR", "prev": "USD"}),
     {"value_columns": ["rev"], "previous_column": "prev"}, ("/data/columns/rev",
                                                             "mixed_units")),
    (channels(), {"value_columns": ["rev"], "theme": "muutto365-dark"},
     ("/params/theme", "invalid_parameter")),
    (channels(), {"value_columns": ["rev"], "sort": "up"}, ("/params/sort",
                                                            "invalid_parameter")),
])
def test_bar_errors(data, params, expected):
    issues = BAR.check(data, category_column="channel", **params)
    assert expected in errors(issues)
    with pytest.raises(TemplateInputError):
        BAR.build(data, category_column="channel", **params)


def test_bar_missing_unit_is_a_warning_and_input_is_not_mutated():
    data = DataSnapshot.from_columns({"channel": ["A", "B"], "v": [1.0, 2.0]})
    before = data.content_hash()
    spec = BAR.build(data, category_column="channel", value_columns=["v"], sort="descending")
    assert data.content_hash() == before
    assert ("/data/columns/v", "missing_unit") in [
        (w["path"], w["code"]) for w in spec.metadata["template"]["warnings"]]


# -------------------------------------------------------------------- funnel


def test_funnel_rates_and_denominators():
    spec = FUNNEL.build(funnel_data([1000, 250, 50]), count_column="n")
    d = spec.metadata["template"]["derived"]
    assert [(s["numerator"], s["denominator"]) for s in d["steps"]] == [(250, 1000), (50, 250)]
    assert [s["rate_percent"] for s in d["steps"]] == pytest.approx([25.0, 20.0])
    assert (d["overall"]["numerator"], d["overall"]["denominator"]) == (50, 1000)
    assert d["overall"]["rate_percent"] == pytest.approx(5.0)
    col = spec.datasets["stages"].columns["step_conversion"]
    assert col.unit == "%" and "previous stage" in col.denominator
    # drawn top-down: dataset rows reversed, rates aligned with their stage
    assert spec.datasets["stages"].columns["stage"].values == ["s2", "s1", "s0"]
    assert col.values[2] is None and col.values[:2] == pytest.approx([20.0, 25.0])
    assert [a.text for a in spec.annotations] == ["25.0% of previous", "20.0% of previous"]
    assert "5.0% (50 of 1,000)" in spec.caption


def test_funnel_zero_count_stage_gives_null_rate_not_zero_or_infinity():
    spec = FUNNEL.build(funnel_data([0, 0, 0]), count_column="n")
    d = spec.metadata["template"]["derived"]
    assert [s["rate_percent"] for s in d["steps"]] == [None, None]
    assert d["overall"]["rate_percent"] is None
    assert spec.datasets["stages"].columns["step_conversion"].values == [None, None, None]
    assert all("undefined" in a.text for a in spec.annotations)
    assert "Overall conversion undefined" in spec.caption
    spec = FUNNEL.build(funnel_data([10, 0, 0]), count_column="n")
    steps = spec.metadata["template"]["derived"]["steps"]
    assert [s["rate_percent"] for s in steps] == [0.0, None]  # 0/10 is a real 0 %
    json.dumps(spec.metadata)  # no inf/nan anywhere


def test_funnel_rising_counts_warn_and_are_not_corrected():
    issues = FUNNEL.check(funnel_data([100, 120, 30]), count_column="n")
    assert errors(issues) == []
    assert warnings(issues) == [("/data/columns/n", "non_monotone_counts")]
    spec = FUNNEL.build(funnel_data([100, 120, 30]), count_column="n")
    assert spec.metadata["template"]["derived"]["steps"][0]["rate_percent"] == 120.0


@pytest.mark.parametrize("data, expected", [
    (funnel_data([100, None, 3]), ("/data/columns/n", "invalid_column_values")),
    (funnel_data([100, -1]), ("/data/columns/n", "invalid_column_values")),
    (funnel_data([100]), ("/data", "invalid_data")),
    (funnel_data([100, 50], stages=["a", "a"]), ("/data/columns/stage",
                                                 "invalid_column_values")),
    (funnel_data([100, 50], stages=["a", None]), ("/data/columns/stage",
                                                  "null_in_key_column")),
])
def test_funnel_errors(data, expected):
    assert expected in errors(FUNNEL.check(data, count_column="n"))
    with pytest.raises(TemplateInputError):
        FUNNEL.build(data, count_column="n")


def test_funnel_text_uses_the_theme_locale():
    spec = FUNNEL.build(funnel_data([12480, 2315]), count_column="n",
                        theme="muutto365-report-light")
    assert spec.theme == "muutto365-report-light"
    assert spec.annotations[0].text == f"18,5{S}% of previous"
    assert f"(2{S}315 of 12{S}480)" in spec.caption
    assert spec.metadata["template"]["label_locale"] == "fi-FI"
    built = build_figure(spec)
    texts = [a.get_text() for a in built.layer_artists[0] if hasattr(a, "get_text")]
    assert texts == [f"2{S}315", f"12{S}480"]  # value labels, bottom bar first
