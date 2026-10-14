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
`expression_too_large`, `unknown_symbol`; warnings `missing_unit`, `empty_series`,
`no_forecast_rows`, `nonfinite_samples`. The full table is in the docstring
of `ieeVisualizer/templates/base.py`. Registry errors (`TemplateRegistryError`)
use `unknown_template`, `unknown_template_version`, `duplicate_template`,
`invalid_version`, `invalid_name`.

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

### `timeseries` 1.0.0

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
- **Uncertainty:** only from caller-supplied `lower_column`/`upper_column`,
  never computed. Spec 1.0 has no band mark, so bounds are two dotted line
  layers, paired in `metadata["template"]["bands"]`.
- **Nulls:** a null value stays null (a gap). A null time, series or role is
  an error because the row cannot be placed. Rows are ordered by time.
- `target` adds a horizontal reference line; axis labels default to the
  columns' descriptions, else names.

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
