# ieeVisualizer roadmap: first executable slices

**Status:** working plan, 2026-10-14. Turns §9 of
[VISION_AND_ARCHITECTURE.md](VISION_AND_ARCHITECTURE.md) into branch-sized
work. Decisions marked *provisional* are defaults chosen to unblock work, not
commitments; revisit them in review.

## Target: the first end-to-end slice

From the vision doc, unchanged:

> An installable Python library renders a function and vector field correctly;
> a declarative spec creates a reproducible figure; a template fills data; the
> result exports as PNG and SVG with provenance; tests cover errors,
> axes/units and repeatability. Later browser/live work should reuse, not
> replace, that spec.

Everything below serves that sentence. Browser adapters, live sessions,
diagrams, Manim and reporting integrations come after it and reuse its spec.

## Provisional decisions

| Decision | Default | Why |
|---|---|---|
| Core language | Python ≥ 3.9; serialized specs are plain JSON | Existing code is Python; JSON keeps a later TS/Vue host possible. |
| Spec modelling | stdlib `dataclasses` + explicit validation, no pydantic | Keeps runtime deps at matplotlib/numpy/sympy. Revisit if validation code grows. |
| Spec versioning | every serialized spec carries `schema_version` | Historical report figures must not silently change meaning. |
| Package layout | `ieeVisualizer/spec/`, `render/`, `artifacts/`, `templates/`; legacy `core.Visualizer` kept | Separates spec / backend / export as §3.2 asks; existing API is bridged, not broken. |
| First backend | Matplotlib only, headless (`Agg`) | Phase 1 scope. Browser backend chosen in Phase 2 after an evaluation. |
| Determinism | SVG `svg.hashsalt` fixed, PDF/PNG/SVG `metadata` dates stripped | "Rerender gives the same output from a frozen snapshot" must be testable byte-for-byte for SVG. |
| Missing data | rendering an empty/absent series is an error or an explicit warning on the result, never a silent zero line | Same rule as the Muutto365 products: a figure on screen is a claim. |
| Tests / CI | pytest + ruff, GitHub Actions on push and PR, Python 3.9 and 3.12 | Nothing in the repo is tested today. |

## Phase 0 / Phase 1 branches

Merge order is the numbering. Each branch is a reviewable unit; branches
marked *stacked* are cut from the branch they depend on.

Status as of 2026-10-14: all five branches are implemented and pushed;
none is merged to `main` yet. Branch 5 contains 1-4 (merged into it as its
base), so its test suite covers the whole slice.

### 1. `claude/phase0-baseline` — working legacy API, tests, CI

**Status: done.** `visualize()` (line / surface) and `visualize_vf()` (2D
quiver + optional streamlines, 3D quiver coloured by magnitude) from
callables or SymPy, shared domain/evaluation helpers (non-finite points
masked with a warning, all-non-finite is an error), pyproject with `dev`
extras, CI on 3.9 and 3.12 with a wheel-install smoke test, two examples.

Closes #2 and #3.

- `Visualizer.visualize()` plots `y = f(x)` (dim=2) and `z = f(x, y)` surfaces
  (dim=3) from plain callables or SymPy expressions (`lambdify`), honours
  `linspace`/`N`/`style`/`colormap`/`show`, returns the matplotlib `Figure`.
- `Visualizer.visualize_vf()` draws 2D vector fields (`quiver`, optional
  `streamplot`) and 3D quiver fields.
- Shared domain-generation helper; clear `ValueError`s for unsupported `dim`,
  wrong arity, non-finite-only output.
- `pyproject.toml`: `[project.optional-dependencies] dev`, pytest/ruff config.
- `.github/workflows/ci.yml`: ruff + pytest, Agg backend.
- `examples/` with a function plot and a vector field script.

### 2. `claude/figurespec-core` — typed declarative spec (parallel with 1)

**Status: done.** `ieeVisualizer.spec`: snapshots/refs with units and
provenance and a content hash, all eight marks, axes/legend/annotations,
`validate()` with JSON-pointer issues, canonical JSON (`spec_hash`),
`apply_patch`, `json_schema()`; numbers and datetimes canonicalised so equal
specs hash equally. Reference: docs/SPEC.md.

Pure data model, **no matplotlib import**.

- `DatasetRef` / inline `DataSnapshot` (columns + units + source/provenance
  fields), with a content hash for reproducibility.
- `FigureSpec`: title, coordinate system, axes (label, unit, scale, limits),
  layers of marks, legend, annotations, theme name.
- Core marks: `line`, `scatter`, `bar`, `area`, `histogram`, `heatmap`,
  `quiver`, `function` (expression/sampled), each with typed encodings
  (`x`, `y`, `color`, `u`, `v`, …) mapped to dataset columns.
- `validate()` returning structured, agent-readable errors (path + message +
  code), JSON round-trip (`to_dict`/`from_dict`/`to_json`) with
  `schema_version`, and a patch helper (JSON-pointer-style set/replace) so an
  agent can edit rather than regenerate.
- Exported JSON Schema for the spec (`ieeVisualizer.spec.json_schema()`).

### 3. `claude/matplotlib-adapter` — render + export with provenance (stacked on 2)

**Status: done (in review).** `ieeVisualizer.render`: adapter protocol,
capability-based selection, `MatplotlibAdapter` for every mark in
cartesian2d/3d/polar to PNG/SVG/PDF with fidelity warnings and
byte-identical SVG/PDF; `ieeVisualizer.artifacts.export()` with a
`VisualizationArtifact` manifest and pluggable writers. Reference:
docs/RENDERING.md.

- `RendererAdapter` protocol and `Capabilities` (3d, vector_export,
  animation, interactive, max_points, …); adapter registry and
  capability-based selection that fails loudly when nothing matches.
- `MatplotlibAdapter` rendering every core mark from branch 2 to PNG, SVG,
  PDF; fidelity warnings on the `RenderResult` (e.g. points downsampled,
  unsupported option ignored).
- `VisualizationArtifact` manifest: artifact id, spec hash, dataset hashes,
  render variants with output hashes, library + backend versions, created-at.
  Storage is a pluggable writer; default is a local directory. No URL is an
  identity.
- Repeatability test: same spec + snapshot → byte-identical SVG.

### 4. `claude/template-registry` — two concrete templates (stacked on 2)

**Status: done (in review).** `ieeVisualizer.templates`: versioned
`ComponentTemplate`, registry with `list_templates`/`get`/`describe`/
`catalog`, structured input checks, and the `timeseries` and
`vector_field_2d` templates. Reference: docs/TEMPLATES.md.

- `ComponentTemplate`: name, version, input schema (required columns, unit
  expectations, parameters with defaults), `build(data, **params) ->
  FigureSpec`, example payload.
- Registry with `list()` / `get(name, version)` / `describe()` (agent
  catalogue view).
- Templates: `timeseries` (one or more series, units on y, optional
  observation/forecast split rendered distinctly; no invented uncertainty
  bands) and `vector_field_2d` (function or sampled field → quiver figure).

### 5. `claude/legacy-bridge-and-gallery` — tie it together (after 1, 3, 4)

**Status: done.** `Visualizer.to_spec(fn, dim=, kind="function" |
"vector_field")` builds the spec from the same samples the legacy calls
draw (snapshot provenance: input expression / callable name, domain, `n`,
sampling mode, seed) and `Visualizer.export()` writes PNG/SVG + manifest.
`visualize()`/`visualize_vf()` still draw directly (they must honour `ax=`,
`style=`, streamlines and 3D magnitude colours, which the spec cannot
express); tests assert both routes draw the same data instead. The adapter
gained a surface for sampled 3D functions, a default 2D quiver scale that
keeps arrows inside the plot (on complete grids over linear axes), and a
visible "no data" note on log axes.
`examples/gallery.py` exports every mark and template; README quick start
covers the three entry points; `tests/test_end_to_end.py` covers the slice
sentence clause by clause.

- `Visualizer.visualize()` / `visualize_vf()` build a `FigureSpec` internally
  and render via the adapter, keeping their public signatures.
- `examples/gallery.py` renders every template and mark to `examples/out/`
  with manifests; README quick-start.
- End-to-end test of the slice definition above.

## Later phases (not started)

- **Phase 2 — interactivity and agent access:** evaluate Plotly vs ECharts
  (export fidelity, bundle size, headless/SSR, licence) and write the result
  down before adding either; persistent `InteractionState`; agent tool surface
  (list templates, inspect data schema, create/patch/render/export); Vue 3 host
  demo; browser → static capture.
- **Composition grammar:** `ViewSpec` with nodes/edges/layout for flow and
  architecture diagrams (CONVERSATIONAL_UI_AND_REPORTING §5.2), rendered first
  to SVG; reporting-pipeline diagram as its own test case.
- **Phase 3 — one live + one scientific showcase:** windowed time series with
  backpressure and snapshot export; linked phase-portrait / projection view.
- **Phase 4 — specialist adapters:** Manim, meshes/fields, economic scenario
  views, report composition templates.

## Out of scope for every branch above

Data authority, querying, statistics/numerical solving (ieeLabTools),
evaluation (ieeVal), report scheduling, permissions, email delivery.
