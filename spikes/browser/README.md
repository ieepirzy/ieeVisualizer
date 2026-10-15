# Browser renderer spikes

Throwaway code behind [docs/BROWSER_RENDERER_EVALUATION.md](../../docs/BROWSER_RENDERER_EVALUATION.md).
**Not part of the Python package** (setuptools only ships `ieeVisualizer*`)
and not a starting point to copy wholesale: the adapters cover exactly four
spec shapes and throw `UnsupportedSpec` on anything else.

Measured on 2026-10-15 with Node 22.22.0, Playwright 1.56.1 and its
Chromium 1194 headless shell (software rendering, no GPU), versions pinned in
`package.json`.

## What is here

| Path | What |
|---|---|
| `reference/make_specs.py` | Writes the four spike specs (`specs/*.json`, canonical `to_dict` form), the theme token file (`specs/theme.muutto365-report-light.json` = `Theme.to_json()`), and the Matplotlib adapter's PNG of each spec (`out/mpl/`). Needs `ieeVisualizer.themes`, which is on the unmerged branch `claude/corporate-themes`. |
| `specs/figurespec.schema.json` | `ieeVisualizer.spec.json_schema()` from `main`. |
| `adapters/common.js` | Engine-neutral helpers: rows, "Label (unit)" axis titles, wall-clock-in-spec-timezone conversion, theme-locale number formatting, forecast legend wording, forecast colour pairing, the full bar grid with missing slots, a spec-derived screen-reader description. |
| `adapters/echarts.js`, `plotly.js`, `vegalite.js` | `figureSpecTo<Engine>(spec, theme)`: line (nulls, isolated points, forecast role), grouped bar (null and absent rows labelled "no data"), heatmap (null cells blank, measured zero coloured), provisional `band` (lower/upper; a null bound breaks it), y reference line, time / category / linear axes. |
| `render.mjs` | ECharts and Vega render to SVG **in Node, no browser**; Plotly renders in headless Chromium (`Plotly.toImage`). Every SVG is screenshotted at 800x500 in Chromium; `out/compare/*.png` puts Matplotlib, ECharts, Plotly and Vega-Lite side by side. Runs with `TZ=America/New_York` (Node) and `timezoneId: America/New_York` (browser) on purpose, so a date drifting to the viewer's zone would show. |
| `probe.mjs` | `a11y` (ARIA attributes, focusable elements, Chromium's accessibility snapshot of each engine's interactive render), `ssr` (ECharts in the browser vs ECharts Node SSR), `det` (byte equality across fresh Node processes / fresh browser pages), `perf` (100k and 1M point line, time to the second animation frame). Writes `out/probe-results.json`. |
| `svg-once.mjs` | One render per process, used by the determinism probe. |
| `measure-sizes.mjs`, `size-entries/` | esbuild bundle (ESM, minified, tree-shaken, production) of each entry; raw, gzip -9 and brotli -11 sizes into `out/sizes.json`. |
| `licences.mjs` | Packages that end up in each bundle (esbuild metafile) and their licence field: `out/licences.json`. |
| `schema-check.mjs` | Ajv (Draft-07) validation of the spike specs against the exported schema; TypeScript types generated from it (`out/figurespec.d.ts`). |
| `gl-probe.mjs` | Whether echarts-gl 2.1.0 runs on ECharts 6.1.0 (`out/findings/echarts-gl-umd-on-echarts6.png`). |
| `out/findings/` | Screenshots of defects found while writing the adapters (before the fix in the adapter), cited by the evaluation. |

## Running

```sh
cd spikes/browser
npm ci                                    # PLAYWRIGHT_BROWSERS_PATH must point at an installed Chromium
git archive origin/claude/corporate-themes | tar -x -C /tmp/iv-themes
PYTHONPATH=/tmp/iv-themes python reference/make_specs.py
npm run sizes && npm run render && npm run probe && npm run schema && npm run licences && npm run gl
```

Timings are single runs on a shared container; two runs of `perf` differed by
up to 60 % (ECharts canvas, 1M points: 1328 ms and 2070 ms). Read them as
orders of magnitude.
