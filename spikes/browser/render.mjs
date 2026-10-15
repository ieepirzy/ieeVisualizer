// Render every spec in specs/ with the three spike adapters, rasterise, and
// build side-by-side comparisons against the Matplotlib references.
//
//   ECharts    : Node, no browser (echarts SSR, renderer 'svg') -> SVG
//   Vega-Lite  : Node, no browser (vega View, renderer 'none', toSVG) -> SVG
//   Plotly     : headless Chromium (Plotly needs a DOM) -> Plotly.toImage SVG
// PNGs: each SVG screenshotted in headless Chromium at 800x500 CSS px.
//
// Runs with the viewer's timezone deliberately "wrong" (America/New_York in
// Node and in the browser context) so a date that drifts shows up.
process.env.TZ = 'America/New_York';

import { build } from 'esbuild';
import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import * as echarts from 'echarts';
import * as vega from 'vega';
import { compile } from 'vega-lite';
import { figureSpecToECharts } from './adapters/echarts.js';
import { figureSpecToVegaLite } from './adapters/vegalite.js';

const W = 800, H = 500;
const sha = (s) => createHash('sha256').update(s).digest('hex').slice(0, 16);
const theme = JSON.parse(readFileSync('specs/theme.report-light.json', 'utf8'));
const names = readdirSync('specs').filter((f) => !f.startsWith('theme.') && !f.endsWith('.schema.json') && f.endsWith('.json')).map((f) => f.slice(0, -5)).sort();
for (const d of ['echarts', 'vegalite', 'plotly', 'compare']) mkdirSync(`out/${d}`, { recursive: true });

function echartsSvg(spec) {
  const c = echarts.init(null, null, { renderer: 'svg', ssr: true, width: W, height: H });
  c.setOption(figureSpecToECharts(spec, theme));
  const svg = c.renderToSVGString();
  c.dispose();
  return svg;
}

async function vegaSvg(spec) {
  const vl = figureSpecToVegaLite(spec, theme);
  const { spec: vg } = compile(vl);
  const view = new vega.View(vega.parse(vg), { renderer: 'none' });
  const svg = await view.toSVG();
  view.finalize();
  return svg;
}

const results = [];
const log = (r) => { results.push(r); console.log(JSON.stringify(r)); };

// Node renders, twice each for a determinism check.
const svgs = {};
for (const n of names) {
  const spec = JSON.parse(readFileSync(`specs/${n}.json`, 'utf8'));
  for (const [engine, fn] of [['echarts', echartsSvg], ['vegalite', vegaSvg]]) {
    let t0 = performance.now();
    const a = await fn(spec);
    const ms = performance.now() - t0;
    const b = await fn(spec);
    writeFileSync(`out/${engine}/${n}.svg`, a);
    svgs[`${engine}/${n}`] = a;
    log({ engine, spec: n, where: 'node', svg_bytes: a.length, ms: +ms.toFixed(1), in_process_repeat_identical: a === b, sha: sha(a) });
  }
}

// Plotly adapter bundled for the page.
const bundle = await build({ entryPoints: ['adapters/plotly.js'], bundle: true, write: false, format: 'iife',
  globalName: 'PlotlyAdapter', platform: 'browser' });
const adapterJs = Buffer.from(bundle.outputFiles[0].contents).toString();

const browser = await chromium.launch();
const ctx = await browser.newContext({ timezoneId: 'America/New_York', viewport: { width: W, height: H }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));

for (const n of names) {
  const spec = JSON.parse(readFileSync(`specs/${n}.json`, 'utf8'));
  await page.setContent('<html><body style="margin:0"><div id="p"></div></body></html>');
  await page.addScriptTag({ path: 'node_modules/plotly.js-dist-min/plotly.min.js' });
  await page.addScriptTag({ content: adapterJs });
  const r = await page.evaluate(async ([spec, theme]) => {
    const fig = window.PlotlyAdapter.figureSpecToPlotly(spec, theme);
    const t0 = performance.now();
    await window.Plotly.newPlot('p', fig.data, fig.layout, fig.config);
    const ms = performance.now() - t0;
    const img = (fmt) => window.Plotly.toImage('p', { format: fmt, width: 800, height: 500 });
    const a = await img('svg'), b = await img('svg');
    return { ms, svg: decodeURIComponent(a.split(',')[1]), same: a === b };
  }, [spec, theme]).catch(async (e) => ({ error: String(e) }));
  if (r.error) { log({ engine: 'plotly', spec: n, error: r.error }); continue; }
  writeFileSync(`out/plotly/${n}.svg`, r.svg);
  svgs[`plotly/${n}`] = r.svg;
  log({ engine: 'plotly', spec: n, where: 'chromium', svg_bytes: r.svg.length, ms: +r.ms.toFixed(1), in_process_repeat_identical: r.same, sha: sha(r.svg) });
}

// Rasterise every SVG at 800x500 in Chromium.
for (const [key, svg] of Object.entries(svgs)) {
  await page.setContent(`<html><body style="margin:0;background:#fff">${svg}</body></html>`);
  await page.screenshot({ path: `out/${key}.png`, clip: { x: 0, y: 0, width: W, height: H } });
}

// Side by side: Matplotlib | ECharts / Plotly | Vega-Lite.
const dataUri = (p) => `data:image/png;base64,${readFileSync(p).toString('base64')}`;
await page.setViewportSize({ width: 2 * W + 30, height: 2 * H + 80 });
for (const n of names) {
  const cell = (label, p) => `<figure style="margin:0"><figcaption style="font:bold 15px sans-serif;padding:4px 6px">${label}</figcaption><img src="${dataUri(p)}" width="${W}" height="${H}" style="outline:1px solid #ccc"></figure>`;
  await page.setContent(`<html><body style="margin:0;padding:6px;background:#fff;display:grid;grid-template-columns:${W}px ${W}px;gap:8px 14px">
    ${cell(`Matplotlib adapter (reference)${n === 'band' ? ' &mdash; spec 1.0 stand-in: bounds as reference lines' : ''}`, `out/mpl/${n}.png`)}
    ${cell('ECharts 6.1.0 &mdash; Node SSR SVG, no browser', `out/echarts/${n}.png`)}
    ${cell('Plotly.js 4.1.2 &mdash; headless Chromium, toImage SVG', `out/plotly/${n}.png`)}
    ${cell('Vega-Lite 6.5.0 / Vega 6.4.0 &mdash; Node View.toSVG, no browser', `out/vegalite/${n}.png`)}
  </body></html>`);
  await page.screenshot({ path: `out/compare/${n}.png`, fullPage: true });
}
await browser.close();
writeFileSync('out/render-results.json', JSON.stringify({ results, pageErrors }, null, 2));
if (pageErrors.length) console.log('page errors:', pageErrors);
