# ieeVisualizer

A unified, agent-accessible framework for **mathematical, scientific, multidimensional, economic, realtime and generative visualization**.

Originally conceived as a simpler interface over `matplotlib.pyplot`, ieeVisualizer's longer-term direction is a coherent visualization layer over specialist plotting, interactive, 3D and animation backends. It focuses on figures, reusable components and templates, declarative visualization specifications, human/agent-guided exploration, and portable visualization artifacts.

**[Read the consolidated vision and architecture](docs/VISION_AND_ARCHITECTURE.md)** and the **[roadmap of first executable slices](docs/ROADMAP.md)**. See also **[conversational UI and agent-generated company reporting](docs/CONVERSATIONAL_UI_AND_REPORTING.md)** for the OpenAI-inspired interaction model and recurring-report workflow. The **[reporting decision loop](docs/REPORTING_DECISION_LOOP.md)** captures the product rationale: evidence → understanding → follow-up → decision, without automating pointless report production.

> **Project status:** early-stage. The vision describes planned capabilities, not features already implemented. See [existing issues](https://github.com/ieepirzy/ieeVisualizer/issues) for immediate baseline work.

ieeVisualizer is **not** a Grafana replacement or an evaluation/numerical computing engine. **ieeVal and ieeLabTools are independent projects** that may produce results for visualization; neither is a prerequisite for ieeVisualizer.

## Quick start

```bash
pip install -e ".[dev]"   # from a clone; installs pytest and ruff too
python examples/gallery.py  # every mark and template -> examples/out/ (PNG, SVG, manifests)
```

There are three ways in. All of them end in the same place: a
[FigureSpec](docs/SPEC.md) that renders to PNG/SVG/PDF and exports with a
provenance manifest ([docs/RENDERING.md](docs/RENDERING.md)).

**1. Legacy: from a function to a figure.** `Visualizer` plots callables or
SymPy expressions with Matplotlib and returns the `Figure`. `to_spec()` gives
the same samples as a declarative spec; `export()` writes PNG + SVG +
`manifest.json` recording the expression, domain, `n` and sampling mode.

```python
import numpy as np
import sympy as sp
from ieeVisualizer import Visualizer

viz = Visualizer(N=200, colormap="viridis")  # linspace=False + rng=seed for random sampling
x, y = sp.symbols("x y")

fig = viz.visualize(lambda t: np.sin(t) / (1 + t**2), dim=2, domain=(-6, 6))  # line y = f(x)
fig = viz.visualize(sp.sin(x) * sp.cos(y), dim=3)                  # surface z = f(x, y)
fig = viz.visualize_vf(lambda x, y: (-y, x), dim=2, streamplot=True)  # quiver + streamlines
fig.savefig("field.png")

spec = viz.to_spec([-y, x], dim=2, kind="vector_field", units={"vector": "m/s"})
artifact = viz.export(sp.sin(x), "out", dim=2, units={"x": "rad"})  # out/<artifact_id>/
print(artifact.artifact_id, [v.format for v in artifact.variants])
```

`visualize()` / `visualize_vf()` accept `ax=` to draw into existing axes and
`show=True` to display. SymPy inputs are the expression's free symbols sorted
by name unless `symbols=` is given. Points where the function is nan/inf are
left out with a `RuntimeWarning` (and stored as null by `to_spec`); if nothing
is finite, a `ValueError` is raised rather than drawing an empty plot.

**2. Declarative spec: JSON in, reproducible figure out.** The same spec and
library versions give byte-identical SVG.

```python
from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.spec import from_json

spec = from_json("""{"schema_version": "1.0", "title": "Cooling",
  "datasets": {"d": {"type": "snapshot", "columns": {
    "t": {"unit": "min", "values": [0, 10, 20]},
    "T": {"unit": "°C", "values": [90.0, null, 41.0]}}}},
  "layers": [{"type": "line", "data": "d", "encoding": {"x": "t", "y": "T"}}]}""")
result = render(spec, RenderRequest(format="svg"))  # axis labels "t (min)", "T (°C)"
print(result.sha256, [w.code for w in result.warnings])  # the null is a gap, reported
```

**3. Template: a versioned recipe fills your data into a spec.**

```python
from ieeVisualizer import templates
from ieeVisualizer.artifacts import LocalDirectoryWriter, export
from ieeVisualizer.spec import DataSnapshot

print([t["name"] for t in templates.list_templates()])
# ['bar_comparison', 'dependency_map', 'funnel', 'network', 'org_chart', 'timeseries', 'vector_field_2d']
data = DataSnapshot.from_columns({"day": [1, 2, 3, 4], "load": [3.2, 3.9, None, 4.4]},
                                 units={"day": "d", "load": "kWh"})
spec = templates.build("timeseries", data, {"time_column": "day", "value_columns": ["load"]})
artifact = export(spec, ["png", "svg"], LocalDirectoryWriter("out"))
print(artifact.artifact_id)  # out/<artifact_id>/{figure.png,figure.svg,spec.json,theme.json,manifest.json}
```

`templates.describe(name)` returns a template's inputs, parameters and an
example payload; see [docs/TEMPLATES.md](docs/TEMPLATES.md).
Graphs and networks (org charts, dependency maps, force layouts, adjacency
matrices) have their own spec and renderer; see [docs/NETWORKS.md](docs/NETWORKS.md).

**Themes** are versioned, accessibility-checked data (palette, colour maps,
type, role styling, fi-FI number format, email/slide sizes):
`FigureSpec(theme="muutto365-report-light")` or
`themes.get("muutto365-admin-dark").request("slide")`. The manifest records
the theme's name, version and hash and stores it as `theme.json`; see
[docs/THEMES.md](docs/THEMES.md).

## Rendering a spec

A declarative [FigureSpec](docs/SPEC.md) renders to PNG/SVG/PDF through a
capability-selected adapter (Matplotlib today) and exports with a provenance
manifest. See [docs/RENDERING.md](docs/RENDERING.md). Figures, text, KPI
tiles, tables, caveats and flow diagrams compose into a ViewSpec rendered to
SVG, static HTML or PNG; see [docs/VIEWS.md](docs/VIEWS.md).

```python
from ieeVisualizer.artifacts import LocalDirectoryWriter, export
from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.spec import Axis, DataSnapshot, FigureSpec, LineMark, Provenance

data = DataSnapshot.from_columns(
    {"week": ["2026-09-07", "2026-09-14", "2026-09-21"], "revenue": [1200.0, None, 1350.5]},
    units={"revenue": "EUR"}, dtypes={"week": "datetime"},
    provenance=Provenance(source="finance-service", retrieved_at="2026-10-14T06:00:00+03:00"),
)
spec = FigureSpec(
    title="Weekly revenue",
    datasets={"observed": data},
    layers=[LineMark(data="observed", encoding={"x": "week", "y": "revenue"})],
    axes={"x": Axis(label="Week"), "y": Axis(label="Revenue")},  # y axis: "Revenue (EUR)"
)

result = render(spec, RenderRequest(format="svg"))
print(result.media_type, [w.code for w in result.warnings])  # the null week is a gap, reported

artifact = export(spec, ["png", "svg"], LocalDirectoryWriter("out"))
print(artifact.artifact_id, artifact.variant("png").sha256)  # out/<artifact_id>/manifest.json
```

## Agent skills library

[`skills/`](skills/README.md) holds agent skills for honest figures and
reports, packaged as a Claude Code plugin. The skill text under `skills/` is
licensed CC BY 4.0 (with third-party portions under the licences listed in
[`skills/NOTICE.md`](skills/NOTICE.md)); the code in this repository is MIT.

## Development priorities

1. Make the existing Python/Matplotlib functions installable, tested and functional.
2. Introduce typed figure specifications, reusable components/templates and provenance-aware exports.
3. Add interactive/browser and agent-facing adapters with persistent visualization state.
4. Validate realtime and scientific/multidimensional workflows before adding further rendering engines.

The branch-by-branch plan for the first end-to-end slice, with the status of
each branch, is in [docs/ROADMAP.md](docs/ROADMAP.md). See the architecture document for backend candidates, live-session concepts, generative UI boundaries, integration scenarios and unresolved design decisions.
