"""A weekly management briefing, end to end: view -> phone email -> .eml.

Run: python examples/weekly_briefing.py  (writes to examples/out/briefing/, gitignored)

The company, its workshops and every number are fictional (illustrative
data, not a measurement). The script builds a ViewSpec - headline and nut
graf, four KPI tiles with their comparison bases, twelve observed weeks plus
a four-week forecast, a workshop comparison, a short table and the
source/method footnote - renders it for ``email-phone``, applies the send
gate, and writes:

* ``briefing.html`` plus the PNGs it references (open it in a browser and
  narrow the window to 360 px to see the phone layout);
* ``briefing.eml``: the message ``build_message`` assembles (HTML, inline
  PNGs by Content-ID, plain-text alternative). Open it in a mail client to
  preview it as a recipient would (docs/MOBILE_AND_EMAIL.md, "Sending a
  briefing").

It prints the gate verdict and exits non-zero when the gate refuses.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from ieeVisualizer import templates
from ieeVisualizer.render import LegibilityError, check_legible
from ieeVisualizer.spec import DataSnapshot, Provenance
from ieeVisualizer.view import (
    Comparison,
    FigureComponent,
    Grid,
    Kpi,
    Note,
    Stack,
    Table,
    Text,
    ViewSpec,
    build_message,
    render_email,
    write_eml,
)

OUT = Path(__file__).parent / "out" / "briefing"
COMPANY = "Fernhill Bicycle Works"  # fictional
SOURCE = Provenance(source="examples/weekly_briefing.py", timezone="Europe/Helsinki",
                    notes="Illustrative data for a fictional company; not a measurement.")

#: Service orders per ISO week, weeks 30-41 of 2026 (observed) ...
ORDERS = [318, 335, 352, 361, 347, 372, 380, 366, 391, 404, 389, 412]
#: ... and the planning forecast for weeks 42-45, with its range.
FORECAST = [405, 398, 384, 371]
FORECAST_LOW = [380, 366, 347, 329]
FORECAST_HIGH = [430, 430, 421, 413]
#: Week 41 by workshop: orders this week and last, revenue, on-time share.
WORKSHOPS = ["Harbour Street", "Old Mill", "North Gate", "Riverside"]
WEEK_ORDERS = [131, 112, 96, 73]
PREV_ORDERS = [118, 109, 97, 65]
REVENUE = [15_420, 13_106, 11_230, 8_474]
ON_TIME = [95.4, 93.8, 92.7, 94.5]


def monday(week: int) -> str:
    """The Monday that starts ISO week ``week`` of 2026."""
    return (date(2025, 12, 29) + timedelta(weeks=week - 1)).isoformat()


def trend_data() -> DataSnapshot:
    weeks = [monday(w) for w in range(30, 46)]
    observed = len(ORDERS)
    snap = DataSnapshot.from_columns(
        {"week": weeks,
         "Orders": ORDERS + FORECAST,
         "low": [None] * observed + FORECAST_LOW,
         "high": [None] * observed + FORECAST_HIGH},
        units={"Orders": "count", "low": "count", "high": "count"},
        dtypes={"week": "datetime", "Orders": "int", "low": "int", "high": "int"},
        provenance=SOURCE)
    snap.columns["week"].description = "Week starting"
    return snap


def workshop_data() -> DataSnapshot:
    snap = DataSnapshot.from_columns(
        {"Workshop": WORKSHOPS, "Orders": WEEK_ORDERS, "Week 40": PREV_ORDERS,
         "Revenue": REVENUE, "On time": ON_TIME},
        units={"Orders": "count", "Week 40": "count", "Revenue": "EUR", "On time": "%"},
        dtypes={"Workshop": "str", "Orders": "int", "Week 40": "int", "Revenue": "int",
                "On time": "float"},
        provenance=SOURCE)
    return snap


def briefing() -> ViewSpec:
    trend = templates.get("timeseries").build(
        trend_data(), time_column="week", value_columns=["Orders"], y_label="Orders",
        forecast_from=monday(42), connect_forecast=True, lower_column="low",
        upper_column="high", title="Service orders per week",
        caption="Weeks 30-41 observed; weeks 42-45 forecast (dashed) with its planning range.")
    # TODO(bar_comparison): use templates "bar_comparison" with value_labels=True once
    # its value labels pass the phone gate (in-flight fix); without them the
    # bars carry no printed numbers, and the table below gives the exact values.
    by_workshop = templates.get("bar_comparison").build(
        workshop_data(), category_column="Workshop", value_columns=["Orders"],
        previous_column="Week 40", previous_label="week 40", value_labels=False,
        orientation="horizontal", value_label="Orders",
        title="Orders by workshop, week 41 vs week 40")
    total, prev = sum(WEEK_ORDERS), sum(PREV_ORDERS)
    assert total == ORDERS[-1] and prev == ORDERS[-2]  # the tiles, chart and table agree
    on_time = sum(o * p for o, p in zip(WEEK_ORDERS, ON_TIME)) / total  # order-weighted
    return ViewSpec(
        title="Week 41 briefing",
        figures={"trend": trend, "workshops": by_workshop},
        datasets={"workshops": workshop_data()},
        metadata={"lang": "en"},
        root=Stack([
            Text(f"{COMPANY} · management team · ISO week 41, 2026", style="small"),
            Text("Orders hit a 12-week high as autumn servicing began", style="heading"),
            Text(f"Service orders rose to {total} in week 41, up {total - prev} on week 40, "
                 "led by Harbour Street. The forecast expects demand to ease through "
                 "November as the commuting season ends, so **the winter staffing plan "
                 "can proceed as drafted**.", style="lead"),
            Grid([
                Kpi("Service orders (week 41)", total, unit="orders",
                    comparison=Comparison(prev, "week 40"), source="workshop system"),
                Kpi("Revenue (week 41)", sum(REVENUE), unit="EUR", decimals=0,
                    comparison=Comparison(48_960.0, "week 40"), source="till export"),
                Kpi("Repairs done on time", on_time, unit="%", decimals=1,
                    comparison=Comparison(92.7, "week 40"), source="workshop system"),
                Kpi("Average repair value", sum(REVENUE) / total, unit="EUR", decimals=2,
                    comparison=Comparison(125.86, "week 40"), source="till export"),
            ], columns=2),
            FigureComponent(figure="trend", aspect=0.62),
            FigureComponent(figure="workshops", aspect=0.7),
            # three columns fit side by side on a 320 px phone; a fourth turns the
            # table into one card per row (the per-workshop on-time share is in the
            # data and behind the "Repairs done on time" tile)
            Table("workshops", columns=["Workshop", "Orders", "Revenue"],
                  title="Week 41 by workshop"),
            Note("The forecast is the planning team's seasonal model; its range is the "
                 "model's 80 % interval, not a guarantee."),
            Note(f"Source: {COMPANY} workshop system and till export, ISO weeks starting "
                 "Monday (Europe/Helsinki). On time = finished by the promised date; the tile is "
                 "the order-weighted share across workshops. "
                 "Illustrative data for a fictional company.", type="footnote"),
        ]),
    )


def main(out: Path | None = None) -> bool:
    """Write the briefing to ``out``; returns whether the gate passed."""
    out = Path(out) if out is not None else OUT
    out.mkdir(parents=True, exist_ok=True)
    view = briefing()
    email = render_email(view, "email-phone")
    try:
        check_legible(email)
        verdict, passed = "PASS: no gating warnings at email-phone", True
    except LegibilityError as exc:
        verdict, passed = "REFUSED: " + ", ".join(sorted({i.code for i in exc.issues})), False
    # the browser preview references the PNGs by file name (the attachment's
    # filename, the Content-ID without "@ieevisualizer"); the message by Content-ID
    def file_name(cid: str) -> str:
        return cid.split("@", 1)[0]

    preview = render_email(view, "email-phone", image_src=file_name)
    (out / "briefing.html").write_text(preview.html, encoding="utf-8")
    for name, content in preview.images.items():
        (out / file_name(name)).write_bytes(content)
    if passed:
        msg = build_message(email, subject=f"{COMPANY}: weekly briefing, week 41",
                            sender="Reporting <reports@example.com>",
                            recipients=["Management team <management@example.com>"])
        write_eml(msg, out / "briefing.eml")
    print(f"gate: {verdict}")
    for w in email.warnings:  # gating ones made it refuse; the rest are notes
        print(f"  {w.code} {w.path}: {w.message}")
    print(f"wrote {out}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
