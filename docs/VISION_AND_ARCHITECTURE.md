# ieeVisualizer: consolidated product vision and architecture

**Status:** design synthesis, 2026-10-15. Direction and proposals, NOT an implementation claim. This expands the original Matplotlib wrapper concept. Historical distinctions and undecided choices are explicit.

## 1. Mission

ieeVisualizer is a **unified visualization framework and agent-accessible library** for representing, manipulating, exploring, animating, exporting and presenting data and models across mathematical, scientific, statistical, economic, financial, operational and other domains. It provides a coherent interface to existing specialist visualization/rendering engines instead of forcing each project or AI agent to learn and integrate each one separately.

The primary differentiator is **on-demand representation of information that was not anticipated by a prebuilt dashboard**. Agents or applications can create figures, compose reusable components, explore data interactively, and conduct a browser-based guided presentation. Static dashboards, recurring reporting and conventional plots are supported uses, **not** the product identity. This is **not a Grafana replacement** and does not need to build proprietary raw-data storage or query engines.

### Strong independence boundary

**ieeVisualizer is independent of ieeVal.** ieeVal is a separate headless evaluation/experiment project and one *possible producer* of data and run artifacts. ieeVisualizer may visualize ieeVal outputs, but must not depend on its schemas, release schedule, retrieval-specific concepts or execution engine. Equally, **ieeLabTools** handles serious numerical solvers/research computation. A small plotting-focused RK4 helper or demonstration may live here for vector fields, phase portraits and bifurcation visualization, but do not turn ieeVisualizer into a general-purpose scientific computing/solver package. The distinction between computation and depiction must survive all integrations.

## 2. Scope and examples

- **Figures and conventional plotting:** line/scatter/bar/area/histogram/density/box/violin/heatmap, subplots, multiple axes, legends, labels, annotations, facets, themes and publication-quality exports.
- **Mathematical visualization:** functions and surfaces; symbolic expressions via SymPy where appropriate; 2D/3D vector fields, quivers and streamlines; trajectories, phase portraits, bifurcation diagrams, parameter sweeps, differential-equation and geometry intuition, coordinate transformations, geometric objects.
- **Higher-dimensional data:** tensors and vector data, embeddings, dimensional projections (e.g. PCA/UMAP/t-SNE provided by analysis substrates), linked projections, pairwise comparisons, slices, brushing, 3D and optional immersive modes. Do not conflate a projection with original-space distances.
- **Scientific and spatial visualization:** meshes, wireframes, surfaces, volumes, point clouds, fields, graphs/networks, trajectories, simulations and time-dependent data; units, errors and uncertainty must remain meaningful.
- **Economic/financial visualization:** time series, stacked components, cumulative flows, distributions, forecasts with uncertainty bands, scenarios, price/volume curves, supply-demand/equilibrium, sensitivity plots, cohort and funnel views, revenue/cost/margin trends, correlation matrices and multi-axis comparisons. Make temporal alignment, currency/units, base periods, aggregation and assumptions explicit.
- **Real-time and temporal:** updating curves/plots, streamed telemetry/simulation state, live animations, incremental graph changes, efficient refresh and interactive time navigation. Preserve the distinction between a reproducible snapshot and a live session.
- **Animation and explanation:** moving camera, evolving graphs/functions/geometry and mathematical exposition. **3Blue1Brown's Manim** is a candidate animation backend, not a universal renderer.
- **Reusable visual components:** figures, tables, metrics, diagrams, mathematical labels, annotations, inspectors, drilldowns, legends, controls and explanatory blocks in larger composed views.
- **Generative UI:** given data + intent, select/parameterize existing components and templates by default. Custom components/code generation only when the registry cannot express the visualization, with validation and safe execution constraints.
- **Artifacts and reports:** static/vector/video exports, browser-embedded interactive views, agent-generated business updates, emails and presentations. E.g. evidence-backed Muutto365 operational briefings three times weekly (cadence was contemplated, not finalized) with reusable templates and static assets for email.

## 3. Design principles

1. **Reuse engines, unify contracts.** Wrap/render through Matplotlib, Plotly, ECharts, browser/WebGL/3D engines, Manim or equivalents as appropriate. These are candidate adapters, **not chosen mandatory dependencies**.
2. **Typed declarative specification first.** Separate data, analytical computation, figure/view specs, backend execution, interaction and export.
3. **Components first, freeform last.** Registries and versioned validated templates reduce arbitrary agent-generated application code. They must not prevent unusual scientific views.
4. **Respect specialization.** Capability negotiation and backend-specific extensions are preferable to a weak “one API supports everything” promise.
5. **Data provenance and reproducibility.** Preserve datasets and query/run references, transformation definitions, axis units and scales, assumptions, timestamps, uncertainties, backend/plugin versions and output manifests.
6. **Human and agent parity.** Programmatic plotters, interactive browser users and agents should work against the same figure model and persistent view state where feasible.
7. **Flexible persistence.** Local/in-memory, object-store and host-provided storage are options; no mandatory S3, excessive egress or asset URL as identity. Snapshot the minimum needed for reproducibility without copying giant source datasets by default.
8. **Host boundary.** An integrated frontend overlay may be Vue 3, but the core visualization schema is not Vue-specific. The host owns auth, ACLs, deployment, app navigation and privileged data access.
9. **Ship a narrow vertical slice before a platform.**

## 4. Conceptual architecture (proposed)

```text
  source data / queries / simulations / analytics / external run artifacts
                             |
              data binding + transforms (typed)
                             |
         FigureSpec / ViewSpec + component template registry
                             |
           capabilities / backend adapter selection
              /       |         |           \
       Matplotlib  Plotly/ECharts  Manim   WebGL/3D
              \       |         |           /
                 render + interactive state
                             |
                artifact / export / live session
                             |
     Python | Vue host/overlay | notebook | agents | email | reports
```

**Logical contracts** (not final class names):
- **DatasetRef / DataSnapshot:** typed tabular, tensor, graph, mesh, point, vector or stream input; schema, units, provenance, versions and access characteristics. Small inline datasets; references/query handles for large ones.
- **FigureSpec:** visualization intention, coordinate system, marks, data mapping, scales, units, legends, labels, annotations, uncertainty, theme, desired interactivity and backend capabilities.
- **ViewSpec:** composition/layout of figures plus text, tables, controls, comparative views, provenance inspectors and interactions.
- **ComponentTemplate:** versioned input schema, constraints, defaults, parameters, interactions and renderer support. Provide a registry/catalog and preview/example.
- **RenderRequest / RenderResult:** requested target (PNG/SVG/PDF/HTML/interactive/video/etc.), viewport, quality, selection/parameter values, renderer choice and fidelity warnings.
- **VisualizationArtifact:** stable ID, versioned spec, data bindings, provenance/manifest, render variants, status, expiry/retention and storage adapter refs. Do not bind identity to an S3 URL.
- **LiveSession:** source subscriptions, event ordering/watermark, incremental updates, pause/resume, bounded buffers, cancellation, backpressure, heartbeat, snapshot/export and reconnection semantics.
- **InteractionState:** active figure, cursor/selection, filtered ranges, camera/zoom, layers, parameter controls, annotations and history; where permitted, agent and human can share or hand off state.

Do not impose one serialization on all data shapes or force every engine to mimic Matplotlib. Backend adapters should explicitly report support for 3D, animation, vector export, shader geometry, large point count, interactive brushing, incremental updates, etc.

### Figure lifecycle

`create → bind/populate → transform → style → annotate → compose → render → inspect/focus/compare → export/share → optionally update live`.

A render is a derivative of the spec and source, not the source of truth. Support rerendering in another medium, variant comparison, interactive-to-static capture, and inspection of the underlying evidence.

## 5. Agent UX and generative interface

Agents need **skills/tools for intent-driven authoring**: enumerate available visual primitives, inspect data schema, recommend a figure type with rationale, create from a template, bind data, change scales/styles, compose multiple views, annotate, explain, focus an outlier, compare alternatives, ask to render/export and retrieve artifacts. Provide structured JSON-schema-like contracts, validation, understandable failures and an edit/patch API rather than making an agent regenerate the entire page on each step.

Default agent behavior: **pick a known template → fill data + parameters → render → inspect result → patch**. A view/template library is a force multiplier, not a cage. Escalate to custom figure grammar, extension or bespoke component only when necessary. If generated code is supported, treat it as untrusted, sandbox and review it, and never assume rendering code may access credentials or other application state.

**Interactive presentations:** an agent can optionally drive a browser to focus, pan, zoom, highlight, change parameter values, compare layers, and narrate an evidence-backed analysis. Prefer declarative commands targeting persistent visualization state; direct browser interaction is an optional, scoped escape hatch. A callable **Vue 3 overlay/runtime** is a useful integration target, but keep UI framework specifics outside core specs. Human interaction should not be silently overwritten by an autonomous agent.

**Composition:** charts and components can be used in static dashboards, briefs, internal app panels, or generated full views. A generated view is not automatically a new permanent product dashboard.

## 6. Backend and library strategy

Candidate adapters, subject to evaluation:
- **Matplotlib / NumPy / SymPy:** foundational Python figures, symbolic evaluation, plotting styles and high-quality publication/static export. Make the existing minimal library actually function before over-architecting.
- **Plotly and/or ECharts:** rich standard interactive graphs, browser interaction and live business plots.
- **Manim:** mathematical animation/explanation and offline video/rendered sequences.
- **WebGL/Three.js or scientific 3D engine(s):** meshes, fields, large point clouds, spatial and high-dimensional projections.
- **Specialized backends when justified:** graph/network visualization, volumetric data, notebook/display protocols, scientific meshes or GPU acceleration.

No frontend library, backend engine, wire protocol or interop technology is irrevocably selected by these notes. Test rendering capability, export fidelity, licensing, bundle cost, performance, deployment complexity and SSR/headless support. Build adapters without recreating all renderer primitives.

**Data libraries are not rendering engines:** numerical integration, PCA, statistics and economic modeling generally run in separate analysis libraries or services. ieeVisualizer renders/coordinates their typed results and may include tightly scoped helper transforms.

## 7. Realtime and multidimensional specifics

- Model **snapshot**, **time-varying sequence/animation**, and **live-streamed data** separately.
- Support bounded/windowed plotting and incremental/delta updates, configurable refresh frequency, downsampling/LOD, batching, asynchronous rendering and backpressure. Preserve fidelity where necessary (peaks, outliers).
- Scientific controls: parameter sliders, linked axes/projections, brushing, slice planes, camera and annotation state. A 2D UMAP or PCA view must never imply it exactly reproduces high-dimensional geometry.
- Durable outputs from streaming sources should record the capture interval/time, underlying provenance and replayability limits.
- For economic and business feeds, be explicit about freshness, delays, revisions, calendar/tz boundaries, denominators, base currencies, forecasting vs observations and uncertainty.

## 8. Embedding in the wider ecosystem

**ieeVisualizer is a horizontal visualization component**, not an appendage to one service. External producers may include ieeLabTools, ieeVal, Mirarun, Miradesign, ieeVisualizer's own small plot helpers, analytical databases and scientific simulations. Each remains independently deployable/maintainable.

- **Mirarun:** agents request/create/modify/export figures using explicit scoped tools; outputs are addressable artifacts.
- **Miradesign:** can propose explanatory views and communication artifacts, with approval boundaries; do not couple visualization lifecycle to task dispatch.
- **Muutto365:** reusable evidence-backed visualizations for periodic email and operational/business updates; templates and permissions are application responsibility.
- **ieeVal:** optional consumer/producer interface: embedding evaluation and diagnostics from immutable experiment artifacts. Its evaluation methodology, benchmark orchestration and statistics belong to ieeVal, not here.
- **ieeLabTools:** optional scientific computation producer; owns heavyweight numerical solving.
- **Data engines, telemetry and storage:** existing systems remain authoritative; avoid rebuilding query, tracing, historical storage or access governance inside ieeVisualizer.

Example exchange:
```json
{
  "figure_type": "timeseries",
  "source": {"kind": "dataset_ref", "id": "authorized-query-result"},
  "mapping": {"x": "timestamp", "y": "conversion_rate"},
  "units": {"y": "ratio"},
  "style": {"show_uncertainty": true},
  "target": "svg"
}
```
This is **illustrative intent**, not a committed API or an excuse to fabricate confidence bands when unavailable.

## 9. Roadmap: executable slices, not a feature explosion

**Phase 0: restore working baseline.** Existing issues #1–#3 note empty `pyproject.toml`, no-op `Visualizer.visualize()`, and no-op `visualize_vf()`. Validate against current code before closing. Make installable with tests; demonstrate a function plot and 2D vector field using Matplotlib, NumPy and SymPy where appropriate. Retain/bridge existing API.

**Phase 1: minimal figure model.** A typed FigureSpec, a handful of core marks, renderer adapter contract, Matplotlib adapter, reproducible PNG/SVG export, basic metadata, tests, example gallery, and a component/template registry with two concrete templates.

**Phase 2: interactivity and agent access.** Add a browser adapter (Plotly or ECharts after an evaluation), persistent view state, commands to create/bind/annotate/focus/export, agent skills and a simple Vue 3 host demo. Include static capture from browser views and a documented permission boundary.

**Phase 3: one live + one scientific showcase.** Incrementally updating time-series with backpressure and snapshots; linked multidimensional scatter/3D or phase-portrait controls. Validate performance/precision; resist generalizing until both work.

**Phase 4: specialist adapters.** Manim mathematical animation, advanced spatial meshes/wireframes/fields, comparative economic scenario visualizations and polished report/presentation templates. Expand only based on actual consuming workflows.

### Definition of the first end-to-end slice

An installable Python library renders a function and vector field correctly; a declarative spec creates a reproducible figure; a template fills data; the result exports as PNG and SVG with provenance; tests cover errors, axes/units and repeatability. Later browser/live work should reuse, not replace, that spec.

## 10. Unresolved decisions (avoid false commitments)

- Formal public API/language boundaries: Python core, serialized specs, TS packages?
- Renderer selection and capability negotiation schema.
- Final component registry, layout grammar and accessibility model.
- How to represent large/lazy/streaming tensors, meshes and remote data efficiently.
- Render service deployment and isolation for untrusted agent inputs.
- Persistence provider, retention policy, S3 vs local/object store economics.
- Browser runtime, realtime protocol, shared session control and concurrent edits.
- Animation timeline grammar vs Manim-native scene escape hatch.
- Library licensing, versioning and plugin ABI.
- Economic/scientific golden test datasets and rendering fidelity metrics.

## 11. Context provenance

This synthesis combines prior conversations (especially 2026-02-22, 2026-07-18, 2026-07-24, 2026-10-08, 2026-10-09 and 2026-10-13), MiraDB observations **#351, #398, #762, #777**, and existing GitHub issues **#1–#3**. Historical MiraDB #351 describes a *possible ieeVal integration*, **not** ieeVisualizer's identity. October discussions specifically sharpened: horizontal visualization library, template/component-first generative UI, agent tools, no Grafana replacement, browser-driven presentation, real-time + scientific rendering, and storage-independent artifacts.

**Guiding rule:** the same figure should be constructible by a person or agent, rendered through a suitable engine, inspected interactively if supported, then exported and reused without becoming entangled with the application that asked for it.
