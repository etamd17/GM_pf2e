// Independent agreement runner, not the client generator.
// Uses the client's pinned development dependencies without changing its checkout.
// node tools/vtt/check-contracts.mjs --client-root PATH [--root PATH] [--json] [--typecheck]

import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';

const args = process.argv.slice(2);
if (args.includes('--help')) {
  console.log('node tools/vtt/check-contracts.mjs --client-root FRONTEND_PATH [--root CONTRACT_PATH] [--json] [--typecheck]');
  process.exit(0);
}
function argument(name, fallback) {
  const index = args.indexOf(name);
  return index < 0 ? fallback : args[index + 1];
}
const root = resolve(argument('--root', join(import.meta.dirname, '../../contracts/vtt/v1')));
const clientRoot = argument('--client-root');
if (!clientRoot) throw new Error('--client-root must name the frontend/vtt development environment');
const requireClient = createRequire(join(resolve(clientRoot), 'package.json'));
const Ajv = requireClient('ajv');
const { compile } = requireClient('json-schema-to-typescript');
const ajv = new Ajv({
  strict: true, strictNumbers: true, allErrors: false, ownProperties: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false,
});
const load = (path) => JSON.parse(readFileSync(path, 'utf8'));
const registry = load(join(root, 'registry.json'));
const schemaFiles = readdirSync(join(root, 'schemas')).filter((n) => n.endsWith('.schema.json')).sort();
const schemas = new Map();
for (const file of schemaFiles) {
  const schema = load(join(root, 'schemas', file));
  schemas.set(file, schema);
  ajv.addSchema(schema);
}
const validators = new Map(Object.entries(registry.schemas).map(([key, file]) => {
  const schema = schemas.get(basename(file));
  return [key, ajv.getSchema(schema.$id)];
}));

// JSON.parse alone loses duplicate keys. This reference test decoder first checks
// syntax with JSON.parse, then scans JSON tokens and rejects duplicate decoded keys
// in each object scope. The client raw-text adapter must enforce the same corpus.
function decodeRaw(raw) {
  if (Buffer.byteLength(raw, 'utf8') > 2_097_152) throw new Error('size');
  const value = JSON.parse(raw);
  const tokens = [...raw.matchAll(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g)].map((m) => m[0]);
  const scopes = [];
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token === '{') scopes.push(new Set());
    else if (token === '[') scopes.push(null);
    else if (token === '}' || token === ']') scopes.pop();
    else if (token.startsWith('"') && tokens[i + 1] === ':') {
      const key = JSON.parse(token);
      const scope = scopes.at(-1);
      if (scope.has(key)) throw new Error('duplicate');
      scope.add(key);
    }
  }
  checkJson(value);
  return value;
}
function checkJson(value, depth = 0) {
  if (depth > 64) throw new Error('depth');
  if (typeof value === 'number' && (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value)))) throw new Error('number');
  if (value !== null && typeof value === 'object') for (const child of Object.values(value)) checkJson(child, depth + 1);
}

const results = load(join(root, 'fixtures/cases.json')).map((c) => {
  let valid = false;
  try {
    const message = Object.hasOwn(c, 'raw') ? decodeRaw(c.raw) : c.message;
    checkJson(message);
    valid = validators.get(c.schema)(message) === true;
  } catch { /* Invalid raw text or boundary input is rejected before validation. */ }
  return { id: c.id, valid, matches: valid === c.valid };
});
const digest = createHash('sha256');
for (const file of ['registry.json', ...schemaFiles.map((f) => `schemas/${f}`)].sort()) {
  const bytes = readFileSync(join(root, file), 'utf8').replaceAll('\r\n', '\n').replaceAll('\r', '\n');
  digest.update(`${file}\0${bytes}\0`, 'utf8');
}
const report = { sourceDigest: digest.digest('hex'), results };

if (args.includes('--typecheck')) {
  const tempRoot = realpathSync(tmpdir());
  const output = mkdtempSync(join(tempRoot, 'vtt-contract-typecheck-'));
  try {
    for (const surface of ['gm', 'table']) {
      const schema = schemas.get(`${surface}.schema.json`);
      const source = await compile(schema, surface === 'gm' ? 'GmFrame' : 'TableFrame', {
        cwd: join(root, 'schemas'), unknownAny: true, additionalProperties: false,
        declareExternallyReferenced: true, bannerComment: '/* Generated contract proof. */',
      });
      writeFileSync(join(output, `${surface}.d.ts`), source, 'utf8');
    }
    writeFileSync(join(output, 'narrowing.ts'), `import type {TableFrame} from './table';
import type {GmFrame} from './gm';
export function table(frame: TableFrame): number {
  if (frame.frame === 'snapshot') {
    const baseline: 0 = frame.seq;
    // @ts-expect-error a snapshot has no patch replacement list
    frame.upserted;
    return baseline + frame.entities.length;
  }
  // @ts-expect-error the table never receives origin-command correlations
  frame.originCommandId;
  return frame.upserted.length;
}
export function gm(frame: GmFrame): string | undefined {
  if (frame.frame === 'patch') return frame.originCommandId;
  return undefined;
}
`, 'utf8');
    const compilerPackage = requireClient.resolve('typescript/package.json');
    const compiler = resolve(dirname(compilerPackage), load(compilerPackage).bin.tsc);
    const checked = spawnSync(process.execPath, [compiler, '--noEmit', '--strict', '--skipLibCheck', '--target', 'ES2022', '--moduleResolution', 'bundler', '--module', 'ESNext', join(output, 'narrowing.ts')], { encoding: 'utf8' });
    if (checked.status !== 0) throw new Error(checked.stdout + checked.stderr);
  } finally {
    if (dirname(realpathSync(output)) !== tempRoot || !basename(output).startsWith('vtt-contract-typecheck-')) throw new Error('Unexpected temporary output path');
    rmSync(output, { recursive: true, force: true });
  }
}
if (args.includes('--json')) console.log(JSON.stringify(report));
else {
  console.log(`${results.filter((r) => r.matches).length}/${results.length} canonical JavaScript cases passed${args.includes('--typecheck') ? '; generated TypeScript narrowing passed' : ''}`);
  for (const r of results.filter((r) => !r.matches)) console.error(`FAIL ${r.id}`);
  console.log(`source digest: ${report.sourceDigest}`);
}
process.exit(results.every((r) => r.matches) ? 0 : 1);
