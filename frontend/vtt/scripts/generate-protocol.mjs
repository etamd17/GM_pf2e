// Generates the browser protocol layer from the canonical VTT wire contracts
// (contracts/vtt/v1/, owned by the platform lead). Outputs go to
// src/protocol/generated/ and are committed with LF endings (S3).
//
//   node scripts/generate-protocol.mjs [--root CONTRACT_DIR]          write outputs
//   node scripts/generate-protocol.mjs [--root CONTRACT_DIR] --check  fail if outputs differ
//   --source-note TEXT records where the source came from in manifest.json
//
// --root defaults to $VTT_CONTRACTS_ROOT, then ../../contracts/vtt/v1. The root
// is build-time tooling configuration, never a protocol field.
//
// For each surface in registry.json it writes one standalone validator module
// (Ajv, compiled ahead of time; the browser never compiles a schema) and one
// type file per schema key (json-schema-to-typescript). The table surface's
// module must not contain any GM schema: a table bundle cannot validate, and so
// cannot accept, a GM shape. manifest.json pins the contract source digest
// (same algorithm as the contract README), the tool versions and options, and a
// hash of every output.

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { basename, join, resolve } from 'node:path';
import Ajv from 'ajv';
import standaloneCode from 'ajv/dist/standalone/index.js';
import { compile } from 'json-schema-to-typescript';

const here = resolve(import.meta.dirname);
const clientRoot = resolve(here, '..');
const outDir = join(clientRoot, 'src', 'protocol', 'generated');
const requireClient = createRequire(join(clientRoot, 'package.json'));

const args = process.argv.slice(2);
const argValue = (name) => {
  const i = args.indexOf(name);
  return i < 0 ? undefined : args[i + 1];
};
const root = resolve(
  argValue('--root') ?? process.env.VTT_CONTRACTS_ROOT ?? join(clientRoot, '..', '..', 'contracts', 'vtt', 'v1'),
);
const checkOnly = args.includes('--check');
// Optional provenance note recorded in manifest.json, e.g. when generating from
// a proposed amendment before the platform publishes it.
const sourceNote = argValue('--source-note');
if (!existsSync(join(root, 'registry.json'))) {
  console.error(`No contract registry at ${root}. Pass --root or set VTT_CONTRACTS_ROOT.`);
  process.exit(2);
}

const lf = (text) => text.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
const readText = (path) => lf(readFileSync(path, 'utf8'));
const readJson = (path) => JSON.parse(readText(path));
const sha256 = (text) => createHash('sha256').update(text, 'utf8').digest('hex');

const registry = readJson(join(root, 'registry.json'));
const schemaFiles = readdirSync(join(root, 'schemas'))
  .filter((n) => n.endsWith('.schema.json'))
  .sort();
const schemasByFile = new Map(schemaFiles.map((f) => [f, readJson(join(root, 'schemas', f))]));
const schemaFor = (key) => {
  const file = registry.schemas[key];
  if (!file) throw new Error(`registry has no schema key ${key}`);
  const schema = schemasByFile.get(basename(file));
  if (!schema) throw new Error(`registry key ${key} points at missing ${file}`);
  return schema;
};

// Contract source digest, exactly as contracts/vtt/v1/README.md (S3) defines it.
function sourceDigest() {
  const hash = createHash('sha256');
  for (const file of ['registry.json', ...schemaFiles.map((f) => `schemas/${f}`)].sort()) {
    hash.update(`${file}\0${readText(join(root, file))}\0`, 'utf8');
  }
  return hash.digest('hex');
}

const AJV_OPTIONS = {
  strict: true,
  strictNumbers: true,
  allErrors: false,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
  ownProperties: true,
  code: { source: true, esm: true, lines: true },
};
const TS_OPTIONS = {
  unknownAny: true,
  additionalProperties: false,
  strictIndexSignatures: true,
  declareExternallyReferenced: true,
  format: true,
};

const pascal = (key) => key.replace(/(^|-)(\w)/g, (_m, _d, c) => c.toUpperCase());
const validatorName = (key) => `validate${pascal(key)}`;
const typeName = (key) => schemaFor(key).title ?? pascal(key);

// Ajv 8.20.0 standalone ESM still emits require() for runtime helpers. Rewrite
// exactly the reviewed helper (S1) to the ESM shim and fail on anything else.
const RUNTIME_HELPERS = { ucs2length: 'ucs2length' };
function rewriteRuntime(code) {
  const imports = new Set();
  const out = code.replace(/const (\w+) = require\("ajv\/dist\/runtime\/(\w+)"\)\.default;/g, (_m, local, helper) => {
    const exported = RUNTIME_HELPERS[helper];
    if (!exported) throw new Error(`unsupported Ajv runtime helper: ${helper}`);
    imports.add(`import { ${exported} as ${local} } from './ajv-runtime.mjs';`);
    return '';
  });
  if (/\brequire\(/.test(out)) throw new Error('generated validator still contains require()');
  if (/new Function|\beval\(/.test(out)) throw new Error('generated validator compiles at runtime');
  return `${[...imports].sort().join('\n')}\n${out}`;
}

const BANNER = '/* Generated by scripts/generate-protocol.mjs from contracts/vtt/v1. Do not edit. */';

async function generate() {
  const files = {};
  for (const [surface, spec] of Object.entries(registry.surfaces)) {
    const ajv = new Ajv(AJV_OPTIONS);
    for (const schema of schemasByFile.values()) ajv.addSchema(schema);
    const exportsMap = Object.fromEntries(spec.validators.map((key) => [validatorName(key), schemaFor(key).$id]));
    const code = rewriteRuntime(standaloneCode(ajv, exportsMap));
    if (surface !== 'gm' && /gm(-subscribe)?\.schema\.json/.test(code)) {
      throw new Error(`${surface} validators embed a GM schema`);
    }
    files[`${surface}.validators.mjs`] = `${BANNER}\n${code}`;
    const decls = spec.validators.map((key) => {
      const t = typeName(key);
      return (
        `import type { ${t} } from './types/${key}';\n` +
        `export declare const ${validatorName(key)}: ((data: unknown) => data is ${t}) & { errors?: readonly unknown[] | null };`
      );
    });
    files[`${surface}.validators.d.mts`] = `${BANNER}\n${decls.join('\n')}\n`;
  }
  const typeKeys = [...new Set(Object.values(registry.surfaces).flatMap((s) => s.types))].sort();
  for (const key of typeKeys) {
    files[`types/${key}.d.ts`] = await compile(schemaFor(key), typeName(key), {
      ...TS_OPTIONS,
      cwd: join(root, 'schemas'),
      bannerComment: BANNER,
    });
  }
  files['ajv-runtime.mjs'] = `${BANNER}\n${readText(join(here, 'ajv-runtime.mjs'))}`;
  files['fixtures.json'] = readText(join(root, 'fixtures', 'cases.json'));
  for (const name of Object.keys(files)) files[name] = lf(files[name]);
  const versions = {
    ajv: requireClient('ajv/package.json').version,
    'json-schema-to-typescript': requireClient('json-schema-to-typescript/package.json').version,
  };
  const manifest = {
    batch: registry.batch,
    schemaVersion: registry.schemaVersion,
    sourceDigest: sourceDigest(),
    ...(sourceNote ? { sourceNote } : {}),
    generator: { versions, ajvOptions: AJV_OPTIONS, typescriptOptions: TS_OPTIONS, runtimeHelpers: RUNTIME_HELPERS },
    outputs: Object.fromEntries(
      Object.keys(files)
        .sort()
        .map((k) => [k, sha256(files[k])]),
    ),
  };
  files['manifest.json'] = `${JSON.stringify(manifest, null, 2)}\n`;
  return files;
}

const files = await generate();
const again = await generate();
for (const name of Object.keys(files)) {
  if (files[name] !== again[name]) throw new Error(`generation is not deterministic for ${name}`);
}
const digest = JSON.parse(files['manifest.json']).sourceDigest;

if (checkOnly) {
  const stale = Object.keys(files).filter((name) => {
    const path = join(outDir, name);
    return !existsSync(path) || readText(path) !== files[name];
  });
  if (stale.length) {
    console.error(`Generated protocol is out of date: ${stale.join(', ')}`);
    process.exit(1);
  }
  console.log(`Generated protocol is current (${Object.keys(files).length} files, source ${digest}).`);
} else {
  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(join(outDir, 'types'), { recursive: true });
  for (const [name, text] of Object.entries(files)) writeFileSync(join(outDir, name), text, 'utf8');
  console.log(`Wrote ${Object.keys(files).length} files to ${outDir}`);
  console.log(`source digest: ${digest}`);
}
