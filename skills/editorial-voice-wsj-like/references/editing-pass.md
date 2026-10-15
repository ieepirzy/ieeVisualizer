# Editing pass

A four-step pass for any draft. It changes wording and order; it never adds
facts. The step structure follows the idea of a house-style editor that
classifies, diagnoses, revises and verifies (see `../SOURCES.md`); the rules
are ours.

## 1. Classify

| Piece | Form | Length guide |
|---|---|---|
| Monitoring brief (routine, nothing unusual) | inverted pyramid, 2–5 sentences | short |
| Exception report (a metric left its normal range) | lede, nut graf, support/counter, decision | 3–8 paragraphs |
| Analysis (answering a question) | lede, nut graf, findings, caveats, method | as needed |
| Figure caption | subtitle facts, notes, source | 1–3 sentences |

## 2. Diagnose

Mark every instance of:

- **N** number without unit, period or comparison base
- **A** claim without attribution (data source, person, or "the analysis")
- **C** causal verb stronger than the design (`statistical-claims`)
- **H** hype or intensifier
- **G** missing nut graf by paragraph 4 (analysis/exception only)
- **O** opinion or recommendation inside a findings paragraph
- **U** unknown hidden (a caveat the evidence requires but the draft omits)
- **T** courtesy title or legal suffix

## 3. Revise

Fix each mark using only the evidence already present. If a fix needs a fact
the draft does not have (the comparison base, the source), write
`[NEEDS: …]` and return it to the author instead of inventing it.

## 4. Verify

- Every number in the revision appears in, or is computed from, the
  evidence. Recompute derived numbers (shares, changes).
- Every `[NEEDS: …]` is resolved or the sentence is cut.
- Read the first paragraph alone: does it carry the news?

## Worked example

Evidence attached to the draft: finance service weekly revenue, €48,300 in
the week to 4 October and €41,900 the week before; Google Ads campaign start
date 28 September. Nothing by region.

Draft (monitoring brief):

> Great week! Revenue skyrocketed thanks to our amazing crews and the new
> Google campaign. Mr. Virtanen's team in Tampere did especially well.

Diagnosis: H (great, skyrocketed, amazing), N (no figure, period, base),
C ("thanks to"), A (no source), T (Mr.).

Revision:

> Revenue was €48,300 in the week to 4 October, up 15% from €41,900 the
> week before, according to the finance service. The Google Ads campaign
> started on 28 September; whether it contributed is not yet measurable.
> Tampere, where Jari Virtanen leads the crew, [NEEDS: Tampere revenue for
> both weeks; the evidence has no regional figures].

The Tampere sentence goes back to the author: the evidence cannot support
"did especially well", and the editor must not invent a figure for it.
