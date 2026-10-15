// Which npm packages end up in each tree-shaken bundle (esbuild metafile), and their licence field.
import { build } from 'esbuild';
import { readFileSync, writeFileSync } from 'node:fs';
const entries = ['echarts-figurespec.js', 'plotly-figurespec.js', 'vega-embed-full.js'];
const out = {};
for (const e of entries) {
  const r = await build({ entryPoints: [`size-entries/${e}`], bundle: true, write: false, metafile: true, minify: true,
    format: 'esm', platform: 'browser', loader: { '.css': 'empty' }, define: { 'process.env.NODE_ENV': '"production"', global: 'globalThis' }, logLevel: 'error' });
  const bytes = {};
  for (const [file, info] of Object.entries(r.metafile.outputs[Object.keys(r.metafile.outputs)[0]].inputs)) {
    const m = file.match(/node_modules\/((?:@[^/]+\/)?[^/]+)/);
    if (m) bytes[m[1]] = (bytes[m[1]] || 0) + info.bytesInOutput;
  }
  const lic = {};
  for (const p of Object.keys(bytes)) {
    let l;
    try { l = JSON.parse(readFileSync(`node_modules/${p}/package.json`, 'utf8')).license; } catch { l = '?'; }
    (lic[l] ||= []).push(p);
  }
  out[e] = { packages: Object.keys(bytes).length, by_licence: Object.fromEntries(Object.entries(lic).map(([k, v]) => [k, v.length])),
    non_permissive: Object.entries(lic).filter(([k]) => !/^(MIT|ISC|BSD-[23]-Clause|Apache-2\.0|0BSD|Unlicense|Zlib)$/.test(k)) };
}
writeFileSync('out/licences.json', JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 1));
