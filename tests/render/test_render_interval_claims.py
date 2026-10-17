"""Interval statements are claims: the caption names each interval's series,
a fan chart's legend swatch shows the region it names, a ±k SE/SD bar with
asymmetric bounds says so, a bar's interval is never dropped silently, and
grouped bars list one interval entry after their bars."""

import numpy as np
import pytest

from ieeVisualizer.render.mpl.adapter import build_figure
from ieeVisualizer.spec import from_dict


def spec(cols, layers, **figure):
    return from_dict(dict({"schema_version": "1.0",
                           "datasets": {"d": {"type": "snapshot", "columns": cols}},
                           "layers": [dict({"data": "d"}, **la) for la in layers]}, **figure))


def legend(built):
    lg = built.axes.get_legend()
    return [t.get_text() for t in lg.get_texts()] if lg is not None else []


T3 = {"t": {"values": [0, 1, 2]}, "y": {"values": [1, 2, 3]}, "y2": {"values": [2, 3, 4]},
      "lo": {"values": [0, 1, 2]}, "hi": {"values": [2, 3, 4]},
      "lo2": {"values": [1, 2, 3]}, "hi2": {"values": [3, 4, 5]}}


def test_hidden_legend_caption_names_the_series_of_each_interval():
    s = spec(T3, [
        {"type": "line", "name": "A", "encoding": {"x": "t", "y": "y", "y_lower": "lo",
                                                   "y_upper": "hi"},
         "interval_kind": "confidence", "interval_level": 0.95},
        {"type": "line", "name": "B", "encoding": {"x": "t", "y": "y2", "y_lower": "lo2",
                                                   "y_upper": "hi2"},
         "interval_kind": "standard-error", "interval_multiplier": 1}],
        legend={"show": False}, caption="Cap.")
    built = build_figure(s)
    assert built.caption.get_text() == (
        "Cap.\nIntervals: A: 95\u00a0% confidence interval; B: ±1 standard error.")


FAN = {"t": {"values": [0.0, 1.0, 2.0, 3.0]}, "m": {"values": [5.0, 5.0, 5.0, 5.0]},
       "q05": {"values": [1.0, 1.0, 1.0, 1.0]}, "q95": {"values": [9.0, 9.0, 9.0, 9.0]},
       "q25": {"values": [4.0, 4.0, 4.0, 4.0]}, "q75": {"values": [6.0, 6.0, 6.0, 6.0]}}


def _fan():
    band = {"type": "band", "interval_kind": "prediction", "style": {"color_of": "m"}}
    return spec(FAN, [
        dict(band, encoding={"x": "t", "y_lower": "q05", "y_upper": "q95"}, interval_level=0.9),
        dict(band, encoding={"x": "t", "y_lower": "q25", "y_upper": "q75"}, interval_level=0.5),
        {"type": "line", "name": "m", "encoding": {"x": "t", "y": "m"}}],
        axes={"y": {"limits": [0, 10]}}, legend={"position": "outside-right"})


def _pixel(canvas, xy_display):
    rgba = np.asarray(canvas.buffer_rgba())
    h = rgba.shape[0]
    x, y = (int(round(v)) for v in xy_display)
    return rgba[h - 1 - y, x, :3].astype(float)


def _alpha(handle):
    if handle.get_alpha() is not None:
        return handle.get_alpha()
    return float(np.asarray(handle.get_facecolor()).reshape(-1, 4)[0][3])


def test_fan_legend_swatch_is_the_colour_of_the_region_it_names():
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    built = build_figure(_fan())
    fig, ax = built.figure, built.axes
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    lg = ax.get_legend()
    labels = [t.get_text() for t in lg.get_texts()]
    handles = lg.legend_handles if hasattr(lg, "legend_handles") else lg.legendHandles
    a = 0.25
    outer = handles[labels.index("90\u00a0% prediction interval")]
    inner = handles[labels.index("50\u00a0% prediction interval")]
    assert _alpha(outer) == pytest.approx(a)
    assert _alpha(inner) == pytest.approx(1 - (1 - a) ** 2)
    # Read back the pixels: each swatch equals the plotted region it names.
    for handle, y_data in ((outer, 8.0), (inner, 5.5)):
        region = _pixel(canvas, ax.transData.transform((0.5, y_data)))
        box = handle.get_window_extent(canvas.get_renderer())
        swatch = _pixel(canvas, ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2))
        assert np.abs(region - swatch).max() <= 2, (region, swatch)


def test_asymmetric_standard_error_bars_say_so():
    cols = {"g": {"values": ["a", "b"]}, "y": {"values": [1.0, 1.0]},
            "lo": {"values": [0.9, 0.0]}, "hi": {"values": [3.0, 1.1]}}
    layer = {"type": "errorbar", "encoding": {"x": "g", "y": "y", "y_lower": "lo",
                                              "y_upper": "hi"},
             "interval_kind": "standard-error", "interval_multiplier": 1}
    built = build_figure(spec(cols, [layer]))
    assert legend(built) == ["±1 standard error (bounds not symmetric)"]
    assert "interval_not_symmetric" in [w.code for w in built.warnings]
    sym = dict(cols, lo={"values": [0.5, 0.0]}, hi={"values": [1.5, 2.0]})
    built = build_figure(spec(sym, [layer]))
    assert legend(built) == ["±1 standard error"]
    assert "interval_not_symmetric" not in [w.code for w in built.warnings]


def test_bar_without_a_value_does_not_drop_its_interval_silently():
    cols = {"g": {"values": ["a", "b", "c"]}, "y": {"values": [1.0, None, 3.0]},
            "lo": {"values": [0.5, 1.5, 2.5]}, "hi": {"values": [1.5, 2.5, 3.5]}}
    built = build_figure(spec(cols, [{"type": "bar", "encoding": {
        "x": "g", "y": "y", "y_lower": "lo", "y_upper": "hi"},
        "interval_kind": "confidence", "interval_level": 0.95}]))
    (w,) = [w for w in built.warnings if w.code == "interval_without_value"]
    assert w.message.startswith("1 row(s) of layer 0")


def test_grouped_bars_list_one_interval_entry_after_their_bars():
    cols = {"g": {"values": ["a", "a", "b", "b"]}, "arm": {"values": ["x", "y", "x", "y"]},
            "y": {"values": [1.0, 2.0, 3.0, 4.0]}, "lo": {"values": [0.5, 1.5, 2.5, 3.5]},
            "hi": {"values": [1.5, 2.5, 3.5, 4.5]}}
    built = build_figure(spec(cols, [{"type": "bar", "encoding": {
        "x": "g", "y": "y", "y_lower": "lo", "y_upper": "hi", "series": "arm"},
        "interval_kind": "confidence", "interval_level": 0.95}]))
    assert legend(built) == ["x", "y", "95\u00a0% confidence interval"]


# --------------------------------------------------------------------------
# Review round 2
# --------------------------------------------------------------------------

ARMS = {"g": {"values": ["North", "North", "South", "South"]},
        "arm": {"values": ["Control", "Treatment", "Control", "Treatment"]},
        "y": {"values": [1.0, 2.0, 3.0, 4.0]},
        "lo": {"values": [0.5, 1.5, 2.5, 3.5]},
        "hi": {"values": [1.5, 2.5, 3.5, 5.0]}}  # only South/Treatment is asymmetric
SE = {"interval_kind": "standard-error", "interval_multiplier": 1.96}


def test_asymmetry_note_belongs_to_the_series_that_has_it():
    enc = {"x": "g", "y": "y", "y_lower": "lo", "y_upper": "hi", "series": "arm"}
    built = build_figure(spec(ARMS, [dict({"type": "errorbar", "encoding": enc}, **SE)]))
    assert legend(built) == ["Control: ±1.96 standard errors",
                             "Treatment: ±1.96 standard errors (bounds not symmetric)"]
    built = build_figure(spec(ARMS, [dict({"type": "bar", "encoding": enc}, **SE)]))
    assert legend(built)[-1] == "±1.96 standard errors (bounds not symmetric: Treatment)"


def test_grouped_bar_entry_names_only_the_series_with_an_interval():
    cols = dict(ARMS, lo={"values": [None, 1.5, None, 3.5]}, hi={"values": [None, 2.5, None, 4.5]})
    enc = {"x": "g", "y": "y", "y_lower": "lo", "y_upper": "hi", "series": "arm"}
    built = build_figure(spec(cols, [{"type": "bar", "encoding": enc,
                                      "interval_kind": "confidence", "interval_level": 0.95}]))
    assert legend(built) == ["Control", "Treatment", "95 % confidence interval (Treatment)"]


def test_fan_swatches_stay_distinct_when_one_row_overshoots():
    cols = dict(FAN, q75={"values": [6.0, 6.0, 6.0, 9.5]})  # the last row leaves the outer band
    built = build_figure(spec(cols, [
        {"type": "band", "interval_kind": "prediction", "interval_level": 0.9,
         "style": {"color_of": "m"}, "encoding": {"x": "t", "y_lower": "q05", "y_upper": "q95"}},
        {"type": "band", "interval_kind": "prediction", "interval_level": 0.5,
         "style": {"color_of": "m"}, "encoding": {"x": "t", "y_lower": "q25", "y_upper": "q75"}},
        {"type": "line", "name": "m", "encoding": {"x": "t", "y": "m"}}]))
    lg = built.axes.get_legend()
    handles = lg.legend_handles if hasattr(lg, "legend_handles") else lg.legendHandles
    labels = [t.get_text() for t in lg.get_texts()]
    inner = handles[labels.index("50 % prediction interval")]
    assert _alpha(inner) == pytest.approx(1 - 0.75 ** 2)


@pytest.mark.parametrize("level", [0.95, 0.8])
@pytest.mark.parametrize("name", [None, "Rate"])
def test_quantile_bars_pass_the_phone_gate(level, name):
    from ieeVisualizer.render import check_legible, get_profile, render

    enc = {"x": "g", "y": "y", "y_lower": "lo", "y_upper": "hi", "series": "arm"}
    layer = {"type": "bar", "encoding": enc, "interval_kind": "quantile", "interval_level": level}
    if name:
        layer["name"] = name
    s = spec(dict(ARMS, hi={"values": [1.5, 2.5, 3.5, 4.5]}), [layer], title="Conversion")
    check_legible(render(s, get_profile("email-phone").figure_request("png")))


def test_quantile_timeseries_passes_the_phone_gate():
    from ieeVisualizer import templates
    from ieeVisualizer.render import check_legible, get_profile, render

    t = templates.get("timeseries")
    payload = t.example()
    payload["params"]["intervals"] = [
        {"lower": "p10", "upper": "p90", "kind": "quantile", "level": 0.8},
        {"lower": "p25", "upper": "p75", "kind": "quantile", "level": 0.5}]
    s = t.build_payload(payload)
    check_legible(render(s, get_profile("email-phone").figure_request("png")))


def test_percent_never_wraps_away_from_its_number():
    s = spec(T3, [{"type": "line", "name": "A", "encoding": {
        "x": "t", "y": "y", "y_lower": "lo", "y_upper": "hi"},
        "interval_kind": "confidence", "interval_level": 0.95}],
        legend={"show": False}, caption="x " * 41)  # pushes the statement to a line end
    from ieeVisualizer.render import RenderRequest

    for width in (2.0, 2.5, 3.0, 3.5, 4.0):
        built = build_figure(s, RenderRequest(width=width, height=3.0))
        lines = built.caption.get_text().split("\n")
        assert not any(line.rstrip().endswith("95") for line in lines), lines
        assert "95 %" in built.caption.get_text()


# --------------------------------------------------------------------------
# Review round 3
# --------------------------------------------------------------------------

NAMES = ["Conversion rate", "Toimitusaika (pv)", "Average repair turnaround days", None]


@pytest.mark.parametrize("theme", [None, "report-light"])
@pytest.mark.parametrize("series", [True, False])
@pytest.mark.parametrize("level", [0.5, 0.8, 0.95])
@pytest.mark.parametrize("name", NAMES)
def test_quantile_passes_the_phone_gate_wherever_confidence_does(name, level, series, theme):
    from ieeVisualizer.render import GATING_CODES, get_profile, render

    enc = {"x": "g", "y": "y", "y_lower": "lo", "y_upper": "hi"}
    if series:
        enc["series"] = "arm"
    cols = dict(ARMS, hi={"values": [1.5, 2.5, 3.5, 4.5]})
    gated = {}
    for kind in ("confidence", "quantile"):
        layer = {"type": "bar", "encoding": enc, "interval_kind": kind, "interval_level": level}
        if name:
            layer["name"] = name
        s = spec(cols, [layer], title="Conversion by region", theme=theme)
        r = render(s, get_profile("email-phone").figure_request("png"))
        gated[kind] = sorted({w.code for w in r.warnings if w.code in GATING_CODES})
    assert gated == {"confidence": [], "quantile": []}


@pytest.mark.parametrize("level", [0.5, 0.8, 0.95])
def test_quantile_timeseries_example_passes_the_phone_gate_at_every_level(level):
    from ieeVisualizer import templates
    from ieeVisualizer.render import check_legible, get_profile, render

    t = templates.get("timeseries")
    payload = t.example()
    payload["params"]["intervals"] = [
        {"lower": "p10", "upper": "p90", "kind": "quantile", "level": level}]
    check_legible(render(t.build_payload(payload),
                         get_profile("email-phone").figure_request("png")))


def test_percentile_label_states_its_kind():
    built = build_figure(spec(ARMS, [{"type": "errorbar", "encoding": {
        "x": "g", "y": "y", "y_lower": "lo", "y_upper": "hi"},
        "interval_kind": "quantile", "interval_level": 0.95}]))
    assert legend(built) == ["P2.5–P97.5 percentiles (95\u00a0%)"]


def test_the_template_connector_row_is_not_a_missing_bound():
    from ieeVisualizer import templates

    t = templates.get("timeseries")
    built = build_figure(t.build_payload(t.example()))
    assert "interval_bounds_missing" not in [w.code for w in built.warnings]
    payload = t.example()
    payload["data"]["columns"]["p10"]["values"][6] = None  # a real gap still warns
    built = build_figure(t.build_payload(payload))
    assert "interval_bounds_missing" in [w.code for w in built.warnings]
