# Pitfall catalogue with worked examples

All figures below are invented for illustration; the business examples
describe a fictional online furniture shop. Codes match the audit table in
`../SKILL.md`.

## Comparison

**C1 Incomplete period.** On 15 October, "October orders: 98, down 54% from
September's 212" compares 14 days with 30, and misses the month-end peak.
Compare 1–14 Oct with 1–14 Sep (and with 1–14 Oct last year), or wait. A
partial period that must be shown is drawn distinctly and labelled
"to 14 Oct".

**C2 Cherry-picked window.** "CPA fell 35% since we paused Campaign B" starts
the window at the worst week of the quarter. Re-run with the start moved one
and two periods earlier. If the effect shrinks to noise, the claim is the
window, not the pause.

**C3 Seasonality.** Furniture demand rises in spring and in the weeks before
the winter holidays. March over February growth is mostly calendar. Compare
with the same period last year, or show several years overlaid.

**C4 Regression to the mean.** The campaign picked for review because its
CPA hit €95 (typical €60) shows €66 the next week. Extreme values tend to be
followed by less extreme ones without intervention. Credit the change only
against a comparison group that was not selected for being extreme.

**C5 Definition shift.** Attribution window changed from 7 to 28 days on
14 Sep; conversions per click rise mechanically. Mark the change on the
chart (`reference_line` annotation) and do not compare across it, or restate
the earlier weeks under the new definition.

**C6 Nominal vs real.** Average order value 2022 vs 2026 in nominal euros
includes inflation. State "nominal" or deflate and name the index.

**C7 Missing base rate.** 1,000 deliveries: 700 flat-pack, 300 assembled
sofas. Damage claims: 20, of which 8 flat-pack (40%). Claims per 100
deliveries: flat-pack 1.1, sofas 4.0. "40% of claims come from flat-pack
deliveries" invites the opposite conclusion to the rate.

## Population

**P1 Survivorship.** Average customer rating of "delivered orders" excludes
orders cancelled after a missed delivery slot. In ML: reporting mean reward
over seeds that converged, or the best checkpoint chosen on the test set.

**P2 Selection on the outcome.** "Customers who used the online room
planner ordered 2× more often": visitors who intend to buy are the ones who
finish a room plan. Define segments by something fixed before the outcome
(traffic source, first-visit device).

**P3 Mix shift / Simpson's paradox.**

| | Aug leads | Aug ordered | Aug rate | Sep leads | Sep ordered | Sep rate |
|---|---|---|---|---|---|---|
| Google Ads | 200 | 80 | 40% | 600 | 246 | 41% |
| Referral | 100 | 50 | 50% | 100 | 51 | 51% |
| **Total** | 300 | 130 | 43.3% | 700 | 297 | 42.4% |

Both channels converted better; the total fell because the mix moved toward
the lower-converting channel. A headline "conversion fell" is true and
misleading. Report rate and mix separately.

**P4 Average of averages.** North region: 120 orders, mean €640. South
region: 15 orders, mean €1,100. Mean of the two means: €870. Revenue per
order: (120 × 640 + 15 × 1,100) / 135 = €691. Always aggregate from rows.

**P5 Join explosion.** Orders joined to delivery-team assignments (2–4
people per delivery) and then summed: revenue triples. Check row counts
after every join; aggregate to the order grain first.

**P6 Small n.** "East-region cancellation rate doubled to 20%" is 2 of 10
vs 1 of 10. Show counts; use a rate only when the denominator can carry it,
and give an interval when it is the point of the chart
(`uncertainty-display`). The "~30" in the audit table is a rough prompt to
look, not a threshold.

**P7 Timezone.** An order at 2026-09-30T22:30Z is 1 October 00:30 in
Berlin (UTC+2 in summer time). Bucket by the business's timezone and
record it (`provenance.timezone`).

## Encoding

**E1 Truncation.** Bars of 41% and 44% on an axis starting at 40% look like
a 4× difference. Bars and areas start at zero. A line or dot plot may use a
non-zero range when the variation is the point; say so if the range is
narrow.

**E2 Dual axes.** Ad spend (€) and orders (count) on two y-axes: the
apparent correlation depends on two arbitrary scale choices. Use two panels
sharing the x-axis, or plot one ratio (cost per order).

**E3 Area.** A bubble whose radius doubles has four times the area. Scale
area to the value, or use length.

**E4 Panel scales.** Six regional panels each auto-scaled: a 3-order swing
in the smallest region looks like the largest region's 60. Share the y-axis
or label "scales differ".

**E5 Aspect ratio.** A wide, short plot flattens; a tall, narrow one
steepens. Check that the visual slope matches the size of change the title
claims.

**E6 Unlabelled transform.** "Revenue index (Jan = 100)", "log scale",
"7-day rolling mean", "cumulative" belong on the axis label or caption.

**E7 Cumulative hides decline.** Cumulative 2026 orders always rise.
Weekly orders may be falling. Show the per-period series when the question
is the trend.

**E8 Forecast styled as data.** A target or plan line drawn solid in the
series colour reads as history. Use a distinct role and say what produced it
("Finance plan, set Jan 2026").
