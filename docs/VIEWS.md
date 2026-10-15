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
| `grid` | `columns` (default 2), `children` (row-major; a row is as tall as its tallest cell) |
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

Numbers are never given invented precision: without `decimals` an integral
value is written without decimals and any other value as stored.

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
and uniform box sizes from measured labels (wrapped at 180 px). Edges leave
the source box's downstream side and end with an arrow on the target box's
upstream side; an edge between different rows elbows right after the source
so its label sits level with its target. A flow wider than its slot is
scaled down to fit.

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
`cycle_in_layered_layout` (info), `self_loop` (info). Issues of embedded or
referenced FigureSpecs are reported with the figure's pointer prefixed, e.g.
`/figures/trend/layers/0/data`.

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
  metadata, comments and DOCTYPE are dropped. Nodes and edges carry
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
- Text measurement assumes DejaVu Sans; corporate themes (fonts, colours)
  are not applied to views yet.
- Tables take inline snapshots only; a `DatasetRef` must be resolved first.
