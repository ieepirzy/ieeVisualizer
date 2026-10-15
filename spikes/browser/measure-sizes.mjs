// Bundle each entry in size-entries/ with esbuild (ESM tree-shaking, minify,
// production NODE_ENV, browser platform) and report raw / gzip -9 / brotli-11 bytes.
// Plotly's prebuilt plotly.min.js is also measured as shipped.
import { build } from 'esbuild';
import { readdirSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { gzipSync, brotliCompressSync, constants } from 'node:zlib';

const kb = (n) => (n / 1024).toFixed(1);
const measure = (buf) => ({
  raw: buf.length,
  gzip: gzipSync(buf, { level: 9 }).length,
  brotli: brotliCompressSync(buf, { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } }).length,
});

mkdirSync('out/bundles', { recursive: true });
const rows = [];
for (const f of readdirSync('size-entries').sort()) {
  const res = await build({
    entryPoints: [`size-entries/${f}`], bundle: true, minify: true, write: false,
    format: 'esm', platform: 'browser', target: 'es2020', logLevel: 'warning',
    // plotly.js/src/registry.js requires maplibre-gl CSS for map traces only; none are bundled here.
    loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"production"', global: 'globalThis' },
  });
  const buf = Buffer.from(res.outputFiles[0].contents);
  writeFileSync(`out/bundles/${f}`, buf);
  rows.push({ entry: f, ...measure(buf) });
}
for (const [label, path] of [
  ['plotly.js-dist-min/plotly.min.js (as shipped)', 'node_modules/plotly.js-dist-min/plotly.min.js'],
  ['plotly.js/dist/plotly-basic.min.js (as shipped)', 'node_modules/plotly.js/dist/plotly-basic.min.js'],
  ['plotly.js/dist/plotly-cartesian.min.js (as shipped)', 'node_modules/plotly.js/dist/plotly-cartesian.min.js'],
  ['echarts/dist/echarts.min.js (as shipped)', 'node_modules/echarts/dist/echarts.min.js'],
]) rows.push({ entry: label, ...measure(readFileSync(path)) });

console.log('| entry | min KiB | min+gzip KiB | min+brotli KiB |\n|---|---:|---:|---:|');
for (const r of rows) console.log(`| ${r.entry} | ${kb(r.raw)} | ${kb(r.gzip)} | ${kb(r.brotli)} |`);
writeFileSync('out/sizes.json', JSON.stringify(rows, null, 2));
