// Label-escaping and tooltip-honesty check for the ECharts adapter.
//
// Fixture: specs/line.json with hostile spec/agent-supplied text in the three
// places the adapter passes user text to ECharts: a series name (legend and
// tooltip), the reference line label (markLine label, which ECharts treats as
// a {a}/{b}/{c} template when given as a string) and the title.
// The text must come out literally: braces not substituted, markup not parsed.
//
//   Node SSR SVG : the label and series name appear as escaped text, no element injected.
//   Chromium     : the HTML tooltip (innerHTML) for a measured week and for a
//                  null week shows the literal name, no injected element, no
//                  script ran, numbers in the theme's fi-FI format with the
//                  unit, the date in the spec's timezone, and "no data" for the null.
//
// ADAPTER=<dir> runs the same check against another copy of the adapters
// (used to confirm the check fails against the adapter before this fix).
// Writes out/escape-results.json; exits 1 on any failed check.
process.env.TZ = 'America/New_York';

import { build } from 'esbuild';
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import * as echarts from 'echarts';

const adapterDir = resolve(process.env.ADAPTER ?? 'adapters');
const { figureSpecToECharts } = await import(`${adapterDir}/echarts.js`);

const HOSTILE_LABEL = 'Target {b} {c} {a|x} <script>window.__pwned="label"</script>';
const HOSTILE_NAME = 'Observed {b} <img src=x onerror="window.__pwned=\'name\'">';
const HOSTILE_TITLE = 'Revenue {c} <b>bold?</b>';

const theme = JSON.parse(readFileSync('specs/theme.report-light.json', 'utf8'));
const spec = JSON.parse(readFileSync('specs/line.json', 'utf8'));
spec.title = HOSTILE_TITLE;
spec.annotations[0].label = HOSTILE_LABEL;
const observed = spec.layers.find((l) => l.name === 'Observed');
observed.name = HOSTILE_NAME;
// The forecast pairs its colour with the observed layer by name; keep the pairing.
mkdirSync('out/escape', { recursive: true });
writeFileSync('out/escape/line-hostile-labels.json', JSON.stringify(spec, null, 2));

const obsRows = spec.datasets[observed.data].columns;
const yCol = observed.encoding.y.field;
const nullIdx = obsRows[yCol].values.findIndex((v) => v == null);
const valIdx = 0;

const checks = [];
const check = (name, ok, detail) => { checks.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? `: ${JSON.stringify(detail)}` : ''}`); };

// 1. Node SSR SVG.
const c = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 800, height: 500 });
c.setOption(figureSpecToECharts(spec, theme));
const svg = c.renderToSVGString();
c.dispose();
writeFileSync('out/escape/line-hostile-labels.svg', svg);
// Exact text of every <text> element, XML-unescaped. Exact equality, not
// substring: a substituted template contains the original label as a substring
// ("Target {b}" -> "Target Target {b} ...").
const unxml = (t) => t.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, '&');
const ssrText = [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => unxml(m[1]));
check('ssr: reference label literal', ssrText.includes(HOSTILE_LABEL), ssrText.filter((t) => t.includes('Target')));
check('ssr: series name literal in legend', ssrText.includes(HOSTILE_NAME));
check('ssr: title literal', ssrText.includes(HOSTILE_TITLE));
check('ssr: no injected element', !/<script|<img|<b>/i.test(svg));

// 2. Chromium: interactive render, HTML tooltip.
const bundle = await build({ entryPoints: [`${adapterDir}/echarts.js`], bundle: true, write: false, format: 'iife',
  globalName: 'EChartsAdapter', platform: 'browser', external: ['echarts'] });
const adapterJs = Buffer.from(bundle.outputFiles[0].contents).toString();
const browser = await chromium.launch();
const ctx = await browser.newContext({ timezoneId: 'America/New_York', viewport: { width: 800, height: 500 } });
const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
await page.setContent('<html><body style="margin:0"><div id="c" style="width:800px;height:500px"></div></body></html>');
await page.addScriptTag({ path: 'node_modules/echarts/dist/echarts.min.js' });
await page.addScriptTag({ content: adapterJs });
// Hover the real way: mouse over the pixel of each data point.
const where = await page.evaluate(([spec, theme, idxs]) => {
  const chart = window.echarts.init(document.getElementById('c'), null, { renderer: 'svg' });
  window.__chart = chart;
  const opt = window.EChartsAdapter.figureSpecToECharts(spec, theme);
  chart.setOption(opt);
  const xs = opt.series[0].data.map((d) => d.value[0]);
  return Object.fromEntries(Object.entries(idxs).map(([k, i]) => [k, chart.convertToPixel({ gridIndex: 0 }, [xs[i], 1300])]));
}, [spec, theme, { value: valIdx, null: nullIdx }]);
const tips = { out: {} };
for (const [key, [x, y]] of Object.entries(where)) {
  await page.mouse.move(x + 2, y); // +2: the first week sits on the grid edge
  await page.waitForTimeout(400);
  tips.out[key] = await page.evaluate(() => {
    // The tooltip is the absolutely positioned div ECharts appends to the container.
    const tip = [...document.querySelectorAll('#c > div')].find((d) => d.style.position === 'absolute' && d.textContent.trim());
    return tip ? { text: tip.textContent, html: tip.innerHTML, injected: tip.querySelectorAll('script,img,b').length } : null;
  });
}
Object.assign(tips, await page.evaluate(() => ({
  svgText: [...document.querySelectorAll('#c svg text')].map((t) => t.textContent), pwned: window.__pwned ?? null })));
await browser.close();

const v = tips.out.value, n = tips.out.null;
check('browser: no script ran', tips.pwned === null, tips.pwned);
check('browser: reference label literal in SVG', tips.svgText.includes(HOSTILE_LABEL));
check('browser: tooltip shown', v && n);
if (v && n) {
  check('browser: tooltip name literal', v.text.includes(HOSTILE_NAME), v.text);
  check('browser: tooltip has no injected element', v.injected === 0 && n.injected === 0, v.html);
  check('browser: tooltip value formatted with unit (fi-FI)', v.text.includes('1 180 EUR'), v.text);
  check('browser: tooltip date in spec timezone', v.text.includes('06.07.2026'), v.text);
  check('browser: null week reads "no data"', n.text.includes(': no data') && !/NaN|: -|: 0\b/.test(n.text), n.text);
}
check('browser: no page errors', pageErrors.length === 0, pageErrors);

const failed = checks.filter((x) => !x.ok).length;
writeFileSync(process.env.ADAPTER ? 'out/escape/control-results.json' : 'out/escape-results.json',
  JSON.stringify({ adapter: process.env.ADAPTER ? 'control' : 'adapters/', fixture: { HOSTILE_LABEL, HOSTILE_NAME, HOSTILE_TITLE },
    tooltips: tips.out, checks, failed }, null, 2));
console.log(`${checks.length - failed}/${checks.length} passed`);
process.exit(failed ? 1 : 0);
