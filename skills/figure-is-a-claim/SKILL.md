---
name: figure-is-a-claim
description: "Use before putting any number, KPI tile, chart, table cell or sparkline in front of a reader, and whenever data is missing, partial, failed to load, not yet collected or not applicable. Decides between showing a value, a gap, an explicit no-data state, or nothing; forbids rendering 0, 0%, an empty axis or a flat line for something that was not measured."
license: CC-BY-4.0
---

# A figure on screen is a claim

Every number, bar, line and tile asserts "this was measured, and this is what it
was". A reader cannot tell a measured €0 from a failed query that defaulted to
€0; the two look identical and mean opposite things. This skill is the gate
every other skill sits behind.

## The five states of a value

Classify every value before drawing it. Only the first is drawn as a number.

| State | Meaning | Show |
|---|---|---|
| **Measured** | Source returned a value for this period/segment | the value, with unit |
| **Measured zero** | Source returned, and the count/sum really is 0 | `0`, and say it is real if it is surprising |
| **Missing** | Should exist, did not arrive (export failed, late batch) | a gap + note naming the cause |
| **Not collected** | Nothing records it (yet) | omit the panel, or a plain no-data state |
| **Not applicable** | Undefined (rate with zero denominator, budget not set) | `n/a` or "no budget set", never 0 |

`budget_total_cents = null` means "no budget set". It is not 0, and a burn rate
against it is undefined, not 0%.

## Rules

1. **No default-to-zero.** `COALESCE(x, 0)`, `fillna(0)`, `?? 0`, `or 0` on a
   measure that feeds a figure is a defect unless the source semantics say a
   missing row *is* a zero (e.g. no booking rows for a day the office was
   open and the export succeeded). Write that justification next to the code.
2. **Failed source → no figure, not an empty one.** If the request for a
   panel's data failed, the panel says it failed (and when it last
   succeeded). It does not render axes with nothing on them.
3. **Gaps stay gaps.** Do not interpolate, carry forward or join points across
   a missing period unless the figure says so in the caption, and draw the
   imputed part distinctly.
4. **Rates need their denominator.** A conversion rate with denominator 0 is
   undefined. A rate on a small denominator is shown with its `n`
   (see `numbers-style`).
5. **Partial periods are labelled or dropped.** The current week on Wednesday
   is not a week (see `when-numbers-lie-audit`).
6. **The UI is not the issue tracker.** A no-data state is short and plain:
   "No ad-spend data for this period." It does not explain which backend
   table is missing or what an engineer should build. Gaps go to the issue
   tracker; the reader gets the fact that there is no data.
7. **The number comes from computation; the agent explains the number.**
   An agent never types a figure into a chart or sentence that a query did
   not return. Narrative cites the computed value; it is not its source.
8. **Placeholders never ship.** Mock arrays, example values and template
   defaults are removed or visibly marked before a figure leaves the
   workspace. A synthetic dataset says "synthetic" in its provenance.

## Where ieeVisualizer enforces this

- Missing values are `null` and stay `null`; `NaN` becomes `null`
  (`ieeVisualizer.spec`, docs/SPEC.md "Columns").
- The matplotlib renderer never draws `null` as 0: lines break, bars with no
  value are labelled "no data", heatmap cells stay blank, and each case adds a
  `null_values_omitted` warning on the `RenderResult` (docs/RENDERING.md
  "Missing data").
- A layer with no complete row is a `RenderError` (`empty_series`), and
  `validate()` reports `all_null_column` and `empty_dataset` as errors.
- Templates refuse `all_null_column` and warn `empty_series`.

What it does **not** enforce: whether a `0` in your data is a real zero. If you
filled nulls with zeros upstream, the library draws them faithfully. That
check is yours.

## Good / bad

Bad: weekly ad spend tile shows `€0` because the Google Ads sync failed.
Good: tile reads "Ad spend: no data (sync failed 6 Oct; last complete week 28
Sep–4 Oct: €1,240)".

Bad: crew utilisation chart for October with a flat line at 0% for crews
whose shift data has not been imported.
Good: those crews are absent from the chart and the caption says "3 of 7
crews: shift data not imported for October."

Bad: eval table shows `0.0` accuracy for a benchmark that timed out.
Good: cell shows `—` with footnote "run timed out after 6 h; not scored".

## Checklist

- [ ] Every drawn value is in state *Measured* or *Measured zero*.
- [ ] Grep the data path for `fillna(0)`, `COALESCE(..., 0)`, `?? 0`, `or 0`;
      each hit has a written justification or is removed.
- [ ] Failed or partial sources produce a stated gap, not an empty chart.
- [ ] No rate is shown with a zero or unstated denominator.
- [ ] No-data text is one plain sentence, with no engineering to-do list.
- [ ] Every number in the narrative traces to a query result or computed field.
- [ ] `RenderResult.warnings` were read; each `null_values_omitted` is
      explained in the caption or text.

Adapted in part from K-Dense `scientific-visualization` (MIT) and UK
Analysis Function chart guidance (OGL v3.0); changed: rewritten around the
measured/zero/missing distinction, examples replaced. Sources and licences:
`SOURCES.md`.
