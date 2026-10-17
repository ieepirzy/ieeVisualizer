# FigureSpec reference (`ieeVisualizer.spec`, schema 1.0)

A FigureSpec is a renderer-independent, JSON-serializable description of one
figure: its datasets, the marks drawn from them, axes, legend and annotations.
It is a pure data model: importing `ieeVisualizer.spec` never imports
matplotlib. Renderers (branch 3) and templates (branch 4) build on it.

```python
from ieeVisualizer.spec import (FigureSpec, DataSnapshot, LineMark, BandMark, ErrorbarMark,
                                Axis, validate, to_json, from_json, apply_patch, json_schema)
```

## Shape

| Field | Meaning |
|---|---|
| `schema_version` | Required in serialized form. Only `"1.0"` is readable; anything else is refused, never reinterpreted. |
| `id`, `title`, `caption`, `theme` | Optional strings. `theme` is a name the renderer resolves. |
| `coordinate_system` | `cartesian2d` (default), `cartesian3d`, `polar` (x = angle, y = radius). |
| `datasets` | `name -> dataset`. `{"type": "snapshot", "columns": {...}, "provenance": {...}}` holds inline data; `{"type": "ref", "kind", "id", "query"?, "version"?, "retrieved_at"?, "columns"?}` points at external data. |
| `layers` | List of marks, drawn in order. |
| `axes` | `"x"`, `"y"` (and `"z"` in 3D) -> `{label, unit, scale, limits}`. `scale`: `linear`, `log`, `symlog`, `time`, `category`, or omitted (renderer infers). `limits`: `[min, max]`, either may be `null`. |
| `legend` | `{show, position, title}`. |
| `annotations` | `text` (at `x`, `y`[, `z`]), `reference_line` (`axis`, `value`), `reference_band` (`axis`, `start`, `end`). |
| `metadata` | Free-form JSON (template name/version, report run id...). |

**Columns** carry `values` plus `dtype` (`float`, `int`, `bool`, `str`,
`datetime` as ISO-8601 strings), `unit`, `description`, `denominator`,
`aggregation`. Missing values are `null` and stay `null`; NaN/NaT become
`null`, infinities are rejected, `-0.0` is stored as `0.0`. **Provenance**
carries `source`, `query`, `retrieved_at`, `source_version`, `timezone`,
`run_id`, `notes`, `extra`. `DataSnapshot.content_hash()` hashes the columns
(values and their meaning), not the provenance.

**Datetime values** have one stored form, whatever they were built from
(`datetime`/`date` objects, numpy `datetime64` of any unit and multiplier, ISO
strings; numpy's non-ISO renderings are widened exactly first: `Y`/`M`/`W` to
days, `h` to minutes):
`YYYY-MM-DDTHH:MM:SS[.ffffff][+HH:MM]`. Seconds are always written, the
fraction only when non-zero (six digits), the offset only for timezone-aware
values and as given (`Z` becomes `+00:00`; values are not converted to UTC).
`"2026-01-01"`, `date(2026, 1, 1)` and `datetime64("2026-01-01", "ns")` all
store `"2026-01-01T00:00:00"`. Input strings must be `YYYY-MM-DD`, optionally
followed by `T` (or a space) and `HH:MM[:SS[.fraction]]` and a `Z`/`+HH:MM`
offset; anything else, an impossible date, or a non-zero sub-microsecond part
is rejected, as is a column that mixes timezone-aware and naive values (all
on construction, so `from_dict` reports a `SpecParseError` at the column's
`values`). Years must have four digits (0001-9999); a `datetime64` outside
that range is refused with a message saying so.

Column values are normalised again on serialization, so values changed after
construction (an `int` put into a `float` column, a date-only string, a `NaN`)
are written canonically: equal data hashes equally however it got there. A
`NaN` put into a column after construction is stored as `null`, like on
construction, and `validate` warns about it (`non_finite_number`, severity
`warning`); an infinity is an error.

**Time axes and date coordinates.** An axis is a *time axis* when its `scale`
is `"time"`, or when it has no `scale` and the columns bound to it are
declared `datetime` (the renderer infers a time scale there). On a time axis,
`limits` and annotation coordinates (`x`/`y`/`z` of a text annotation, a
reference line's `value`, a band's `start`/`end`) are ISO-8601 strings, stored
in the canonical datetime form above on construction, on parse and on
serialization, so `"2026-01-01"` and `"2026-01-01T00:00:00"` hash equally.
`validate` reports a string there that is not a date/time (`invalid_limits`
for a limit, `invalid_value` for an annotation) and **a number there as an
error** (`scale_dtype_mismatch`): numbers carry no unit or timezone, so they
are never read as timestamps. Reversed limits and bands are detected by
comparing the parsed instants, not the text, and mixing an aware and a naive
end is `invalid_limits`. Conversely a string coordinate on a `linear`, `log`
or `symlog` axis is `scale_dtype_mismatch`. Strings on other axes (a
`category` axis, or no scale with non-datetime data) are kept verbatim.

**Numbers** are canonical so that equal specs hash equally: fields declared
as numbers (function `domain` and `parameters`, histogram `bin_edges`, quiver
`scale`, numeric axis `limits` and annotation coordinates) are stored as
floats, so `5` and `5.0` (or a JavaScript `JSON.stringify` round trip that
writes `5.0` as `5`) give the same JSON and the same `spec_hash`. Integer
fields (`samples`, `bins`) accept integer-valued floats (`200.0`) and store
ints. NaN and infinities are not JSON numbers: `from_json` refuses the
`NaN`/`Infinity` constants anywhere in the text, `from_dict` refuses them
with the pointer of the value, constructors raise `ValueError`, and
`validate` reports `non_finite_number` for one assigned later. An integer
beyond the float64 range (e.g. `10**400`; JSON itself has no limit) cannot be
held by any reader that parses numbers as doubles: in those fields, and in a
`float` column, construction and parsing raise `SpecParseError` (a
`ValueError`) with code `number_out_of_range` and the value's pointer
(relative to the constructed object on construction), never a raw
`OverflowError`. An `int` column may store it exactly, but `validate` reports
`number_out_of_range` there, and for any such value assigned later. Free-form JSON
(`metadata`, `style`, `provenance.extra`) may nest at most 64 levels
(`MAX_JSON_DEPTH`); deeper is refused by `from_dict` and reported by
`validate` as `too_deep`, so no input reaches recursive code (copying,
hashing) that would raise `RecursionError`.

**Marks** share `type`, `data` (dataset name), `encoding` (channel ->
`{"field": column, "title"?}`; `"y": "col"` shorthand is accepted),
`role` (`observed` | `forecast` | `reference`), `name` (legend label) and
`style` (renderer hints: `color`, `colormap`, `linewidth`, `linestyle`,
`marker`, `opacity`).

| Mark | Required channels (2D) | Optional | Extra fields |
|---|---|---|---|
| `line`, `scatter` | x, y (+ z in 3D) | color, series (scatter: size); y_lower + y_upper (2D) | interval fields |
| `area` | x, y | color, series | |
| `bar` | x, y | color, series; y_lower + y_upper (vertical) | `orientation` (horizontal: categories on y), interval fields |
| `band` | x, y_lower, y_upper | color, series | interval fields (required) |
| `errorbar` | x, y_lower, y_upper | y (the point estimate), color, series | interval fields (required) |
| `histogram` | x | color, series | `bins` or `bin_edges`, `density` |
| `heatmap` | x, y, color (cell value) | | |
| `quiver` | x, y, u, v (+ z, w in 3D) | color | `scale`, `normalize` |
| `function` | none with `expression`; x, y with `data` | z, series | `expression`, `variables`, `domain`, `samples`, `parameters` |

3D supports line, scatter, quiver, function; polar supports line, scatter,
area, bar, function. Band, errorbar and the interval channels are 2D
(`cartesian2d`) only.

## Intervals

An interval is a claim about uncertainty, so it must say what it is. It is
drawn by a `band` or `errorbar` mark, or by `y_lower` + `y_upper` on a line
(drawn as a band around it), scatter or vertical bar (error bars), and the
layer states:

| Field | Meaning |
|---|---|
| `interval_kind` | `confidence`, `credible`, `prediction`, `quantile`, `min-max`, `standard-error`, `standard-deviation`. Required. |
| `interval_level` | Coverage as a fraction, strictly between 0 and 1 (`0.95`, not `95`). Required for confidence, credible and prediction, and for quantile unless `interval_quantiles` is given; refused for the others (min-max has none). A quantile interval with a level is **central (equal-tailed)**: `0.8` means the 10th to the 90th percentile. |
| `interval_quantiles` | A quantile interval that is not central: `[lower, upper]` quantiles with 0 < lower < upper < 1 (`[0.05, 0.75]`), instead of a level. Quantile kind only. |
| `interval_multiplier` | How many standard errors / deviations the bar spans on each side (`1`, `1.96`). Required for standard-error and standard-deviation; refused for the others. A ±k bar claims value ± one half-width: bounds that are not symmetric about the value are drawn as given, with a warning and "(bounds not symmetric)" in the legend. |

Renderers state it in words where the reader sees it: the legend entry
("95% prediction interval", "P10–P90 percentiles (80%)",
"P5–P75 percentiles", "±1 standard error", "min-max range";
percentiles in P-notation, so locale decimals need no ordinal: "P2,5";
`interval_label(kind, level, multiplier, quantiles=...)`), or the caption,
each statement prefixed with its series as the legend would name it, when
the legend is hidden. Levels and multipliers are written exactly (up to 4
decimals of a percent; more, up to 12, so a value does not read as a whole
number it is not, e.g. 99.99999 % rather than 100 %). Differences beyond 12
decimals are float noise and are dropped on purpose: 0.9500000000000001 is
written 95 %. A level (or
quantile) that cannot be written distinctly from 0 % / 100 % (P0 / P100)
within 12 decimals, such as `1 - 1e-15`, is refused (`invalid_interval_level`
/ `invalid_interval_quantiles`), as is a quantile pair whose two percentiles
are written the same (`[0.1, 0.10000000000000002]` would read "P10–P10"), or
a central level so small that its two tails would (7.3e-15: "P50–P50").
"%" follows the theme locale's percent style, as its other numbers do: none
in English ("95%", the default), a no-break space in fi-FI ("95 %"), so a
wrapped legend or caption never separates "95" from "%". The bounds are data: drawn as given, never computed, widened,
symmetrised or smoothed. A null bound is a gap in the interval, never zero.
Several bands of one series at different levels make a fan chart.

`validate` reports (with the pointer of the field or row to fix):
`missing_interval_kind`, `invalid_interval_kind`, `missing_interval_level`,
`invalid_interval_level`, `unexpected_interval_level`,
`missing_interval_multiplier`, `invalid_interval_multiplier`,
`unexpected_interval_multiplier`, `invalid_interval_quantiles`,
`unexpected_interval_quantiles`, `conflicting_interval_level` (a level and
explicit quantiles together); `missing_channel` for one bound without
the other; `interval_without_bounds` for interval fields on a layer that
draws none; `interval_not_numeric`; `interval_unit_mismatch` when a bound's
unit is not the value's (a bound with no unit next to a value with one
counts); `interval_inverted` at `/datasets/<d>/columns/<lower>/values/<row>`
for a row whose lower bound is above its upper bound; `unsupported_channel`
for intervals on a horizontal bar or outside `cartesian2d`;
`log_scale_nonpositive` for a bound <= 0 on a log axis; and the warnings
`value_outside_interval` for a point estimate outside its own interval and
`interval_not_symmetric` for a ±k SE/SD bar whose value is not midway
between its bounds: the two half-widths differ by more than 1e-6 times the
larger one (`SYMMETRY_TOLERANCE`), loose enough for float rounding of
computed bounds and tight enough that any visible asymmetry is reported.
Bounds rounded to fewer digits than the value (10.3 ± 1.96 SE written as 8.4
and 12.3) are reported too: what is drawn is not symmetric.

The interval fields exist only on line, scatter, bar, band and errorbar
marks and are written to the JSON only when set, so a spec without
intervals serializes, and hashes, exactly as before they existed
(`schema_version` stays `"1.0"`; a reader that predates them refuses a spec
that uses them instead of dropping the interval).

## Functions

| Function | Notes |
|---|---|
| `validate(spec) -> list[ValidationIssue]` | Never raises for a bad spec. Each issue has `path` (JSON pointer), `code`, `message` (names the fix), `severity` (`error`/`warning`/`info`). Codes are listed in `validate.py`. Field types are checked too (`invalid_type`), as are the classes inside each collection (a `dict` among `layers`, `datasets`, `axes` or `annotations` is `invalid_type`, not a crash) and each column's values, re-normalised against its `dtype` as construction does (`invalid_values`), so a spec without errors also survives `from_dict(to_dict(spec))`. Encoding shorthand (`"y": "col"`) assigned after construction is accepted as `{"field": "col"}`. |
| `to_dict` / `from_dict` / `to_json(spec, indent=None)` / `from_json` | `from_*` raise `SpecParseError` (with `.issues`) on structural problems: wrong types, unknown keys or mark types, bad `schema_version`. `to_json` with `indent=None` is canonical (sorted keys, compact) and stable for hashing. |
| `spec_hash(spec)` | `sha256:<hex>` of the canonical JSON, inline data included. |
| `apply_patch(spec, ops, *, check=True)` | RFC-6902 `add`/`replace`/`remove`/`test` on the dict form. Atomic, returns a new spec, never mutates the input. Re-parses, and by default raises `SpecValidationError` if the result has errors. Array indices follow RFC 6901: ASCII digits without leading zeros (or `-` to append with `add`); anything else, such as `01` or `²`, is a `PatchError` (`invalid_pointer`). `replace` needs an existing member, so use `add` to set a field that is currently unset (unset optional fields are omitted from the dict form). `test` compares JSON values by type: `true` is not `1` and `"1"` is not `1`, while numbers compare by value as RFC 6902 specifies (`1` equals `1.0`). |
| `json_schema()` | Draft-07 JSON Schema of the serialized form (structure and enums; cross-references such as "column exists" are only checked by `validate`). |

## Example: observed + forecast time series

```json
{
  "schema_version": "1.0",
  "title": "Weekly revenue",
  "caption": "Forecast is a model output, not a measurement.",
  "datasets": {
    "observed": {
      "type": "snapshot",
      "columns": {
        "week": {"dtype": "datetime", "values": ["2026-09-07", "2026-09-14", "2026-09-21"]},
        "revenue": {"dtype": "float", "unit": "EUR", "aggregation": "sum per ISO week",
                    "values": [1200.0, null, 1350.5]}
      },
      "provenance": {"source": "finance-service", "query": "weekly_revenue",
                     "retrieved_at": "2026-10-14T06:00:00+03:00", "timezone": "Europe/Helsinki"}
    },
    "forecast": {
      "type": "snapshot",
      "columns": {
        "week": {"dtype": "datetime", "values": ["2026-09-28", "2026-10-05"]},
        "revenue": {"dtype": "float", "unit": "EUR", "values": [1410.0, 1450.0]}
      }
    }
  },
  "layers": [
    {"type": "line", "data": "observed", "encoding": {"x": "week", "y": "revenue"},
     "name": "Observed"},
    {"type": "line", "data": "forecast", "encoding": {"x": "week", "y": "revenue"},
     "name": "Forecast", "role": "forecast", "style": {"linestyle": "dashed"}}
  ],
  "axes": {"x": {"label": "Week", "scale": "time"},
           "y": {"label": "Revenue", "unit": "EUR", "limits": [0, null]}},
  "annotations": [{"type": "reference_line", "axis": "y", "value": 1300, "label": "Target"}]
}
```

The `null` revenue week is a gap, not a zero.

## Example: forecast with an 80 % prediction interval

```json
{
  "schema_version": "1.0",
  "title": "Weekly orders",
  "caption": "Forecast model v3; band: its 80 % prediction interval.",
  "datasets": {
    "forecast": {
      "type": "snapshot",
      "columns": {
        "week": {"dtype": "datetime", "values": ["2026-10-05", "2026-10-12", "2026-10-19"]},
        "orders": {"unit": "orders", "values": [212.0, 220.0, 226.0]},
        "p10": {"unit": "orders", "values": [190.0, 192.0, null]},
        "p90": {"unit": "orders", "values": [236.0, 250.0, 262.0]}
      }
    }
  },
  "layers": [
    {"type": "band", "data": "forecast", "role": "forecast",
     "encoding": {"x": "week", "y_lower": "p10", "y_upper": "p90"},
     "interval_kind": "prediction", "interval_level": 0.8},
    {"type": "line", "data": "forecast", "role": "forecast",
     "encoding": {"x": "week", "y": "orders"}, "name": "Forecast"}
  ],
  "axes": {"x": {"label": "Week", "scale": "time"}, "y": {"label": "Orders"}}
}
```

The legend reads "80% prediction interval (forecast)". The missing `p10`
of the last week ends the band a week early; it is not drawn down to zero.

## Example: 2D vector field

```json
{
  "schema_version": "1.0",
  "title": "Rotation field",
  "datasets": {
    "field": {
      "type": "snapshot",
      "columns": {
        "x": {"unit": "m", "values": [-1.0, 0.0, 1.0, 0.0]},
        "y": {"unit": "m", "values": [0.0, -1.0, 0.0, 1.0]},
        "u": {"unit": "m/s", "values": [0.0, 1.0, 0.0, -1.0]},
        "v": {"unit": "m/s", "values": [-1.0, 0.0, 1.0, 0.0]}
      }
    }
  },
  "layers": [
    {"type": "quiver", "data": "field",
     "encoding": {"x": "x", "y": "y", "u": "u", "v": "v"}}
  ],
  "axes": {"x": {"label": "x", "unit": "m"}, "y": {"label": "y", "unit": "m"}}
}
```

## Patching instead of regenerating

```python
issues = validate(spec)            # e.g. [ValidationIssue("/layers/1/encoding/y/field",
                                   #        "unknown_column", "... Did you mean 'revenue'? ...")]
spec2 = apply_patch(spec, [{"op": "replace", "path": issues[0].path, "value": "revenue"}])
```
