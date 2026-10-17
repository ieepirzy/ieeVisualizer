"""The text alternative of a figure (an email with images blocked) carries
its intervals' bounds, named as the legend states the interval."""

from ieeVisualizer.spec import from_dict
from ieeVisualizer.view.email import figure_summary


def test_band_bounds_are_in_the_text_alternative():
    spec = from_dict({"schema_version": "1.0", "datasets": {"d": {"type": "snapshot", "columns": {
        "t": {"values": [0, 1, 2]}, "y": {"unit": "count", "values": [5, 6, 7]},
        "lo": {"unit": "count", "values": [4, None, 6]},
        "hi": {"unit": "count", "values": [6, 7, 9]}}}},
        "layers": [
            {"type": "band", "data": "d", "role": "forecast", "interval_kind": "min-max",
             "encoding": {"x": "t", "y_lower": "lo", "y_upper": "hi"}},
            {"type": "line", "data": "d", "name": "Orders",
             "encoding": {"x": "t", "y": "y", "y_lower": "lo", "y_upper": "hi"},
             "interval_kind": "confidence", "interval_level": 0.9}]})
    text = figure_summary(spec)
    assert "min-max range (forecast), lower bound: 2 values from 4 to 6 count" in text
    assert "min-max range (forecast), upper bound: 2 values from 6 to 9 count" in text
    assert "Orders: 90% confidence interval, lower bound: 2 values from 4 to 6 count" in text
    assert "Orders: 3 values from 5 to 7 count" in text
