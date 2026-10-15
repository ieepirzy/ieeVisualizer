---
name: ieevisualizer-report-figures
description: "Use when an agent prepares figures and text for a Muutto365 founder briefing or another recurring company report with ieeVisualizer, or answers a founder's follow-up question about one: classifies each item as monitoring, exception or exploration, runs evidence → figure → caveat → decision, keeps figures to the few that change a decision, freezes artifacts for reproducibility, and refuses to generate reports or charts nobody needs."
license: CC-BY-4.0
---

# Report figures for founder briefings

The output is not "a report was sent". It is "a founder saw a specific
change, understood how sure we are, and made a better decision with little
extra effort". Every figure, sentence and section is judged against that.

## Who owns what

| Owner | Responsibility |
|---|---|
| Host reporting workflow (e.g. a Mirarun worker) | schedule and cadence, permissions, recipient filtering, approval, delivery |
| Domain systems and analytics | authoritative data and every computed metric, interval and forecast |
| ieeVisualizer | visual form, templates, rendering, export, manifests |
| The agent (you) | choose items and forms, bind evidence, inspect figures, write the explanation |

You never compute a business metric inside the figure step, never widen
access beyond the permission-filtered evidence you were given, and never
send anything externally without the host's approval step.

## Step 1: classify every candidate item

| Class | Test | Treatment |
|---|---|---|
| **Monitoring** | metric within its normal range vs the stated base | one line in the top block; no figure unless a trend is the point |
| **Exception** | outside normal range, a threshold crossed, or a source failed | lede + one figure + caveat + decision request |
| **Exploration** | a founder asked a question | answer first, then evidence; reuse existing artifacts |

"Normal range" must be defined (e.g. same weeks last year ± the historical
spread, or a threshold the founders set), not felt. A failed source is
always an exception: say so and show no figure for it (`figure-is-a-claim`).

## Step 2: evidence → figure → caveat → decision

For each exception or exploration item:

1. **Evidence.** The computed values, with provenance and the comparison
   base. Run `when-numbers-lie-audit`.
2. **Figure** only if it shows something a sentence cannot
   (`chart-choice-by-relationship`); build with `ieevisualizer-figures`;
   put the report run id in `spec.metadata` and the provenance.
3. **Caveat.** One sentence naming the main thing that could make the figure
   misleading or the competing explanation.
4. **Decision.** What the founders could do, the owner, and when the
   question can be settled. Or "no action".

## Step 3: assemble

Use `company-report-structure`: the four-line top block (what changed, why
it matters, what is uncertain, what warrants action), then exceptions, then
explorations, then method. Prose in `editorial-voice-wsj-like`; numbers in
`numbers-style`; every headline checked by `headline-and-deck`; final pass
with `figure-review-redteam`.

## Metric traps (Muutto365-like examples)

| Metric | Define | Common trap |
|---|---|---|
| Confirmed bookings | accepted quote with deposit, by week of acceptance | counting by move date vs booking date mixes two series |
| Quote-to-booking conversion | bookings / quotes, same cohort | immature cohorts: recent quotes have not had time to convert |
| Revenue, revenue per move | € excl. VAT, recognised by move date | average of regional averages; restatements after late invoices |
| Ad spend per booking (CPA), ROAS | spend / attributed bookings, named attribution window | window changes; platform-reported vs observed conversions |
| On-time arrival | arrivals within the promised window / moves | moves with no recorded arrival time silently excluded |
| Damage claims per 100 moves | claims by move date / moves × 100 | claims arrive weeks later: the latest month is incomplete |
| Crew utilisation | booked crew-hours / available crew-hours | missing shift data read as zero availability or zero use |
| Cancellation rate | cancellations / bookings, by booking cohort | small regional bases; percentages without counts |

## Follow-up questions

When a founder asks "why did conversion fall?":

1. Reopen the report's artifacts (spec + frozen snapshot) rather than
   starting from a screenshot.
2. Compare with preceding periods and the same period last year.
3. Segment where the data permits (channel, region, cohort, device) and
   decompose rate vs mix.
4. Check the usual suspects: tracking or attribution change, price change,
   traffic quality, response latency, seasonality, immature cohorts.
5. Report supported findings with uncertainty. Name a likely cause only as
   far as the design allows (`statistical-claims`), and say what would
   verify it (a holdout, a week more data, a reconciliation).
6. A refreshed analysis is a new artifact; the sent figures stay as they were.

## No autonomous bureaucracy

- Do not create new recurring reports, sections or charts on your own
  initiative. Propose them; humans decide.
- A quiet period gets a short monitoring note (or nothing, if that is the
  agreed rule), not padding.
- Prefer one decisive figure to five descriptive ones.
- Prefer human approval for external distribution, sensitive data and
  consequential recommendations.
- Usefulness is measured by questions resolved, time to explanation and
  decisions supported, not by reports, pages or charts produced.

## Good / bad

Bad: 14-chart weekly PDF covering every metric, each titled with the metric
name, sent automatically every Monday.
Good: top block with three monitoring lines and one exception ("Meta ads
attribution export failed; ad spend per booking unavailable this week"),
one figure for the conversion change with its caveat, and a decision request.

## Checklist

- [ ] Every item classified monitoring / exception / exploration against a defined base.
- [ ] Source failures reported as exceptions with no figure.
- [ ] Each exception: evidence, at most one figure, one caveat, one decision with owner.
- [ ] Figures built with `ieevisualizer-figures`, inspected, run id in metadata.
- [ ] Top block written last and checked against the body.
- [ ] Follow-ups reuse frozen artifacts; refreshed analyses are new artifacts.
- [ ] Nothing added for completeness; nothing sent without the host's approval.

Sources and licences: `SOURCES.md`.
