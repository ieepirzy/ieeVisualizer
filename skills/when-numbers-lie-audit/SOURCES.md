# Sources: when-numbers-lie-audit

Format of every entry: title and URL, `Licence:`, `Use:` (`adapted` = text
paraphrased and restructured under the licence's terms; `cited` = idea or
finding only, no text taken).

- **Anthropic, knowledge-work-plugins `data/skills/validate-data`** <https://github.com/anthropics/knowledge-work-plugins/tree/main/data/skills/validate-data> (commit e6b5053)
  - Licence: Apache-2.0
  - Use: adapted — pitfall list (join explosion, survivorship, incomplete period, denominator shifting, average of averages, timezone mismatch, selection bias in segmentation, Simpson's paradox, cherry-picked ranges) and the "recompute independently" step. Reorganised into three audits, rewritten, all examples replaced. Modified work; Apache-2.0 text in `../LICENSES/Apache-2.0.txt`.
- **UK Government Analysis Function, "Data visualisation: charts"** <https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-charts/>
  - Licence: OGL-UK-3.0
  - Use: adapted — do not break bar axes; dual-axis charts not recommended, use separate charts; neutral aspect ratio for line charts. Paraphrased.
- **Alberto Cairo, *How Charts Lie* (W. W. Norton, 2019)** <https://wwnorton.com/books/9781324001560>
  - Licence: Copyrighted (book)
  - Use: cited — the framing that correct data can mislead through encoding, scale and missing context. No text used.
- **Sarah Leo, "Mistakes, we've drawn a few", The Economist (2019)** <https://medium.economist.com/mistakes-weve-drawn-a-few-8cdd8a42d368>
  - Licence: Copyrighted (article)
  - Use: cited — categories of misleading charts (truncation, dual axes, cherry-picked comparisons). No text or images used. URL not verified (unreachable from the build environment); check before relying on it.
- **Edward Tufte, *The Visual Display of Quantitative Information* (Graphics Press, 2nd ed. 2001)** <https://www.edwardtufte.com/book/the-visual-display-of-quantitative-information/>
  - Licence: Copyrighted (book)
  - Use: cited — the principle that the visual size of an effect should match the size of the effect in the data. No text used.
- **E. H. Simpson, "The Interpretation of Interaction in Contingency Tables", JRSS B 13(2), 1951** <https://doi.org/10.1111/j.2517-6161.1951.tb00088.x>
  - Licence: Copyrighted (article)
  - Use: cited — the paradox named after it. Example invented.
