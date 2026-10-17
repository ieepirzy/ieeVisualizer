"""Follow-ups to the interval review: exact connector rows, phone legend
wrapping that never splits a series name, and import order."""

import subprocess
import sys
from pathlib import Path

import pytest

from ieeVisualizer import templates
from ieeVisualizer.render.mpl.adapter import build_figure
from ieeVisualizer.spec import DataSnapshot, from_dict

IV = [{"lower": "lo", "upper": "hi", "kind": "prediction", "level": 0.8}]


def _missing(cols, dtypes, **params):
    units = {"y": "count", "lo": "count", "hi": "count"}
    snap = DataSnapshot.from_columns(cols, units=units, dtypes=dtypes)
    spec = templates.get("timeseries").build(snap, time_column="t", value_columns=["y"],
                                             connect_forecast=True, intervals=IV, **params)
    return [w.message for w in build_figure(spec).warnings
            if w.code == "interval_bounds_missing"]


def test_a_forecast_row_next_to_the_connector_still_warns():
    got = _missing({"t": [1, 2, 3, 4, 5, 6], "y": [1, 2, 3, 4, 5, 6],
                    "lo": [None, None, None, None, 4.5, 5.5],
                    "hi": [None, None, None, 4.5, 5.5, 6.5]}, {"t": "int"}, forecast_from=4)
    assert len(got) == 1 and got[0].startswith("1 row(s)")


def test_forecasts_overlapping_actuals_still_warn():
    got = _missing({"t": [1, 2, 3, 4, 3, 4, 5, 6], "y": [1, 2, 3, 4, 3.1, 4.1, 5, 6],
                    "role": ["observed"] * 4 + ["forecast"] * 4,
                    "lo": [None] * 4 + [None, None, 4.5, 5.5],
                    "hi": [None] * 4 + [None, None, 5.5, 6.5]},
                   {"t": "int", "role": "str"}, role_column="role")
    assert len(got) == 1 and got[0].startswith("2 row(s)")


def test_ragged_series_still_warn():
    got = _missing({"t": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 6], "s": ["A"] * 5 + ["B"] * 6,
                    "y": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 6],
                    "role": ["observed"] * 8 + ["forecast"] * 3,
                    "lo": [None] * 8 + [None, None, 5.5], "hi": [None] * 8 + [None, None, 6.5]},
                   {"t": "int", "role": "str", "s": "str"}, role_column="role",
                   series_column="s")
    assert len(got) == 1 and got[0].startswith("2 row(s)")


def test_template_records_exactly_its_boundary_copies():
    t = templates.get("timeseries")
    spec = t.build_payload(t.example())
    assert spec.metadata["template"]["forecast"]["boundary_rows"] == [0]
    old = templates.get("timeseries", "1.0.0")
    assert "boundary_rows" not in old.build_payload(old.example()).metadata["template"][
        "forecast"]


@pytest.mark.parametrize("label, lines", [
    ("Region: North (EUR): P2.5–P97.5 percentiles (95 %)",
     ["Region: North (EUR):", "P2.5–P97.5 percentiles", "(95 %)"]),
    ("Toimitusaika (pv) (mediaani): 95 % confidence interval",
     ["Toimitusaika (pv) (mediaani):", "95 % confidence interval"]),
])
def test_phone_wrapping_never_splits_a_series_name(label, lines):
    from ieeVisualizer.render.mpl.intervals import narrow_label

    statements = {"P2.5–P97.5 percentiles (95 %)", "95 % confidence interval"}
    assert narrow_label(label, 150.0, 12.0, statements).split("\n") == lines


NAMES = ["Region: North (EUR)", "Toimitusaika (pv) (mediaani)", "A"]


@pytest.mark.parametrize("mark", ["bar", "errorbar"])
@pytest.mark.parametrize("name", NAMES)
def test_quantile_passes_the_phone_gate_wherever_confidence_does(name, mark):
    from ieeVisualizer.render import GATING_CODES, get_profile, render

    cols = {"g": {"values": ["North", "North", "South", "South"]},
            "arm": {"values": ["Control", "Treatment"] * 2},
            "y": {"values": [1.0, 2.0, 3.0, 4.0]}, "lo": {"values": [0.5, 1.5, 2.5, 3.5]},
            "hi": {"values": [1.5, 2.5, 3.5, 4.5]}}
    gated = {}
    for kind in ("confidence", "quantile"):
        layer = {"type": mark, "name": name, "data": "d", "interval_kind": kind,
                 "interval_level": 0.95, "encoding": {"x": "g", "y": "y", "y_lower": "lo",
                                                      "y_upper": "hi", "series": "arm"}}
        s = from_dict({"schema_version": "1.0", "title": "Conversion by region",
                       "datasets": {"d": {"type": "snapshot", "columns": cols}},
                       "layers": [layer]})
        r = render(s, get_profile("email-phone").figure_request("png"))
        gated[kind] = sorted({w.code for w in r.warnings if w.code in GATING_CODES})
    assert gated["quantile"] == gated["confidence"] == []


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("order", [
    ["ieeVisualizer.render.mpl.intervals"],
    ["ieeVisualizer.render.mpl.intervals", "ieeVisualizer.render.mpl.marks"],
    ["ieeVisualizer.render.mpl.marks", "ieeVisualizer.render.mpl.intervals"],
    ["ieeVisualizer.render.mpl.adapter"],
    ["ieeVisualizer.render", "ieeVisualizer.render.mpl.intervals"],
])
def test_modules_import_in_any_order(order):
    code = ("import importlib, sys\n"
            f"for name in {order!r}:\n"
            "    importlib.import_module(name)\n"
            "from ieeVisualizer.render.mpl.marks import DRAW\n"
            "assert {'band', 'errorbar'} <= set(DRAW)\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, env={"MPLBACKEND": "Agg", "PATH": "", "PYTHONPATH": str(ROOT)})
    assert out.returncode == 0, out.stderr
