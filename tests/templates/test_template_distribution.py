"""The ``distribution`` template: closed forms, intervals that say what they
are, discrete families as bars, pointered parameter errors, bounded time."""

from __future__ import annotations

import math
import time

import pytest

from ieeVisualizer import templates
from ieeVisualizer.spec import validate
from ieeVisualizer.templates import TemplateInputError
from ieeVisualizer.templates._distributions import FAMILIES, Dist, betainc, gammainc

T = templates.get("distribution")


def build(**params):
    return T.build(**params)


def errors(**params):
    return {(i.path, i.code) for i in T.check(**params) if i.severity == "error"}


def one(family, **params):
    return [{"family": family, "params": params}]


CASES = [("normal", {"mu": 1.5, "sigma": 2.0}), ("lognormal", {"mu": 0.3, "sigma": 0.6}),
         ("beta", {"alpha": 2.5, "beta": 4.0}), ("gamma", {"shape": 3.0, "scale": 1.5}),
         ("student_t", {"df": 4.0, "loc": 1.0, "scale": 2.0}),
         ("poisson", {"lambda": 3.5}), ("binomial", {"n": 15, "p": 0.3})]


def test_example_builds_and_says_the_curves_are_theoretical():
    spec = T.build_payload(T.example())
    assert [i for i in validate(spec) if i.severity == "error"] == []
    meta = spec.metadata["template"]
    assert meta["name"] == "distribution" and meta["version"] == "1.0.0"
    assert [d["formula"] for d in meta["distributions"]] == [
        FAMILIES["lognormal"].formula, FAMILIES["gamma"].formula]
    assert "Theoretical distributions from closed-form formulas, not data." in spec.caption
    for ds in spec.datasets.values():
        assert "not a measurement" in ds.provenance.notes


@pytest.mark.parametrize("family,params", CASES)
def test_density_is_the_recorded_closed_form_and_matches_sympy_stats(family, params):
    sp = pytest.importorskip("sympy")
    from sympy import stats

    f = FAMILIES[family]
    d = Dist(f, {**{p.name: p.default for p in f.params if p.default is not None}, **params})
    var = "k" if f.discrete else "x"
    sym = {n: sp.Symbol("lamda" if n == "lambda" else n) for n in d.p}  # lambda: a keyword
    expr = sp.sympify(f.formula, locals={s.name: s for s in sym.values()} | {var: sp.Symbol(var)})
    rv = {"normal": lambda p: stats.Normal("X", p["mu"], p["sigma"]),
          "lognormal": lambda p: stats.LogNormal("X", p["mu"], p["sigma"]),
          "beta": lambda p: stats.Beta("X", p["alpha"], p["beta"]),
          "gamma": lambda p: stats.Gamma("X", p["shape"], p["scale"]),
          "student_t": lambda p: p["loc"] + p["scale"] * stats.StudentT("X", p["df"]),
          "poisson": lambda p: stats.Poisson("X", p["lambda"]),
          "binomial": lambda p: stats.Binomial("X", int(p["n"]), p["p"])}[family](d.p)
    points = [0, 1, 2, 4, 7] if f.discrete else [
        d.quantile(q) for q in (0.05, 0.3, 0.5, 0.8, 0.97)]
    for x in points:
        formula = float(expr.subs({sym[n]: v for n, v in d.p.items()} | {sp.Symbol(var): x}))
        if family == "student_t":  # a location-scale t: density of (x - loc)/scale, / scale
            ref = float(stats.density(stats.StudentT("X", d.p["df"]))(
                (x - d.p["loc"]) / d.p["scale"])) / d.p["scale"]
        else:
            ref = float(stats.density(rv)(x))
        assert d.density(x) == pytest.approx(ref, rel=1e-10)
        assert formula == pytest.approx(ref, rel=1e-10)


def test_incomplete_beta_and_gamma_match_mpmath():
    mpmath = pytest.importorskip("mpmath")
    import random

    rng = random.Random(5)
    for _ in range(200):
        a, b, x = rng.uniform(0.2, 40), rng.uniform(0.2, 40), rng.random()
        assert betainc(a, b, x) == pytest.approx(
            float(mpmath.betainc(a, b, 0, x, regularized=True)), abs=1e-12)
        a, x = rng.uniform(0.2, 60), rng.uniform(0, 120)
        assert gammainc(a, x) == pytest.approx(
            float(mpmath.gammainc(a, 0, x, regularized=True)), abs=1e-12)


@pytest.mark.parametrize("family,params,q,want", [
    ("normal", {"mu": 0, "sigma": 1}, 0.975, 1.959963985),
    ("student_t", {"df": 3}, 0.975, 3.182446305),
    ("gamma", {"shape": 2, "scale": 3}, 0.5, 5.035040970),
    ("beta", {"alpha": 2, "beta": 5}, 0.5, 0.264449983),
    ("lognormal", {"mu": 0, "sigma": 1}, 0.95, 5.180251602),
    ("poisson", {"lambda": 4}, 0.95, 8), ("binomial", {"n": 20, "p": 0.3}, 0.05, 3)])
def test_quantiles(family, params, q, want):
    f = FAMILIES[family]
    d = Dist(f, {**{p.name: p.default for p in f.params if p.default is not None}, **params})
    assert d.quantile(q) == pytest.approx(want, rel=1e-8)
    if not f.discrete:
        assert d.cdf(d.quantile(q)) == pytest.approx(q, abs=1e-12)


@pytest.mark.parametrize("item,path,code", [
    (one("normal", mu=0, sigma=0), "/params/distributions/0/params/sigma", "invalid_parameter"),
    (one("normal", mu=0, sigma=-1), "/params/distributions/0/params/sigma", "invalid_parameter"),
    (one("binomial", n=10, p=1.5), "/params/distributions/0/params/p", "invalid_parameter"),
    (one("binomial", n=2.5, p=0.5), "/params/distributions/0/params/n", "invalid_parameter"),
    (one("beta", alpha=0, beta=1), "/params/distributions/0/params/alpha", "invalid_parameter"),
    (one("poisson"), "/params/distributions/0/params/lambda", "missing_parameter"),
    (one("gamma", shape=2, scale=1, rate=3), "/params/distributions/0/params/rate",
     "unknown_parameter"),
    ([{"family": "nromal", "params": {}}], "/params/distributions/0/family",
     "invalid_parameter"),
    ([{"params": {}}], "/params/distributions/0/family", "missing_parameter"),
    ([{"family": "normal", "params": {"mu": 0, "sigma": 1}, "colour": "red"}],
     "/params/distributions/0/colour", "invalid_parameter"),
])
def test_parameter_errors_point_at_the_value(item, path, code):
    assert (path, code) in errors(distributions=item)
    with pytest.raises(TemplateInputError):
        build(distributions=item)


def test_two_curves_with_one_name_are_refused():
    same = one("normal", mu=0, sigma=1) * 2
    assert ("/params/distributions", "invalid_parameter") in errors(distributions=same)


def test_misspelt_family_offers_the_right_one():
    msg = [i.message for i in T.check(distributions=[{"family": "nromal", "params": {}}])]
    assert any("'normal'" in m for m in msg)


@pytest.mark.parametrize("view", ["pdf", "cdf"])
def test_discrete_families_are_bars_never_lines(view):
    spec = build(view=view, distributions=one("poisson", **{"lambda": 3.0})
                 + one("binomial", n=8, p=0.4))
    assert {m.type for m in spec.layers} == {"bar"}
    for ds in spec.datasets.values():
        assert ds.columns["x"].dtype == "int"
        assert all(float(v).is_integer() for v in ds.columns["x"].values)


def test_central_interval_is_the_area_between_the_quantiles_and_says_so():
    spec = build(distributions=one("normal", mu=10, sigma=2),
                 interval={"kind": "quantile", "level": 0.9})
    area = next(m for m in spec.layers if m.type == "area")
    assert area.name == "Central 90% of the distribution (P5–P95)"
    xs = spec.datasets[area.data].columns["x"].values
    assert xs[0] == pytest.approx(10 - 2 * 1.6448536, abs=1e-6)
    assert xs[-1] == pytest.approx(10 + 2 * 1.6448536, abs=1e-6)
    assert spec.metadata["template"]["interval"] == {
        "kind": "quantile", "level": 0.9, "quantiles": [pytest.approx(0.05),
                                                        pytest.approx(0.95)],
        "label": "central 90% of the distribution (P5–P95)"}
    assert [m.type for m in spec.layers] == ["area", "line"]  # the curve drawn on top


def test_explicit_quantiles_and_credible_labels():
    a = build(distributions=one("normal", mu=0, sigma=1),
              interval={"kind": "quantile", "quantiles": [0.05, 0.75]})
    assert a.layers[0].name == "P5–P75 of the distribution (70%)"
    b = build(distributions=one("beta", alpha=3, beta=9),
              interval={"kind": "credible", "level": 0.95}, theme="report-light")
    assert b.layers[0].name == "95 % credible interval (central, P2,5–P97,5)"


def test_a_discrete_interval_states_what_it_really_covers():
    spec = build(distributions=one("poisson", **{"lambda": 6.5}),
                 interval={"kind": "quantile", "level": 0.9})
    band = spec.annotations[0]
    entry = spec.metadata["template"]["distributions"][0]["interval"]
    assert (band.start, band.end) == (entry["lower"] - 0.5, entry["upper"] + 0.5)
    assert entry["coverage"] >= 0.9
    d = Dist(FAMILIES["poisson"], {"lambda": 6.5})
    assert entry["coverage"] == pytest.approx(d.cdf(entry["upper"]) - d.cdf(entry["lower"] - 1))
    assert band.label.endswith(f"; 3–11 covers {entry['coverage'] * 100:.1f}%")


@pytest.mark.parametrize("interval,path,code", [
    ({"kind": "confidence", "level": 0.9}, "/params/interval/kind", "invalid_interval_kind"),
    ({"kind": "standard-error"}, "/params/interval/kind", "invalid_interval_kind"),
    ({"kind": "quantile"}, "/params/interval/level", "missing_interval_level"),
    ({"kind": "quantile", "level": 90}, "/params/interval/level", "invalid_interval_level"),
    ({"kind": "credible", "quantiles": [0.1, 0.6]}, "/params/interval/quantiles",
     "unexpected_interval_quantiles"),
    ({"kind": "quantile", "level": 0.9, "width": 2}, "/params/interval/width",
     "invalid_parameter"),
])
def test_interval_must_state_kind_and_level(interval, path, code):
    assert (path, code) in errors(distributions=one("normal", mu=0, sigma=1), interval=interval)


def test_comparisons_that_could_not_be_told_apart_are_refused():
    cont = one("normal", mu=0, sigma=1) + one("normal", mu=1, sigma=1)
    disc = one("poisson", **{"lambda": 2.0}) + one("poisson", **{"lambda": 4.0})
    iv = {"kind": "quantile", "level": 0.8}
    assert ("/params/distributions", "conflicting_parameters") in errors(
        distributions=one("normal", mu=0, sigma=1) + one("poisson", **{"lambda": 2.0}))
    assert ("/params/interval", "conflicting_parameters") in errors(distributions=disc,
                                                                    interval=iv)
    assert ("/params/interval", "conflicting_parameters") in errors(distributions=cont,
                                                                    interval=iv, view="cdf")
    assert ("/params/quantile_markers", "conflicting_parameters") in errors(
        distributions=disc, quantile_markers=[0.5])
    spec = build(distributions=cont, interval=iv)  # PDFs: one area per curve, in its colour
    areas = [m for m in spec.layers if m.type == "area"]
    assert [a.style["color_of"] for a in areas] == ["Normal(μ = 0, σ = 1)",
                                                    "Normal(μ = 1, σ = 1)"]


def test_too_many_bars_and_extreme_parameters_are_refused_quickly():
    assert ("/params/distributions", "invalid_parameter") in errors(
        distributions=one("poisson", **{"lambda": 5000.0}))
    assert ("/params/distributions/0/params/alpha", "invalid_parameter") in errors(
        distributions=one("beta", alpha=1e7, beta=1))
    start = time.perf_counter()
    for item in (one("binomial", n=1000000, p=0.5), one("poisson", **{"lambda": 1e6}),
                 one("beta", alpha=1e6, beta=1e6), one("gamma", shape=1e6, scale=1),
                 one("student_t", df=0.01)):
        T.check(distributions=item, view="cdf")
    assert time.perf_counter() - start < 10


def test_quantile_markers_sit_on_the_curve_and_their_values_are_written():
    spec = build(distributions=one("normal", mu=0, sigma=1), view="cdf",
                 quantile_markers=[0.95, 0.5, 0.05], x_unit="kg")
    pts = spec.datasets["quantiles_0"].columns
    assert pts["y"].values == [0.05, 0.5, 0.95]
    assert pts["x"].values[1] == 0.0  # bisection noise is not written as -1e-17
    assert "P5 = −1.645 kg; P50 = 0 kg; P95 = 1.645 kg." in spec.caption
    fi = build(distributions=one("normal", mu=0, sigma=1), quantile_markers=[0.025],
               theme="report-light")
    assert "P2,5 = −1,96." in fi.caption
    assert fi.metadata["template"]["label_locale"] == "fi-FI"


def test_an_unbounded_density_runs_off_the_plot_and_the_caption_says_so():
    spec = build(distributions=one("gamma", shape=0.5, scale=1.0))
    ys = spec.datasets["dist_0"].columns["density"].values
    assert None not in ys and max(ys) > spec.axes["y"].limits[1]
    assert "unbounded at x = 0" in spec.caption
    assert spec.metadata["template"]["distributions"][0]["density_unbounded_at"] == [0.0]


def test_a_narrow_x_range_says_how_much_it_shows():
    spec = build(distributions=one("normal", mu=0, sigma=1), x_range=[-1, 1])
    assert "holds 68.3% of its probability" in spec.caption
    assert "holds" not in build(distributions=one("normal", mu=0, sigma=1)).caption


def test_parameter_labels_use_the_theme_locale():
    spec = build(distributions=one("normal", mu=1.5, sigma=0.25), theme="report-light")
    assert spec.layers[0].name == "Normal(μ = 1,5; σ = 0,25)"


def test_exact_values_are_reproducible():
    spec = build(distributions=one("normal", mu=0, sigma=1), samples=21)
    ys = spec.datasets["dist_0"].columns["density"].values
    assert ys[10] == float(f"{1 / math.sqrt(2 * math.pi):.10g}")
    assert build(distributions=one("normal", mu=0, sigma=1), samples=21).datasets == \
        spec.datasets
