# ieeVisualizer

A unified, agent-accessible framework for **mathematical, scientific, multidimensional, economic, realtime and generative visualization**.

Originally conceived as a simpler interface over `matplotlib.pyplot`, ieeVisualizer's longer-term direction is a coherent visualization layer over specialist plotting, interactive, 3D and animation backends. It focuses on figures, reusable components and templates, declarative visualization specifications, human/agent-guided exploration, and portable visualization artifacts.

**[Read the consolidated vision and architecture](docs/VISION_AND_ARCHITECTURE.md).** See also **[conversational UI and agent-generated company reporting](docs/CONVERSATIONAL_UI_AND_REPORTING.md)** for the OpenAI-inspired interaction model and recurring-report workflow. The **[reporting decision loop](docs/REPORTING_DECISION_LOOP.md)** captures the product rationale: evidence → understanding → follow-up → decision, without automating pointless report production.

> **Project status:** early-stage. The vision describes planned capabilities, not features already implemented. See [existing issues](https://github.com/ieepirzy/ieeVisualizer/issues) for immediate baseline work.

ieeVisualizer is **not** a Grafana replacement or an evaluation/numerical computing engine. **ieeVal and ieeLabTools are independent projects** that may produce results for visualization; neither is a prerequisite for ieeVisualizer.

## Quick start (current API)

What works today is the original `Visualizer` convenience API over Matplotlib:

```bash
pip install -e ".[dev]"   # from a clone; installs pytest and ruff too
```

```python
import numpy as np
import sympy as sp
from ieeVisualizer import Visualizer

viz = Visualizer(N=200, colormap="viridis")  # linspace=False + rng=seed for random sampling

fig = viz.visualize(lambda x: np.sin(x) / (1 + x**2), dim=2, domain=(-6, 6))  # line y = f(x)
x, y = sp.symbols("x y")
fig = viz.visualize(sp.sin(x) * sp.cos(y), dim=3)  # surface z = f(x, y)

fig = viz.visualize_vf(lambda x, y: (-y, x), dim=2, streamplot=True)  # 2D quiver + streamlines
fig = viz.visualize_vf([y, -x, sp.Integer(1)], dim=3, symbols=(x, y, sp.Symbol("z")))  # 3D quiver
fig.savefig("field.png")
```

Every call returns the Matplotlib `Figure` (pass `show=True` to display it, or
`ax=` to draw into existing axes). SymPy inputs are the expression's free
symbols sorted by name unless `symbols=` is given. Points where the function is
nan/inf are left out with a `RuntimeWarning`; if nothing is finite, a
`ValueError` is raised rather than drawing an empty plot. `examples/` has
runnable scripts that write PNGs to `examples/out/`.

## Development priorities

1. Make the existing Python/Matplotlib functions installable, tested and functional.
2. Introduce typed figure specifications, reusable components/templates and provenance-aware exports.
3. Add interactive/browser and agent-facing adapters with persistent visualization state.
4. Validate realtime and scientific/multidimensional workflows before adding further rendering engines.

See the architecture document for backend candidates, live-session concepts, generative UI boundaries, integration scenarios and unresolved design decisions.
