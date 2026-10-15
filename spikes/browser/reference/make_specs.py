"""Write the spike FigureSpecs, the theme tokens and the Matplotlib reference PNGs.

Run with an ieeVisualizer that has ``ieeVisualizer.themes`` (branch
claude/corporate-themes at the time of writing), e.g.::

    git archive origin/claude/corporate-themes | tar -x -C /some/dir
    PYTHONPATH=/some/dir python spikes/browser/reference/make_specs.py

Outputs (next to this file's parent): ``specs/*.json`` (canonical FigureSpec
JSON as the browser adapters read it), ``specs/theme.muutto365-report-light.json``
(``Theme.to_json()``, i.e. the token export the Vue package would consume) and
``out/mpl/*.png`` (the Matplotlib adapter's render of each spec).

The band spec uses a provisional ``band`` layer (ROADMAP "Uncertainty is
first-class"); spec 1.0 has no such mark, so its Matplotlib reference renders the
spec-1.0 equivalent the timeseries template uses today: the bounds as two
``reference`` lines in the colour of the central line.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from ieeVisualizer import themes
from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.spec import from_dict, to_dict, validate

HERE = Path(__file__).resolve().parent.parent
SPECS = HERE / "specs"
MPL_OUT = HERE / "out" / "mpl"
THEME = "muutto365-report-light"
WIDTH_PX, HEIGHT_PX = 800, 500


def weeks(start_day: int, month: int, n: int) -> list[str]:
    import datetime as dt

    d0 = dt.date(2026, month, start_day)
    return [(d0 + dt.timedelta(weeks=i)).isoformat() + "T00:00:00+03:00" for i in range(n)]


def line_spec() -> dict:
    obs_weeks = weeks(6, 7, 12)
    revenue = [1180.0, 1215.5, 1260.0, 1242.0, None, 1301.0, None, 1288.0, 1335.0, 1350.5,
               1342.0, 1398.0]
    fc_weeks = weeks(28, 9, 4)
    return {
        "schema_version": "1.0",
        "title": "Weekly revenue",
        "caption": "Forecast is a model output, not a measurement. Two weeks have no data.",
        "theme": THEME,
        "datasets": {
            "observed": {
                "type": "snapshot",
                "columns": {
                    "week": {"dtype": "datetime", "values": obs_weeks},
                    "revenue": {"dtype": "float", "unit": "EUR",
                                "aggregation": "sum per ISO week", "values": revenue},
                },
                "provenance": {"source": "spike fixture", "timezone": "Europe/Helsinki"},
            },
            "forecast": {
                "type": "snapshot",
                "columns": {
                    "week": {"dtype": "datetime", "values": fc_weeks},
                    "revenue": {"dtype": "float", "unit": "EUR",
                                "values": [1410.0, 1432.0, 1450.0, 1471.0]},
                },
                "provenance": {"source": "spike fixture", "timezone": "Europe/Helsinki"},
            },
        },
        "layers": [
            {"type": "line", "data": "observed", "encoding": {"x": "week", "y": "revenue"},
             "name": "Observed"},
            {"type": "line", "data": "forecast", "encoding": {"x": "week", "y": "revenue"},
             "name": "Forecast", "role": "forecast"},
        ],
        "axes": {"x": {"label": "Week", "scale": "time"},
                 "y": {"label": "Revenue", "unit": "EUR", "limits": [0, None]}},
        "annotations": [{"type": "reference_line", "axis": "y", "value": 1300,
                         "label": "Target"}],
    }


def bar_spec() -> dict:
    regions, years, values = [], [], []
    data = {
        "Helsinki": {"2025": 412, "2026": 468},
        "Espoo": {"2025": 233, "2026": 251},
        "Vantaa": {"2025": 180, "2026": 176},
        "Tampere": {"2026": 142},           # no 2025 row at all
        "Turku": {"2025": 96, "2026": None},  # 2026 is null
    }
    for r, by_year in data.items():
        for y, v in by_year.items():
            regions.append(r)
            years.append(y)
            values.append(v)
    return {
        "schema_version": "1.0",
        "title": "Bookings by region",
        "caption": "Tampere has no 2025 row; Turku 2026 is null (not collected).",
        "theme": THEME,
        "datasets": {"b": {"type": "snapshot", "columns": {
            "region": {"dtype": "str", "values": regions},
            "year": {"dtype": "str", "values": years},
            "bookings": {"dtype": "int", "unit": "bookings", "values": values},
        }}},
        "layers": [{"type": "bar", "data": "b",
                    "encoding": {"x": "region", "y": "bookings", "series": "year"}}],
        "axes": {"x": {"label": "Region"}, "y": {"label": "Bookings", "unit": "bookings"}},
        "legend": {"show": True, "title": "Year"},
    }


def heatmap_spec() -> dict:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    slots = ["08", "10", "12", "14", "16"]
    xs, ys, vs = [], [], []
    for di, d in enumerate(days):
        for si, s in enumerate(slots):
            xs.append(d)
            ys.append(s)
            if d == "Sun":
                v = None                      # not collected on Sundays
            elif (d, s) == ("Wed", "12"):
                v = None                      # a single gap
            elif (d, s) == ("Sat", "16"):
                v = 0                         # a real, measured zero
            else:
                v = (di * 3 + si * 5) % 17 + 2
            vs.append(v)
    return {
        "schema_version": "1.0",
        "title": "Bookings by weekday and start time",
        "caption": "Sunday not collected (blank). Sat 16 is a measured zero.",
        "theme": THEME,
        "datasets": {"h": {"type": "snapshot", "columns": {
            "weekday": {"dtype": "str", "values": xs},
            "slot": {"dtype": "str", "values": ys},
            "bookings": {"dtype": "int", "unit": "bookings", "values": vs},
        }}},
        "layers": [{"type": "heatmap", "data": "h",
                    "encoding": {"x": "weekday", "y": "slot", "color": "bookings"}}],
        "axes": {"x": {"label": "Weekday"}, "y": {"label": "Start time", "unit": "h"}},
    }


def band_spec() -> dict:
    days = list(range(1, 31))
    obs = [100 + 2.0 * d + (3 if d % 3 == 0 else -2) for d in days[:20]] + [None] * 10
    med = [None] * 19 + [100 + 2.0 * d for d in days[19:]]
    half = [None] * 19 + [0.0] + [1.5 * (d - 20) ** 0.9 + 2 for d in days[20:]]
    lo = [m - h if m is not None else None for m, h in zip(med, half)]
    hi = [m + h if m is not None else None for m, h in zip(med, half)]
    lo[24] = hi[24] = None  # one missing interval: the band must break, not drop to 0
    return {
        "schema_version": "1.0",
        "title": "Daily volume with 90 % prediction interval",
        "caption": ("Shaded: 90 % prediction interval (provisional band mark). "
                    "Day 25 interval missing."),
        "theme": THEME,
        "datasets": {"d": {"type": "snapshot", "columns": {
            "day": {"dtype": "int", "unit": "d", "values": days},
            "observed": {"dtype": "float", "unit": "m3", "values": obs},
            "median": {"dtype": "float", "unit": "m3", "values": med},
            "lower": {"dtype": "float", "unit": "m3", "values": lo},
            "upper": {"dtype": "float", "unit": "m3", "values": hi},
        }}},
        "layers": [
            # Provisional mark (not in schema 1.0): encoding y_lower/y_upper plus the
            # interval metadata the roadmap makes mandatory.
            {"type": "band", "data": "d", "role": "forecast", "name": "90 % prediction interval",
             "encoding": {"x": "day", "y_lower": "lower", "y_upper": "upper"},
             "interval_level": 0.9, "interval_kind": "prediction",
             "style": {"color_of": "Forecast"}},
            {"type": "line", "data": "d", "encoding": {"x": "day", "y": "observed"},
             "name": "Observed"},
            {"type": "line", "data": "d", "encoding": {"x": "day", "y": "median"},
             "name": "Forecast", "role": "forecast"},
        ],
        "axes": {"x": {"label": "Day", "unit": "d"}, "y": {"label": "Volume", "unit": "m3"}},
    }


def band_as_spec10(spec: dict) -> dict:
    """The spec-1.0 stand-in for the band: two reference lines (lower, upper)."""
    s = copy.deepcopy(spec)
    band = s["layers"].pop(0)
    for key in ("y_lower", "y_upper"):
        s["layers"].append({"type": "line", "data": band["data"], "role": "reference",
                            "name": band["name"], "style": {"color_of": "Forecast"},
                            "encoding": {"x": band["encoding"]["x"], "y": band["encoding"][key]}})
    return s


def main() -> int:
    SPECS.mkdir(parents=True, exist_ok=True)
    MPL_OUT.mkdir(parents=True, exist_ok=True)
    theme = themes.get(THEME)
    (SPECS / f"theme.{THEME}.json").write_text(theme.to_json() + "\n")

    builders = {"line": line_spec, "grouped_bar": bar_spec, "heatmap": heatmap_spec,
                "band": band_spec}
    request = RenderRequest(format="png", width=WIDTH_PX, height=HEIGHT_PX, units="px", dpi=100)
    for name, build in builders.items():
        raw = build()
        mpl_dict = band_as_spec10(raw) if name == "band" else raw
        spec = from_dict(mpl_dict)
        errors = [i for i in validate(spec) if i.severity == "error"]
        if errors:
            print(name, "INVALID", errors)
            return 1
        # Browser adapters read the canonical form (Python's to_dict) except for the
        # provisional band layer, which Python cannot parse yet.
        out = raw if name == "band" else to_dict(spec)
        (SPECS / f"{name}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
        res = render(spec, request)
        (MPL_OUT / f"{name}.png").write_bytes(res.content)
        print(f"{name}: {res.size_bytes} B png, warnings: "
              f"{[w.code for w in res.warnings]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
