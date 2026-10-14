# FigureSpec reference (`ieeVisualizer.spec`, schema 1.0)

A FigureSpec is a renderer-independent, JSON-serializable description of one
figure: its datasets, the marks drawn from them, axes, legend and annotations.
It is a pure data model: importing `ieeVisualizer.spec` never imports
matplotlib. Renderers (branch 3) and templates (branch 4) build on it.

```python
from ieeVisualizer.spec import (FigureSpec, DataSnapshot, LineMark, Axis,
                                validate, to_json, from_json, apply_patch, json_schema)
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
`null`, infinities are rejected. **Provenance** carries `source`, `query`,
`retrieved_at`, `source_version`, `timezone`, `run_id`, `notes`, `extra`.
`DataSnapshot.content_hash()` hashes the columns (values and their meaning),
not the provenance.

**Marks** share `type`, `data` (dataset name), `encoding` (channel ->
`{"field": column, "title"?}`; `"y": "col"` shorthand is accepted),
`role` (`observed` | `forecast` | `reference`), `name` (legend label) and
`style` (renderer hints: `color`, `colormap`, `linewidth`, `linestyle`,
`marker`, `opacity`).

| Mark | Required channels (2D) | Optional | Extra fields |
|---|---|---|---|
| `line`, `scatter` | x, y (+ z in 3D) | color, series (scatter: size) | |
| `area` | x, y | color, series | |
| `bar` | x, y | color, series | `orientation` (horizontal: categories on y) |
| `histogram` | x | color, series | `bins` or `bin_edges`, `density` |
| `heatmap` | x, y, color (cell value) | | |
| `quiver` | x, y, u, v (+ z, w in 3D) | color | `scale`, `normalize` |
| `function` | none with `expression`; x, y with `data` | z, series | `expression`, `variables`, `domain`, `samples`, `parameters` |

3D supports line, scatter, quiver, function; polar supports line, scatter,
area, bar, function.

## Functions

| Function | Notes |
|---|---|
| `validate(spec) -> list[ValidationIssue]` | Never raises for a bad spec. Each issue has `path` (JSON pointer), `code`, `message` (names the fix), `severity` (`error`/`warning`/`info`). Codes are listed in `validate.py`. |
| `to_dict` / `from_dict` / `to_json(spec, indent=None)` / `from_json` | `from_*` raise `SpecParseError` (with `.issues`) on structural problems: wrong types, unknown keys or mark types, bad `schema_version`. `to_json` with `indent=None` is canonical (sorted keys, compact) and stable for hashing. |
| `spec_hash(spec)` | `sha256:<hex>` of the canonical JSON, inline data included. |
| `apply_patch(spec, ops, *, check=True)` | RFC-6902 `add`/`replace`/`remove`/`test` on the dict form. Atomic, returns a new spec, never mutates the input. Re-parses, and by default raises `SpecValidationError` if the result has errors. `replace` needs an existing member, so use `add` to set a field that is currently unset (unset optional fields are omitted from the dict form). |
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
