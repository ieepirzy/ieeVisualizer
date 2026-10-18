"""Distributions, sample distributions and fan charts as drawn: legible on a
phone in English and Finnish, discrete families drawn as bars, every
interval stated in the legend with its kind and level."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from matplotlib.container import BarContainer
from matplotlib.lines import Line2D

from ieeVisualizer import templates
from ieeVisualizer.render import RenderRequest, build_figure, check_legible, get_profile, render

NEW = ("distribution", "sample_distribution", "fan_chart")
PHONE = get_profile("email-phone").figure_request("png")
EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"


def _gallery():
    spec = importlib.util.spec_from_file_location("gallery_dist", EXAMPLES / "gallery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def example(name, theme=None):
    t = templates.get(name)
    payload = t.example()
    if theme:
        payload["params"] = dict(payload["params"], theme=theme)
    return t.build_payload(payload)


@pytest.mark.parametrize("theme", [None, "report-light"])
@pytest.mark.parametrize("name", NEW)
def test_examples_pass_the_phone_gate_in_english_and_finnish(name, theme):
    spec = example(name, theme)
    check_legible(render(spec, PHONE))
    if theme:  # template text in the theme's locale: decimal comma, no-break space before %
        assert spec.metadata["template"]["label_locale"] == "fi-FI"


def test_finnish_labels_are_what_the_phone_shows():
    built = build_figure(example("distribution", "report-light"), PHONE)
    texts = [t.get_text().replace("\n", " ") for t in built.axes.get_legend().get_texts()]
    assert "Model A (log-normal): central 90 % of the distribution (P5–P95)" in texts
    assert "P50 = 2,718 days." in built.caption.get_text().replace("\n", " ")


@pytest.mark.parametrize("name", ["dist-pmf-comparison", "dist-pmf-interval",
                                  "dist-cdf-markers", "dist-beta-unbounded", "sample-dotplot",
                                  "sample-ecdf-groups", "fan-chart-quantiles",
                                  "fan-chart-random-walk"])
def test_gallery_entries_pass_the_phone_gate(name):
    spec = _gallery().distribution_specs()[name]
    check_legible(render(spec, PHONE))
    spec.theme = "report-light"
    check_legible(render(spec, PHONE))


@pytest.mark.parametrize("view", ["pdf", "cdf"])
def test_a_discrete_distribution_is_drawn_as_bars_not_a_line(view):
    spec = templates.get("distribution").build(
        view=view, distributions=[{"family": "binomial", "params": {"n": 10, "p": 0.4}}])
    built = build_figure(spec, RenderRequest(format="png"))
    arts = built.layer_artists[0]
    bars = len(spec.datasets["dist_0"].columns["x"].values)
    assert bars >= 9 and any(isinstance(a, BarContainer) and len(a.patches) == bars
                             for a in arts)
    assert not any(isinstance(a, Line2D) for a in arts)
    assert not [ln for ln in built.axes.get_lines() if len(ln.get_xdata()) > 2]


def test_every_band_states_its_kind_and_level_in_the_legend():
    built = build_figure(example("fan_chart"), RenderRequest(format="png"))
    texts = [t.get_text() for t in built.axes.get_legend().get_texts()]
    assert texts == ["P2.5–P97.5 percentiles (95%, forecast)",
                     "P10–P90 percentiles (80%, forecast)",
                     "P25–P75 percentiles (50%, forecast)", "Median of 200 paths (forecast)"]
    built = build_figure(example("sample_distribution"), RenderRequest(format="png"))
    texts = [t.get_text() for t in built.axes.get_legend().get_texts()]
    assert texts == ["Histogram, n = 400", "Kernel density estimate, h = 23.4 ms"]
