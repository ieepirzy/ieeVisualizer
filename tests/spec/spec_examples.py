"""Example specs shared by the FigureSpec tests (imported, not a test module).

Each builder returns a fresh, *valid* FigureSpec; tests break copies of them
to exercise one check at a time.
"""

from __future__ import annotations

from ieeVisualizer.spec import (
    Axis,
    BarMark,
    Column,
    ColumnSchema,
    DatasetRef,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    HeatmapMark,
    HistogramMark,
    Legend,
    LineMark,
    Provenance,
    QuiverMark,
    ReferenceBand,
    ReferenceLine,
    ScatterMark,
    TextAnnotation,
)


def timeseries_spec() -> FigureSpec:
    weeks = ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    observed = DataSnapshot(
        columns={
            "week": Column(weeks, dtype="datetime"),
            "revenue": Column([1200.0, None, 1350.5, 1410.0], unit="EUR",
                              description="Net revenue", aggregation="sum per ISO week"),
        },
        provenance=Provenance(source="finance-service", query="weekly_revenue",
                              retrieved_at="2026-10-14T06:00:00+03:00", timezone="Europe/Helsinki",
                              run_id="report-2026-W42"),
    )
    forecast = DataSnapshot.from_columns(
        {"week": ["2026-10-05", "2026-10-12"], "revenue": [1450, 1500]},
        units={"revenue": "EUR"}, dtypes={"week": "datetime", "revenue": "float"},
    )
    return FigureSpec(
        id="fig-revenue",
        title="Weekly revenue",
        caption="Observed vs forecast; forecast is a model output, not a measurement.",
        datasets={"observed": observed, "forecast": forecast},
        layers=[
            LineMark(data="observed", encoding={"x": "week", "y": "revenue"}, name="Observed"),
            LineMark(data="forecast", encoding={"x": "week", "y": "revenue"}, name="Forecast",
                     role="forecast", style={"linestyle": "dashed"}),
        ],
        axes={"x": Axis(label="Week", scale="time"),
              "y": Axis(label="Revenue", unit="EUR", limits=[0, None])},
        legend=Legend(position="upper-left"),
        annotations=[ReferenceLine(axis="y", value=1300, label="Target"),
                     ReferenceBand(axis="x", start="2026-09-28", end="2026-10-05",
                                   label="Holiday")],
        theme="corporate",
        metadata={"template": "timeseries", "template_version": "1"},
    )


def quiver_spec() -> FigureSpec:
    xs, ys, us, vs = [], [], [], []
    for x in (-1.0, 0.0, 1.0):
        for y in (-1.0, 0.0, 1.0):
            xs.append(x)
            ys.append(y)
            us.append(-y)
            vs.append(x)
    field = DataSnapshot.from_columns({"x": xs, "y": ys, "u": us, "v": vs},
                                      units={"x": "m", "y": "m", "u": "m/s", "v": "m/s"})
    return FigureSpec(
        title="Rotation field",
        datasets={"field": field},
        layers=[QuiverMark(data="field", encoding={"x": "x", "y": "y", "u": "u", "v": "v"})],
        axes={"x": Axis(label="x", unit="m"), "y": Axis(label="y", unit="m")},
        annotations=[TextAnnotation(text="origin", x=0, y=0)],
    )


def function_spec() -> FigureSpec:
    return FigureSpec(
        title="Damped oscillation",
        layers=[FunctionMark(expression="exp(-a*x)*sin(x)", domain={"x": [0.0, 10.0]},
                             samples=400, parameters={"a": 0.3}, role="reference")],
    )


def misc_spec() -> FigureSpec:
    """Heatmap with nulls and a horizontal bar chart."""
    grid = DataSnapshot.from_columns(
        {"gx": [0, 1, 0, 1], "gy": [0, 0, 1, 1], "val": [0.1, None, 0.3, 0.4]})
    cats = DataSnapshot.from_columns({"stage": ["lead", "quote", "booked"], "n": [40, 12, 7]},
                                     units={"n": "count"})
    return FigureSpec(
        datasets={"grid": grid, "cats": cats},
        layers=[
            HeatmapMark(data="grid", encoding={"x": "gx", "y": "gy", "color": "val"}),
            BarMark(data="cats", orientation="horizontal", encoding={"y": "stage", "x": "n"}),
        ],
        axes={"y": Axis(scale="category")},
    )


def histogram_ref_spec() -> FigureSpec:
    """A histogram over an external DatasetRef that declares its columns."""
    ref = DatasetRef(kind="query", id="bookings.latency", version="3",
                     columns={"latency": ColumnSchema(dtype="float", unit="s")})
    return FigureSpec(
        datasets={"lat": ref},
        layers=[HistogramMark(data="lat", bins=20, encoding={"x": "latency"})],
        axes={"x": Axis(label="Latency", unit="s", scale="log")},
    )


def scatter3d_spec() -> FigureSpec:
    pts = DataSnapshot.from_columns({"a": [1, 2], "b": [3, 4], "c": [5, 6], "s": [1.0, 2.0]})
    return FigureSpec(
        coordinate_system="cartesian3d",
        datasets={"pts": pts},
        layers=[ScatterMark(data="pts", encoding={"x": "a", "y": "b", "z": "c", "size": "s"})],
        axes={"z": Axis(label="height")},
    )


EXAMPLES = {
    "timeseries": timeseries_spec,
    "quiver": quiver_spec,
    "function": function_spec,
    "misc": misc_spec,
    "histogram_ref": histogram_ref_spec,
    "scatter3d": scatter3d_spec,
}

