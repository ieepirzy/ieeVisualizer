# Views and flow diagrams (`ieeVisualizer.view`, schema 1.0)

A **ViewSpec** composes figures with the context around them - text, KPI
tiles, tables, caveats and footnotes - and with **flow diagrams**, which are
first-class components rather than pictures
([CONVERSATIONAL_UI_AND_REPORTING.md](CONVERSATIONAL_UI_AND_REPORTING.md)
section 5.2). It follows the FigureSpec conventions ([SPEC.md](SPEC.md)):
dataclasses, strict parsing with JSON-pointer errors, `validate_view()`
returning `ValidationIssue`s, canonical JSON, `view_hash()` and
`apply_view_patch()`.

```python
from ieeVisualizer.artifacts import LocalDirectoryWriter
from ieeVisualizer.view import render_view, export_view, validate_view
from ieeVisualizer.view.examples import weekly_briefing, reporting_pipeline

view = weekly_briefing()
assert validate_view(view) == []
svg = render_view(view, "svg").content          # also "html" and "png"
export_view(view, ["html", "svg", "png"], LocalDirectoryWriter("out"))
```

## Shape

```json
{"schema_version": "1.0", "title": "Weekly briefing",
 "figures": {"trend": {"schema_version": "1.0", "...": "a FigureSpec"}},
 "datasets": {"weekly": {"type": "snapshot", "columns": {}}},
 "root": {"type": "stack", "direction": "vertical", "children": []},
 "metadata": {}}
```

| Node | Fields |
|---|---|
| `stack` | `direction` `vertical` (default) / `horizontal` (equal-width columns), `children` |
| `grid` | `columns` (default 2), `children` (row-major; a row is as tall as its tallest cell). A grid never has more columns than children: `columns: 200` with 4 children lays out as 4 columns |
| `card` | optional `title`, `children` stacked vertically inside a frame |
| `figure` | exactly one of `figure` (key into `figures`) or `spec` (an embedded FigureSpec); `aspect` = height/width of its slot (default 0.6, 0.1-4) |
| `text` | `text` in the markdown subset below; `style` `body` / `lead` / `heading` / `small` |
| `kpi` | `label`, `value` (number or `null`), optional `unit`, `decimals`, `comparison` `{value, label}`, `source` (shown text), `dataset` (key into `datasets`; its provenance goes to the manifest) |
| `table` | `data` (key of a **snapshot** in `datasets`), optional `columns`, `title`, `max_rows` |
| `caveat` / `footnote` | `text` (markdown subset). Caveats are for limitations and uncertainty, footnotes for sources and provenance |
| `flow` | see below |

**Text** supports paragraphs (blank line), `- ` bullets, `**bold**`,
`*italic*`/`_italic_` and `` `code` ``. Nothing else is interpreted: there is
no raw HTML and no links, and every string is escaped in every output.

**Missing values.** A KPI with `value: null` shows *no data*, never `0`; a
null comparison value shows "<label>: no data" and no delta is computed. A
null table cell shows "—" (a real `0` stays `0`). The delta arrow is neutral
in colour: whether up is good is not something the view knows.

Numbers are never given invented precision or invented digits. A float is
formatted from its shortest decimal form (`Decimal(repr(v))`), never from its
binary expansion: without `decimals` an integral value is written without
decimals and any other value as stored; with `decimals` (an integer from 0
to 10) that decimal form is rounded half-to-even (`2.675` at 2 decimals is
`2.68`). From 10^15 in magnitude, where a float no longer holds every integer
digit, a number is written in exponent notation with at most 17 significant
digits and `decimals` is ignored: `1e23` is `1e+23`, not
`99 999 999 999 999 991 611 392`. A KPI delta is computed exactly in
`Decimal` on the numbers as written (`0.3` vs `0.1` gives `+0.2`, not
`+0.19999999999999998`), so it can neither gain digits nor overflow:
`1e308` vs `-1e308` is `▲ +2e+308`, never `inf`. The arrow and sign follow
the delta *as displayed*: a delta that rounds to zero at `decimals` is
`= 0.0`, and no value is ever written `−0` / `−0.0`. Only a leading minus is
typeset as `−`; the exponent of `1e-05` is left alone. Every line of a KPI
tile stays inside the tile: the value shrinks from 30 to 14 px and then
wraps.

**Wrapping.** Text, titles, labels and flow node labels wrap at spaces; a
word wider than its line is broken across lines, so nothing is drawn past its
slot. Table cells and headers that do not fit their column are cut with `…`
(only their first 1000 characters are ever measured).

## Flow diagrams

```json
{"type": "flow", "title": "Reporting pipeline",
 "nodes": [{"id": "agent", "label": "Reporting agent", "component": "card"},
           {"id": "viz", "label": "ieeVisualizer", "component": "process",
            "group": "Rendering"}],
 "edges": [{"from": "agent", "to": "viz", "kind": "dataflow", "label": "view data"}],
 "layout": {"algorithm": "layered", "direction": "LR"}}
```

* Nodes: `id`, `label`, `component` (`card`, `process`, `data`, `external`;
  drawn as rounded / square / green rounded / dashed boxes), optional
  `group` (a swimlane).
* Edges: `from`, `to`, optional `label`, `kind`: `dataflow` (default, solid),
  `dependency` (dashed grey, reads "A is needed by B") or `causal` (dashed
  purple). **A causal edge is a claim**: it must have a label stating what is
  claimed (`causal_edge_unlabelled` otherwise), is drawn as "claim: …" and
  is worded "is claimed to affect" in the text version.
* `layout` is only a hint and is kept apart from the graph: `"layered"`
  (shorthand for `{"algorithm": "layered", "direction": "LR"}`), direction
  `LR` or `TB`, or `{"algorithm": "grid", "columns": n}`.

**Layered layout** is pure Python and deterministic (ties are always broken
by declaration order): cycles are broken by reversing DFS back edges (the
graph still lays out; validation reports `cycle_in_layered_layout` as
*info*, naming the edges drawn against the flow), longest-path layering,
dummy slots so long edges bend around boxes, barycentre ordering (four
down/up sweeps, keeping the order with fewest crossings) within swimlanes,
and uniform box sizes from measured labels (wrapped at 180 px). Layering is
O(V + E) and crossing counts O(E log E), so layout time grows close to
linearly within the size limits below. Edges leave
the source box's downstream side and end with an arrow on the target box's
upstream side; an edge between different rows elbows right after the source
so its label sits level with its target. A flow wider than its slot is
scaled down to fit.

**Grid layout** places nodes row-major. An edge is a straight line when that
line clears every other box; otherwise it detours orthogonally through the
empty gaps between rows and columns, so an edge never passes through a node
it does not connect: `A → C` in a row
`A B C` is drawn under `B`, never through it. Every detour gets its own lane
(8 px apart) in each gap it runs along, and a gap is widened until all its
lanes fit, so two edges that share no node never run along the same line;
within a gap, runs whose stubs leave the boxes above (left) take the lanes
nearer those boxes, so stubs from opposite sides never overlap either.

**Text alternative.** `flow_outline(flow)` lists every node in reading order
(layer by layer, then lane and position; declaration order for `grid`) with
its outgoing links, e.g. `"3. ieeVisualizer: feeds Email report (PNG / HTML);
feeds Interactive report (SVG + spec)."`. It is written into the SVG
`<desc>` of the flow and, in HTML, into a `<details>` "Text version" list.

## Validation codes

`unsupported_schema_version`, `invalid_value`, `invalid_type`,
`empty_container` (warning), `figure_source_missing`,
`figure_source_conflict`, `unknown_figure`, `unused_figure` (info),
`unknown_dataset`, `table_needs_snapshot`, `unknown_column`, `empty_flow`,
`duplicate_node_id`, `unknown_node`, `causal_edge_unlabelled`,
`cycle_in_layered_layout` (info), `self_loop` (info),
`column_length_mismatch` (a view dataset's columns differ in length; a table
is never drawn with zero rows instead), `invalid_text`, the size-limit codes
below and `table_truncated` (warning). Issues of embedded or
referenced FigureSpecs are reported with the figure's pointer prefixed, e.g.
`/figures/trend/layers/0/data`.

## Strings and size limits

Every string of a view - text, labels, titles, table cells, metadata and
the strings of embedded or referenced FigureSpecs - must be writable as XML:
a C0 control character other than tab, newline and carriage return, a lone
surrogate or U+FFFE/U+FFFF is refused with `invalid_text` and the string's
pointer (by `view_from_dict`/`view_from_json` at parse time, and by
`validate_view` for views built in Python; at most 20 such issues are listed
per view). If a figure's SVG still fails to parse, rendering raises a
`RenderError` (`figure_render_failed`, with the figure's pointer) rather than
an XML exception.

A view comes from an agent, so its size is bounded. These limits
(`ieeVisualizer.view.limits`) are checked by `validate_view` **before any
layout**, so an oversized view is refused in milliseconds with a pointer to
where the budget ran out. Flow, table, text and figure budgets are summed
over the whole view.

| Limit | Value | Code when exceeded |
|---|---|---|
| `MAX_DEPTH` | 32 | `too_deep` - container nesting (the root is depth 1); also checked while parsing |
| `MAX_VIEW_NODES` | 300 | `too_many_nodes` - containers and components |
| `MAX_FIGURES` | 12 | `too_many_figures` |
| `MAX_FLOW_NODES` | 400 | `too_many_nodes` - flow nodes |
| `MAX_FLOW_EDGES` | 800 | `too_many_edges` |
| `MAX_FLOW_DUMMY_NODES` | 4000 | `too_many_dummy_nodes` - sum of `span - 1` over the edges of layered flows (the slots long edges are routed through) |
| `MAX_TABLE_ROWS` | 300 | `too_many_rows` for a larger `max_rows`; a longer table without `max_rows` draws its first 300 rows, says "Showing 300 of N rows" and gets a `table_truncated` warning |
| `MAX_TABLE_CELLS` | 1000 | `too_many_cells` - drawn rows x columns |
| `MAX_TABLE_COLUMNS` | 50 | `too_many_columns` - columns of one table, counted whatever `max_rows` is (headers are drawn even with no rows) |
| `MAX_TOTAL_TABLE_COLUMNS` | 200 | `too_many_columns` - table columns summed over the view |
| `MAX_TEXT_CHARS` | 10 000 | `text_too_long` - any one string |
| `MAX_TOTAL_TEXT_CHARS` | 50 000 | `too_much_text` - every string the layout measures or draws: view title, text, notes, KPI labels/sources/comparison labels, KPI units (counted three times: value, delta, reference), card/table/flow titles, flow descriptions, node labels, lane names, edge labels, table headers (column name + unit, with or without rows), drawn string cells and the drawn strings of embedded figures (title, caption, axis labels, legend title, layer names, annotation labels) |
| `MAX_JSON_DEPTH` | 64 | `too_deep` - nesting of free-form JSON (view and figure `metadata`, `style`, `provenance.extra`), at parse time and in `validate_view`; `view_to_dict`/`view_hash`/patching a Python-built view nested deeper raise `SpecParseError` `too_deep`, never `RecursionError` |
| `MIN_SLOT_WIDTH` | 80 | `too_narrow` - narrowest slot a grid cell, horizontal-stack child or card body may get (px); validated at the default width 960 and checked again by `render_view` at the width it is given |
| `MAX_PNG_SIDE_PX` | 32 000 | `view_too_large` (`RenderError`) - PNG side in pixels (`height x scale`), checked after layout; HTML and SVG are not limited |

The values were chosen so that the largest view that passes renders in a few
seconds. Measured on the development container (Python 3.13, matplotlib
3.11; svg / html / png at scale 2):

| Largest allowed input | svg | html | png |
|---|---|---|---|
| layered chain, 400 nodes | 0.6 s | 0.03 s | 1.6 s |
| layered random DAG, 400 nodes / 800 edges, 3 lanes | 0.2 s | 0.1 s | 3.3 s |
| layered flow at 3916 dummy slots | 0.1 s | 0.1 s | 0.7 s |
| grid, 400 nodes / 800 edges | 0.2 s | 0.2 s | 1.8 s |
| table 300 x 3 (900 cells, truncated cells) | 0.4 s | 0.01 s | 4.2 s |
| table cells of 10 000 characters (truncation) | 1.8 s | 0.01 s | 1.8 s |
| 5 texts of one unbroken 10 000-character word (broken across lines; png at scale 1) | 1.1 s | 0.00 s | 5.4 s |
| table of 4 columns with 10 000-character units, `max_rows` 0 (png at scale 1) | 0.04 s | 0.00 s | 0.03 s |
| 299 KPI tiles | 0.3 s | 0.01 s | 5.1 s |
| 12 distinct figures | 1.5 s | 1.8 s | 2.3 s |
| every budget at once (flow, table, 12 figures, KPIs; png at scale 1) | 3.0 s | 1.8 s | 7.2 s |

`tests/view/string_field_audit.py` repeats this for every string field that
is laid out or drawn (titles, text, notes, KPI label/unit/source/comparison
label, table title/column name/unit/cell, flow title/description/node
label/lane/id/edge label, embedded figure title/caption, metadata,
provenance): a 10 000-character value, as many times as validation allows,
renders in at most 3.7 s as SVG or HTML and 7.9 s as PNG at scale 1 (the
PNG time is matplotlib drawing about 100 us per glyph, which is why
`MAX_TOTAL_TEXT_CHARS` is 50 000). The strings of embedded figures (title,
caption, axis labels, legend title, layer names, annotation labels) count
toward `MAX_TOTAL_TEXT_CHARS` too.

For comparison, before these limits a 10 000-node chain took 10.6 s, an
800-node / 1597-edge flow 68 s and a 5000-row table 43.6 s (11.6 MB PNG);
they are now refused (or, for the table, capped) in under 0.3 s. A table of
200 columns with 10 000-character units and `max_rows: 0` took 57 s as SVG
and passed validation; it is now refused (`too_many_columns`,
`too_much_text`) in 0.01 s. PNG time is
dominated by matplotlib drawing one text artist per label.

## Rendering

`render_view(view, format="svg" | "html" | "png", *, width=960, scale=2,
resolver=None) -> RenderResult` (adapter name `ieeVisualizer.view`). A view
with validation errors is refused with `SpecValidationError`.

* **SVG**: one document. Text, KPI tiles, tables and flows are native SVG
  (DejaVu Sans, with Verdana/Arial fallbacks). Each figure is rendered by the
  real FigureSpec pipeline (`ieeVisualizer.render.render`, i.e. the
  matplotlib adapter) to SVG and nested as an `<svg data-figure="<pointer>">`
  element; its ids and `#id`/`url(#id)` references are prefixed per
  placement (`v0-`, `v1-` ...) so two figures never collide, and its RDF
  metadata, comments and DOCTYPE are dropped. Its `<style>` block is not
  copied (it would style the whole page): matplotlib's
  `*{stroke-linejoin: round; stroke-linecap: butt}` becomes the same
  inherited properties on a `<g>` wrapping just that figure. Nodes and edges carry
  `data-node`, `data-edge`, `data-from`, `data-to`, `data-kind` so the
  connectivity stays inspectable in the output.
* **HTML**: a static, self-contained page: native HTML text, `<table>` and
  KPI blocks, inline SVG figures and flows, one `<style>` block, no script,
  no external stylesheet, font, image or link. Suitable for a browser or an
  "email-ish" viewer; mail clients that strip `<style>` or inline SVG should
  get the PNG.
* **PNG** (decision): no SVG rasteriser is added as a dependency. The layout
  emits one display list (rects, text runs, polylines, embedded figures)
  that is painted both as SVG text and onto a matplotlib canvas; figures are
  rendered to PNG by the real renderer at the target pixel size and placed
  pixel-exact. SVG and PNG therefore share every coordinate. `scale` is the
  pixel ratio (default 2: 1920 px wide).

Text is measured with matplotlib's FreeType binding on DejaVu Sans (always
installed with matplotlib), so wrapping is identical in SVG and PNG. A
browser without DejaVu Sans falls back to Verdana/Arial, whose widths differ
slightly; the HTML output reflows natively and is unaffected.

Output is deterministic: the same view, width, scale and library versions
give byte-identical SVG, HTML and PNG.

## Export

`export_view(view, formats=("html", "svg", "png"), writer, *, width, scale,
resolver, clock, name)` renders every format first, then stores
`view.<fmt>`, `view.json` (canonical; its hash is `view_hash`),
`figure-<n>.spec.json` per placed figure and `manifest.json` last. The
manifest (`kind: "view"`) lists each figure's pointer, spec hash and the
`DatasetRecord` (content hash, provenance, column meaning) of every dataset
it draws, the view-level datasets, every KPI's label/value/unit/source, and
per format the file hash, size and the figures' fidelity warnings (re-pathed
under the figure pointer). The artifact id is content-derived, as for a
figure artifact.

## Known gaps / follow-ups

- No JSON Schema for ViewSpec yet (the FigureSpec has `json_schema()`).
- No `ViewArtifact.from_dict`; the manifest is written, not yet re-read.
- PDF output for views; images, formulas and controls as components.
- Flow layout: edges are polylines (no splines); parallel edges between the
  same pair overlap; lanes are bands orthogonal to the layer direction only.
  In a grid, detours sharing a gap are offset 8 px apart but their labels
  can still overlap, and two detours entering the same side of a box share
  the entry point.
- Text measurement assumes DejaVu Sans; corporate themes (fonts, colours)
  are not applied to views yet.
- Tables take inline snapshots only; a `DatasetRef` must be resolved first.
