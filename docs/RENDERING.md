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
| `theme` | `None` | overrides `spec.theme`; a registered theme `name` or `name@version` ([THEMES.md](THEMES.md)): `default`, `minimal`, `presentation`, `dashboard-dark`, `report-light` |
| `adapter` | `None` | pin an adapter by name |

`RenderResult` has `content`, `format`, `media_type`, `adapter_name`,
`adapter_version`, `warnings`, `request`, `theme` (the resolved theme's
name, version and hash), plus `sha256` and `size_bytes`.

## What the matplotlib adapter draws

All eight marks in `cartesian2d`; line, scatter, quiver and function
(surface for `z = f(x, y)`) in `cartesian3d`. A *sampled* function in 3D is
a surface too: its rows must cover each (x, y) pair of a rectilinear grid
exactly once, in any order (`not_a_grid` otherwise; use a line mark for a 3D
curve), a null z is a hole and a null x/y is an error. Line, scatter, area,
bar and function in `polar` (`x` = angle in radians, or degrees when the
column/axis unit is `deg`). Axis labels are `"Label (unit)"`, from the axis' own label
and unit or else the first bound column's title/name and unit. Datetime
columns are drawn as dates; timestamps with UTC offsets are placed at their
true instant and labelled in the datasets' `provenance.timezone` (or in UTC,
with a warning). String/bool columns, or `scale: "category"`, make a
category axis ordered by first appearance; mixing kinds on one axis is an
error.

**Missing data.** A null is never drawn as 0. Lines and areas break at a null
(a lone point between gaps gets a marker so it stays visible); scatter
points, arrows and bars with a null are left out, and a labelled bar slot
with no value says "no data" (at the plot edge on a log value axis, where 0
has no position); null heatmap cells stay blank. Each case adds a
`null_values_omitted` warning. A layer or series with no complete row is a
`RenderError` (`empty_series`).

**Quiver scale.** A 2D quiver without `scale` whose positions form a
complete rectilinear grid (every (x, y) pair exactly once, as for a sampled
3D surface) on two linear numeric axes gets one so the longest arrow spans
one grid cell (the smaller of the x and y extents divided by the number of
distinct positions minus one), drawn in data coordinates (`angles="xy"`),
and the view is widened to include every arrow tip, so no arrow runs out of
the plot. Otherwise (a log axis, scattered or incomplete positions)
matplotlib's own arrow autoscaling is used: arrows are sized relative to
the plot, equal vectors look equal, and the view follows the arrow tails
only. In 3D the default length factor is 1.

**Huge numbers.** The spec stores exact integers of any size, but a column
value beyond the float64 range (e.g. `10**400`) cannot be drawn: `validate()`
reports it as a `number_out_of_range` error, so a render refuses it with
`SpecValidationError` (and the renderer itself raises a structured
`value_out_of_range` `RenderError`, never a raw `OverflowError`, should such a
column reach it unvalidated).

**Bars.** Bar groups of one layer (`series`/categorical `color`) sit side by
side in each slot. Bar layers of the same orientation that share a category
(or position) are dodged the same way, in layer order, instead of being
drawn over each other. Sharing is followed through: layers linked by a
shared position, directly or through another layer (A shares a category
with B, B one with C), form one dodge group, which takes one slot per layer
(and group) at every position of the group, so its bars never overlap. A
bar layer that shares no position with another is laid out exactly as a
single layer, with no slot at other layers' positions. A layer with an
explicit `bar_width` / `bar_offset` style is placed where those say
(fractions of the category slot) and is left out of the dodging; when its
bars then cover bars of dodged layers, a `bars_overlap` warning names the
layers (explicitly placed layers may overlap each other, e.g. a narrow
target bar inside a wide one). Horizontal dodged bars are laid out upwards
(the first layer lowest in each category), so their legend entries are
listed in reverse layer order, top to bottom as the bars are. Every slot
with no bar -- a null value, or a (category, group) or (category, layer)
combination with no row within a dodge group -- is labelled "no data", so
an absent bar never reads as 0. The note starts at the zero line and reads
away from it on the positive side, or on the negative side when only that
side has room inside the axes (an all-negative value axis, whose zero line
is the plot's edge), so it never lands outside the plot or on the title.
When `axes.<ch>.limits` exclude zero (e.g. `[50, 100]`) or the value axis is
logarithmic, it starts at the plot edge nearest to zero instead. In `polar`
each note is drawn along its slot's angle, outwards from beside the centre
inside the slot's empty wedge; a note that does not fit there (narrow wedge,
small figure, or over a radial tick label) is left out and the slot listed
in a `no_data_unplaceable` warning. Bar value labels (`value_labels`) are
arranged after layout; see [THEMES.md](THEMES.md). They are drawn on 2D
cartesian bars only: in `polar` they are not drawn (`style_key_ignored`).

**Roles.** `forecast` is always distinguishable, whatever style it asks for:
a forecast line uses the requested dash pattern (`solid` or unspecified
count as `dashed`; a solid request is overridden with a warning) with every
gap doubled, a pattern no observed `linestyle` produces, and is drawn at
0.75 times the requested opacity; markers are hollow, bars/areas hatched,
and the legend label says "forecast". So a dotted forecast bound never looks
like a dotted observed bound. `reference` is lighter and thinner.

**Style keys** `color`, `colormap` (a matplotlib name, or `sequential` /
`diverging` for the theme's maps), `linewidth`, `linestyle`, `marker`,
`opacity`; bars also `value_labels`, `bar_width`, `bar_offset`
([THEMES.md](THEMES.md)). Unknown keys (`unknown_style_key`), keys the mark does not use
(`style_key_ignored`) and invalid values (`invalid_style_value`) are warnings.

**Many series.** Series take colours from the theme's palette (matplotlib's
10-colour cycle under `default`).
When it wraps, a `colour_cycle_wrapped` warning is given once, and observed
lines / scatter points of later rounds get a dashed, dotted, dash-dot
linestyle or a different marker (unless the style chose one) so no two
series look the same; bars, areas and histograms repeat colours.

**Paired colours.** `color_of: "<layer name>"` (line, scatter, area, bar,
histogram, function) draws each series group of the layer in the colour of
the named layer's series with the same group, e.g. uncertainty-bound lines
in the colour of the value they bound (the timeseries template sets it). The
name must match exactly one other, non-reference layer; otherwise
`unknown_color_of` is a warning and the layer gets its own colour. An
explicit `color` wins (`style_key_ignored` on `color_of`). Legend entries
with the same label that look identical (e.g. a lower and an upper bound
line both named "range") are shown once.

**Legend placement.** An explicit `legend.position` is used as given. For
`"auto"`, matplotlib's `"best"` spot inside the axes is kept only when the
legend has at most five entries and, at that spot, overlaps no plotted data
(a line's drawn segments or markers, a bar or span, a filled area or an
unfilled area's outline, a scatter or quiver point, a heatmap mesh, an
annotation's text). Otherwise the legend goes outside the axes: to the
right, or below in as many columns as fit for a figure at least 1.6 times as
wide as tall, or when a right-hand legend would take more than 40% of the
width. Constrained layout makes room for it, so neither the legend nor tick
or axis labels are clipped. The choice is computed from the figure's own
geometry, so it is deterministic; a figure whose legend stays inside renders
byte-identically to before this rule. Bar value labels (`value_labels`) and
"no data" notes are annotation text, so they count as data here; the value
axis is fitted to the labels before the legend is measured (both passes run
in the layout engine on every draw), so an `"auto"` legend that would cover
a value label is moved outside instead.

**Functions.** Expressions are checked before SymPy sees them: only numbers,
`+ - * / ^ ( )`, the mark's variables and parameters, and an allow-list of
math functions (`sin`, `exp`, `log`, `sqrt`, ...) and constants (`pi`, `E`).
Non-finite samples are gaps with a warning; no finite sample is an error.
SymPy evaluates exactly while it builds an expression, so a short string
such as `9^9^9` or `gamma(10^6)` could hang a render. The expression is
parsed unevaluated and rebuilt bottom-up; before any power, `exp` of a
logarithm or `gamma` is evaluated (with parameters substituted) it is
checked: exponents at most 1000 in magnitude, no number beyond about
10^+-330 (outside float64's range anyway), `gamma` arguments within
+-1000, at most 1000 characters, 500 terms and 100 levels of nesting.
Anything else is an `expression_too_large` error, raised immediately.
A sub-expression that evaluates to an undefined or infinite constant
(`1/0`, `tan(pi/2)`, `log(0)`, `gamma(0)`, a parameter making a
denominator zero) is an `invalid_expression` error: it is not a function
that can be sampled, so it is refused rather than drawn as a gap.

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

## From the legacy API

`Visualizer.to_spec(fn, dim=..., kind="function" | "vector_field")` builds the
spec equivalent of `visualize()` / `visualize_vf()` from the same samples
(same sampling code path): dataset `samples` with fixed column names (`x`,
`y`, `f`; or `x`, `y`[, `z`], `u`, `v`[, `w`], `magnitude`), the user's
symbol names in the axis labels, optional `units=` by axis (`"vector"` for
field components), and provenance recording the input (SymPy expression
string, or the callable's `module.qualname`, never its address-bearing
`repr`; a `functools.partial` as the wrapped callable plus its arguments
when they are plain literals; a `sympy.lambdify` function as "generated by
sympy.lambdify" with its generated source), domain, `n`, sampling mode, the
integer seed (`seed_scope` `"call"` for a per-call `rng=`, `"visualizer"`
for the constructor's, whose samples depend on call order) and the number of
non-finite points (stored as null). The layer role defaults to `reference`
(a computed formula, drawn lighter). `Visualizer.export(fn, out, dim=...)`
renders and stores it with a manifest, so the figure can be re-rendered
from `spec.json` without the original callable.

Not carried over (no spec 1.0 equivalent): streamlines, the equal aspect of
2D fields, per-arrow magnitude colours in 3D (lengths match, colour is
uniform), and the `ax=`/`style=`/`show=` drawing options. For those reasons
`visualize()` and `visualize_vf()` still draw directly with Matplotlib; the
tests check that both routes draw the same data.

## Artifacts

`export(spec, formats, writer, *, resolver=None, clock=None, request=None,
name=None, registry=None)` renders every format first, then writes
`figure.<fmt>`, `spec.json` (canonical spec; its SHA-256 is the spec hash),
`theme.json` (the resolved theme, so the figure can be re-rendered after the
registered theme changes),
`spec.resolved.json` when references were resolved (so the figure can be
re-rendered after the source changes), and `manifest.json` last. A failure
writes nothing.

The `VisualizationArtifact` manifest records `artifact_id`, `schema_version`,
`spec_hash`, per-dataset content hash, provenance and column meaning (unit,
dtype, aggregation, denominator), each variant's format, media type, SHA-256,
byte size, adapter name/version, request and warnings, the theme
(`name`, `version`, `sha256`, `file`), library versions
(ieeVisualizer, matplotlib, numpy, sympy, python) and `created_at` (from
`clock`). Two exports of the same spec differ only in `created_at`.

`artifact_id` is `<name or "viz">-<16 hex>` over the spec hash, the
content hash of every dataset drawn, the list of formats (in order) and the
render request (width, height, units, dpi, theme, adapter) and the resolved
theme's record (name, version, hash), so different data behind the same
reference, or the same spec at another size, dpi, theme, theme version or
set of formats, gives a different id. It never depends on a
storage path or URL.

Writers implement `write(artifact_id, name, content, media_type) -> str`
and may implement `write_artifact(artifact_id, files) -> str`, which
replaces everything stored under the id with exactly `files`; `export()`
uses it when present. `LocalDirectoryWriter(root)` stages the files in a
temporary directory under `root` and swaps it in for `<root>/<artifact_id>`
(the old directory is moved aside, then deleted), so an artifact directory
always holds exactly the files its `manifest.json` lists: exporting again
under the same id replaces the directory as a whole and never leaves files
from an earlier export behind. Concurrent exports of one id (threads or
processes) are serialised by a per-id lock file `<root>/.lock-<id>`, which
stays in place; if the directory already holds exactly the same files (the
usual case for a content-addressed id) nothing is rewritten. A failed export
leaves the previous version and no staging directories; leftovers of a killed
export are removed by the next export of that id. While *different* content
replaces a directory, a reader may briefly find `<root>/<id>` missing (there
is no portable atomic directory exchange). `MemoryWriter` does the same for
its dict.
A custom writer with only `write` gets one call per file, manifest last.

## Known gaps

- No downsampling: more than `max_points` (1,000,000) points is refused, not
  thinned.
- Drawing time grows with sizes an agent can choose, so they are bounded:
  a histogram may have at most `max_bins` (10,000) bins (`bins` or
  `bin_edges`; a capability, so more is a `CapabilityError` before anything
  is drawn) and a category axis at most 5,000 distinct values
  (`too_many_categories`). Above 50 categories only every n-th tick label is
  drawn (`category_labels_thinned` warning); the bars are all drawn.
- Reference lines/bands are not drawn in 3D (warning); log scales in 3D are
  refused.
- Forecast heatmaps, quivers and surfaces are distinguished only by
  transparency and say so in a `role_style_approximate` warning.
- Area fills in polar use straight edges between samples.
- A `function` mark's domain is always in radians in polar.
