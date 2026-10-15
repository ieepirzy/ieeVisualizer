# ieeVisualizer skills library

Agent skills for making figures and reports that are honest, rigorous and
useful for a decision: corporate reporting held to a scientist's standard of
evidence. The question running through all of them is *when do the numbers
lie?*, and the house rule behind them is that **a figure on screen is a
claim**.

The library is packaged as a Claude Code plugin (`.claude-plugin/plugin.json`
at the repository root; skills are discovered from this `skills/` directory).
Each skill is `skills/<name>/SKILL.md` in the Agent Skills format, with a
`SOURCES.md` listing every upstream source, its licence and what was taken.

Business examples describe a fictional online furniture shop and research
examples use invented AI/ML results; all figures are invented for
illustration and are not real company data.

## Install

```text
/plugin marketplace add ieepirzy/ieeVisualizer
/plugin install ieevisualizer-skills@ieevisualizer
```

Or copy individual `skills/<name>/` directories into `~/.claude/skills/` or a
project's `.claude/skills/`. Keep `SOURCES.md`, `NOTICE.md`, `LICENSE.md`
and `LICENSES/` with any copy.

A team that needs a private marketplace can use a CI-maintained, one-way
mirror of this directory and `.claude-plugin/`: see
[docs/SKILLS_MIRROR.md](../docs/SKILLS_MIRROR.md). Edit the skills here,
never in the mirror.

## Catalogue

### Truth and rigour

| Skill | Use it to |
|---|---|
| [`figure-is-a-claim`](figure-is-a-claim/SKILL.md) | decide whether a value may be shown at all: measured, zero, missing, not collected, not applicable; never draw 0 for "no data" |
| [`when-numbers-lie-audit`](when-numbers-lie-audit/SKILL.md) | audit comparisons, populations and encodings for the classic ways correct numbers mislead |
| [`statistical-claims`](statistical-claims/SKILL.md) | match wording to evidence: effect sizes with intervals, p-values, multiplicity, causal-language ladder |
| [`uncertainty-display`](uncertainty-display/SKILL.md) | show uncertainty and make every interval state kind, level, n and method |
| [`ml-results-reporting`](ml-results-reporting/SKILL.md) | report AI/ML results: seeds, eval standard errors, paired comparisons, IQM with bootstrap CIs, equal budgets |
| [`figure-review-redteam`](figure-review-redteam/SKILL.md) | adversarial last gate: alternative framings, image inspection, severity-ranked fix list |

### Form and design

| Skill | Use it to |
|---|---|
| [`chart-choice-by-relationship`](chart-choice-by-relationship/SKILL.md) | pick a sentence, table or chart form by the relationship the reader must see |
| [`perceptual-encoding`](perceptual-encoding/SKILL.md) | put the key comparison on the most accurate visual channel; colour roles |
| [`color-and-accessibility`](color-and-accessibility/SKILL.md) | colour roles and limits, contrast, colour-vision safety, alt text |
| [`scientific-figure-production`](scientific-figure-production/SKILL.md) | final size, format, raw points for small n, captions, reproducible export |

### Words and numbers

| Skill | Use it to |
|---|---|
| [`chart-text-wsj-like`](chart-text-wsj-like/SKILL.md) | WSJ-like chart and table text: finding-led title, subtitle, labels, annotations, source line |
| [`headline-and-deck`](headline-and-deck/SKILL.md) | check every atom of a headline against a claim ledger |
| [`editorial-voice-wsj-like`](editorial-voice-wsj-like/SKILL.md) | write and edit report prose in a WSJ-like news style: nut graf, story blocks, attribution |
| [`numbers-style`](numbers-style/SKILL.md) | rounding, pp vs %, units, VAT, locale (English/Finnish), dates and periods |
| [`source-and-provenance`](source-and-provenance/SKILL.md) | attach sources, definitions and lineage; methodology note; frozen vs refreshed |

### Reports and ieeVisualizer

| Skill | Use it to |
|---|---|
| [`company-report-structure`](company-report-structure/SKILL.md) | structure a report by decision depth with a four-line top block |
| [`ieevisualizer-figures`](ieevisualizer-figures/SKILL.md) | make figures with ieeVisualizer: template vs composed marks, binding, validation, render, inspect, patch, export |
| [`ieevisualizer-report-figures`](ieevisualizer-report-figures/SKILL.md) | run a recurring management briefing: monitoring vs exception vs exploration, evidence → figure → caveat → decision |

### How they fit together

A typical report figure passes through: `figure-is-a-claim` (may it be
shown?) → `chart-choice-by-relationship` → `ieevisualizer-figures` (build,
render, inspect) → `chart-text-wsj-like` and `numbers-style` → `when-numbers-lie-audit`
→ `figure-review-redteam`; the report around it uses
`company-report-structure`, `editorial-voice-wsj-like`, `headline-and-deck` and
`source-and-provenance`, orchestrated by `ieevisualizer-report-figures`.

## Library enforcement vs skill guards

Several rules are enforced by ieeVisualizer itself and the skills point at the
API: nulls are never drawn as zero (`null_values_omitted`, "no data" bar
slots, `empty_series`), units are never converted (`mixed_units`,
`axis_unit_conflict`), forecasts are always drawn distinctly
(`role="forecast"`), templates never invent uncertainty, dual y-axes cannot be
expressed, and every export carries a provenance manifest. Required interval
level and kind are planned ([ROADMAP](../docs/ROADMAP.md) "Phase 2 onward").
Everything else —
zero baselines for bars, complete periods, fair comparisons, truthful titles,
filled-in provenance, contrast, alt text — is guarded only by the skills.

## Conventions

- `SKILL.md` frontmatter: `name` (equals the directory name), `description`
  (what and when, trigger-oriented), `license`. At most 300 lines; longer
  material goes in `references/`.
- Each skill: concrete, checkable rules; good/bad examples with
  metrics from a fictional online shop or from AI research (all invented);
  a closing checklist.
- Examples are generic and self-contained: no names, paths, issue numbers,
  metric definitions or incidents from private projects or organisations.
  `tests/test_skills.py` fails if a listed internal term appears anywhere
  under `skills/` or `.claude-plugin/`.
- `SOURCES.md`: one bullet per source with URL, `Licence:` and `Use:`
  (`adapted` or `cited`). `adapted` is allowed only for Apache-2.0, MIT,
  OGL-UK-3.0, CC-BY-4.0, CC0-1.0 or same-owner sources; everything else is
  `cited` (ideas only, our own words).
- Skills adapting Apache-2.0 text end with a line stating the change.
- `tests/test_skills.py` checks all of the above, validates the plugin
  manifests, and runs the Python examples in
  `ieevisualizer-figures/references/examples.md` against the library.

## Licensing

**Decision: the skill text is CC BY 4.0; repository code stays MIT.**

Why:

- The repository (`../LICENSE`) is MIT. MIT is a software licence and fits
  the code here (the test and the plugin manifests), but the skills are prose.
- The skills adapt text from Apache-2.0 (Anthropic), MIT (K-Dense, Joe
  Amditis), Open Government Licence v3.0 (UK Analysis Function, ONS) and
  CC BY 4.0 (several papers) sources, plus CC0 (Rougier et al.). All of these
  require attribution except CC0. CC BY 4.0 is designed for prose, is
  compatible with OGL v3.0 (whose attribution statement is carried in
  `NOTICE.md`), and carries an attribution requirement forward to anyone who
  reuses the skills. A public-domain dedication (CC0 or MIT-0) would not be
  honest about the adapted portions, whose attribution obligations persist.
- Apache-2.0 and MIT allow derivative works under different terms provided
  their conditions are met. They are met here: copies of the licences and
  the upstream copyright notices are in `LICENSES/`; `NOTICE.md` lists
  every upstream work and where it was used; each skill that adapts
  Apache-2.0 text states that it was changed. Neither Apache upstream ships a
  NOTICE file of its own.
- Sources without a licence, or under restrictive terms (books, newspaper
  articles, the FT Visual Vocabulary content, Wilke's CC BY-NC-ND book,
  repositories without a licence file, Anthropic's proprietary document
  skills), were used for ideas only and are cited, not adapted. No
  newspaper's fonts, logos or text are included. `editorial-voice-wsj-like` and
  `chart-text-wsj-like` describe a "WSJ-like" style: our own adaptation from
  cited public sources, not the Journal's style guide, with no claimed
  affiliation or endorsement.

See `LICENSE.md` for the licence notice and suggested credit, and
`NOTICE.md` for third-party attributions. This is not legal advice.
