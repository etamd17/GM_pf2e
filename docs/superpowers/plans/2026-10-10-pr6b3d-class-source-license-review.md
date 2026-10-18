# PR6B3D Class Source and License Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain canonical Paizo publication evidence and publish a schema-v2 class-review successor that approves exactly eight publication-identity gates while activating no mechanics.

**Architecture:** Add a strict offline evidence-bundle module beside the existing immutable overlay compiler, then extend the overlay contract by schema version so v1 bytes and behavior remain unchanged. Schema v2 verifies an exact v1 parent and evidence bundle, permits only evidence-backed source/license review transitions, and emits the same create-only four-file package layout.

**Tech Stack:** Python 3.11, canonical JSON and SHA-256, pathlib/os filesystem hardening, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-pr6b3d-class-source-license-review-design.md`

## Global Constraints

- Preserve `systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/` byte-for-byte at overlay hash `273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614`.
- Retain exactly eight metadata-only Paizo dossiers; copy no rules prose, descriptive product prose, or art.
- Approve exactly eight `source_review` gates; keep eight `license_review` and 29 `rules_review` gates pending.
- Keep `activation: none`, `enabled_mechanics: 0`, 29 mapped classes, and 18,493 remaining pending base identities.
- Exact printing remains unverified for all eight products. Rage of Elements remains license-neutral and no aggregate ORC-only claim is permitted.
- Compilation and verification are deterministic, offline, application-independent, `DATA_DIR`-independent, and fail closed on unsafe path relationships, links/reparse points, tampering, or binding drift.
- Do not modify runtime, persistence, routes, templates, class mechanics, or GM/player UI.

## Review Focus

- A canonical but self-consistent replacement parent must fail unless its ID, trusted hash, schema, and complete reproduced bytes match PR6B3C.
- A dossier hash reused for another source or review scope must fail even when the digest exists in the bundle.
- Parent or evidence leaves that alias the exact child target, snapshot, corpus, or each other through case, Unicode normalization, links/reparse points, hardlinks, or ancestors must fail before reading mutable inputs; distinct parent/child siblings under one review store must succeed.
- Schema-v1 callers that omit parent and evidence arguments must still reproduce the exact PR6B3C bytes and reject completed gates.
- Runtime application import or request startup must not import or read the evidence/overlay tooling or retained artifacts.

---

### Task 1: Canonical Paizo class-source evidence bundle

**Files:**
- Create: `systems/pf2e/rules/ingestion/class_source_evidence.py`
- Create: `tests/pf2e_rules/test_class_source_evidence.py`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/*.json`

**Interfaces:**
- Consumes: `canonical_json`, `digest`, `read_file`, `read_json`, `reject_links`, and the existing `RulesValidationError` validation helpers.
- Produces: `verify_class_source_evidence_bundle(manifest_path: Path, expected_hash: str | None = None) -> dict` returning detached `manifest`, `dossiers_by_hash`, and `dossiers_by_source`; `class_source_evidence_hashes(bundle: dict, source_id: str, review_scope: str) -> frozenset[str]`.

- [ ] **Step 1: Write failing evidence contract tests**

Add tests that name the production mutation they catch and assert exact layout, eight source mappings, canonical bytes, manifest/dossier hash closure, bundle self-hash, bounded metadata-only fields, the Player Core artifact observation, all-printings-unverified state, and Rage of Elements' neutral pending rights state. Add fail-closed cases for unknown extra files, links/reparse points, duplicate source/evidence IDs/hashes, traversal/nonportable paths, tampered bytes, wrong trusted hash, and a dossier whose source or review scope is swapped.

- [ ] **Step 2: Run the evidence tests and observe the missing-module/contract failure**

Run: `python -m pytest -q tests/pf2e_rules/test_class_source_evidence.py --strict-markers`

Expected: FAIL because `class_source_evidence` and the retained bundle do not exist.

- [ ] **Step 3: Implement the strict reader/verifier and canonical retained bundle**

Implement schema/version constants and the two interfaces above. Bound entry counts and byte sizes, require the exact manifest-plus-eight-dossier layout, reject links/reparse points, canonicalize every document, verify every digest and the manifest self-hash, and return deep-copied indexes. Create the eight dossiers with the spec's exact Paizo metadata and scope enums.

- [ ] **Step 4: Run the evidence tests to green**

Run: `python -m pytest -q tests/pf2e_rules/test_class_source_evidence.py --strict-markers`

Expected: PASS with no warnings or network access.

- [ ] **Step 5: Commit the evidence boundary**

```text
git add systems/pf2e/rules/ingestion/class_source_evidence.py systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1 tests/pf2e_rules/test_class_source_evidence.py
git commit -m "feat: retain Paizo class source evidence"
```

### Task 2: Schema-v2 immutable parent and review lifecycle

**Files:**
- Modify: `systems/pf2e/rules/ingestion/class_review_overlay.py`
- Modify: `tests/pf2e_rules/test_class_review_overlay.py`

**Interfaces:**
- Consumes: Task 1's bundle verifier and evidence-scope lookup, plus the exact four-file PR6B3C parent.
- Produces: backward-compatible `compile_class_review_overlay`, `write_class_review_overlay`, and `verify_class_review_overlay` APIs with keyword-only `parent_overlay_path: Path | None` and `evidence_manifest_path: Path | None`; schema-v2 normalization, parent-diff, evidence-resolution, and lifecycle validation.

- [ ] **Step 1: Write failing schema-v2 lifecycle tests**

Add tests for exact v1 byte/hash compatibility and v1 rejection of supplied parent/evidence arguments; required v2 parent/evidence arguments and manifest bundle ID/hash; correct parent ID/hash/schema; self/cyclic, missing, tampered, swapped, wrong-snapshot, noncanonical, and self-consistent-replacement parents; exact allowed review-only child diff; frozen identities/source assignments/titles/local reconciliation/rules reviews; unknown, duplicate, cross-source, and wrong-scope evidence hashes; exact 8-approved/37-pending counts; zero mechanics; determinism across hash seeds; unsafe leaf aliases; and the valid same-store parent/child sibling layout.

- [ ] **Step 2: Run the focused lifecycle tests and observe the unsupported-v2/signature failure**

Run: `python -m pytest -q tests/pf2e_rules/test_class_review_overlay.py -k "schema_v2 or parent or evidence or v1_bytes" --strict-markers`

Expected: FAIL because schema v2 and parent/evidence parameters are not implemented.

- [ ] **Step 3: Implement version-dispatched normalization, compilation, publication, and verification**

Keep schema-v1 constants, lifecycle checks, compiler version, manifest shape, and generated bytes unchanged. For schema v2, require the exact external parent plus the manifest's evidence bundle ID/hash, verify both before resolving reviews, compare every frozen parent/child field, validate each completed review hash against the same-source/same-scope dossier index, compute general gate counts, and pin the production count contract. Extend leaf-path overlap checks before input reads without rejecting distinct parent/child siblings under one review store, and keep the atomic create-only writer unchanged after compilation.

- [ ] **Step 4: Run overlay and evidence tests to green**

Run: `python -m pytest -q tests/pf2e_rules/test_class_review_overlay.py tests/pf2e_rules/test_class_source_evidence.py --strict-markers`

Expected: PASS, including the existing v1 writer/adversarial filesystem suite.

- [ ] **Step 5: Commit the schema-v2 compiler**

```text
git add systems/pf2e/rules/ingestion/class_review_overlay.py tests/pf2e_rules/test_class_review_overlay.py
git commit -m "feat: bind class reviews to parent evidence"
```

### Task 3: Public offline CLI for schema-v2 inputs

**Files:**
- Modify: `tools/pf2e_rules.py`
- Modify: `tests/pf2e_rules/test_cli.py`

**Interfaces:**
- Consumes: Task 2's keyword-only public API parameters.
- Produces: `class-review-compile --parent <dir> --evidence <manifest>` and `class-review-verify --parent <dir> --evidence <manifest>`; v1 commands remain valid without either option.

- [ ] **Step 1: Write failing CLI tests**

Add a real subprocess round trip for schema v2; argument-pair validation; friendly bounded errors for missing/wrong parent and evidence; v1 omission compatibility; output readback budget; offline network-denial audit; no Flask/app import; and unchanged `DATA_DIR` assertions.

- [ ] **Step 2: Run the CLI tests and observe unrecognized-argument failure**

Run: `python -m pytest -q tests/pf2e_rules/test_cli.py -k "class_review" --strict-markers`

Expected: FAIL because `--parent` and `--evidence` are not recognized or forwarded.

- [ ] **Step 3: Add and forward optional parent/evidence arguments**

Add both options to compile and verify parsers and pass them by keyword into Task 2's APIs. Leave v1 behavior unchanged and retain the strict compact JSON result/error boundary.

- [ ] **Step 4: Run all CLI tests to green**

Run: `python -m pytest -q tests/pf2e_rules/test_cli.py --strict-markers`

Expected: PASS with subprocess output that fits existing strict readback limits.

- [ ] **Step 5: Commit the CLI extension**

```text
git add tools/pf2e_rules.py tests/pf2e_rules/test_cli.py
git commit -m "feat: expose class review ancestry inputs"
```

### Task 4: Publish and pin the PR6B3D immutable child

**Files:**
- Create: `systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/authoring.json`
- Create: `systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/manifest.json`
- Create: `systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/records.json`
- Create: `systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/sources.json`
- Create: `tests/pf2e_rules/test_current_class_source_review.py`
- Modify: `docs/remediation/pr6b3d-class-source-license-review.md`
- Modify: `docs/remediation/README.md`

**Interfaces:**
- Consumes: Tasks 1-3's canonical evidence bundle, schema-v2 compiler/verifier, and CLI.
- Produces: the exact immutable PR6B3D child ID/hash, current-artifact integration contract, reproducible operator commands, and remediation index entry.

- [ ] **Step 1: Write the failing current-artifact integration tests**

Pin the exact parent bytes/hash, evidence bundle ID/hash and eight dossier hashes, child layout and trusted hash placeholder, all 29 source assignments, eight evidence-backed source approvals, eight pending license reviews, 29 pending rules reviews, exact counts, v1/v2 deterministic recompilation, offline/app-independent execution, runtime non-import/non-read behavior, and no `DATA_DIR` mutation.

- [ ] **Step 2: Run the integration test and observe the missing-child failure**

Run: `python -m pytest -q tests/pf2e_rules/test_current_class_source_review.py --strict-markers`

Expected: FAIL because the immutable child has not been published and its trusted hash is not pinned.

- [ ] **Step 3: Compile once into an empty temporary store, move only generated canonical files into the new immutable ID, and pin its measured hash**

Use the public CLI with the exact PR6B3C parent, frozen snapshot, local corpus, and retained evidence manifest. Do not hand-edit generated sidecars. Update the integration test and remediation document with the measured child and bundle hashes.

- [ ] **Step 4: Run the complete PR6B3D and PF2e suites**

Run: `python -m pytest -q tests/pf2e_rules --strict-markers`

Expected: PASS with only the established platform-specific hardlink skip, if still applicable.

- [ ] **Step 5: Update operator documentation and commit the immutable child**

Document exact offline compile/verify commands, approved/pending distinctions, printing/license blockers, and next review slices. Index PR6B3D in the remediation README.

```text
git add systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1 tests/pf2e_rules/test_current_class_source_review.py docs/remediation/pr6b3d-class-source-license-review.md docs/remediation/README.md
git commit -m "docs: publish class source review overlay"
```

### Task 5: Repository-wide verification and review gate

**Files:**
- Modify only if a failing test or fresh review produces a verified Critical/Important defect.

**Interfaces:**
- Consumes: the complete Task 1-4 branch and this plan's ledger.
- Produces: fresh full-suite evidence and one fresh-context whole-branch review package.

- [ ] **Step 1: Run repository verification**

Run in order:

```text
python tools/check_templates.py
python -m pytest -q tests/pf2e_rules --strict-markers
python -m pytest -q -m "not postgresql and not browser" --strict-markers
git diff --check origin/main...HEAD
```

Expected: all commands exit 0; this metadata-only slice creates no browser or PostgreSQL behavior obligation.

- [ ] **Step 2: Generate the whole-branch review package and dispatch a fresh reviewer**

Use `review-package` from the executing-plans workflow with merge base `origin/main`, include this plan, its spec, Review Focus, and every ledger ruling, and request correctness/security/immutability review.

- [ ] **Step 3: Re-grade findings and perform at most one TDD fix pass**

For each Critical/Important finding, write a reproducing test, observe RED, implement the smallest fix, observe GREEN, and rerun the complete verification commands. Record deferred Minor findings and all rulings in the ledger.

- [ ] **Step 4: Commit verified review fixes, if any**

```text
git add <only files changed by verified fixes>
git commit -m "fix: close PR6B3D review findings"
```

- [ ] **Step 5: Push, open the pull request, wait for GitHub CI, and merge when green**

Attach the PR to the task after creation. Do not wait for Railway after merge; deployment follow-up is owned by the user's standing workflow.
