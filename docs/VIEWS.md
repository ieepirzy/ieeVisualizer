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

Numbers are never given invented precision or invented digits, and nothing
non-zero is ever shown as zero. A float is formatted from its shortest
decimal form (`Decimal(repr(v))`), never from its binary expansion. Without
`decimals` it is written with at most 15 significant digits, the precision
a float64 holds for every decimal, so float noise disappears (`0.1 + 0.2`
is `0.3`) and no digit is ever added; an integral value is written without
decimals (a numpy float32 is written from its own shortest form, `0.1`; once
widened to a Python float it is `0.100000001490116`, a different number).
With `decimals` (an integer from 0 to 10) the decimal form is rounded
**half-up**, the rule of `ieeVisualizer.themes.locale`: `2.5` -> `3`,
`0.125` -> `0.13`, `18.25` -> `18.3`, `2.675` -> `2.68`. A non-zero value
that rounds to zero is written as the bound it lies within: `<0.001`, or
`>−0.001` when negative (exactly `0` stays `0.000`); with the unit `%`, 100
is a bound too (`99.97` at one decimal is `>99.9%`, `100.03` is `<100.1%`,
exactly 100 stays `100.0%`). From 10^15 in magnitude, decided on the value
as rounded, where a float no longer holds every integer digit, a number is
written in exponent notation and `decimals` is ignored
(`999999999999999.9` at 0 decimals is `1e+15`; `1e23` is `1e+23`, not
`99 999 999 999 999 991 611 392`), with at most 15 significant digits for a
float and 17 for an exact integer. Thousands are grouped with U+202F NARROW
NO-BREAK SPACE, so a number is never broken across lines, in our layout or
in a browser; every minus sign, of the number and of its exponent, is
U+2212 (`−1.5e−05`), and nothing is ever written `−0`.

A KPI delta is computed exactly in `Decimal` on the numbers as written
(`0.3` vs `0.1` gives `+0.2`, not `+0.19999999999999998`), so it can
neither gain digits nor overflow: `1e308` vs `-1e308` is `▲ +2e+308`, never
`inf`. The arrow follows the sign of the exact delta: only an exact tie is
`= 0.0`; a delta below the last decimal is `▲ <+0.1` / `▼ >−0.1`. The
delta of a percentage is in **percentage points**: 94.2 % vs 92.7 % is
`▲ +1.5 pp`, never `+1.5%`, which would read as a relative change (that is
+1.6 %). A percentage is any unit starting with `%` or `％` (what follows is
kept: `% YoY` gives `▲ +2.0 pp YoY`) and `pct`, `percent`, `per cent`,
`%-points`, `pp`. Relative (percent) changes are not offered. A percentage
KPI whose values all lie in [0, 1] gets the non-gating warning
`kpi_percent_looks_like_fraction` (0.942 meant as 94.2 %?). Every line
of a KPI tile stays inside the tile, and no number is split: the value
shrinks from 30 to 14 px and then wraps between words (at ASCII spaces
only); a number that still does not fit shrinks down to 8 px and then is
written in compact exponent notation with 6, 3, 2 and finally 1
significant digits (`1.2e+8`), the same for the comparison line.

**Wrapping.** Text, titles, labels and flow node labels wrap at ASCII
spaces and tabs (never at no-break or thin spaces); a
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
| `MAX_TOTAL_TEXT_CHARS` | 50 000 | `too_much_text` - every string the layout measures or draws: view title, text, notes, KPI labels/sources/comparison labels, KPI units (counted three times: value, delta, reference), card/table/flow titles, flow descriptions, node labels, lane names, edge labels, table headers (column name + unit, with or without rows), drawn string cells and the drawn strings of embedded figures (title, caption, axis labels, legend title, layer names, annotation labels; counted twice, `FIGURE_TEXT_WEIGHT`) |
| `MAX_JSON_DEPTH` | 64 | `too_deep` - nesting of free-form JSON (view and figure `metadata`, `style`, `provenance.extra`), at parse time and in `validate_view`; `view_to_dict`/`view_hash`/patching a Python-built view nested deeper raise `SpecParseError` `too_deep`, never `RecursionError` |
| `MIN_SLOT_WIDTH` | 80 | `too_narrow` - narrowest slot a grid cell, horizontal-stack child or card body may get (px); validated at the default width 960 and checked again by `render_view` at the width it is given |
| `MAX_PNG_SIDE_PX` | 32 000 | `view_too_large` (`RenderError`) - PNG side in pixels (`height x scale`), checked after layout; HTML and SVG are not limited |
| `MAX_PNG_PIXELS` | 64 000 000 | `view_too_large` (`RenderError`) - PNG area in pixels (`round(width x scale) x round(height x scale)`), checked after layout and before rasterising: memory follows the area, so a wide, tall PNG within the side limit (16 000 x 32 000 px used 2 GB) is refused. The default width and scale (1920 px wide) reach the side limit first |

The values were chosen so that the largest view that passes renders in
seconds, not minutes. Measured on the development container (Python 3.13,
matplotlib 3.11, 4 cores) at the **default** `render_view` arguments (width
960, scale 2), one view per row:

| Largest allowed input | svg | html | png |
|---|---|---|---|
| layered chain, 400 nodes | 0.4 s | 0.03 s | 1.8 s |
| layered random DAG, 400 nodes / 800 edges, 3 lanes | 0.2 s | 0.07 s | 6.6 s |
| layered flow at 3315 dummy slots | 0.1 s | 0.4 s | 1.0 s |
| grid, 400 nodes / 800 edges | 0.1 s | 0.1 s | 2.1 s |
| table 300 x 3 (900 cells of 50 characters, truncated) | 2.7 s | 0.00 s | 8.6 s |
| table cells of 10 000 characters (truncation) | 0.02 s | 0.00 s | 0.09 s |
| 5 texts of one unbroken 10 000-character word (broken across lines) | 1.5 s | 0.00 s | 7.5 s |
| table of 4 columns with 10 000-character units, `max_rows` 0 | 0.05 s | 0.00 s | 0.03 s |
| 299 KPI tiles | 0.4 s | 0.01 s | 4.7 s |
| 12 distinct figures | 2.2 s | 2.9 s | 8.6 s |
| flow 120 / 240, 30-row table, 12 figures and 40 KPIs in one view | 2.8 s | 2.7 s | 11.0 s (peak RSS 772 MB) |

So the worst case at the default scale is about **9 s for one component kind
and 11 s for a PNG mixing all of them**; SVG and HTML stay under 3 s. PNG
time is matplotlib drawing about 100 us per glyph and rasterising every
embedded figure at the output scale, so it is roughly halved at `scale=1`.
These limits were kept rather than lowered to reach a uniform 8 s: lowering
`MAX_FIGURES` or `MAX_TOTAL_TEXT_CHARS` further would refuse ordinary
briefings, and SVG/HTML (the interactive formats) are fast throughout. A
view using every budget at once is taller than `MAX_PNG_SIDE_PX` at scale 2
and is refused as PNG in 0.07 s (`view_too_large`); its SVG and HTML take
2.6 s and 2.5 s.

`tests/view/string_field_audit.py` repeats this for every string field that
is laid out or drawn (titles, text, notes, KPI label/unit/source/comparison
label, table title/column name/unit/cell, flow title/description/node
label/lane/id/edge label, embedded figure title/caption, metadata,
provenance): a 10 000-character value, as many times as validation allows,
renders at the default scale in at most 2.9 s as SVG or HTML and 8.6 s as
PNG (two audit processes side by side). The strings of embedded figures
(title, caption, axis labels, legend title, layer names, annotation labels)
count **twice** toward `MAX_TOTAL_TEXT_CHARS` (`FIGURE_TEXT_WEIGHT`):
matplotlib lays a figure's text out twice, once to place an `auto` legend
and once to draw it, so four figures with a 10 000-character title took
6.6 s as SVG before the weighting and two take 2.1 s now.

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

**Profiles.** `render_view(view, fmt, profile="email-phone")` takes width
and scale from the profile, keeps text at its minimum, stacks columns on a
narrow profile and reports what still does not fit; `render_email(view)`
writes email-safe HTML with the figures as attached PNGs
([MOBILE_AND_EMAIL.md](MOBILE_AND_EMAIL.md)).

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
