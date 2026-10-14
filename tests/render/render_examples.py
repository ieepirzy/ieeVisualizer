"""Small specs for the renderer tests (imported, not a test module)."""

from __future__ import annotations

from ieeVisualizer.spec import (
    AreaMark,
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

WEEKS = ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
REVENUE = [1200.0, None, 1350.5, 1410.0]


def revenue_spec(**style) -> FigureSpec:
    observed = DataSnapshot(
        columns={
            "week": Column(list(WEEKS), dtype="datetime"),
            "revenue": Column(list(REVENUE), unit="EUR", aggregation="sum per ISO week"),
        },
        provenance=Provenance(source="finance-service", query="weekly_revenue",
                              retrieved_at="2026-10-14T06:00:00+03:00",
                              timezone="Europe/Helsinki", run_id="report-2026-W42"),
    )
    forecast = DataSnapshot.from_columns(
        {"week": ["2026-10-05", "2026-10-12"], "revenue": [1450.0, 1500.0]},
        units={"revenue": "EUR"}, dtypes={"week": "datetime"},
    )
    return FigureSpec(
        id="fig-revenue",
        title="Weekly revenue",
        caption="Forecast is a model output, not a measurement.",
        datasets={"observed": observed, "forecast": forecast},
        layers=[
            LineMark(data="observed", encoding={"x": "week", "y": "revenue"}, name="Observed"),
            LineMark(data="forecast", encoding={"x": "week", "y": "revenue"}, name="Forecast",
                     role="forecast", style=dict(style)),
        ],
        axes={"x": Axis(label="Week", scale="time"),
              "y": Axis(label="Revenue", unit="EUR", limits=[0, None])},
        legend=Legend(position="upper-left"),
        annotations=[ReferenceLine(axis="y", value=1300, label="Target"),
                     ReferenceBand(axis="x", start="2026-09-28", end="2026-10-05",
                                   label="Holiday"),
                     TextAnnotation(text="peak", x="2026-09-28", y=1410.0)],
        metadata={"template": "timeseries", "template_version": "1"},
    )


def one_layer(mark, columns, units=None, dtypes=None, **kw) -> FigureSpec:
    ds = DataSnapshot.from_columns(columns, units=units, dtypes=dtypes)
    mark.data = "d"
    return FigureSpec(datasets={"d": ds}, layers=[mark], **kw)


def scatter_spec() -> FigureSpec:
    return one_layer(
        ScatterMark(encoding={"x": "a", "y": "b", "size": "s", "color": "c"}),
        {"a": [1.0, 2.0, 3.0, 4.0], "b": [10.0, None, 30.0, 40.0], "s": [1.0, 2.0, 3.0, 4.0],
         "c": [0.1, 0.2, 0.3, 0.4]},
        units={"b": "kg", "c": "K"},
    )


def bar_spec(horizontal: bool = False) -> FigureSpec:
    enc = {"y": "stage", "x": "n"} if horizontal else {"x": "stage", "y": "n"}
    return one_layer(
        BarMark(encoding=enc, orientation="horizontal" if horizontal else "vertical"),
        {"stage": ["lead", "quote", "booked", "lost"], "n": [40, 12, 7, None]},
        units={"n": "count"},
    )


def area_spec() -> FigureSpec:
    return one_layer(AreaMark(encoding={"x": "t", "y": "v"}),
                     {"t": [0.0, 1.0, 2.0, 3.0, 4.0], "v": [1.0, 2.0, None, 3.0, 1.0]},
                     units={"t": "s", "v": "m"})


def histogram_spec(**kw) -> FigureSpec:
    return one_layer(HistogramMark(encoding={"x": "lat"}, **kw),
                     {"lat": [0.1, 0.4, 0.5, 0.9, 1.2, 1.3, 2.0, None, 2.5]},
                     units={"lat": "s"})


def heatmap_spec() -> FigureSpec:
    return one_layer(HeatmapMark(encoding={"x": "gx", "y": "gy", "color": "val"}),
                     {"gx": [0, 1, 0, 1], "gy": [0, 0, 1, 1], "val": [0.1, None, 0.3, 0.4]},
                     units={"val": "°C"})


def quiver_spec(**kw) -> FigureSpec:
    return one_layer(QuiverMark(encoding={"x": "x", "y": "y", "u": "u", "v": "v"}, **kw),
                     {"x": [-1.0, 0.0, 1.0, 0.0], "y": [0.0, -1.0, 0.0, 1.0],
                      "u": [0.0, 2.0, 0.0, -1.0], "v": [-1.0, 0.0, 3.0, 0.0]},
                     units={"x": "m", "y": "m", "u": "m/s", "v": "m/s"},
                     axes={"x": Axis(label="x"), "y": Axis(label="y")})


def function_spec(**kw) -> FigureSpec:
    return FigureSpec(title="Damped oscillation", layers=[
        FunctionMark(expression="exp(-a*x)*sin(x)", domain={"x": [0.0, 10.0]}, samples=50,
                     parameters={"a": 0.3}, **kw)])


def surface_spec() -> FigureSpec:
    return FigureSpec(coordinate_system="cartesian3d", layers=[
        FunctionMark(expression="sin(x)*cos(y)", variables=["x", "y"],
                     domain={"x": [-3.0, 3.0], "y": [-3.0, 3.0]}, samples=20)])


def line3d_spec() -> FigureSpec:
    return one_layer(LineMark(encoding={"x": "a", "y": "b", "z": "c"}),
                     {"a": [0.0, 1.0, 2.0], "b": [0.0, 1.0, 4.0], "c": [1.0, None, 3.0]},
                     coordinate_system="cartesian3d")


def polar_spec(unit: str = "rad") -> FigureSpec:
    return one_layer(LineMark(encoding={"x": "theta", "y": "r"}),
                     {"theta": [0.0, 90.0, 180.0] if unit == "deg" else [0.0, 1.0, 2.0],
                      "r": [1.0, 2.0, 3.0]},
                     units={"theta": unit, "r": "m"}, coordinate_system="polar")


def ref_spec() -> FigureSpec:
    ref = DatasetRef(kind="query", id="bookings.latency", version="3",
                     columns={"latency": ColumnSchema(dtype="float", unit="s")})
    return FigureSpec(datasets={"lat": ref},
                      layers=[HistogramMark(data="lat", bins=4, encoding={"x": "latency"})],
                      axes={"x": Axis(label="Latency")})


def latency_snapshot(values=(0.5, 1.0, 2.0, 3.0, None), unit=None) -> DataSnapshot:
    return DataSnapshot(columns={"latency": Column(list(values), dtype="float", unit=unit)},
                        provenance=Provenance(source="bookings-db", query="latency_v3"))
