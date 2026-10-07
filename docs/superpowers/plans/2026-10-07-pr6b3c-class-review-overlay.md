# PR6B3C Immutable Class Review Overlay Implementation Plan

> **For agentic workers:** Use `superpowers:test-driven-development` for every
> behavior slice and `superpowers:verification-before-completion` before
> claiming the branch is ready.

**Goal:** Compile and verify immutable, non-runtime stable IDs and local
reconciliation for all 29 PF2e class identities while preserving the base
ledger's 18,522 pending reviews and adding 45 pending overlay review gates.

**Architecture:** A separate ingestion module verifies the frozen PR6B3B
snapshot, inspects only the bounded local class directory, compiles four
canonical sidecar files, and applies their dispositions only to a deep-copied
in-memory ledger. The existing runtime package registry never loads this
overlay.

**Spec:**
`docs/superpowers/specs/2026-10-07-pr6b3c-class-review-overlay-design.md`

## Global constraints

- Do not edit PR6B3B evidence bytes or existing class JSON.
- Do not modify Flask, templates, builder/runtime class registries, deployment,
  dependencies, or database code.
- Do not infer approval from AoN, Foundry license labels, or remaster labels.
- Do not scan the complete compendium when only 27 class files are in scope.
- Every generated artifact is immutable, deterministic, offline, and
  `activation: none`.
- Identity-to-rule and identity-to-source mappings are explicit operator input;
  display names never derive IDs.

### Task 1: Freeze the schema and review gates

**Files:**
- Create: `tests/pf2e_rules/test_class_review_overlay.py`
- Create: `systems/pf2e/rules/ingestion/class_review_overlay.py`

- [ ] Write failing tests for exact shapes, IDs, timestamps, hashes, parent
  nullability, duplicate identifiers, and pending/approved/rejected review
  invariants.
- [ ] Run the focused tests and record the expected RED failures.
- [ ] Implement only strict detached normalization and repository-text hashing.
- [ ] Run the focused tests to GREEN.

### Task 2: Bind the frozen snapshot and all 29 identities

**Files:**
- Modify: `tests/pf2e_rules/test_class_review_overlay.py`
- Modify: `systems/pf2e/rules/ingestion/class_review_overlay.py`

- [ ] Test wrong snapshot/category/hash, missing or extra identities, and drift
  in name, URL, source, locator, fingerprint, or evidence hash.
- [ ] Verify the complete snapshot and exact class census/ledger shard before
  accepting authoring data.
- [ ] Enforce the eight source groups and an exact 29-record bijection.

### Task 3: Reconcile the bounded local class corpus

**Files:**
- Modify: `.gitattributes`
- Modify: `tests/pf2e_rules/test_class_review_overlay.py`
- Modify: `systems/pf2e/rules/ingestion/class_review_overlay.py`

- [ ] Test LF/CRLF parity, substantive byte drift, path escape, links/reparse
  points, unexpected files, duplicate Foundry IDs, wrong type/name, and orphan
  classes.
- [ ] Add narrow LF attributes without changing existing class blobs.
- [ ] Implement a bounded flat scanner for `compendium_data/classes`.
- [ ] Derive and test exactly 21 aligned, 6 source-drift, and 2 missing-local.

### Task 4: Prove non-mutating application and zero activation

**Files:**
- Modify: `tests/pf2e_rules/test_class_review_overlay.py`
- Modify: `systems/pf2e/rules/ingestion/class_review_overlay.py`

- [ ] Test that base inputs remain byte/value identical.
- [ ] Test effective dispositions of 29 mapped and 18,493 pending.
- [ ] Test all 18,522 reviews remain pending and mechanics enabled remains 0.
- [ ] Implement the pure deep-copy application/summary helper.

### Task 5: Compile, publish, and verify immutable bytes

**Files:**
- Modify: `tests/pf2e_rules/test_class_review_overlay.py`
- Modify: `systems/pf2e/rules/ingestion/class_review_overlay.py`

- [ ] Test canonical four-file output, deterministic order, tamper detection,
  exact layout, expected hash, no overwrite, concurrent writer behavior, and
  pairwise ancestor/descendant overlap among store, snapshot, and corpus,
  including Unicode/case aliases, existing-object identities, and bounded
  path scans.
- [ ] Inject write, flush, fsync, and rename failures; prove no partial target,
  cleanup of only matching private staging/lock claims, preservation of stale
  locks, and fail-closed quarantine on every observed swap. Treat the review
  store as trusted against active same-account mutation after the final
  portable pathname identity check.
- [ ] Recompile under multiple `PYTHONHASHSEED` values and require identical
  bytes.
- [ ] Implement canonical compilation, create-only process-visible atomic
  publication, and complete readback verification. Flush and fsync each file;
  document platform-dependent directory-entry durability rather than claiming
  power-loss durability.

### Task 6: Add the offline CLI

**Files:**
- Modify: `tools/pf2e_rules.py`
- Modify: `tests/pf2e_rules/test_cli.py`

- [ ] Add failing compile/verify, error-contract, expected-hash, offline,
  application-independence, and non-mutation tests.
- [ ] Implement `class-review-compile` and `class-review-verify` with bounded
  ASCII-safe output and exit code 2 on validation/I/O errors.
- [ ] Run the CLI and focused overlay tests to GREEN.

### Task 7: Generate and lock the current artifact

**Files:**
- Create: `systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/*`
- Create: `tests/pf2e_rules/test_current_class_review.py`
- Create: `docs/remediation/pr6b3c-class-review-overlay.md`
- Modify: `docs/remediation/README.md`

- [ ] Build authoring data for all 29 identities with every review pending.
- [ ] Compile the artifact through the operator CLI; never hand-edit generated
  output.
- [ ] Test exact source counts, 21/6/2 reconciliation, 29/18,493 disposition
  totals, 18,522 base reviews plus all 45 overlay gates pending, zero
  activation, current artifact reproduction, and unchanged 27-class runtime
  state.
- [ ] Pin existing Fighter and Player Core rule/source IDs and assert the
  artifact contains only the exact four JSON files with no prose or mechanics.
- [ ] Document operator verification, boundaries, and next review batches.

### Task 8: Repository verification and review

- [ ] Run focused overlay and CLI tests.
- [ ] Run all `tests/pf2e_rules` tests.
- [ ] Recompile into a clean temporary store and prove byte equality.
- [ ] Run template parsing and `git diff --check`.
- [ ] Run the non-browser regression suite and rely on Linux CI for the
  authoritative cross-platform full gate.
- [ ] Inspect the complete diff and obtain an independent whole-branch review.
- [ ] Commit, push, open a PR, and merge only when every required check passes.
