# PR6B3A AoN Evidence Inventory Implementation Plan

> **For agentic workers:** Execute this approved plan in the isolated
> `remediation/pr6b3a-aon-evidence` worktree with strict TDD and one independent
> whole-branch review.

**Goal:** Establish an offline, deterministic evidence inventory that can prove
whether every observed Archives of Nethys identity is mapped, pending review, or
explicitly excluded, and can report source drift without changing runtime rules.

**Architecture:** Add a sidecar evidence layer under
`systems/pf2e/rules/ingestion`. Keep schema v2 packages byte-compatible. Compare
an independently captured AoN census with a separately reviewed disposition
ledger; scan the existing Foundry-derived `compendium_data` as a third,
independent local corpus. Nothing performs live network access.

**Tech stack:** Python 3.11 standard library and the existing pytest suite.

**Spec:** `docs/adr/0003-versioned-pf2e-rules-kernel.md`

## Global constraints

- No Flask runtime, UI, database, dependency, deployment, or production change.
- Do not alter schema v2, compiled package files, package hashes, or registry
  layout.
- AoN is a transcription cross-check, not primary book/errata provenance and not
  a redistribution license.
- Store identity and compact evidence metadata only; never retain rules prose or
  scraped HTML.
- AoN identity is `(page_family, numeric_id)`. Names, full URLs, redirect query
  flags, and bare numeric IDs are not identity.
- All commands are offline and deterministic; timestamps are supplied inputs,
  never read from the clock.
- Existing campaigns and the current character builders remain untouched.

## Review focus

- Completeness is proven only by comparing independent census and ledger files.
- One external identity cannot map to two stable IDs, and a stable ID cannot be
  rebound across snapshots.
- Folder metadata and generated spell projections never become rule records.
- Symlinks, duplicate JSON keys, non-finite numbers, malformed URLs, and
  nondeterministic ordering fail safely.
- A clean report must not be mistaken for verified rules or license approval.

## Task 1: strict AoN census and reviewed-ledger contracts

Files: `systems/pf2e/rules/ingestion/evidence_inventory.py`,
`tests/pf2e_rules/test_evidence_inventory.py`, and synthetic fixtures under
`tests/pf2e_rules/fixtures/`.

Interfaces:

- `normalize_aon_census(document: dict) -> dict`
- `normalize_evidence_ledger(document: dict) -> dict`
- `evidence_fingerprint(entry: dict) -> str`

Steps:

1. Write failing tests for closed shapes, family-scoped numeric identities,
   canonical AoN URLs, stable IDs, rules era, supplied capture dates, review
   states, duplicate identity/ID refusal, and prohibition of rules prose.
2. Run the focused tests and confirm failures identify the missing interfaces.
3. Implement bounded detached normalization and canonical evidence
   fingerprints without importing the application or changing package schema.
4. Prove reordered inputs and different `PYTHONHASHSEED` values yield identical
   normalized bytes and fingerprints.

Expected: strict synthetic census and ledger fixtures normalize deterministically;
malformed evidence raises `RulesValidationError` with stable code/path.

## Task 2: independent completeness audit and drift model

Files: `systems/pf2e/rules/ingestion/evidence_inventory.py`,
`tests/pf2e_rules/test_evidence_drift.py`.

Interfaces:

- `audit_evidence_inventory(census: dict, ledger: dict) -> dict`
- `diff_evidence_inventories(before: dict, after: dict) -> dict`

Steps:

1. Write failing tests proving every observed identity has exactly one of
   `mapped`, `pending`, or `excluded`; missing, stale, duplicate, and conflicting
   dispositions fail or appear in explicit stable categories.
2. Add failing mutation tests for added, removed, evidence-changed,
   newly-excluded, restored, and exclusion-changed records.
3. Implement mutually exclusive, sorted drift categories and coverage totals by
   kind, rules era, and disposition.
4. Reject stable-ID rebinding and external-identity rekeying as `unstable_id`
   rather than hiding them as ordinary add/remove drift.

Expected: independent artifacts produce a complete, deterministic report; an
inventory cannot manufacture its own proof of completeness.

## Task 3: deterministic local PF2e corpus census

Files: `systems/pf2e/rules/ingestion/evidence_inventory.py`,
`tests/pf2e_rules/test_corpus_inventory.py`.

Interface: `scan_corpus(root: Path) -> dict`.

Steps:

1. Write failing fixture-tree tests across all `compendium_data` packs, with
   explicit semantic coverage for classes, ancestries, heritages, backgrounds,
   class/ancestry features, feats, actions, conditions, spells, equipment,
   effects, deities, hazards, vehicles, and campaign/adventure-scoped records,
   including archetype/dedication feats and weapon/equipment subtypes.
2. Add failing tests that classify every `_folders.json` as structural and
   `spells/master_spells.json` as generated, reject links/duplicate keys, and
   preserve finite Foundry numeric values without using package JSON rules.
3. Implement sorted traversal, raw-byte SHA-256, pack-scoped Foundry identity,
   declared-type-first classification, publication/remaster metadata, semantic
   subcategory and scope projection, and explicit structural/generated
   inventories. Iconic actors remain examples, and campaign/adventure records
   remain visible but disabled by default rather than silently becoming core.
4. Run the scanner against the checked-in corpus and pin only summary counts and
   declared gaps in documentation; do not commit thousands of inferred AoN IDs.

Expected: every supported local file is accounted for exactly once without
silently treating path, name, or bare Foundry ID as canonical PF2e identity.

## Task 4: offline operator CLI, documentation, and verification

Files: `tools/pf2e_rules.py`, `tests/pf2e_rules/test_cli.py`,
`docs/remediation/pr6b3a-aon-evidence.md`, `.github/workflows/ci.yml` only if the
focused command is not already covered.

Interfaces:

- `evidence-scan ROOT`
- `evidence-audit CENSUS LEDGER`
- `evidence-diff BEFORE AFTER`

Steps:

1. Write failing subprocess tests for real files, clean/drift/invalid exit codes,
   deterministic JSON, no tracebacks, invalid links, and no output mutation.
2. Implement thin CLI wiring using strict local-file reads only.
3. Document identity, fingerprint, capture, coverage, drift, trust, licensing,
   and deferred live-acquisition boundaries plus the measured local census.
4. Run focused PF2e tests, the non-browser/non-PostgreSQL regression suite,
   template parsing, CLI smoke checks, `git diff --check`, and an independent
   whole-branch review. Fix every material finding through RED to GREEN.

Expected: operators can generate and compare review evidence offline; no result
claims that the underlying mechanics or distribution rights have been approved.

## Acceptance criteria

- Census, ledger, local corpus, and drift outputs are independently derived,
  canonical, deterministic, bounded, and fail closed.
- Every observed AoN identity must have one explicit disposition before coverage
  can be clean.
- All target player-rule families can be represented without copying rule text.
- Current package bytes/hashes and all runtime behavior remain unchanged.
- The documentation identifies missing classes, unsupported caster-profile data,
  and all unreviewed records instead of silently accepting them.
