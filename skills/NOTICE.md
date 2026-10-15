# NOTICE

ieeVisualizer skills library
Copyright (c) 2026 Ilari Pirkkalainen

The skill text in this directory is licensed under the Creative Commons
Attribution 4.0 International licence (CC BY 4.0); see `LICENSE.md`. Parts of
it are adapted from the third-party works below. Each adapted portion was
paraphrased, restructured and given new examples; every skill that adapts a
work says so in a closing line of its `SKILL.md`, and its `SOURCES.md` says
what was taken. The original licence terms of these works continue to apply
to the adapted portions.

## Apache License 2.0

The full licence text is in `LICENSES/Apache-2.0.txt`. Neither upstream
repository ships a NOTICE file.

- **Anthropic, knowledge-work-plugins (`data/skills/validate-data`,
  `data-visualization`, `analyze`)**,
  <https://github.com/anthropics/knowledge-work-plugins>, commit e6b5053.
  Modified and adapted in: `when-numbers-lie-audit`,
  `chart-choice-by-relationship`, `perceptual-encoding`,
  `color-and-accessibility`, `company-report-structure`,
  `figure-review-redteam`.
- **google-research/rliable**, <https://github.com/google-research/rliable>:
  named as a tool in `ml-results-reporting`; no text or code copied.

## MIT License

The full licence texts with their copyright notices are in `LICENSES/`.

- **K-Dense Inc., Scientific Agent Skills** (`scientific-visualization`,
  `uncertainty-and-units`, `statistical-analysis`, `scientific-writing`),
  <https://github.com/K-Dense-AI/scientific-agent-skills>, commit a1043ef.
  Copyright (c) 2025 K-Dense Inc. — `LICENSES/MIT-K-Dense.txt`.
  Adapted in: `figure-is-a-claim`, `uncertainty-display`,
  `statistical-claims`, `scientific-figure-production`,
  `color-and-accessibility`, `numbers-style`, `source-and-provenance`.
- **Joe Amditis, claude-skills-journalism** (`data-journalism`,
  `fact-check-workflow`),
  <https://github.com/jamditis/claude-skills-journalism>, commit e3e2172.
  Copyright (c) 2025 Joe Amditis — `LICENSES/MIT-jamditis.txt`.
  Adapted in: `statistical-claims`, `editorial-voice-wsj-like`, `numbers-style`,
  `headline-and-deck`, `source-and-provenance`.

## Open Government Licence v3.0

Contains public sector information licensed under the Open Government
Licence v3.0 <https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/>:

- UK Government Analysis Function, "Data visualisation: charts",
  <https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-charts/>
  (images on that site are excluded from the OGL and were not used).
- Office for National Statistics, Service Manual, "Using colours in charts",
  <https://service-manual.ons.gov.uk/data-visualisation/colours/using-colours-in-charts>.

Adapted in: `figure-is-a-claim`, `when-numbers-lie-audit`,
`chart-choice-by-relationship`, `color-and-accessibility`, `chart-text-wsj-like`,
`numbers-style`.

## Creative Commons Attribution 4.0

Findings paraphrased (no figures or verbatim text) from:

- R. Agarwal et al., "Deep Reinforcement Learning at the Edge of the
  Statistical Precipice", NeurIPS 2021, <https://arxiv.org/abs/2108.13264>
  — `ml-results-reporting`.
- J. Dodge et al., "Show Your Work", EMNLP-IJCNLP 2019,
  <https://aclanthology.org/D19-1224/> — `ml-results-reporting`.
- R. Tang et al., "Showing Your Work Doesn't Always Work", ACL 2020,
  <https://aclanthology.org/2020.acl-main.246> — `ml-results-reporting`.
- J. Hullman, P. Resnick, E. Adar, "Hypothetical Outcome Plots Outperform
  Error Bars and Violin Plots…", PLOS ONE 2015,
  <https://doi.org/10.1371/journal.pone.0142444> — `uncertainty-display`.
- T. L. Weissgerber et al., "Beyond Bar and Line Graphs", PLOS Biology 2015,
  <https://doi.org/10.1371/journal.pbio.1002128> —
  `scientific-figure-production`.

Licence: <https://creativecommons.org/licenses/by/4.0/>. Changes: findings
restated in our own words and applied to new examples.

## CC0 1.0

- N. P. Rougier, M. Droettboom, P. E. Bourne, "Ten Simple Rules for Better
  Figures", PLOS Computational Biology 2014,
  <https://doi.org/10.1371/journal.pcbi.1003833> —
  `scientific-figure-production`. Attribution given as a courtesy.

## Cited, not adapted

Copyrighted books, articles and web pages (Wong, Cairo, Tufte, Wilke,
Cleveland & McGill, Heer & Bostock, Kay, Fernandes, the ASA statements, the
NeurIPS checklist, the FT Visual Vocabulary, The Economist, Poynter, Nieman,
Talking Biz News, Longreads) and repositories without a licence
(dsmacleod/bdn-style-skill) contributed ideas only; no text, images, fonts or
logos from them are included. Each is listed with its use in the relevant
`SOURCES.md`. Nothing in this library is endorsed by, or affiliated with, any
publisher or newspaper named in a `SOURCES.md`.
