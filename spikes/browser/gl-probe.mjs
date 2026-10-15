// Does echarts-gl 2.1.0's prebuilt UMD run on echarts 6.1.0? (Its ESM build does
// not bundle: it deep-imports echarts/zrender internals that echarts 6's
// package "exports" map does not expose.)  Renders a surface + scatter3D.
import { chromium } from 'playwright';
const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 640, height: 480 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
await page.setContent('<html><body style="margin:0"><div id="c" style="width:640px;height:480px"></div></body></html>');
await page.addScriptTag({ path: 'node_modules/echarts/dist/echarts.min.js' });
await page.addScriptTag({ path: 'node_modules/echarts-gl/dist/echarts-gl.min.js' });
const info = await page.evaluate(async () => {
  const data = [];
  for (let i = -10; i <= 10; i++) for (let j = -10; j <= 10; j++) data.push([i / 2, j / 2, Math.sin(i / 3) * Math.cos(j / 3)]);
  const c = echarts.init(document.getElementById('c'));
  c.setOption({ animation: false, xAxis3D: { type: 'value' }, yAxis3D: { type: 'value' }, zAxis3D: { type: 'value' }, grid3D: {},
    series: [{ type: 'surface', data, wireframe: { show: true } }, { type: 'scatter3D', data: data.filter((_, k) => k % 7 === 0), symbolSize: 4 }] });
  await new Promise((r) => setTimeout(r, 1500));
  return { canvases: document.querySelectorAll('canvas').length, webgl: !!document.createElement('canvas').getContext('webgl') };
});
await page.screenshot({ path: 'out/findings/echarts-gl-umd-on-echarts6.png' });
console.log(JSON.stringify({ ...info, errors }));
await browser.close();
