# Graphs and networks (`ieeVisualizer.network`, schema 1.0)

A **NetworkSpec** is a semantic graph - nodes, edges and what their numbers
mean - plus a separate **layout hint**. The graph is the claim; the layout is
how it is drawn. The same spec renders as a node-link diagram, as an
adjacency matrix, or reads as a text outline
([CONVERSATIONAL_UI_AND_REPORTING.md](CONVERSATIONAL_UI_AND_REPORTING.md)
section 5.2). It follows the FigureSpec conventions ([SPEC.md](SPEC.md)):
dataclasses, strict parsing with JSON-pointer errors, `validate_network()`
returning `ValidationIssue`s, canonical JSON, a hash, and RFC-6902 patches.

```python
from ieeVisualizer.network import (NetworkSpec, Node, Edge, Quantity, Layout,
                                   validate_network, render_network, network_outline,
                                   export_network, network_to_json, network_hash)
from ieeVisualizer.render import RenderRequest

spec = NetworkSpec(
    nodes=[Node("a", "Alice", group="R&D", size=12), Node("b", "Bob", group="Sales", size=4)],
    edges=[Edge("a", "b", weight=3.0)],
    node_size=Quantity("Headcount", "people"),
    edge_weight=Quantity("Messages", "msg/week"),
    layout=Layout("force", seed=0))
assert [i for i in validate_network(spec) if i.severity == "error"] == []
png = render_network(spec, RenderRequest(format="png")).content
print(network_outline(spec))
```

Importing `ieeVisualizer.network` (or `ieeVisualizer.templates`) imports
neither numpy nor matplotlib; layout and drawing import them when called.

## Shape

```json
{"schema_version": "1.0", "kind": "network", "title": "Organisation",
 "nodes": [{"id": "ceo", "label": "Chief executive", "group": "Management", "size": 2},
           {"id": "ops", "label": "Operations", "group": "Operations", "size": 4,
            "position": [0.0, 1.0], "attributes": {"cost_centre": "100"}}],
 "edges": [{"source": "ceo", "target": "ops", "weight": 1.0, "directed": false,
            "kind": "manages", "label": "line"}],
 "directed": false, "self_loops": "forbid",
 "node_size": {"title": "Headcount", "unit": "people", "signed": false},
 "edge_weight": {"title": "Messages", "unit": "msg/week", "signed": false},
 "layout": {"algorithm": "layered", "seed": 0, "iterations": 200, "direction": "TB",
            "order": "group"},
 "provenance": {"source": "hr-system"}, "metadata": {}}
```

| Field | Meaning |
|---|---|
| `nodes[].id` | Unique, non-empty. `label` defaults to the id. |
| `nodes[].group` | Community / department; coloured with the categorical palette (matplotlib's 10 colours; more groups repeat colours with a different marker shape and a `colour_cycle_wrapped` warning). |
| `nodes[].size` | A value of `node_size`. Marker **area** is proportional to it (floored at 6 pt² so a tiny value stays visible). `null` = not recorded: drawn hollow at a neutral size with its own legend entry, never as a small disc. |
| `nodes[].position` | `[x, y]`: pins the node in a `force` layout (other layouts ignore it, `position_ignored` warning). The force layout works at a scale of about ±1, so give pins in that range; \|x\|, \|y\| ≤ `MAX_ABS_POSITION` = 10⁶ (`position_out_of_range`), which keeps its forces finite. A pin is a layout hint: it does not change `graph_hash`. |
| `nodes[].attributes` | Free-form JSON carried along (not drawn). With `metadata` and `provenance.extra`: JSON values only, at most 64 levels deep and 2 MB of canonical JSON in total (see size limits). |
| `edges[].weight` | A value of `edge_weight`. `null` = not recorded. |
| `edges[].directed` | Overrides the spec-wide `directed` for this edge. |
| `edges[].kind` | Free text; each kind gets a dash pattern and a legend entry. `label` appears in the outline. |
| `node_size`, `edge_weight` | **What the numbers mean**: `title` and `unit` (use `"1"` for dimensionless). A size or weight without its quantity declared is an error (`undeclared_quantity`), so a legend can always state the unit. `signed: true` allows negative edge weights (e.g. correlations); node sizes cannot be signed. |
| `self_loops` | `forbid` (a loop is an error) or `allow` (info; drawn as a small ring on the node). |
| `layout` | Only a hint, see below. The string shorthand `"layout": "circular"` is accepted. |

`network_to_json(spec)` is canonical (sorted keys, compact, numbers as
floats so `3` and `3.0` hash alike); `network_hash(spec)` is its SHA-256;
`graph_hash(spec)` hashes only nodes (without their layout pin
`position`), edges, `directed` and the quantities, so the same graph under a
different layout, title or pinning has the same `graph_hash`.
`network_from_dict` / `network_from_json` refuse unknown fields, wrong
types, `NaN`/`Infinity`, unsupported versions, and text that cannot be
stored or drawn (`invalid_text`, see below) with a `SpecParseError`
pointing at the value.

**Text.** Every string (ids, labels, groups, kinds, title, caption, units,
provenance, and every key and string inside the free-form JSON) must be
valid Unicode that XML 1.0 can hold: no lone surrogates (which cannot be
encoded as UTF-8, so neither hashed nor drawn) and no C0 control
characters other than tab, newline and carriage return, nor U+FFFE/U+FFFF
(which would make the SVG and its embedded outline malformed). Parsing and
`validate_network` both report them as `invalid_text` at the offending
value. `apply_network_patch(spec, ops)` is
`ieeVisualizer.spec.apply_patch` (same implementation, same RFC 6902 rules)
on the network's dict form, atomic, and by default refuses a result with
validation errors.

## Layouts

All layouts are deterministic: no hash-order dependence, ties broken by
declaration order, randomness only from `numpy.random.default_rng(seed)`.
The test suite checks the force layout and the SVG are byte-identical in
fresh processes with different `PYTHONHASHSEED`s.

| `algorithm` | What it does |
|---|---|
| `force` | Fruchterman-Reingold (numpy, O(n²) per iteration), seeded random start, linear cooling, a weak pull to the centre so components stay together, pinned nodes held at `position`. **Edge weights do not move nodes**: distances in a force layout are not a measurement. |
| `circular` | Nodes on a circle, clockwise from the top, grouped by `group` (groups in order of first appearance). Labels point outwards. |
| `layered` | DAG layers top-down (`direction: "TB"`) or left-right (`"LR"`). Cycles are broken by reversing DFS back edges (`cycle_in_layered_layout` info names them); longest-path layering. A forest (each node at most one parent) is drawn as a tidy tree, each parent centred over its children; otherwise four barycentre sweeps order each layer. An edge spanning more than one layer is bowed so it does not run through the layers in between. |
| `grid` | Row-major in group order. |
| `bipartite` | Exactly two groups, one per column (first group on the left); right-hand nodes ordered by the mean position of their neighbours. Fewer or more groups, or an edge inside a group, is `not_bipartite`. |
| `matrix` | Adjacency matrix (rows = source, columns = target; an undirected edge fills both cells). `order`: `group` (groups contiguous, declaration order inside, lines between groups), `seriation` (reverse Cuthill-McKee: a bandwidth-reducing order that puts connected nodes next to each other; components in order, each starting at its lowest-degree node), or `input`. No edge is a blank cell, never 0; an edge with no recorded weight is a hatched grey cell with its own legend entry. Colours: viridis from 0 (RdBu centred on 0 for signed weights), colour bar labelled "title (unit)". Rows and columns are labelled up to 60 nodes (`matrix_labels_omitted` warning above). |

## Encodings and legends

* **Edge weight → width and shade.** Width runs linearly from 0.6 pt
  (weight 0) to 5 pt (the largest |weight|) and the grey darkens with it, so
  the mapping is monotone and redundant (readable without colour). Signed
  weights: width by |weight|, colour red (negative) / blue (positive). The
  legend section is titled "title (unit)" and shows two to four round
  reference values, each labelled with the unit, drawn at the width they
  map to.
* **Missing weight** (`null`): drawn thin (1 pt) in a colour the weight
  scale never uses - orange next to the grey scale, a neutral grey next to
  the red/blue signed scale - listed as "not recorded (n)" in the legend, a
  `missing_weight` warning, and "not recorded" in the outline. It is never
  drawn at the width or shade of 0. It keeps its kind's dash pattern, so
  kind and missingness are both visible. (For signed weights the grey is
  told apart from red and blue by lightness as well as hue; under a
  red-green colour-vision deficiency the legend and the outline remain the
  reference.)
* **Parallel and reciprocal edges.** Edges joining the same two nodes (a ->
  b and b -> a; or edges of different kinds or directions) bow apart
  symmetrically about the straight line, 0.3 x length apart, so none is
  hidden under another; further self-loops on one node are larger rings.
  In the matrix such edges share a cell: it shows no single weight, is
  cross-hatched with the number of edges, has its own legend entry and an
  `edges_combined_in_cell` warning naming the cells; the outline lists each
  edge with its own weight.
* **Node size → area**, legend "title (unit)" with round reference values.
* **Directed edges** end in an arrowhead whose tip touches the target's rim;
  undirected edges run rim to rim. Edge geometry is computed in display
  space from the node radii, so the tests can check every edge starts and
  ends at its nodes.
* **Labels** sit under their node (outwards on a circle; beside it in a
  top-down layered layout, where the edges to its children leave from
  below, falling back to under it when the side would overlap or leave the
  plot) with a light halo. Collision avoidance is greedy in priority order (degree, then size,
  then declaration order): the full label, else abbreviations to 12 and 6
  characters + "…", else the label is left out. A label never covers
  another label or another node. Abbreviated or omitted labels give
  `labels_abbreviated` / `labels_skipped` warnings naming the nodes (the
  matrix's row/column labels, cut at 18 characters, give
  `labels_abbreviated` too); the outline always has every label in full.
  With a render profile (`RenderRequest(profile=...)`) text is at least the
  profile's minimum, a label is wrapped at spaces before it is shortened, the
  figure grows to fit and a phone puts the legend below the graph
  ([MOBILE_AND_EMAIL.md](MOBILE_AND_EMAIL.md)); the send gate refuses any
  label still shortened or left out. Every render takes a place for a label
  only if it is at least 3 pt nearer its own node than any other node's rim;
  a label with no such place is drawn at the first free one with a
  `label_ambiguous` warning.

## Text alternative

`network_outline(spec)` is the accessible version: a summary line (node
and edge counts, directed / undirected / mixed), the weight's meaning and
how many values are missing, every node in reading order (layer by layer for
`layered`, the row order for `matrix`, declaration order otherwise) with
group, size and degree (in/out for directed edges), then every edge with
kind, label and weight:

```
Network: 2 nodes, 1 directed edges.
Nodes:
1. Root (in-degree 0, out-degree 1)
2. Child (in-degree 1, out-degree 0)
Edges:
- Root -> Child
```

The SVG output carries it as its description (`dc:description` in the SVG
metadata), so the connectivity stays inspectable in the file itself.

## Validation

`validate_network(spec)` never raises. Codes (errors unless noted):
`invalid_type`, `invalid_value`, `unsupported_schema_version`,
`empty_network`, `duplicate_node_id`, `unknown_node` (with did-you-mean),
`self_loop` (error under `forbid`, info under `allow`), `parallel_edges`
(same ends, direction and kind: merge upstream and say how weights were
combined; nothing is summed silently), `undeclared_quantity`,
`missing_unit`, `negative_weight` (unless `edge_weight.signed`),
`negative_size`, `not_bipartite`, `invalid_text`, `non_finite_number`,
`position_out_of_range`, `too_deep`, `too_large`; warnings `missing_weight`,
`missing_value` (node size), `position_ignored`; info
`cycle_in_layered_layout`, `disconnected` (number and sizes of connected
components). At most 20 issues per code are listed, then one info line
with the remainder. Rendering raises `SpecValidationError` on any error and
copies warnings into `RenderResult.warnings`, plus the drawing warnings
`labels_abbreviated`, `labels_skipped`, `label_ambiguous`, `colour_cycle_wrapped`,
`matrix_labels_omitted`, `edges_combined_in_cell`. `compute_layout` checks
its own output: a NaN or infinite coordinate raises `RenderError`
(`layout_not_finite`) instead of drawing an empty figure.

## Size limits

Checked first, in O(nodes + edges + text), before any layout: an
oversized graph is refused in milliseconds (100 000 nodes / 200 000 edges:
17 ms) and nothing is laid out.

| Limit | Value | Code |
|---|---|---|
| `MAX_NODES` | 1000 | `too_many_nodes` |
| `MAX_EDGES` | 5000 | `too_many_edges` |
| `MAX_FORCE_NODES` | 500 | `too_many_nodes` at `/layout/algorithm` |
| `MAX_FORCE_WORK` | nodes² × iterations ≤ 50 000 000 | `too_much_layout_work` |
| `iterations` | 1 - 1000 | `invalid_value` |
| `MAX_MATRIX_NODES` | 1000 | `too_many_nodes` |
| `MAX_LABEL_CHARS` | 200 per id / label / group / kind | `label_too_long` |
| `MAX_TOTAL_LABEL_CHARS` | 50 000 | `too_much_text` |
| `MAX_JSON_DEPTH` | 64 levels in `attributes`, `metadata`, `provenance.extra` | `too_deep` |
| `MAX_FREE_JSON_BYTES` | 2 000 000 bytes of canonical JSON, all free-form JSON together | `too_large` |
| `MAX_ABS_POSITION` | \|pinned coordinate\| ≤ 10⁶ | `position_out_of_range` |

Free-form JSON is walked with an explicit stack (parsing and validation
alike), so a deeply nested document is refused at depth 65 with a pointer
rather than exhausting the interpreter stack, and the walk stops as soon as
the byte budget is spent. The layered layout's tidy-tree placement is
iterative too: a 1000-node chain lays out from any call depth and the
interpreter's recursion limit is never changed.

Measured on the development container (Python 3.13, matplotlib 3.11,
numpy 2.5; random graphs with 5 groups and labels on every node; default
6.4 × 4.8 in at 100 dpi):

| Largest allowed input | validate | layout | svg | png |
|---|---|---|---|---|
| force, 500 nodes / 5000 edges, 200 iterations | 0.02 s | 3.1 s | 4.8 s | 3.7 s |
| circular, 1000 / 5000 | 0.02 s | <0.01 s | 2.6 s | 2.3 s |
| grid, 1000 / 5000 | 0.03 s | <0.01 s | 5.6 s | 4.5 s |
| layered, 1000 / 5000 | 0.02 s | 0.03 s | 5.0 s | 4.9 s |
| matrix (seriation), 1000 / 5000 | 0.02 s | 0.01 s | 0.6 s | 0.2 s |
| matrix (group), 1000 / 5000 | 0.03 s | <0.01 s | 0.2 s | 0.2 s |

Render times include the layout. Before the budget, a 500-node force
layout at 1000 iterations took 14.8 s (17 s to SVG); it is now refused.
Drawing edges as one `LineCollection` with precomputed arrowheads instead of
one `FancyArrowPatch` each cut the 1000-node circular PNG from 10.6 s to
2.3 s. Most of the remaining node-link time is label placement (one text
measurement per candidate label).

## Templates

Registered in the default template registry (`templates.get(name)`); they
follow the [template contract](TEMPLATES.md) (declared parameters and
columns, `check()` never raises, issue paths into `{"data", "params"}`,
`metadata["template"]` with name, version, resolved params and warnings)
but return a **NetworkSpec**. Units come from the columns; a weight or
size column without a unit and without a `*_unit` parameter is
`missing_unit`, and a `*_unit` that contradicts the column is
`unit_override_conflict`. Nothing is converted.

* **`org_chart`** 1.0.0 - one row per unit or person: `id_column`,
  `parent_column` (null for a root), optional `label_column`,
  `group_column`, `size_column` (e.g. headcount), `direction` TB/LR.
  Layered tidy tree, undirected edges parent → child. A parent that is not
  an id is `unknown_parent`, a cycle `not_a_hierarchy`, a repeated id
  `duplicate_key`, a null id `null_in_key_column`.
* **`dependency_map`** 1.0.0 - one row per edge, the source depends on the
  target (arrows point at the dependency): `source_column`,
  `target_column`, optional `weight_column` (+ `weight_unit`,
  `weight_title`) and `kind_column`; `layout` layered (default, LR) or
  force.
* **`network`** 1.0.0 - generic and scientific: `data = {"edges": table,
  "nodes": optional table}`; edge columns as above plus `weight_signed`,
  `directed`; node table `id_column`, `label_column`, `group_column`,
  `size_column`; any `layout` including `matrix` with `order`; `seed`;
  `self_loops`. Without a node table the nodes are the edge endpoints in
  order of appearance.

## Export

`export_network(spec, formats, writer, *, request=None, name=None,
clock=None)` writes `figure.<fmt>`, `spec.json` (canonical network JSON;
the manifest's `spec_hash` is `network_hash`) and `manifest.json` last, with
the same `VisualizationArtifact` manifest as FigureSpec exports. The graph
is recorded as dataset `graph` with `graph_hash` and the spec's provenance.
The gallery (`examples/gallery.py`) exports every network template plus a
seeded stochastic block model (force, circular, matrix by group and by
seriation) and a bipartite allocation graph.

## Decisions

* **A separate package and spec.** FigureSpec has no node/edge model, so
  networks live in `ieeVisualizer/network/` with their own plain
  dataclasses, following the spec's conventions (the spec models stay
  stdlib dataclasses; see ROADMAP, "Phase 2 onward", Foundation). The only edits elsewhere: `templates/__init__`
  registers the three templates, and the gallery and its test dispatch on
  `kind: "network"`.
* **No graph library.** Layouts are ~250 lines of numpy; networkx/graphviz
  would add a dependency for less control over determinism.
* **Quantities are declared once per spec**, not per node or edge, so mixed
  units cannot occur and every legend can state the unit.
* **Parallel edges of the same kind and direction are refused** rather than
  summed or overdrawn: combining them is an aggregation the data owner must
  state. Edges that differ in kind or direction are distinct facts; they
  are drawn apart (bowed) and, in the matrix, flagged where they share a
  cell, never hidden.
* **Weights never influence positions.** A force layout that pulls heavy
  edges together looks like a measurement of distance and is not one.

## Known gaps

* No edge labels drawn on the canvas (they are in the outline); no edge
  bundling; parallel edges of the same kind and direction are refused, not
  drawn side by side.
* The layered layout has no dummy nodes; long edges are bowed, not routed
  around nodes, and the barycentre heuristic does not guarantee minimum
  crossings. Flow diagrams in the view layer (#12) route long edges
  through dummy slots; the two should share one layered engine later.
* No interactive output (hover, focus, keyboard navigation) and no HTML
  view; the outline is the accessible alternative today.
* No JSON Schema for NetworkSpec yet.
* Matrix cells are square and unlabelled above 60 nodes.
