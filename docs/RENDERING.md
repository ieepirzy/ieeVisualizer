# Rendering and exporting a FigureSpec

`ieeVisualizer.render` turns a [FigureSpec](SPEC.md) into PNG, SVG or PDF bytes
through a backend adapter chosen by capability. `ieeVisualizer.artifacts`
stores those bytes with a provenance manifest. Neither package imports
matplotlib until something is drawn.

```python
from ieeVisualizer.render import render, RenderRequest
from ieeVisualizer.artifacts import export, LocalDirectoryWriter

result = render(spec, RenderRequest(format="svg", width=6.4, height=4.8))  # inches
result.content, result.media_type, result.warnings      # bytes, "image/svg+xml", [...]

artifact = export(spec, ["png", "svg"], LocalDirectoryWriter("out/"))
artifact.artifact_id, artifact.variant("png").sha256
```

## Pipeline

1. **Validate.** `validate(spec)` errors refuse the render with
   `SpecValidationError` (its `.issues` are the validator's issues). Validator
   warnings are copied onto the result.
2. **Resolve references.** Layers bound to a `DatasetRef` need
   `resolver=callable(DatasetRef) -> DataSnapshot`; without one the render
   fails with `UnresolvedDatasetError`. The resolved data must agree with the
   columns the reference declared (dtype and unit), and is validated again.
3. **Select an adapter.** `requirements_for(spec, request)` lists what is
   needed: coordinate system, mark and annotation types, output format, 3D,
   vector export, number of points. `select_adapter` returns the first
   registered adapter whose `Capabilities` cover all of it, or raises
   `CapabilityError` listing the needs and, per adapter, what it lacks.
   `RenderRequest(adapter="name")` pins one (no fallback).
4. **Draw and export**, collecting `RenderWarning(code, message, path)` for
   everything not honoured exactly.

## RenderRequest / RenderResult

| `RenderRequest` field | Default | Notes |
|---|---|---|
| `format` | `"png"` | `png`, `svg`, `pdf` (matplotlib); `html` is a known format no current adapter supports |
| `width`, `height`, `units` | `6.4`, `4.8`, `"in"` | `units="px"` converts with `dpi`; a PNG measures exactly `request.size_px` |
| `dpi` | `100` | |
| `theme` | `None` | overrides `spec.theme`; matplotlib themes: `default`, `minimal`, `presentation` |
| `adapter` | `None` | pin an adapter by name |

`RenderResult` has `content`, `format`, `media_type`, `adapter_name`,
`adapter_version`, `warnings`, `request`, plus `sha256` and `size_bytes`.

## What the matplotlib adapter draws

All eight marks in `cartesian2d`; line, scatter, quiver and function
(surface for `z = f(x, y)`) in `cartesian3d`; line, scatter, area, bar and
function in `polar` (`x` = angle in radians, or degrees when the column/axis
unit is `deg`). Axis labels are `"Label (unit)"`, from the axis' own label
and unit or else the first bound column's title/name and unit. Datetime
columns are drawn as dates; timestamps with UTC offsets are placed at their
true instant and labelled in the datasets' `provenance.timezone` (or in UTC,
with a warning). String/bool columns, or `scale: "category"`, make a
category axis ordered by first appearance; mixing kinds on one axis is an
error.

**Missing data.** A null is never drawn as 0. Lines and areas break at a null
(a lone point between gaps gets a marker so it stays visible); scatter
points, arrows and bars with a null are left out, and a labelled bar slot
with no value says "no data"; null heatmap cells stay blank. Each case adds a
`null_values_omitted` warning. A layer or series with no complete row is a
`RenderError` (`empty_series`).

**Roles.** `forecast` is always distinguishable: dashed lines (a solid style
is overridden with a warning), hollow markers, hatched bars/areas, and the
legend label says "forecast". `reference` is lighter and thinner.

**Style keys** `color`, `colormap`, `linewidth`, `linestyle`, `marker`,
`opacity`. Unknown keys (`unknown_style_key`), keys the mark does not use
(`style_key_ignored`) and invalid values (`invalid_style_value`) are warnings.

**Functions.** Expressions are checked before SymPy sees them: only numbers,
`+ - * / ^ ( )`, the mark's variables and parameters, and an allow-list of
math functions (`sin`, `exp`, `log`, `sqrt`, ...) and constants (`pi`, `E`).
Non-finite samples are gaps with a warning; no finite sample is an error.

**Determinism.** Rendering runs under matplotlib's default style plus a fixed
`rc_context` (`svg.hashsalt`, `svg.fonttype="path"`), with dates stripped
from SVG/PDF metadata and a fixed PNG `Software` tag. The same spec and
library versions give byte-identical SVG and PDF, in one process or a fresh
one. Figures are built with `matplotlib.figure.Figure`, never `pyplot`. The
rc context is process-global: serialise concurrent renders across threads.

**Inspection hook.** `build_figure(spec, request=None, resolver=None)`
returns a `BuiltFigure` (`figure`, `axes`, `layer_artists[i]`,
`annotation_artists`, `caption`, `warnings`) without exporting; tests use it
to compare drawn data with the dataset.

## Artifacts

`export(spec, formats, writer, *, resolver=None, clock=None, request=None,
name=None, registry=None)` renders every format first, then writes
`figure.<fmt>`, `spec.json` (canonical spec; its SHA-256 is the spec hash),
`spec.resolved.json` when references were resolved (so the figure can be
re-rendered after the source changes), and `manifest.json` last. A failure
writes nothing.

The `VisualizationArtifact` manifest records `artifact_id`, `schema_version`,
`spec_hash`, per-dataset content hash, provenance and column meaning (unit,
dtype, aggregation, denominator), each variant's format, media type, SHA-256,
byte size, adapter name/version, request and warnings, library versions
(ieeVisualizer, matplotlib, numpy, sympy, python) and `created_at` (from
`clock`). Two exports of the same spec differ only in `created_at`.

`artifact_id` is `<name or "viz">-<16 hex>` over the spec hash and the
content hash of every dataset drawn, so different data behind the same
reference gives a different id. It never depends on a storage path or URL.
Writers implement `write(artifact_id, name, content, media_type) -> str`;
`LocalDirectoryWriter(root)` (atomic replace) and `MemoryWriter` are
provided. Exporting the same figure again overwrites the same directory.

## Known gaps

- No downsampling: more than `max_points` (1,000,000) points is refused, not
  thinned.
- Reference lines/bands are not drawn in 3D (warning); log scales in 3D are
  refused.
- Forecast heatmaps, quivers and surfaces are distinguished only by
  transparency and say so in a `role_style_approximate` warning.
- Area fills in polar use straight edges between samples.
- A `function` mark's domain is always in radians in polar.
