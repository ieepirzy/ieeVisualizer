# Component templates (`ieeVisualizer.templates`)

A template is a named, **versioned** recipe that turns data + parameters into a
[FigureSpec](SPEC.md). It declares its inputs up front (columns with accepted
dtypes and unit expectations, typed parameters with defaults and allowed
values), checks a request against them with structured errors, and stamps its
identity on every spec it builds. Templates produce specs only; rendering is a
separate step. Importing the package does not import matplotlib, numpy or
sympy.

```python
from ieeVisualizer import templates

templates.list_templates()             # [{"name", "version", "versions", "title", "description"}, ...]
templates.describe("timeseries")       # JSON catalogue entry: columns, parameters, notes, example
templates.catalog()                    # {"templates": [describe(...) for every template]}

ts = templates.get("timeseries")       # latest release; get(name, "1.0.0") for an exact version
ts.check(data, value_columns=["revenue"])        # list[ValidationIssue]; never raises
spec = ts.build(data, value_columns=["revenue"]) # FigureSpec, or raises TemplateInputError
spec = ts.build_payload(ts.example())            # {"data": ..., "params": {...}}
spec = templates.build("timeseries", data, {"value_columns": ["revenue"]})
```

`data` may be a `DataSnapshot`, its serialized dict (`{"type": "snapshot",
"columns": {...}}`) or a plain `{column: [values]}` mapping. The input is
copied, never mutated.

## Agent workflow

1. `list_templates()` → pick a template; `describe(name)` for its schema.
2. Fill `{"data": ..., "params": {...}}` (start from `describe(name)["example"]`).
3. `check(...)` or `build(...)`. Every problem comes back as a
   `ValidationIssue(path, code, message, severity)`, the class `validate()`
   uses. `path` points into the request: `/params/<name>`,
   `/data/columns/<column>` or `/data`. Messages name the fix and offer a
   did-you-mean for misspelt parameters and columns.
4. Patch the produced spec with `apply_patch` rather than rebuilding it for
   small changes.

Codes: `unknown_parameter`, `missing_parameter`, `invalid_parameter`,
`conflicting_parameters`, `missing_data`, `invalid_data`,
`unresolved_reference`, `empty_dataset`, `missing_column`, `wrong_dtype`,
`all_null_column`, `null_in_key_column`, `invalid_column_values`,
`mixed_units`, `unit_override_conflict`, `invalid_expression`,
`expression_too_large`, `unknown_symbol`, and the interval codes the spec
uses (`missing_interval_kind`, `invalid_interval_kind`,
`missing_interval_level`, `invalid_interval_level`,
`unexpected_interval_level`, `missing_interval_multiplier`,
`invalid_interval_multiplier`, `unexpected_interval_multiplier`,
`invalid_interval_quantiles`, `unexpected_interval_quantiles`,
`conflicting_interval_level`; see
[SPEC.md](SPEC.md#intervals)); warnings `missing_unit`, `empty_series`,
`no_forecast_rows`, `non_monotone_counts`, `nonfinite_samples`,
`null_samples_dropped`. The full table is in the docstring
of `ieeVisualizer/templates/base.py`. Registry errors (`TemplateRegistryError`)
use `unknown_template`, `unknown_template_version`, `duplicate_template`,
`invalid_version`, `invalid_name`.

Numeric parameters (`target`, a numeric `forecast_from`, ranges, constants)
must be finite: NaN, infinities and integers beyond float64 are an
`invalid_parameter` issue from `check()` and a `TemplateInputError` from
`build()`, never a failure of the spec built from them.

Errors stop the build. Warnings do not; they are copied into the spec as
`metadata["template"]["warnings"]`. `build()` also runs `validate()` on its
own output and raises `SpecValidationError` rather than return a spec with
errors.

## Provenance in the spec

Every built spec carries:

```json
"metadata": {"template": {
  "name": "timeseries", "version": "1.0.0",
  "params": {"...": "every parameter, defaults included"},
  "input_hash": "sha256:... of the input snapshot as given",
  "warnings": ["... only when there were any"]
}}
```

plus template-specific facts (`forecast` split summary and `bands` for
timeseries; `expressions`, `parsed` and `grid_order` for an expression vector
field).

## Built-in templates

### `timeseries` 2.0.0 (and 1.0.0)

`get("timeseries")` is 2.0.0; `get("timeseries", "1.0.0")` still builds
exactly what it did. **2.0.0 is a breaking change for unversioned callers**
(docs/ROADMAP.md "Breaking changes"): passing `lower_column` without a
version is now an error, and unversioned specs record version 2.0.0. It is
1.0.0 with one change, to uncertainty:
`lower_column`/`upper_column` (two unlabelled dotted lines) are replaced by
**`intervals`**, a list of `{"lower", "upper", "kind", "level"?,
"multiplier"?, "quantiles"?}`: two bound columns of the single value column (or the long
format's one), the interval's kind and its level as a fraction (central
for `quantile`), the SE/SD multiplier, or explicit `quantiles` [lower,
upper], checked as the spec checks them (paths
`/params/intervals/<k>/kind` etc.). Each becomes a `band` layer per segment
(observed / forecast rows; forecast bands are forecast-role, hatched),
drawn before the value line in its colour (`color_of`) and labelled in the
legend ("80% prediction interval (forecast)"). Several intervals make a fan
chart: they are ordered widest level first. Bounds are copied as given; a
segment with no row holding both bounds gets no band; a lower bound above
its upper bound is `invalid_column_values`. The layers are recorded in
`metadata["template"]["intervals"]`. **2.0.1** (non-breaking, the default
since it was published; 2.0.0 stays registered and builds exactly what it
did): with `connect_forecast`, the observed rows copied into dataset
`forecast` are also recorded, by identity (`{"x": <time>}`, plus `"series"`
in long format), in `metadata["template"]["forecast"]["boundary_rows"]`. A
renderer exempts a row from missing-bound warnings only while it has such
an identity and is still a copy of an observed row; a 2.0.0 spec (no key)
gets the same copy rule from its own split, so neither gains a false
warning. Passing `lower_column` to 2.0.0 is
`unknown_parameter` with a message saying how to state the interval.

Example payload (2.0.0, abridged): `"intervals": [{"lower": "p10", "upper":
"p90", "kind": "prediction", "level": 0.8}, {"lower": "p25", "upper": "p75",
"kind": "prediction", "level": 0.5}]` (`describe("timeseries")["example"]`).

The rest of this section is common to both versions.

Lines over time from `time_column` (datetime, or numeric with a unit) and
`value_columns` (wide format), or one value column plus `series_column` (long
format).

- **Forecast split:** `forecast_from` (rows with time ≥ it are forecast) or
  `role_column` (`"observed"`/`"forecast"`, or bool true = forecast). Forecast
  rows go to dataset `forecast` and layers with `role="forecast"` and a dashed
  style hint. Each row is in exactly one layer; `connect_forecast=true` also
  copies the last observed point of each series into the forecast layer so
  the lines join (recorded as `shared_boundary`).
- **Units:** the y unit comes from the columns. All drawn columns must share
  one; `y_unit` only fills in a missing unit and is an error if it
  contradicts a declared one. Nothing is converted.
- **Uncertainty (1.0.0):** only from caller-supplied `lower_column`/`upper_column`,
  never computed. Spec 1.0 has no band mark, so bounds are two dotted line
  layers, paired in `metadata["template"]["bands"]`. Bounds of forecast rows
  are `role="forecast"` layers (drawn as forecasts). Each bound layer has the
  style hint `color_of: "<value layer name>"` so the renderer draws it in its
  value series' colour, and both bounds of a segment are named
  `"<value> range (<lower> to <upper>)"`, which the matplotlib renderer shows
  as one legend entry per series.
- **Nulls:** a null value stays null (a gap). A null time, series or role is
  an error because the row cannot be placed. Rows are ordered by time.
- `target` adds a horizontal reference line; axis labels default to the
  columns' descriptions, else names.

### `bar_comparison` 1.0.0

Grouped bars: `category_column` x `value_columns` (one bar layer per
measure; the renderer dodges bar layers that share categories side by side
in layer order, so the template sets no `bar_width`/`bar_offset`).

- **Previous period:** `previous_column` (one measure only, else
  `conflicting_parameters`) adds a bar left of (horizontal: below) the
  current one, role `reference` (lighter, the theme's neutral colour), named
  `"<measure>, <previous_label>"` in the legend. It is the first layer (that
  is what puts it left/below), so the legend lists it first for vertical
  bars and last for horizontal ones, in the order the bars are read. It is
  observed data; the role only changes how it is drawn.
- **Order:** rows as given, or `sort` `ascending`/`descending` by the first
  measure (a null sorts last). `orientation` `auto` turns horizontal for a
  label over 12 characters or over 12 categories; horizontal lists the first
  category on top. `metadata["template"]["category_order"]` is the reading
  order.
- **Values:** a null stays null (the renderer writes "no data" in its slot);
  negative values are drawn below a solid zero line. One row per category
  (a repeat or a null category is an error). All drawn columns share one
  unit (`mixed_units` otherwise); nothing is converted.
- `value_labels` (default true) prints each value in the render theme's
  locale (`1 234,50 €` for EUR under `report-light`). Vertical
  bars too narrow for their labels get them turned upright on the bar; the
  renderer never lets two labels overlap (THEMES.md).

### `estimate_comparison` 1.0.0

Point estimates with caller-supplied intervals across groups (A/B test arms,
model variants): one `errorbar` layer, `estimate_column` per `group_column`
on a category axis in row order, `lower_column`/`upper_column` its bounds.
`interval_kind` is required (one of the spec's kinds) with `interval_level`
`interval_multiplier` or `interval_quantiles` as the kind needs; the legend
states it. Nothing is
computed: no difference, no significance. For a claim that B beats A, pass
the difference and its interval and `reference=0` (a "No difference" line),
rather than two intervals whose overlap the reader must interpret.

- A null estimate draws the interval alone; a null bound leaves that group's
  interval out (the renderer warns `interval_bounds_missing`).
- A repeated group (`invalid_column_values`), a null group
  (`null_in_key_column`), a lower bound above its upper bound
  (`invalid_column_values`) or bounds in another unit than the estimate
  (`mixed_units`) is an error. `caption` should say n and the method.

### `funnel` 1.0.0

Horizontal bars of `count_column` per `stage_column`, first stage on top,
rows in funnel order.

- **Derived values (transparent, not analytics):** step conversion
  `count[i] / count[i-1] * 100` and overall `count[last] / count[first] *
  100`. `metadata["template"]["derived"]` records the formulas and, per
  step, numerator, denominator and rate; the dataset gains a
  `step_conversion` column (unit `%`, `denominator` described). A zero
  denominator gives a null rate, shown as "rate undefined", never 0 or
  infinity.
- A null count, a negative count, a repeated or null stage, or fewer than
  two stages is an error. Rising counts are allowed with a
  `non_monotone_counts` warning (stages may be counted over different
  windows); rates above 100 % are shown as computed.
- Rates are text annotations at the right; the overall rate is appended to
  the caption. Text the template writes uses the `theme` parameter's locale
  (`18,5 % of previous` for fi-FI) and records it as
  `metadata["template"]["label_locale"]`; rendering under a theme with a
  different locale adds a `label_locale_mismatch` warning.

`bar_comparison` and `funnel` take `theme` (a registered theme name, else `invalid_parameter`),
which becomes the spec's theme.

### `distribution` 1.0.0

Named probability distributions from their closed forms, with no data:
`distributions=[{"family", "params", "label"?}]`, families `normal` {mu,
sigma}, `lognormal` {mu, sigma of log x}, `beta` {alpha, beta}, `gamma`
{shape, scale}, `student_t` {df, loc = 0, scale = 1} (continuous: the PDF
is a line) and `poisson` {lambda}, `binomial` {n, p} (discrete: the PMF is
bars, never a line). `view="cdf"` draws the CDF (bars for a discrete
family). Several distributions share the axes for comparison; continuous
and discrete ones cannot (`conflicting_parameters`).

- **Parameters** are checked with the pointer of the value:
  `/params/distributions/0/params/sigma` for sigma <= 0, `.../p` for p
  outside [0, 1], `.../n` for a non-integer n, `unknown_parameter` with a
  did-you-mean for a misspelt one, `invalid_parameter` for an unknown
  family. Shape, count and rate parameters are at most 10^6.
- **Numerics:** PDF/PMF closed forms, recorded as SymPy expressions
  (`metadata["template"]["distributions"][k]["formula"]`, tested against
  `sympy.stats`); CDFs from `erfc` and the regularized incomplete beta and
  gamma functions (continued fractions, capped iterations: bounded time);
  quantiles by bisection (on log x for a support bounded at 0, so P5 of
  Gamma(0.01), about 4e-131, is found), each checked: |F(x) − p| must be
  at most 1e-4 of the smaller tail, else the quantile is refused (P5 of
  Gamma(0.003) is below the smallest float). `check()` computes the figure
  once, so whatever `build()` could not draw (an unrepresentable bound,
  an overflowing range or density) is an `invalid_parameter` at
  `/params/distributions/<k>`. Plain Python: SciPy is not a dependency,
  and mpmath fails to converge on large shapes. Values stored to 10
  significant digits; numbers below 1e-4 are written 4.466×10⁻¹³¹.
- **`interval`** `{kind, level?, quantiles?}`: `quantile` with a level is
  the central share, labelled "central 90% of the distribution (P5–P95)";
  with `quantiles` [lower, upper] "P5–P75 of the distribution (70%)";
  `credible` (with `represents="posterior"`) / `prediction` (with
  `represents` `"posterior_predictive"` or `"predictive"`), "90% credible
  interval (central, P5–P95)": the caller says what the distribution is,
  and the caption says "Posterior distribution (as stated by the caller)"
  instead of "Theoretical distribution ..., not data". Kind and level
  are checked as the spec checks intervals (`missing_interval_level`,
  ...); `confidence`, `min-max` and SE/SD describe estimates or samples and
  are refused (`invalid_interval_kind`), as is `credible`/`prediction`
  without a matching `represents`. On the PDF of one distribution the area
  under the curve between the two quantiles is filled; with several, each
  interval is a dashed outline on its own curve (drop lines at both ends,
  in the curve's colour: blended fills would match no legend swatch). On a
  CDF or PMF the span is a reference band, for one distribution only (several
  would be indistinguishable: `conflicting_parameters`). A discrete
  interval cannot cover exactly its level, so its label adds what it does
  cover ("3–11 covers 92.3%") and the metadata records `coverage`.
- **`quantile_markers`** (up to 9 probabilities): diamonds on each curve at
  those quantiles (on a discrete CDF at (k, F(k)), which is ≥ p, in a
  colour of their own so they stand out from the bars); their values, with `x_unit`, are written in the caption
  ("P5 = −1.645 kg; P50 = 0 kg").
- **Range and honesty notes:** x runs from the 0.1 % to the 99.9 %
  quantile (or the support's end) of every distribution unless `x_range`
  is given; a range that holds less than that of a distribution says how
  much in the caption. A discrete range of more than 200 values is refused.
  A density that is infinite at an end of its support (beta with alpha or
  beta < 1, gamma with shape < 1) is sampled ever closer to that end so the
  curve leaves the plot, the y axis is cut, and the caption says so. The
  caption always says the curves are theoretical, from closed-form
  formulas, not data; the dataset provenance says the same.
- `theme` gives the number locale of every number the template writes
  (fi-FI: "Normal(μ = 1,5; σ = 0,25)", "P2,5 = −1,96").

### `sample_distribution` 1.0.0

The distribution of a numeric sample (`value_column`), as
`display="histogram"` (density-scaled, with a Gaussian KDE unless
`kde=false`), `"ecdf"` (the empirical CDF as an exact step function,
optionally one per `group_column` value, for comparisons) or `"dotplot"`
(a quantile dotplot: `dots` dots, each 1/`dots` of the sample, at the
sample quantiles (i − 0.5)/dots, stacked in columns of a stated width).

- **KDE bandwidth** is the claim a KDE makes, so its method and value are
  recorded (`metadata["template"]["kde"]`: kernel, bandwidth,
  bandwidth_method, the rule's formula) and stated in the curve's legend
  entry ("Kernel density estimate, h = 23.4 ms") and the caption.
  `bandwidth_method`: `scott` (h = σ̂·n^(−1/5), as
  `scipy.stats.gaussian_kde`), `silverman` (0.9·min(σ̂, IQR/1.34)·n^(−1/5),
  Silverman's rule of thumb as R's `bw.nrd0`; scipy's "silverman" is a
  different factor) or `fixed` with `bandwidth` in the sample's unit. The
  KDE is drawn to 3 bandwidths beyond the extreme values; a sample with no
  negative value is reflected at 0 (boundary correction: no density below
  0, the grid starts at 0; `kde.boundary` and the caption say so).
  Dotplot dots are drawn at their column's centre, which the caption says.
- **Bins:** `bins` equal widths over the range, or the Freedman–Diaconis
  rule (Sturges when the IQR is 0), recorded with the edges and stated in
  the caption with n.
- Null values are not samples: they are dropped and counted
  (`null_samples_dropped`). A sample whose bandwidth would be 0 (all
  values equal), fewer than 2 values (or fewer than `dots`), or more than
  100 000 values is refused. Groups are only for the ECDF (overlaid
  histograms hide each other: `conflicting_parameters`).

### `fan_chart` 1.0.0

Nested quantile bands over time around a median line, built by
`timeseries` 2.0.1 (whose metadata is kept under
`metadata["template"]["timeseries"]`), from either:

- **an ensemble** (`member_column` + `value_column`, long format, e.g. from
  `ieeVisualizer.stochastic`): at each time the empirical quantiles of the
  paths (type 7, numpy's default) for each central `levels` entry
  (default 0.5, 0.8, 0.95) and their median. A level whose tail holds less
  than one path (P2.5 needs 40 paths), or whose band edges would be named
  like another level's or the time column (P25), is refused at
  `/params/levels/<k>`. Path counts per time and the method are recorded;
  or
- **given quantiles** (`quantile_columns` `{column: quantile}`, plus
  `median_column` or a 0.5 column): drawn as supplied. Columns pair from
  the outside in; a symmetric pair is a central level, another pair an
  explicit [lower, upper] (`interval_kind` `quantile`; `prediction` or
  `credible` need symmetric pairs). Unpaired quantiles
  (`conflicting_parameters`) and crossing quantiles in a row
  (`invalid_column_values`) are refused.

Bands are labelled with their kind and level ("P10–P90 percentiles (80%,
forecast)"); `role` `forecast` (default) draws them as forecasts. The
caption says what the line and bands are, and for synthetic input
(`provenance.extra.synthetic`) that it is a seeded simulation, with the
seed.

**Seeded ensembles** (`ieeVisualizer.stochastic`, plot-focused, not a
simulation library): `random_walk(paths, steps, seed, start=, drift=,
sigma=, dt=)` and `gbm(paths, steps, seed, s0=, mu=, sigma=, dt=)` (exact
geometric Brownian motion on the grid) return a long-format snapshot
(`time`, `path`, `value`) whose provenance is marked synthetic with the
seed, model, parameters and generator (`random.Random`, so a seed gives
the same paths on every Python and numpy version). At most 2 000 paths,
2 000 steps and 200 000 values per call.

### `vector_field_2d` 1.0.0

A quiver over dataset `field`.

- **Expression mode** (no data): `u`, `v` in `x`, `y`, plus optional
  `constants`, over `x_range` × `y_range` on `grid` points (y outer, x inner).
  The template samples with SymPy `lambdify` + NumPy, imported only at build
  time, and records the expressions in the dataset provenance and in
  metadata. Before SymPy sees an expression it is checked against an AST
  whitelist: numbers, `x`, `y`, constants, `pi`, `E`, a fixed list of
  functions and `+ - * / **`. SymPy computes exactly while it builds, so
  the expression is then parsed unevaluated and rebuilt with every power,
  `exp` of a logarithm and factorial-like call checked after constants are
  substituted and before it is evaluated (exponents at most 1000 in
  magnitude, no number beyond about 10^±330, bounded size):
  `(0*x+9)**999999999` is an immediate `expression_too_large` error, not a
  hang. A nan/inf sample is stored as null and
  reported as `nonfinite_samples`. The layer role defaults to `reference`
  because a formula is not a measurement.
- **Sampled mode:** `data` with `x_column`, `y_column`, `u_column`,
  `v_column` (defaults `x`, `y`, `u`, `v`). `u` and `v` must share a unit;
  `x_unit`/`y_unit`/`vector_unit` only fill in missing units. The role
  defaults to `observed`.

Example payload (expression mode):

```json
{"data": null,
 "params": {"u": "-y", "v": "x", "x_range": [-2, 2], "y_range": [-2, 2], "grid": 11,
            "title": "Rotation field"}}
```

### `org_chart`, `dependency_map`, `network` 1.0.0

These build a **NetworkSpec** (a graph, not a FigureSpec) and are described
in [NETWORKS.md](NETWORKS.md#templates): a hierarchy from an id/parent table,
directed dependencies from an edge table, and a generic graph from an edge
table plus an optional node table. Render the result with
`ieeVisualizer.network.render_network`, not `ieeVisualizer.render.render`.

## Adding a template

1. Subclass `ComponentTemplate` and set `name`, `version` (semver), `title`,
   `description`, `parameters` (a tuple of `Parameter`), `columns` (a tuple of
   `ColumnRequirement`; each names the parameter that holds the column name)
   and `notes`. Set `data_required = False` if the template can run without
   data.
2. Implement `_check(data, params, issues)` for rules the generic checks
   cannot express (conflicts, units, key nulls). Report with
   `issues.error(path, code, message)` / `issues.warn(...)`, reuse the codes
   above and name the fix in the message. `check()` should report everything
   `build()` would refuse.
3. Implement `_build(data, params, issues) -> FigureSpec`. `params` is fully
   resolved (defaults applied, values normalised). Copy the data you draw.
   Do not invent values, uncertainty or units. Put template-specific
   provenance in `spec.metadata["template"]`; the base class adds
   `name`/`version`/`params`/`warnings` and validates the result.
4. Implement `example()` returning a JSON-serializable
   `{"data": ..., "params": {...}}` that builds cleanly.
5. Register it with `templates.default_registry.register(MyTemplate())` or with
   your own `TemplateRegistry`. A name+version pair can be registered only
   once. Change behaviour by publishing a new version. `get(name)` returns the
   newest release (pre-releases only when no release exists).
6. Add tests: the example validates clean, layers/roles/units are right, each
   error path returns the expected `(path, code)`, and at least one test fails
   against a deliberately broken implementation.
