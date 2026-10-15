// JS-side spec checking: (1) structural validation with Ajv against the Draft-07
// schema exported by ieeVisualizer.spec.json_schema(); (2) TypeScript types
// generated from the same schema. Semantic checks (unknown column, unit/scale
// mismatch...) exist only in Python's validate(); see the evaluation doc.
import Ajv from 'ajv';
import { compile } from 'json-schema-to-typescript';
import { readFileSync, writeFileSync } from 'node:fs';

const schema = JSON.parse(readFileSync('specs/figurespec.schema.json', 'utf8'));
const ajv = new Ajv({ allErrors: true, strict: false });
const validate = ajv.compile(schema);
const result = {};
for (const n of ['line', 'grouped_bar', 'heatmap', 'band']) {
  const spec = JSON.parse(readFileSync(`specs/${n}.json`, 'utf8'));
  result[n] = validate(spec) ? 'valid' : validate.errors.slice(0, 3).map((e) => `${e.instancePath} ${e.message} ${JSON.stringify(e.params)}`);
}
// A semantic error the schema cannot see: an encoding naming a column that does not exist.
const bad = JSON.parse(readFileSync('specs/line.json', 'utf8'));
bad.layers[0].encoding.y.field = 'revenu';
result['line with unknown column (semantic error)'] = validate(bad) ? 'valid (schema cannot catch it)' : validate.errors;
const ts = await compile(schema, 'FigureSpec', { bannerComment: '// Generated from ieeVisualizer.spec.json_schema() by spikes/browser/schema-check.mjs' });
writeFileSync('out/figurespec.d.ts', ts);
result.generated_ts_lines = ts.split('\n').length;
console.log(JSON.stringify(result, null, 1));
