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
    # by identity (time, and series in long format), never by position
    assert spec.metadata["template"]["forecast"]["boundary_rows"] == [
        {"x": "2026-09-28T00:00:00"}]
    assert t.version == "2.0.1"
    for version in ("1.0.0", "2.0.0"):  # released versions build exactly what they did
        old = templates.get("timeseries", version)
        assert "boundary_rows" not in old.build_payload(old.example()).metadata["template"][
            "forecast"]


@pytest.mark.parametrize("label, lines", [
    ("Region: North (EUR): P2.5–P97.5 percentiles (95%)",
     ["Region: North (EUR):", "P2.5–P97.5 percentiles", "(95%)"]),
    ("Toimitusaika (pv) (mediaani): 95% confidence interval",
     ["Toimitusaika (pv) (mediaani):", "95% confidence interval"]),
])
def test_phone_wrapping_never_splits_a_series_name(label, lines):
    from ieeVisualizer.render.mpl.intervals import narrow_label

    statements = {"P2.5–P97.5 percentiles (95%)", "95% confidence interval"}
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


# --------------------------------------------------------------------------
# Review of #35: connector rows by identity and content, never by position
# --------------------------------------------------------------------------

import copy  # noqa: E402

from ieeVisualizer.spec import to_dict  # noqa: E402


def _built():
    cols = {"t": [1, 2, 3, 4, 5, 6], "y": [1, 2, 3, 4, 5, 6],
            "lo": [None, None, None, None, 4.5, 5.5], "hi": [None, None, None, 4.5, 5.5, 6.5]}
    snap = DataSnapshot.from_columns(cols, units={"y": "count", "lo": "count", "hi": "count"},
                                     dtypes={"t": "int"})
    return templates.get("timeseries").build(snap, time_column="t", value_columns=["y"],
                                             connect_forecast=True, forecast_from=4,
                                             intervals=IV)


def _count(d):
    ws = [w.message for w in build_figure(from_dict(d)).warnings
          if w.code == "interval_bounds_missing"]
    return int(ws[0].split()[0]) if ws else 0


def _each_column(d, fn):
    for name, col in d["datasets"]["forecast"]["columns"].items():
        col["values"] = fn(name, col["values"])


EDITS = {
    "as built": (lambda d: None, 1),
    "connector filtered out": (lambda d: _each_column(d, lambda n, v: v[1:]), 1),
    "rows reversed": (lambda d: _each_column(d, lambda n, v: v[::-1]), 1),
    "rows 0/1 swapped": (lambda d: _each_column(d, lambda n, v: [v[1], v[0]] + v[2:]), 1),
    "real row prepended": (lambda d: _each_column(
        d, lambda n, v: [{"t": 7, "y": 7}.get(n)] + v), 2),
    "real row appended": (lambda d: _each_column(
        d, lambda n, v: v + [{"t": 7, "y": 7}.get(n)]), 2),
    "boundary_rows forged as positions": (lambda d: d["metadata"]["template"]["forecast"]
                                          .__setitem__("boundary_rows", list(range(10))), 2),
    "boundary_rows forged as every x": (lambda d: d["metadata"]["template"]["forecast"]
                                        .__setitem__("boundary_rows",
                                                     [{"x": x} for x in range(10)]), 1),
}


@pytest.mark.parametrize("name", sorted(EDITS))
def test_only_a_row_that_is_still_the_connector_is_exempt(name):
    fn, missing = EDITS[name]
    d = copy.deepcopy(to_dict(_built()))
    fn(d)
    assert _count(d) == missing


def test_an_animation_window_does_not_move_the_exemption():
    from ieeVisualizer.animation import AnimationSpec, FrameColumn
    from ieeVisualizer.animation.mpl import MatplotlibAnimationAdapter

    anim = AnimationSpec(timeline=FrameColumn(dataset="forecast", column="t", display="window",
                                              window=2), figure=_built(), fps=2)
    frames = [f for f in MatplotlibAnimationAdapter().frame_specs(anim) if f is not None]
    seen = 0
    for f in frames:
        ds = f.datasets["forecast"].columns
        # t=4 has no lower bound; a frame whose window has no complete row
        # leaves the band out entirely, so there is no interval to warn about
        drawn = any(m.type == "band" for m in f.layers)
        real_gap = drawn and any(t == 4 for t in ds["t"].values)
        got = [w for w in build_figure(f).warnings if w.code == "interval_bounds_missing"]
        assert bool(got) == real_gap, (ds["t"].values, [w.message for w in got])
        seen += real_gap
    assert seen


@pytest.mark.parametrize("version", ["2.0.0", "2.0.1"])
def test_a_spec_built_by_either_version_gets_no_false_connector_warning(version):
    t = templates.get("timeseries", version)
    assert not [w for w in build_figure(t.build_payload(t.example())).warnings
                if w.code == "interval_bounds_missing"]
    cols = {"t": [1, 2, 3, 4, 5, 6], "y": [1, 2, 3, 4, 5, 6],
            "lo": [None, None, None, 3.5, 4.5, 5.5], "hi": [None, None, None, 4.5, 5.5, 6.5]}
    snap = DataSnapshot.from_columns(cols, units={"y": "count", "lo": "count", "hi": "count"},
                                     dtypes={"t": "int"})
    s = t.build(snap, time_column="t", value_columns=["y"], connect_forecast=True,
                forecast_from=4, intervals=IV)
    assert not [w for w in build_figure(s).warnings if w.code == "interval_bounds_missing"]


def test_a_series_name_with_percentiles_is_not_split_for_another_statement():
    from ieeVisualizer.render.mpl.intervals import narrow_label

    label = "Top percentiles (EUR): 95% confidence interval"
    assert narrow_label(label, 150.0, 12.0, {"95% confidence interval"}).split("\n") == [
        "Top percentiles (EUR):", "95% confidence interval"]


def test_phone_date_offset_states_the_year_not_a_month():
    from ieeVisualizer.render import RenderRequest, get_profile

    t = templates.get("timeseries")
    s = t.build_payload(t.example())
    for req in (get_profile("email-phone").figure_request("png"),
                get_profile("email-tablet").figure_request("png")):
        built = build_figure(s, req)
        offset = built.axes.xaxis.get_major_formatter().get_offset()
        assert offset == "2026", offset
    desktop = build_figure(s, RenderRequest()).axes.xaxis.get_major_formatter().get_offset()
    assert desktop == "2026-Oct"  # other renders keep matplotlib's text (bytes unchanged)


def test_briefing_percent_style_is_one_style():
    import importlib.util

    path = ROOT / "examples" / "weekly_briefing.py"
    mod_spec = importlib.util.spec_from_file_location("_briefing_pct", path)
    wb = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(wb)
    view = wb.briefing()
    from ieeVisualizer.view import render_email

    text = render_email(view, "email-phone").text
    assert "94.2%" in text and "80% prediction interval" in text
    assert "80 %" not in text and "80 %" not in text
