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
        "Cap.\nIntervals: A: 95 % confidence interval; B: ±1 standard error.")


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
    outer = handles[labels.index("90 % prediction interval")]
    inner = handles[labels.index("50 % prediction interval")]
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
    assert legend(built) == ["x", "y", "95 % confidence interval (all series)"]
