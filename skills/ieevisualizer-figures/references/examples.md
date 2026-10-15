# Runnable examples

All three blocks run against ieeVisualizer `main` (spec 1.0) and are executed
by `tests/test_skills.py`, so they cannot drift from the API. Data are
invented and describe a fictional online furniture shop.

## 1. Template: weekly orders with a gap and a forecast

Rung 1 of the ladder. The week with no data stays `None`; the forecast rows
come from a forecasting service, not from this code. Looking at the first
render found what validation could not: the legend covered the first point,
single weekly values were hard to see without markers, and the unit
"orders" repeated the axis label ("Paid orders (orders)"). The patch below
fixes the first two; the unit is declared as `count`. A line chart does not
need a zero baseline, so none is forced.

```python
from ieeVisualizer import templates
from ieeVisualizer.artifacts import MemoryWriter, export
from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.spec import apply_patch

data = {
    "type": "snapshot",
    "columns": {
        "week": {"dtype": "datetime", "description": "ISO week (Monday)",
                 "values": ["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21",
                            "2026-09-28", "2026-10-05", "2026-10-12"]},
        "orders": {"dtype": "int", "unit": "count", "description": "Paid orders",
                   "aggregation": "count per ISO week, Europe/Berlin",
                   "values": [41, 38, None, 44, 47, 49, 50]},
        "role": {"dtype": "str",
                 "values": ["observed"] * 5 + ["forecast"] * 2},
    },
    "provenance": {"source": "order-service + orders-forecast v3",
                   "query": "weekly_paid_orders v3",
                   "retrieved_at": "2026-10-05T06:00:00+02:00",
                   "timezone": "Europe/Berlin", "run_id": "report-2026-W40",
                   "notes": "Week 38 export failed; forecast rows are model output."},
}

ts = templates.get("timeseries")
params = {"time_column": "week", "value_columns": ["orders"], "role_column": "role",
          "title": "Weekly orders rose to 47 by late September",
          "caption": ("Paid orders per ISO week. Week of 14 Sep: no data (export failed). "
                      "Dashed: forecast, orders model v3. Source: order service, "
                      "retrieved 5 Oct 2026 06:00 CEST.")}
assert ts.check(data, **params) == []          # issues are (path, code, message, severity)
spec = ts.build(data, **params)

result = render(spec, RenderRequest(format="png", width=6.4, height=4.0, dpi=100))
codes = [w.code for w in result.warnings]
assert codes == ["null_values_omitted"]        # the gap; explained in the caption

spec = apply_patch(spec, [
    {"op": "add", "path": "/layers/0/style/marker", "value": "o"},
    {"op": "replace", "path": "/legend/position", "value": "lower-right"},
])
artifact = export(spec, ["png", "svg"], MemoryWriter())
print(artifact.artifact_id)
```

## 2. Composed marks: orders by channel with a missing channel

Rung 2. A ranked horizontal bar chart. Rendering the first draft and looking
at it found three problems that validation could not: the largest bar was at
the bottom (categories are placed bottom-up in order of first appearance), the
y label was the raw column name, and the title "Google Ads brought the most
orders" was not supported because Meta ads had no data. The version below
fixes all three.

```python
from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.spec import apply_patch, from_dict, validate

spec = from_dict({
    "schema_version": "1.0",
    "title": "Google Ads led the tracked channels in September",
    "caption": ("Paid orders by first-touch channel, September 2026. Meta ads: no data "
                "(attribution export failed). Source: order service, retrieved 5 Oct 2026."),
    "datasets": {"by_channel": {
        "type": "snapshot",
        "columns": {
            # ascending order so the largest bar is drawn at the top
            "channel": {"dtype": "str", "values": ["Meta ads", "Organic", "Referral", "Google Ads"]},
            "orders": {"dtype": "int", "unit": "count", "description": "Paid orders",
                       "aggregation": "count, Sep 2026", "values": [None, 48, 61, 74]},
        },
        "provenance": {"source": "order-service", "query": "orders_by_channel_monthly",
                       "retrieved_at": "2026-10-05T06:00:00+02:00",
                       "timezone": "Europe/Berlin"},
    }},
    "layers": [{"type": "bar", "data": "by_channel", "orientation": "horizontal",
                "encoding": {"x": "orders", "y": "channel"}, "name": "Orders"}],
    "axes": {"x": {"label": "Paid orders", "limits": [0, None]},
             "y": {"label": "First-touch channel"}},
})
assert [i for i in validate(spec) if i.severity == "error"] == []

result = render(spec, RenderRequest(format="png", width=6.4, height=3.6, dpi=100))
assert [w.code for w in result.warnings] == ["null_values_omitted"]  # Meta ads: "no data" slot

# A reviewer asks for the month in the axis label: patch, do not rebuild.
spec = apply_patch(spec, [{"op": "replace", "path": "/axes/x/label",
                           "value": "Paid orders, September 2026"}])
```

## 3. Reading a validation issue and patching it

```python
from ieeVisualizer.spec import apply_patch, from_dict, validate

spec = from_dict({
    "schema_version": "1.0",
    "datasets": {"d": {"type": "snapshot", "columns": {
        "step": {"dtype": "int", "unit": "steps", "values": [1000, 2000, 3000]},
        "loss": {"dtype": "float", "unit": "nats", "values": [2.31, 1.87, 1.64]}}}},
    "layers": [{"type": "line", "data": "d", "encoding": {"x": "step", "y": "los"}}],
})
issues = validate(spec)
bad = [i for i in issues if i.code == "unknown_column"][0]
spec = apply_patch(spec, [{"op": "replace", "path": bad.path, "value": "loss"}])
assert not [i for i in validate(spec) if i.severity == "error"]
```
