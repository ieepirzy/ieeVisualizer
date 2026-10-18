"""``sample_distribution`` (histogram + KDE, ECDF, quantile dotplot),
``fan_chart`` and the seeded ensembles of ``ieeVisualizer.stochastic``."""

from __future__ import annotations

import math
import random

import pytest

from ieeVisualizer import stochastic, templates
from ieeVisualizer.spec import validate
from ieeVisualizer.templates.sample_distribution import quantile7

S = templates.get("sample_distribution")
F = templates.get("fan_chart")


def sample(n=300, seed=4):
    rng = random.Random(seed)
    return {"v": [round(rng.gauss(50, 8), 2) for _ in range(n)]}


def errors(t, data, **params):
    return {(i.path, i.code) for i in t.check(data, **params) if i.severity == "error"}


# ---------------------------------------------------------------- KDE / hist


@pytest.mark.parametrize("method", ["scott", "silverman"])
def test_kde_bandwidth_is_recorded_and_stated(method):
    data = sample()
    xs = sorted(data["v"])
    n = len(xs)
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
    iqr = quantile7(xs, 0.75) - quantile7(xs, 0.25)
    want = sd * n ** -0.2 if method == "scott" else 0.9 * min(sd, iqr / 1.34) * n ** -0.2
    spec = S.build({"v": data["v"]}, value_column="v", bandwidth_method=method)
    kde = spec.metadata["template"]["kde"]
    assert kde["bandwidth"] == pytest.approx(want, rel=1e-9)
    assert kde["bandwidth_method"] == method and kde["kernel"] == "gaussian"
    shown = f"{want:.3g}"
    assert f"bandwidth h = {shown}" in spec.caption
    assert spec.layers[1].name == f"Kernel density estimate, h = {shown}"
    assert ("Scott's rule" if method == "scott" else "Silverman's rule") in spec.caption


def test_kde_is_the_gaussian_sum_and_integrates_to_one():
    data = sample(120)
    spec = S.build(data, value_column="v", bandwidth_method="fixed", bandwidth=3.0)
    cols = spec.datasets["kde"].columns
    gx, gy = cols["v"].values, cols["density"].values
    k = 100
    direct = sum(math.exp(-0.5 * ((gx[k] - x) / 3.0) ** 2) for x in data["v"]) / (
        len(data["v"]) * 3.0 * math.sqrt(2 * math.pi))
    assert gy[k] == pytest.approx(direct, rel=1e-9)
    area = sum((gx[i + 1] - gx[i]) * (gy[i] + gy[i + 1]) / 2 for i in range(len(gx) - 1))
    assert area == pytest.approx(1.0, abs=2e-3)
    assert spec.layers[0].density is True  # the histogram is on the same (density) scale
    assert spec.axes["y"].label == "Density"


def test_histogram_bins_follow_the_stated_rule():
    data = sample()
    spec = S.build(data, value_column="v", kde=False)
    hist = spec.metadata["template"]["histogram"]
    xs = sorted(data["v"])
    width = 2 * (quantile7(xs, 0.75) - quantile7(xs, 0.25)) * len(xs) ** (-1 / 3)
    assert hist["bins"] == math.ceil((xs[-1] - xs[0]) / width)
    assert spec.layers[0].bin_edges[0] == xs[0] and spec.layers[0].bin_edges[-1] == xs[-1]
    assert "Freedman–Diaconis" in spec.caption and len(spec.layers) == 1


@pytest.mark.parametrize("params,path,code", [
    ({"bandwidth_method": "fixed"}, "/params/bandwidth", "missing_parameter"),
    ({"bandwidth": 2.0}, "/params/bandwidth", "conflicting_parameters"),
    ({"bandwidth_method": "fixed", "bandwidth": -1.0}, "/params/bandwidth",
     "invalid_parameter"),
    ({"bins": 0}, "/params/bins", "invalid_parameter"),
    ({"display": "ecdf", "dots": 3}, "/params/dots", "invalid_parameter"),
    ({"group_column": "g"}, "/params/group_column", "conflicting_parameters"),
])
def test_sample_parameter_errors(params, path, code):
    data = dict(sample(), g=["a", "b"] * 150)
    assert (path, code) in errors(S, data, value_column="v", **params)


def test_degenerate_samples_are_refused_not_drawn():
    assert ("/data/columns/v", "invalid_column_values") in errors(S, {"v": [3.0] * 10},
                                                                  value_column="v")
    assert ("/data/columns/v", "invalid_column_values") in errors(
        S, {"v": [1.0, 2.0, 3.0]}, value_column="v", display="dotplot")
    big = {"v": [float(i % 97) for i in range(100_001)]}
    assert ("/data/columns/v", "invalid_data") in errors(S, big, value_column="v")


def test_null_samples_are_dropped_and_counted():
    data = sample(50)
    data["v"][3] = data["v"][9] = None
    spec = S.build(data, value_column="v")
    warns = spec.metadata["template"]["warnings"]
    assert [w["code"] for w in warns if w["code"] != "missing_unit"] == ["null_samples_dropped"]
    assert len(spec.datasets["samples"].columns["v"].values) == 48
    assert "n = 48" in spec.caption


# ---------------------------------------------------------------- ECDF


def test_ecdf_is_an_exact_step_function_per_group():
    rng = random.Random(2)
    data = {"v": [rng.random() for _ in range(30)], "g": ["a"] * 10 + ["b"] * 20}
    spec = S.build(data, value_column="v", group_column="g", display="ecdf")
    cols = spec.datasets["ecdf"].columns
    for g, n in (("a", 10), ("b", 20)):
        rows = [i for i, x in enumerate(cols["g"].values) if x == g]
        xs = [cols["v"].values[i] for i in rows]
        ys = [cols["share"].values[i] for i in rows]
        assert len(rows) == 2 * n and ys[0] == 0.0 and ys[-1] == 100.0
        assert xs == sorted(xs) and ys == sorted(ys)
        assert all(xs[2 * i] == xs[2 * i + 1] for i in range(n))  # vertical risers
        assert ys[1] == pytest.approx(100 / n)
    assert "Empirical CDFs, n = a: 10; b: 20." in spec.caption
    assert spec.layers[0].encoding["series"].field == "g"


# ---------------------------------------------------------------- dotplot


def test_quantile_dotplot_has_one_dot_per_quantile():
    data = sample(400)
    spec = S.build(data, value_column="v", display="dotplot", dots=25)
    meta = spec.metadata["template"]["dotplot"]
    xs = sorted(data["v"])
    assert meta["quantiles"] == [pytest.approx(quantile7(xs, (i + 0.5) / 25))
                                 for i in range(25)]
    dots = spec.datasets["dots"].columns
    assert len(dots["v"].values) == 25
    width = meta["column_width"]
    for x, q in zip(dots["v"].values, meta["quantiles"]):
        assert abs(x - q) <= width / 2 + 1e-9  # each dot within its own column
    assert "each dot is 4% of n = 400 values" in spec.caption
    assert spec.layers[0].name == "25 dots, each 4% of the sample"


# ---------------------------------------------------------------- stochastic


def test_simulations_are_seeded_reproducible_and_labelled_synthetic():
    a = stochastic.random_walk(paths=3, steps=4, seed=1)
    assert a.columns == stochastic.random_walk(paths=3, steps=4, seed=1).columns
    assert a.columns != stochastic.random_walk(paths=3, steps=4, seed=2).columns
    # pinned: the same on every Python >= 3.9 (random.Random, not numpy)
    assert a.columns["value"].values[:5] == [0.0, 0.6074558576, 0.5932304121, 1.824137641,
                                             2.839618808]
    extra = a.provenance.extra
    assert extra["synthetic"] is True and extra["seed"] == 1
    assert extra["generator"] == stochastic.GENERATOR
    assert "Synthetic" in a.provenance.notes and "seed 1" in a.provenance.notes
    g = stochastic.gbm(paths=50, steps=20, seed=3, s0=10.0, mu=0.05, sigma=0.3)
    assert min(g.columns["value"].values) > 0
    assert g.provenance.extra["parameters"] == {"s0": 10.0, "mu": 0.05, "sigma": 0.3,
                                                "paths": 50, "steps": 20, "dt": 1.0}


@pytest.mark.parametrize("kwargs", [{"paths": 0, "steps": 5}, {"paths": 5, "steps": 3000},
                                    {"paths": 2000, "steps": 200}, {"paths": 5, "steps": 5,
                                                                    "seed": None}])
def test_simulations_are_bounded(kwargs):
    kwargs.setdefault("seed", 1)
    with pytest.raises(ValueError):
        stochastic.random_walk(**kwargs)


# ---------------------------------------------------------------- fan chart


def test_ensemble_fan_bands_are_empirical_quantiles_with_kind_and_level():
    np = pytest.importorskip("numpy")
    spec = F.build_payload(F.example())
    assert [i for i in validate(spec) if i.severity == "error"] == []
    bands = [m for m in spec.layers if m.type == "band"]
    assert [(b.interval_kind, b.interval_level) for b in bands] == [
        ("quantile", 0.95), ("quantile", 0.8), ("quantile", 0.5)]
    assert all(m.role == "forecast" for m in spec.layers)
    ex = F.example()["data"]["columns"]
    t, v = ex["time"]["values"], ex["value"]["values"]
    fc = spec.datasets["forecast"].columns
    for k, time_ in enumerate(fc["time"].values):
        vals = [x for tt, x in zip(t, v) if tt == time_]
        assert fc["P2.5"].values[k] == pytest.approx(float(np.quantile(vals, 0.025)),
                                                     rel=1e-9)
        assert fc["Median of 200 paths"].values[k] == pytest.approx(
            float(np.median(vals)), rel=1e-9)
    meta = spec.metadata["template"]
    assert meta["ensemble"]["paths"] == 200 and "type 7" in meta["ensemble"]["quantile_method"]
    assert meta["timeseries"]["name"] == "timeseries"
    assert "Synthetic data: a seeded simulation (seed 7), not a measurement." in spec.caption


def test_a_band_thinner_than_one_path_is_refused():
    walk = stochastic.random_walk(paths=30, steps=5, seed=2)
    found = errors(F, walk, member_column="path", value_column="value", levels=[0.5, 0.95])
    assert ("/params/levels/1", "invalid_parameter") in found
    assert ("/params/levels/0", "invalid_parameter") not in found


def _given():
    return {"t": [0.0, 1.0, 2.0], "p05": [8.0, 7.0, 6.0], "p50": [10.0, 10.5, 11.0],
            "p95": [12.0, 14.0, 16.0], "p25": [9.0, 9.0, 8.5], "p80": [11.0, 12.0, 13.0]}


def test_given_quantiles_pair_from_the_outside_in():
    spec = F.build(_given(), time_column="t", interval_kind="quantile", role="observed",
                   quantile_columns={"p05": 0.05, "p25": 0.25, "p50": 0.5, "p80": 0.8,
                                     "p95": 0.95})
    bands = spec.metadata["template"]["bands"]
    assert [(b["lower_column"], b["upper_column"], b["level"], b["quantiles"])
            for b in bands] == [("p05", "p95", 0.9, [0.05, 0.95]),
                                ("p25", "p80", None, [0.25, 0.8])]
    layers = [m for m in spec.layers if m.type == "band"]
    assert [(m.interval_level, m.interval_quantiles) for m in layers] == [
        (0.9, None), (None, [0.25, 0.8])]
    assert "quantiles as supplied" in spec.caption


@pytest.mark.parametrize("change,path,code", [
    ({"quantile_columns": {"p05": 0.05, "p50": 0.5}}, "/params/quantile_columns",
     "conflicting_parameters"),
    ({"quantile_columns": {"p05": 0.05, "p95": 0.95}}, "/params/median_column",
     "missing_parameter"),
    ({"member_column": "m"}, "/params/member_column", "conflicting_parameters"),
    ({"quantile_columns": {"p05": 0.05, "p50": 0.5, "p95": 1.5}},
     "/params/quantile_columns/p95", "invalid_parameter"),
    ({"quantile_columns": {"p05": 0.05, "p50": 0.5, "p25": 0.95}}, "/data/columns/p05",
     "invalid_column_values"),
    ({"interval_kind": "prediction", "quantile_columns": {"p25": 0.25, "p50": 0.5,
                                                         "p80": 0.8}},
     "/params/quantile_columns/p25", "unexpected_interval_quantiles"),
])
def test_fan_chart_input_errors(change, path, code):
    params = {"time_column": "t", "quantile_columns": {"p05": 0.05, "p50": 0.5, "p95": 0.95}}
    params.update(change)
    assert (path, code) in errors(F, dict(_given(), m=["a", "b", "c"]), **params)
