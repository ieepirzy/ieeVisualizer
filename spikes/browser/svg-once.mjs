// Render one spec with one Node engine and print the SVG (used by probe.mjs
// to compare fresh processes).  node svg-once.mjs echarts|vegalite <spec>
process.env.TZ = 'America/New_York';
import { readFileSync } from 'node:fs';
import * as echarts from 'echarts';
import * as vega from 'vega';
import { compile } from 'vega-lite';
import { figureSpecToECharts } from './adapters/echarts.js';
import { figureSpecToVegaLite } from './adapters/vegalite.js';

const [engine, name] = process.argv.slice(2);
const theme = JSON.parse(readFileSync('specs/theme.muutto365-report-light.json', 'utf8'));
const spec = JSON.parse(readFileSync(`specs/${name}.json`, 'utf8'));
if (engine === 'echarts') {
  const c = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 800, height: 500 });
  c.setOption(figureSpecToECharts(spec, theme));
  const svg = c.renderToSVGString();
  c.dispose(); // an undisposed SSR instance keeps the event loop alive (process never exits)
  process.stdout.write(svg);
} else {
  const view = new vega.View(vega.parse(compile(figureSpecToVegaLite(spec, theme)).spec), { renderer: 'none' });
  process.stdout.write(await view.toSVG());
}
