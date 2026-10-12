# Protocol tooling spike (throwaway)

This proves that the generator tools named in `docs/vtt/design/shared-v1-schemas-proposal.md`
behave as the proposal assumes, before any canonical schema exists.

**The schemas here are not the contract.** Codex owns the canonical `contracts/vtt/v1/`. This
directory is deleted once the real generator (`scripts/generate-protocol.mjs`) runs against
those schemas.

```bash
node spike/protocol/run.mjs                    # generate into out/, 17 checks
npx tsc -p spike/protocol/out-tsconfig.json     # discriminant narrowing on generated types
```

`out-tsconfig.json` type-checks the generated files in `out/`.

Pinned tools: `json-schema-to-typescript` 16.0.0 and `ajv` 8.20.0, both dev-only.

Findings, 2026-10-12 (see VTT-20261012-015):

- Ajv's standalone `code.esm` output still emits `require("ajv/dist/runtime/ucs2length")` for
  `minLength` and `maxLength`. That fails as an ES module. Switching to `unicode: false` would
  count UTF-16 code units instead of the code points Python's `len()` counts. The fix here is an
  allowlisted rewrite to the ESM shim `ajv-runtime.mjs`; any other `require()` fails generation.
  The shim matches Ajv's helper on surrogate edge cases. After the rewrite, generated validators
  import only the shim, so Ajv is a dev dependency.
- Generation is deterministic: two runs produce a byte-identical digest.
- Tagged unions, cross-file `$ref`, the empty completion patch, rejection of GM-private and
  unknown fields, and TypeScript narrowing all work. The table validator contains no GM schema.
