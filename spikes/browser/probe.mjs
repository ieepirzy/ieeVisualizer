// Browser probes (headless Chromium via Playwright):
//  1. accessibility: what each engine's *interactive* render exposes (ARIA
//     attributes, roles, focusable elements, the accessibility tree);
//  2. ECharts in the browser vs ECharts Node SSR (text measurement);
//  3. determinism across fresh pages / fresh Node processes;
//  4. time to first render for 100k and 1M points.
// Usage: node probe.mjs [a11y|ssr|det|perf ...]   (default: all)
import { build } from 'esbuild';
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';

const want = new Set(process.argv.slice(2).length ? process.argv.slice(2) : ['a11y', 'ssr', 'det', 'perf']);
const sha = (s) => createHash('sha256').update(s).digest('hex').slice(0, 16);
const theme = JSON.parse(readFileSync('specs/theme.muutto365-report-light.json', 'utf8'));
const spec = (n) => JSON.parse(readFileSync(`specs/${n}.json`, 'utf8'));
mkdirSync('out/probe', { recursive: true });
const report = {};

async function iife(entry, globalName) {
  const r = await build({ entryPoints: [entry], bundle: true, write: false, format: 'iife', globalName, platform: 'browser' });
  return Buffer.from(r.outputFiles[0].contents).toString();
}
const ADAPTERS = {
  echarts: await iife('adapters/echarts.js', 'EChartsAdapter'),
  plotly: await iife('adapters/plotly.js', 'PlotlyAdapter'),
  vegalite: await iife('adapters/vegalite.js', 'VegaLiteAdapter'),
};
const LIBS = {
  echarts: ['node_modules/echarts/dist/echarts.min.js'],
  plotly: ['node_modules/plotly.js-dist-min/plotly.min.js'],
  vegalite: ['node_modules/vega/build/vega.min.js', 'node_modules/vega-lite/build/vega-lite.min.js', 'node_modules/vega-embed/build/vega-embed.min.js'],
  plot: ['node_modules/d3/dist/d3.min.js', 'node_modules/@observablehq/plot/dist/plot.umd.min.js'],
  uplot: ['node_modules/uplot/dist/uPlot.iife.min.js'],
};

const browser = await chromium.launch();
async function freshPage(libs) {
  const ctx = await browser.newContext({ viewport: { width: 800, height: 500 }, timezoneId: 'America/New_York', locale: 'fi-FI' });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.log('pageerror', String(e)));
  await page.setContent('<html><body style="margin:0"><div id="c" style="width:800px;height:500px"></div></body></html>');
  for (const l of libs) await page.addScriptTag({ path: l });
  return { ctx, page };
}

// Mount the line spec interactively with each engine's normal browser API.
const MOUNT = {
  echarts: async ([spec, theme, renderer]) => {
    const c = window.echarts.init(document.getElementById('c'), null, { renderer, width: 800, height: 500 });
    c.setOption(window.EChartsAdapter.figureSpecToECharts(spec, theme));
    window.__chart = c;
  },
  plotly: async ([spec, theme]) => {
    const f = window.PlotlyAdapter.figureSpecToPlotly(spec, theme);
    await window.Plotly.newPlot('c', f.data, f.layout, f.config);
  },
  vegalite: async ([spec, theme, renderer]) => {
    await window.vegaEmbed('#c', window.VegaLiteAdapter.figureSpecToVegaLite(spec, theme), { renderer, actions: true });
  },
};

async function a11y() {
  const out = {};
  for (const [engine, renderer] of [['echarts', 'svg'], ['echarts', 'canvas'], ['plotly', null], ['vegalite', 'svg'], ['vegalite', 'canvas']]) {
    console.log('a11y', engine, renderer);
    const { ctx, page } = await freshPage(LIBS[engine]);
    await page.addScriptTag({ content: ADAPTERS[engine] });
    await page.evaluate(MOUNT[engine], [spec('line'), theme, renderer]);
    console.log('  mounted');
    await page.waitForTimeout(300);
    const dom = await page.evaluate(() => {
      const root = document.getElementById('c');
      const all = [...root.querySelectorAll('*')];
      const aria = {};
      for (const el of [root, ...all]) for (const a of el.getAttributeNames()) if (a.startsWith('aria-') || a === 'role') aria[a] = (aria[a] || 0) + 1;
      const labels = [root, ...all].map((el) => el.getAttribute('aria-label')).filter(Boolean).map((s) => s.slice(0, 300));
      const focusable = all.filter((el) => el.tabIndex >= 0 && !el.hasAttribute('disabled')
        && (el.tabIndex > 0 || ['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY', 'DETAILS'].includes(el.tagName) || el.hasAttribute('tabindex'))).length;
      return { elements: all.length, svg: !!root.querySelector('svg'), canvas: root.querySelectorAll('canvas').length,
        aria, labels: [...new Set(labels)].slice(0, 4), focusable, tables: root.querySelectorAll('table').length };
    });
    const tree = await page.locator('#c').ariaSnapshot({ timeout: 10000 }).catch((e) => `ariaSnapshot failed: ${e}`);
    out[`${engine}${renderer ? `/${renderer}` : ''}`] = { ...dom, ariaSnapshot: tree.slice(0, 1200) };
    await ctx.close();
  }
  report.a11y = out;
}

async function ssr() {
  // Same option, ECharts' own SVG renderer in Chromium (real text metrics) vs Node SSR.
  const out = {};
  for (const n of ['line', 'band', 'grouped_bar']) {
    const { ctx, page } = await freshPage(LIBS.echarts);
    await page.addScriptTag({ content: ADAPTERS.echarts });
    await page.evaluate(MOUNT.echarts, [spec(n), theme, 'svg']);
    await page.screenshot({ path: `out/probe/echarts-browser-${n}.png` });
    const svg = await page.evaluate(() => window.__chart.renderToSVGString());
    const node = readFileSync(`out/echarts/${n}.svg`, 'utf8');
    out[n] = { browser_svg_bytes: svg.length, node_svg_bytes: node.length, identical: svg === node };
    await ctx.close();
  }
  report.ssr = out;
}

async function det() {
  // Fresh Node processes (render.mjs's in-process repeat shares ID counters).
  const once = (engine, n) => execFileSync(process.execPath, ['svg-once.mjs', engine, n], { maxBuffer: 1 << 26 }).toString();
  const out = {};
  for (const n of ['line', 'grouped_bar', 'heatmap', 'band']) {
    for (const engine of ['echarts', 'vegalite']) {
      const a = once(engine, n), b = once(engine, n);
      out[`${engine}/${n}`] = { fresh_processes_identical: a === b, sha: sha(a) };
    }
    const shots = [];
    for (let i = 0; i < 2; i++) {
      const { ctx, page } = await freshPage(LIBS.plotly);
      await page.addScriptTag({ content: ADAPTERS.plotly });
      await page.evaluate(MOUNT.plotly, [spec(n), theme]);
      shots.push(await page.evaluate(async () => decodeURIComponent((await window.Plotly.toImage('c', { format: 'svg', width: 800, height: 500 })).split(',')[1])));
      await ctx.close();
    }
    // Plotly gives every plot a random 6-hex uid used in element ids and url(#...) refs.
    const strip = (s) => { const uid = s.match(/defs-([0-9a-f]{6})/)?.[1]; return uid ? s.split(uid).join('UID') : s; };
    const firstDiff = (a, b) => { if (a === b) return null; let i = 0; while (a[i] === b[i]) i++; return [a.slice(Math.max(0, i - 50), i + 40), b.slice(Math.max(0, i - 50), i + 40)]; };
    const [s0, s1] = shots.map(strip);
    out[`plotly/${n}`] = { fresh_pages_identical: shots[0] === shots[1], identical_after_id_normalisation: s0 === s1,
      first_diff_raw: firstDiff(shots[0], shots[1])?.[0] ?? null, first_diff_normalised: firstDiff(s0, s1) };
  }
  report.determinism = out;
}

async function perf() {
  const out = {};
  for (const N of [100_000, 1_000_000]) {
    const run = async (engine, label, fn) => {
      const { ctx, page } = await freshPage(LIBS[engine]);
      try {
        const ms = await page.evaluate(async ([N, src]) => {
          const x = new Float64Array(N), y = new Float64Array(N);
          let v = 0;
          for (let i = 0; i < N; i++) { x[i] = i; v += Math.sin(i * 0.001) + ((i * 7919) % 13) / 13 - 0.5; y[i] = v; }
          const t0 = performance.now();
          await (0, eval)(`(${src})`)(x, y, N);
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
          return performance.now() - t0;
        }, [N, fn.toString()]);
        out[`${label} @ ${N}`] = +ms.toFixed(0);
      } catch (e) { out[`${label} @ ${N}`] = `failed: ${String(e).slice(0, 120)}`; }
      await ctx.close();
      console.log(label, N, out[`${label} @ ${N}`]);
    };
    await run('echarts', 'ECharts canvas line (no sampling)', async (x, y) => {
      const c = echarts.init(document.getElementById('c'), null, { renderer: 'canvas' });
      const d = new Array(x.length); for (let i = 0; i < x.length; i++) d[i] = [x[i], y[i]];
      c.setOption({ animation: false, xAxis: { type: 'value' }, yAxis: { type: 'value', scale: true }, series: [{ type: 'line', showSymbol: false, data: d }] });
    });
    await run('echarts', 'ECharts canvas line (sampling: lttb)', async (x, y) => {
      const c = echarts.init(document.getElementById('c'), null, { renderer: 'canvas' });
      const d = new Array(x.length); for (let i = 0; i < x.length; i++) d[i] = [x[i], y[i]];
      c.setOption({ animation: false, xAxis: { type: 'value' }, yAxis: { type: 'value', scale: true }, series: [{ type: 'line', showSymbol: false, sampling: 'lttb', data: d }] });
    });
    await run('echarts', 'ECharts SVG line (sampling: lttb)', async (x, y) => {
      const c = echarts.init(document.getElementById('c'), null, { renderer: 'svg' });
      const d = new Array(x.length); for (let i = 0; i < x.length; i++) d[i] = [x[i], y[i]];
      c.setOption({ animation: false, xAxis: { type: 'value' }, yAxis: { type: 'value', scale: true }, series: [{ type: 'line', showSymbol: false, sampling: 'lttb', data: d }] });
    });
    await run('plotly', 'Plotly scatter (SVG) line', async (x, y) => {
      await Plotly.newPlot('c', [{ type: 'scatter', mode: 'lines', x, y }], { width: 800, height: 500 });
    });
    await run('plotly', 'Plotly scattergl (WebGL) line', async (x, y) => {
      await Plotly.newPlot('c', [{ type: 'scattergl', mode: 'lines', x, y }], { width: 800, height: 500 });
    });
    await run('vegalite', 'Vega-Lite canvas line', async (x, y) => {
      const values = new Array(x.length); for (let i = 0; i < x.length; i++) values[i] = { x: x[i], y: y[i] };
      await vegaEmbed('#c', { width: 700, height: 400, data: { values }, mark: 'line',
        encoding: { x: { field: 'x', type: 'quantitative' }, y: { field: 'y', type: 'quantitative', scale: { zero: false } } } }, { renderer: 'canvas', actions: false });
    });
    await run('plot', 'Observable Plot (SVG) line', async (x, y) => {
      document.getElementById('c').append(Plot.lineY(y, { x }).plot({ width: 800, height: 500 }));
    });
    await run('uplot', 'uPlot (canvas) line', async (x, y) => {
      new uPlot({ width: 800, height: 500, series: [{}, { stroke: 'green' }] }, [x, y], document.getElementById('c'));
    });
  }
  report.perf_ms = out;
}

if (want.has('a11y')) await a11y();
if (want.has('ssr')) await ssr();
if (want.has('det')) await det();
if (want.has('perf')) await perf();
await browser.close();
let prev = {};
try { prev = JSON.parse(readFileSync('out/probe-results.json', 'utf8')); } catch { /* first run */ }
writeFileSync('out/probe-results.json', JSON.stringify({ ...prev, ...report, chromium: 'playwright 1.56.1 chromium-1194 headless' }, null, 2));
console.log(JSON.stringify(report, null, 2));
