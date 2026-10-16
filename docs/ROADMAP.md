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
| Spec modelling | stdlib `dataclasses` + explicit validation; pydantic (v2) only as an optional extra, `ieeVisualizer[schemas]`, for the new schema features | The core runtime stays matplotlib/numpy/sympy, and nothing in the spec, render or artifact path imports pydantic. Revisited 2026-10-15: a hash-identical migration (PR #18, closed) validated nothing the dataclasses did not; pydantic is used only where it is the validator (see "Phase 2 onward", Foundation). |
| Spec versioning | every serialized spec carries `schema_version` | Historical report figures must not silently change meaning. |
| Package layout | `ieeVisualizer/spec/`, `render/`, `artifacts/`, `templates/`; legacy `core.Visualizer` kept | Separates spec / backend / export as §3.2 asks; existing API is bridged, not broken. |
| First backend | Matplotlib only, headless (`Agg`) | Phase 1 scope. Browser backend chosen in Phase 2 after an evaluation. |
| Determinism | SVG `svg.hashsalt` fixed, PDF/PNG/SVG `metadata` dates stripped | "Rerender gives the same output from a frozen snapshot" must be testable byte-for-byte for SVG. |
| Missing data | rendering an empty/absent series is an error or an explicit warning on the result, never a silent zero line | A figure on screen is a claim: an uncollected value must not look like a measured zero. |
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
  down before adding either (written: docs/BROWSER_RENDERER_EVALUATION.md,
  ECharts primary); persistent `InteractionState`; agent tool surface
  (list templates, inspect data schema, create/patch/render/export); Vue 3 host
  demo; browser → static capture.
- **Composition grammar:** `ViewSpec` with nodes/edges/layout for flow and
  architecture diagrams (CONVERSATIONAL_UI_AND_REPORTING §5.2), rendered first
  to SVG; reporting-pipeline diagram as its own test case.
- **Phase 3 — one live + one scientific showcase:** windowed time series with
  backpressure and snapshot export; linked phase-portrait / projection view.
- **Phase 4 — specialist adapters:** Manim, meshes/fields, economic scenario
  views, report composition templates.

## Phase 2 onward: decided direction (2026-10-15)

Ilari's direction after the first slice. Each item becomes one or more
reviewable branches; order follows dependencies, not importance.

### Foundation

- **Pydantic where it validates, not as a blanket migration.** The existing
  spec, render, artifact and template models stay stdlib dataclasses with the
  explicit validator. A hash-identical migration of them (PR #18, closed)
  left pydantic validating nothing: every rule still lived in `validate()`,
  so it added a dependency and a second model layer for no checked property.
  Pydantic (v2), as the optional extra `ieeVisualizer[schemas]` and never a
  core runtime dependency, is used where it is the validator: agent-contributed
  declarative template schemas, template parameter schemas, report and email
  configuration, and an exported pydantic view of the spec for FastAPI /
  JSON Schema consumers. That view is generated from, and tested against,
  the dataclass spec; the dataclasses and `validate()` stay authoritative,
  and the JSON wire format, `schema_version` and `spec_hash` do not change.
- **Distribution: PyPI only.** No npm packages. The browser / Vue assets are
  built and shipped inside the Python package (package data), so one version
  number covers the spec, the validator, the themes and the browser code.
  ECharts is pinned to an exact version and vendored into the package, not
  fetched from a CDN or resolved by a host's package manager.
- **Python is the single validator.** The browser does no independent
  validation: it renders specs Python has validated, and every edit (agent or
  person) round-trips through Python (`apply_patch` + `validate`) before it is
  rendered. No TypeScript port of `validate()`, no second schema check that
  could disagree with the first.
- **Uncertainty is first-class.** A `band` mark (lower/upper), `errorbar`
  mark, interval encodings on line/scatter/bar (`y_lower`, `y_upper`,
  `interval_level`, `interval_kind`: confidence / credible / prediction /
  min-max / quantile), an `ensemble` mark for many trajectories, and fan
  charts from quantiles. The interval's level and kind are required metadata
  and must appear in the legend or caption; an interval without a stated level
  is a validation error. Never computed or widened by the renderer.
  *Status (branch `claude/uncertainty-marks`):* band and errorbar marks,
  `y_lower`/`y_upper` on line/scatter/bar, the required kind/level (plus
  `standard-error`/`standard-deviation` with an `interval_multiplier`),
  legend/caption statement, `timeseries` 1.1.0 `intervals` (fan charts) and
  the `estimate_comparison` template. Not yet: the `ensemble` mark, horizontal
  intervals (bars and errorbars on x).

### Statistical and mathematical content

- **Arbitrary functions** (exists: expression strings through the bounded
  SymPy parser; legacy callables) gain piecewise and parametric curves and
  implicit plots.
- **Distributions:** `distribution` template for named families (normal,
  log-normal, beta, gamma, Poisson, binomial, Student t, …) via SymPy's
  closed forms — PDF/PMF, CDF, shaded intervals, quantile markers, comparison
  of several distributions. Densities from samples (histogram + KDE with the
  bandwidth recorded), quantile dotplots, ECDF.
- **Bayesian:** prior / likelihood / posterior template, credible intervals
  (equal-tailed and HDI, labelled as such), posterior predictive bands,
  prior→posterior update animation.
- **Stochastic processes:** random walks, Brownian motion / GBM, Monte Carlo
  ensembles with seed recorded, fan charts, hitting-time histograms.
  Simulation helpers are small and seeded (plot-focused, like the RK4 helper
  the vision allows); heavy simulation stays in ieeLabTools.
- **Trajectory animations for non-technical audiences:** a model's projected
  path with its uncertainty growing over time; scale changes shown explicitly
  (linear vs log, doubling-time annotations).

### Structure and composition

- **Graphs and networks:** node-link diagrams with deterministic force and
  layered layouts, adjacency matrices, weighted/directed edges, communities;
  size limits like the view layer. Corporate uses (org charts, process and
  dependency maps) share the machinery with #12's flow diagrams.
- **Agent-contributed templates:** a declarative template format (a pydantic
  schema, which is what validates it; no executable code) that agents can propose; a contribution harness
  checks it builds, validates and renders its example under every theme,
  passes the accessibility checks, and records author and provenance; humans
  approve before registration. Code templates stay human-reviewed PRs.

### Frontends

- **Browser renderer evaluation** (Plotly vs ECharts vs Vega-Lite): export
  fidelity vs the Matplotlib output, bundle size, licence, SSR/headless,
  accessibility. Written decision first.
- **Vue 3 components** that render FigureSpec / ViewSpec JSON with the chosen
  engine and the same themes (tokens exported from `ieeVisualizer.themes`),
  plus interaction state and static capture. Shipped inside the Python
  package with vendored, exact-pinned ECharts (see Foundation); no npm
  publication.

### Accessibility priority

- **Very strict for mobile and tablet viewports**, above all for reporting
  emails. Email clients do not run JavaScript, so email content is Matplotlib
  PNG plus HTML tables, never an interactive chart: figures and tables must
  stay legible at phone width (sizes, text, labels that do not collide or
  truncate), and the table carries every number the figure claims.
- **Disability accessibility (screen readers, colour-vision deficiency) is
  not a current requirement.** The existing WCAG contrast and CVD checks in
  `ieeVisualizer.themes` stay and keep running, but are not extended until
  recipients who need them are on the roadmap.

### Brand and organisation content

- **No organisation-specific content in this library.** Organisation themes,
  metrics, templates and example wording live in the organisation's own
  repository and register through the theme and template registries
  (`ieeVisualizer.themes.register`,
  `ieeVisualizer.templates.default_registry.register`). The built-in
  themes are neutral designs; `tests/test_no_org_specifics.py` fails if a
  known organisation or product name appears in any tracked file other than
  the three owner-authored vision documents.

### Domains

- **AI research figures:** training curves with seed variance, scaling-law
  log–log fits with fit uncertainty, calibration/reliability diagrams,
  confusion matrices, attention heatmaps, embedding projections (labelled as
  projections, never as distances).
- **Corporate reporting with scientific rigour:** themes and business
  templates exist (#13); report composition templates follow views (#12).

### Skills and editorial voice

- **Skills library** (`skills/` in this repo, SKILL.md format): rigorous
  presentation ("when do the numbers lie": baselines, denominators, base
  rates, truncated axes, dual axes, cherry-picked windows, survivorship,
  Simpson's paradox, uncertainty display), chart choice, accessibility,
  report writing. Adapted only from sources whose licence allows it, with
  attribution; otherwise written from cited principles.
- **Editorial voice** modelled on The Wall Street Journal's news style and
  its graphics guide (principles, not copied text).

### Backends

- **ffmpeg** stays an optional system dependency, detected at runtime
  (~35 MB of libraries). **Manim** stays an optional extra, never a default:
  it adds ~340 MB to an environment (its own bundled ffmpeg, SciPy,
  Cairo/Pango), needs system Pango headers to build, LaTeX for typeset maths,
  and renders seconds per frame where Matplotlib takes tens of milliseconds.
  If heavy renders become common, run them in a separate render container.

## Breaking changes

* **2026-10-15:** the two organisation-named built-in themes
  (`<organisation>-admin-dark`, `<organisation>-report-light`) were renamed
  to `dashboard-dark` and `report-light` and redesigned as neutral themes, with no deprecated
  aliases (pre-1.0, unpublished). An old name now renders with `default`
  plus an `unknown_theme` warning, not an error. Organisation themes register
  through `ieeVisualizer.themes.register`. Details: THEMES.md, "Breaking
  changes".

## Out of scope for every branch above

Data authority, querying, statistics/numerical solving (ieeLabTools),
evaluation (ieeVal), report scheduling, permissions, email delivery.
