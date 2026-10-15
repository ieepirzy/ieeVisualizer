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
| `reference/make_specs.py` | Writes the four spike specs (`specs/*.json`, canonical `to_dict` form), the theme token file (`specs/theme.report-light.json` = `Theme.to_json()`), and the Matplotlib adapter's PNG of each spec (`out/mpl/`). Needs `ieeVisualizer.themes`; written against the then-unmerged `claude/corporate-themes` branch, re-run from this branch after merging `main` (themes merged): the four FigureSpecs came out byte-identical, the theme file gained `roles.forecast_line_alpha` (0.85), which the adapters now read. |
| `specs/figurespec.schema.json` | `ieeVisualizer.spec.json_schema()` from `main`. |
| `adapters/common.js` | Engine-neutral helpers: rows, "Label (unit)" axis titles, wall-clock-in-spec-timezone conversion, theme-locale number formatting, forecast legend wording, forecast colour pairing, the full bar grid with missing slots, a spec-derived screen-reader description. |
| `adapters/echarts.js`, `plotly.js`, `vegalite.js` | `figureSpecTo<Engine>(spec, theme)`: line (nulls, isolated points, forecast role), grouped bar (null and absent rows labelled "no data"), heatmap (null cells blank, measured zero coloured), provisional `band` (lower/upper; a null bound breaks it), y reference line, time / category / linear axes. |
| `render.mjs` | ECharts and Vega render to SVG **in Node, no browser**; Plotly renders in headless Chromium (`Plotly.toImage`). Every SVG is screenshotted at 800x500 in Chromium; `out/compare/*.png` puts Matplotlib, ECharts, Plotly and Vega-Lite side by side. Runs with `TZ=America/New_York` (Node) and `timezoneId: America/New_York` (browser) on purpose, so a date drifting to the viewer's zone would show. |
| `probe.mjs` | `a11y` (ARIA attributes, focusable elements, Chromium's accessibility snapshot of each engine's interactive render), `ssr` (ECharts in the browser vs ECharts Node SSR), `det` (byte equality across fresh Node processes / fresh browser pages), `perf` (100k and 1M point line, time to the second animation frame). Writes `out/probe-results.json`. |
| `escape-check.mjs` | Hostile-label fixture (`out/escape/line-hostile-labels.json`: `{b}`, `{c}`, `<script>`, `<img onerror>` in a series name, the reference label and the title) through the ECharts adapter: the text must be drawn literally in Node SSR SVG and in Chromium, the HTML tooltip must contain no injected element, show `1 180 EUR` and `06.07.2026`, and "no data" for a null week. `ADAPTER=<dir>` runs it against another copy of the adapters (the control run against the pre-fix adapter: 8/13). Writes `out/escape-results.json`; exits 1 on a failure. |
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
PYTHONPATH=../.. python reference/make_specs.py   # needs matplotlib; ieeVisualizer.themes is on main
npm run sizes && npm run render && npm run escape && npm run probe && npm run schema && npm run licences && npm run gl
```

Timings are single runs on a shared container; two runs of `perf` differed by
up to 60 % (ECharts canvas without sampling, 1M points: 1328 ms in an earlier
run, 2070 ms in the committed `out/probe-results.json`; with LTTB 2625 ms).
The evaluation's "1.3-2.6 s" is the spread over both canvas variants and both
runs. Read them as orders of magnitude.
