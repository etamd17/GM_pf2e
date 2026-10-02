# PR6A: versioned rules package tooling

Local implementation of the package-foundation part of ADR-0003. No runtime
rules, UI, dependencies or database behavior changes. The package is not imported
by the web application. PR6B adds reviewed mechanics and golden rule examples;
PR6C adds shadow derivation; PR7 connects authoritative character transitions.

## Next gate: PR6B

Before enabling real content, freeze and review the source/errata ledger and
explicit exclusions for the September 2026 target in ADR-0003. The planned
vertical slice covers representative ancestry/background choices, Fighter,
Wizard, Magus, Summoner, Psychic, spells and one Eidolon. Require reviewed source
and license provenance, deterministic source-backed golden fixtures and tested
executable derivation before PR6C shadow comparison. PR6A's synthetic tests do
not establish this content gate or the correctness of any current class rules.

PR5's authenticated production checklist remains separately open. It does not
prevent offline PR6B preparation, but must not be reported as passed based on
public health checks or CI. UI mockups still require user approval before any
visible GM/player design is implemented.

## Commands

Use Python 3.11 and the repository test environment:

```text
python tools/pf2e_rules.py compile tests/pf2e_rules/fixtures/synthetic.json --store /path/to/new/package-store
python tools/pf2e_rules.py validate /path/to/new/package-store/pf2e-test-fixture-0.1.0 --expected-hash <trusted-package-hash>
python tools/pf2e_rules.py diff /path/to/old/package /path/to/new/package
python -m pytest -q tests/pf2e_rules --strict-markers
```

The sample is synthetic, draft-only reference data. Its checks do not establish
PF2e accuracy or distribution rights. Successful commands print JSON to stdout;
validation/I/O errors print a stable error code and field path to stderr and exit
with code 2. No command reads production data or contacts source websites.

## Authoring and publication

An input has exactly `manifest`, `sources`, and `records`; the synthetic fixture
shows all required fields. Unknown fields, duplicate keys, floats/nonfinite
numbers, wrong types, missing fields, unsupported versions and unsafe IDs fail.
The maximum individual file size is 16 MiB and JSON nesting is limited to 32.

Source IDs use `pf2e.source.<slug>` or `pf2e.errata.<slug>`. Rule IDs use
`pf2e.<kind>.<slug>`. Supported kinds are class, ancestry, heritage, background,
feat, spell, condition, linked_actor, equipment and archetype. IDs use lower-case
ASCII and are independent of display names. Source references include a page or
equivalent locator. Enabled records require verified sources, known distribution
rights and retained notices. Published status additionally requires named rules
and license reviews; test-only sources cannot be published as enabled content.
These checks validate recorded evidence, not the actual quality of human review.

Quarantine is explicit: set `state=quarantined`, give a `quarantine_reason`, and
declare the ID in manifest exclusions. It remains visible in package review
data but cannot be returned by `RulePackage.get` or referenced by enabled rules.
Do not put copyrighted prose or secrets into quarantine: the entire authoring
document is retained in the compiled package.

Only `reference_only` and `gm_adjudicated` automation are currently accepted for
enabled records. The other declared levels require future tested implementations.
PR6A has no grant or derivation semantics. Base packages, overlays and aliases are
also unsupported; ineffective composition is rejected rather than ignored.

Prerequisites are structurally validated but never evaluated in PR6A:

| Operation | Fields beyond `op` |
| --- | --- |
| `all`, `any` | nonempty `children` array |
| `not` | one `child` |
| `level` | integer `min` from 1 to 20 |
| `feat`, `class`, `ancestry`, `access` | stable rule `id` |
| `proficiency` | `statistic`, `rank` |
| `attribute` | `attribute` (str/dex/con/int/wis/cha), integer `min` modifier |
| `trait`, `tradition`, `deity`, `sanctification`, `rarity` | slug `value` |

## Integrity and immutability

All output uses UTF-8, sorted object keys, compact JSON separators, literal Unicode
and one trailing LF. Unordered record/source/ID lists are sorted. The timestamp
is supplied by the author; compilation never inserts the current clock time.

Each ID's directory contains exactly `authoring.json`, `sources.json`,
`records.json` and `manifest.json`. The manifest includes compiler version and
SHA-256 digests of the first three files. The package hash is SHA-256 of canonical
manifest bytes with only `package_hash` omitted. Loading recompiles the normalized
authoring input and compares every byte. Extra/missing files, symlinks/reparse
points and modified bytes fail verification. Loaded values are deeply immutable.

Integrity hashes are not signatures. To detect a consistently replaced package,
supply an expected hash from an independently trusted campaign binding or review
record. Reading the expected hash from the same suspect package supplies no trust.

The compiler writes only a new ID under the explicitly selected store. It uses
an exclusive per-ID lock, a private staging directory, file flushes and rename;
existing IDs are never overwritten, including identical recompiles. Use a new
immutable ID for corrected data. The store and parent directories must be owned
by a trusted operator: these tools do not protect against a malicious local user
who can rename directories or replace locks during publication. A stale lock
after an interrupted publisher requires operator investigation before removal.
This is offline artifact publication, not a database/filesystem transaction or
a backup system. Retain the source and independently recorded hashes.

## Verification

Tests exercise schema/provenance and quarantine boundaries, prerequisite shape,
input-order/hash-seed reproducibility, byte tampering, trusted binding mismatch,
immutable lookups, publication races, symlinks, exact package layout and real CLI
subprocesses. CI runs these explicitly before the existing regression suite.
No live package or current-rules roster is published by PR6A.

Local verification on 2026-10-01: 75 package/CLI tests passed, all 92 templates
parsed, and all 20 PR5 builder-adapter tests passed after correcting the existing
Windows-only UTF-8 test read. One independent review identified nested errata
provenance and mutable-input destination bugs; both were reproduced and fixed
with regression tests. A complete native Windows regression run is pending.
The project owner authorized pushing this branch for a draft PR review.
