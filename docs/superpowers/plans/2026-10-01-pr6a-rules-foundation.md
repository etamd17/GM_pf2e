# PR6A Rules Foundation Implementation Plan

> **For agentic workers:** Implement the accepted ADR-0003 foundation in this
> session with focused tests and one independent final review.

**Goal:** Compile, inspect and load immutable PF2e packages offline with strict
provenance and deterministic integrity checks.

**Architecture:** Pure standard-library domain code under `systems/pf2e/rules`.
A thin CLI operates on supplied local authoring files and package stores. Nothing
connects the package to character or campaign runtime yet.

**Tech Stack:** Python 3.11 standard library; existing pytest environment.

**Spec:** `docs/superpowers/specs/2026-10-01-pr6a-rules-foundation-design.md`

## Global constraints

- No runtime, UI, database, dependency or production configuration change.
- Explicit UTF-8 for every text file operation; support Windows and Linux.
- Synthetic fixtures only; no current-rules or license-review claims.
- Preserve the old checkout and other sessions' worktrees.
- User authorized implementation and subsequent commit/push for a draft PR.

## Review focus

- Self-consistent tampering must fail when a caller supplies the trusted hash.
- A quarantined rule cannot satisfy an enabled prerequisite or reference.
- Nested JSON must not provide a route to arbitrary executable behavior.
- Concurrent publishers of the same ID must never overwrite each other.
- Unknown schema/compiler versions and filesystem anomalies must fail cleanly.

## Task 1: schema and semantic validation

Files: `systems/pf2e/rules/{schema,predicates}.py`, `tests/pf2e_rules/`.
Interface: `normalize_authoring(dict) -> dict` returns canonical ordering after
strict schema and reference checks. `RulesValidationError` carries code/path.

- [x] Add fixture and failing tests for valid reference records, invalid fields,
  types, provenance, source/page links, roster, quarantine and predicates.
- [x] Run `python -m pytest -q tests/pf2e_rules`; confirm missing module failure.
- [x] Implement validators, typed frozen values and closed predicate grammar.
- [x] Run the focused tests; require all assertions to pass.

## Task 2: compiler, verified registry and immutable publication

Files: `systems/pf2e/rules/{manifest,registry}.py`,
`systems/pf2e/rules/ingestion/{compile_pack,diff_pack}.py`.
Interfaces: those listed in the spec; immutable return values expose manifest
metadata and record lookup, never a writable shared dictionary.

- [x] Add failing behavioral tests for deterministic byte output, seeded process
  compilation, tampering, trusted hashes, immutable values and quarantined lookup.
- [x] Implement canonical encoding, bounded strict parsing, fixed-file packaging,
  verified loading and exclusive publication.
- [x] Add/run collision, concurrency, symlink and unexpected-file tests.
- [x] Add/run deterministic record/source/metadata diff tests.

## Task 3: CLI, documentation and final verification

Files: `tools/pf2e_rules.py`, `docs/remediation/pr6a-rules-foundation.md`,
`.github/workflows/ci.yml`, `tests/pf2e_rules/test_cli.py`.

- [x] Add failing CLI subprocess tests for compile, pinned validation, diff,
  invalid JSON and refusal to overwrite a published ID.
- [x] Implement CLI and add the focused package test gate to CI.
- [x] Document commands, hash scope, deferred features and trust boundaries.
- [x] Run focused tests and appropriate existing regressions; record platform
  limits and baseline failures rather than hiding them.
- [x] Obtain one independent review, reproduce and fix material findings, then
  rerun affected tests and perform `git diff --check`.

## Execution record

- Base: `e669facd`; clean worktree `GM_pf2e_pr6a`.
- Native worktree creation could not resolve the parent workspace as a repository;
  created a sibling Git worktree inside the authorized workspace instead.
- Signed-in production PR5 acceptance remains pending because the Codex browser
  helper fails before tab discovery with a Windows sandbox ACL error.
- Schema first run: 35 failures from the missing module; implemented and 35 passed.
- Package first run: 23 failures from missing compiler/registry; implemented and
  the combined suite passed 58 tests. CLI first run: 6 failures; implemented and
  the combined suite passed 64 tests.
- Independent review found two P2 defects. Reproduced five nested provenance
  failures, expanded to ten cases with cycles, and reproduced a mutable-input
  publication destination mismatch. Traversal now checks every reachable erratum;
  destination now comes from the compiled manifest. Final focused suite: 75 passed.
- Review boundaries: actual rules accuracy, reviewer authenticity, distribution
  rights, power-loss durability, hostile filesystem writers, future policy
  evaluation and Linux execution are not established by this foundation work.
  The handoff documents these boundaries. No second independent review is claimed.
- Reproduced the existing PR5 cp1252 test-read failure; explicit UTF-8 fixed it.
  Entire builder-adapter file now passes: 20 tests in 215.70 seconds on Windows.
- All 92 templates parsed. CLI compile/validate succeeded against the synthetic
  fixture. Full native Windows regression run is still in progress; it imported
  the builder-adapter test before the UTF-8 fix and reported that known failure.
