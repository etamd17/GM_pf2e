# PR6B3E1 Class Legal Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a fail-closed, immutable readiness bundle that records the legal-notice, printing, revision, and errata evidence currently available for the eight class publications bound to PR6B3D, while approving no reviews and enabling no mechanics.

**Architecture:** Add a focused offline verifier beside the existing class-source evidence and class-review overlay modules. The verifier preflights every fixed package leaf before reading bytes, verifies the complete PR6B3C/PR6B3D/source-evidence chain through existing public APIs, validates one canonical manifest plus eight closed-schema dossiers, and returns detached data. A separate pure function renders deterministic per-source blockers. Two read-only CLI commands expose verification and reporting; no compiler, approval, runtime, or UI path is added.

**Tech Stack:** Python 3.11, canonical UTF-8 JSON, SHA-256, pathlib/os filesystem hardening, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-pr6b3e1-class-legal-readiness-design.md`

## Global Constraints

- Preserve PR6B3C at overlay hash `273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614` and PR6B3D at overlay hash `e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb` byte-for-byte.
- Preserve the source-evidence bundle at hash `4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3` byte-for-byte.
- Publish exactly one new immutable bundle, `pf2e-class-legal-readiness-2026-10-11.1`, with scope `class-license-readiness`, one manifest, and eight dossiers.
- Retain no PDF bytes, book text, rules text, errata correction text, legal-notice prose, product-description prose, or art. Store only bounded identifiers, hashes, dates, URLs, page locators, enums, and blocker codes.
- Keep all eight `license_review` gates and all 29 `rules_review` gates pending. Keep `activation: none`, `enabled_mechanics: 0`, and the existing 18,493 pending base identities.
- Treat Player Core's observed ORC notice as an observation, not redistribution approval. Keep every printing and revision unverified. Keep Rage of Elements license-neutral.
- Add no evidence lookup that can satisfy `license_review`; the only supported review scope is `license_readiness`.
- Verification and reporting remain deterministic, offline, Flask-independent, and `DATA_DIR`-independent.
- Do not modify runtime, persistence, routes, templates, class mechanics, or GM/player UI. Any later UI change still requires a user-approved mockup first.
- Do not begin PR6B3E2 approval work unless seven missing final artifacts/legal pages, printing/revision evidence, applicable official errata, and a named license reviewer are available.

## Public Interfaces

Implement exactly these public functions in `systems/pf2e/rules/ingestion/class_legal_readiness.py`:

```python
def verify_class_legal_readiness_bundle(
    manifest_path: Path,
    class_review_overlay_path: Path,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    expected_hash: str | None = None,
    *,
    parent_overlay_path: Path,
    source_evidence_manifest_path: Path,
) -> dict:
    """Verify the exact readiness bundle and its complete immutable chain."""


def class_legal_readiness_report(bundle: dict) -> dict:
    """Return a detached, deterministic summary of counts and blockers."""
```

The verifier returns detached data with exactly these top-level keys:

```python
{
    "manifest": deepcopy(manifest),
    "dossiers_by_hash": {sha256: deepcopy(dossier), ...},
    "dossiers_by_source": {source_id: deepcopy(dossier), ...},
}
```

The pure report returns:

```python
{
    "bundle_id": manifest["bundle_id"],
    "bundle_hash": manifest["bundle_hash"],
    "counts": deepcopy(manifest["counts"]),
    "sources": [
        {
            "source_id": source_id,
            "blockers": deepcopy(dossier["review_readiness"]["blockers"]),
        },
        ...,
    ],
}
```

`sources` is sorted by exact `source_id`; each blocker list is already sorted and unique. The report function performs no filesystem or network access, validates that it received the verifier's bounded detached shape, and returns new objects on every call.

## Review Focus

- Every readiness, PR6B3D, PR6B3C, and source-evidence manifest/dossier leaf must be checked for regular-file type, link/reparse status, and hardlink count before any package bytes are read.
- Readiness, overlay, parent, source-evidence, snapshot, and corpus input trees must not alias, contain one another, or overlap through case, Unicode normalization, resolved identity, or hardlinks. Legitimate sibling packages under the same `reviews` or `evidence/paizo` parent must remain valid.
- The verifier must call `verify_class_review_overlay(...)` with the exact trusted PR6B3D hash and `verify_class_source_evidence_bundle(...)` with the exact trusted source-bundle hash; an internally consistent replacement is not acceptable.
- Manifest counts must be recomputed from dossiers, not trusted. A missing blocker, false `ready` state, completed review identity, verified printing/revision, unsupported notice family, or license-review evidence claim must fail.
- The Player Core errata inventory must be complete as captured from the official Paizo FAQ on 2026-10-11, contain locator metadata only, and keep applicability unresolved because the supplied PDF printing is unknown.
- Runtime application import/startup must not import the new module or read the readiness bundle.

---

### Task 1: Closed schema, pre-read verifier, and deterministic report

**Files:**
- Create: `systems/pf2e/rules/ingestion/class_legal_readiness.py`
- Create: `tests/pf2e_rules/test_class_legal_readiness.py`

**Consumes:**
- `canonical_json`, `digest`, `read_file`, `read_json`, and `reject_links` from `systems/pf2e/rules/manifest.py`.
- `RulesValidationError`, `bounded_json`, `choice`, `https_url`, `integer`, `iso_date`, `require`, `sequence`, `shape`, `strings`, `text`, and `timestamp` from `systems/pf2e/rules/validation.py`.
- Public `verify_class_review_overlay(...)` and `verify_class_source_evidence_bundle(...)`; do not import underscore-prefixed helpers from those modules.

**Internal contract:**
- Define version, authority, scope, size, ID-pattern, enum, blocker, exact-source-roster, and production-binding constants at module scope.
- Require readiness dossier IDs to be exactly `paizo.class-legal-readiness.<source-slug>`, where `<source-slug>` is the bound `source_id` with `pf2e.source.` removed; require each dossier path to be exactly `dossiers/<source-slug>.json`.
- Normalize the closed manifest and dossier schemas through small composable helpers: `_sha256`, `_relative_dossier_path`, `_product_ids`, `_notice_locator`, `_errata_locator`, `_artifact_observation`, `_unverified_observation`, `_legal_notice_observation`, `_errata_observation`, `_downstream_compliance`, `_review_readiness`, `_dossier`, and `_manifest`.
- Sort errata locators by the exact tuple `(release_label, printing_label, entry_type, tuple(pages))`. Require each locator's pages to be sorted, unique, positive integers. Require all blocker arrays to be exact-code-point sorted and unique.
- Derive the expected `review_readiness.blockers` from the observations. Do not accept caller-supplied blocker omissions or extras.
- Derive all 13 manifest counts from normalized dossiers and require exact equality with the manifest.

Freeze the downstream-compliance blocker list for every PR6B3E1 dossier to
exactly this sorted array; a future reviewed schema may narrow it, but this
readiness bundle must not infer that any category is already satisfied:

```python
[
    "attribution-review",
    "branding-review",
    "distribution-review",
    "notice-review",
    "reserved-material-review",
    "trademark-review",
]
```

Derive readiness blockers exactly. Player Core has:

```python
[
    "downstream-compliance-missing",
    "errata-applicability-unresolved",
    "named-reviewer-missing",
    "printing-unverified",
    "revision-unverified",
]
```

Each of the other seven sources has:

```python
[
    "downstream-compliance-missing",
    "errata-applicability-unresolved",
    "errata-inventory-unverified",
    "final-artifact-missing",
    "final-legal-notice-missing",
    "named-reviewer-missing",
    "printing-unverified",
    "revision-unverified",
]
```

Define the pre-read boundary in this task so the first green verifier is already safe:

```python
def _preflight_class_legal_readiness_inputs(
    manifest_path: Path,
    class_review_overlay_path: Path,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    parent_overlay_path: Path,
    source_evidence_manifest_path: Path,
) -> None:
    """Reject unsafe layouts and shared identities before any payload read."""
```

It must enumerate the exact fixed readiness, PR6B3D, PR6B3C, source-evidence,
and snapshot-manifest leaves without first parsing them; require regular,
non-link, non-reparse, single-link files; and reject pairwise input-tree
overlap. The same initial RED test matrix supplies exhaustive adversarial
coverage, so no unsafe intermediate verifier exists.

- [ ] **Step 1: Write the complete failing verifier/report test matrix**

Add a test fixture builder that writes one canonical temporary readiness bundle while using the real frozen PR6B3C, PR6B3D, source-evidence, snapshot, and corpus inputs. Use an explicitly synthetic valid errata response hash, capture timestamp, and nonempty locator tuple in this unit fixture; do not present those fixture values as captured production facts. The helper may calculate fixture hashes, but production capture values and hashes are pinned separately in Task 2.

Cover:

- the exact manifest and dossier key sets from the approved spec;
- the exact eight-source roster and exact `evidence_id`/`source_evidence_id`/upstream dossier-hash bindings;
- canonical bytes and manifest self-hash with only `bundle_hash` omitted;
- one `hash_observed` artifact and seven `missing` artifacts;
- one observed legal notice and seven missing notices;
- eight unverified printings and revisions;
- one complete-as-captured errata inventory, seven unverified inventories, and eight unresolved applicability states;
- exact derived blockers, exact 13 counts, zero ready/approved reviews, and zero mechanics;
- detached verifier returns and detached repeated reports;
- deterministic report ordering under different insertion orders;
- the absence of arbitrary notes, prose, review identity, `license_review`, activation, and rules payload fields.

Add parameterized RED cases for unknown/missing keys, wrong types, invalid IDs/hashes/dates/URLs, oversized labels, embedded newlines, duplicate/unsorted pages, duplicate/unsorted locators, duplicate/unsorted blockers, and unsupported enum values.

In the same RED commit, add named adversarial cases for:

- extra/missing/wrong-case package entries and noncanonical/tampered bytes;
- traversal, backslash, drive-qualified, reserved Windows, trailing-dot, trailing-space, and Unicode-normalization-confusable paths;
- duplicate source/evidence IDs, paths, and dossier hashes;
- rehashed dossier swaps, wrong-source bindings, wrong product IDs, and unsupported review scope;
- wrong, missing, drifted, or self-consistent-replacement PR6B3D overlay, PR6B3C parent, source bundle, source dossier, snapshot, and corpus;
- readiness/source/overlay directories swapped into another argument;
- direct and ancestor overlap, case aliases, Unicode aliases, symlinks, directory/file reparse points, and non-regular payloads;
- a hardlinked readiness leaf, a hardlinked upstream leaf, and a cross-package hardlink;
- a monkeypatched `read_file` proving an unsafe fixed leaf fails before the first payload read;
- mutating a returned bundle/report and then freshly verifying unchanged disk bytes; and
- mutating a dossier or manifest after one successful verification and requiring the next verification to fail.

Use real filesystem hardlinks where supported. If the platform cannot create symlinks or reparse points, skip only that specific creation test with the existing explicit capability pattern; hardlink coverage must run on NTFS.

- [ ] **Step 2: Run the focused tests and observe the missing-module failure**

Run:

```text
python -m pytest -q tests/pf2e_rules/test_class_legal_readiness.py --strict-markers
```

Expected: FAIL because `class_legal_readiness` does not exist. This single RED gate precedes both the normal verifier and its security boundary; no unsafe intermediate verifier is committed.

- [ ] **Step 3: Implement the closed schema, integrity checks, and report**

Implement the two public functions, normalizers, exact-layout reader, and full pre-read boundary together. The verifier must:

1. run `_preflight_class_legal_readiness_inputs(...)` before reading bytes;
2. verify PR6B3D through `verify_class_review_overlay(...)`, passing `expected_hash=PRODUCTION_CLASS_REVIEW_OVERLAY_HASH`, the exact parent, and exact source-evidence manifest;
3. independently verify the source bundle through `verify_class_source_evidence_bundle(...)`, passing `expected_hash=PRODUCTION_SOURCE_BUNDLE_HASH`;
4. parse and normalize the readiness manifest and each dossier;
5. require canonical raw bytes, reference/dossier hash agreement, unique hashes/IDs/sources/paths, and the exact filename derived from `source_id`;
6. recompute `bundle_hash` from the canonical manifest with only `bundle_hash` omitted;
7. bind the manifest to the verified PR6B3D and source-evidence IDs/hashes;
8. bind each readiness dossier to the exact upstream source dossier's source ID, evidence ID, SHA-256, hardcover/PDF IDs, artifact observation, printing/revision observations, and notice locator facts;
9. enforce the immutable known production observations—Player Core's exact artifact hash and notice locator, its complete-as-captured/unresolved errata state, the seven-source missing state, and the recomputed count table—while treating the capture-specific response hash, timestamp, and locator tuples as valid bounded data that Task 2 pins through the immutable bundle hash; and
10. validate optional `expected_hash` at `$.binding.bundle_hash` before returning deep copies.

Do not add a `class_legal_readiness_hashes` helper or any other API that could be used to approve a license-review gate.

The preflight must locally implement bounded readiness-specific path and identity checks rather than importing private PR6B3D helpers. Define the roots exactly as:

```text
readiness_root = manifest_path.absolute().parent
overlay_root = class_review_overlay_path.absolute()
snapshot_root = snapshot_manifest_path.absolute().parent
corpus_tree = corpus_root.absolute()
parent_root = parent_overlay_path.absolute()
source_root = source_evidence_manifest_path.absolute().parent
```

Require those six trees to be pairwise disjoint by lexical, resolved, normalized-component, ancestor, and existing file-identity checks. Exact directory inventory applies only to the readiness, overlay, parent, and source-evidence package roots. The snapshot directory may contain its required shards and the corpus may contain its normal data tree; their public upstream verifiers own those layouts. The outer preflight checks the snapshot manifest as a fixed leaf and checks both entire roots for disjointness. Legitimate readiness/source and PR6B3C/PR6B3D sibling directories remain valid.

Enumerate the exact fixed leaves without parsing any manifest:

- readiness `manifest.json` plus the eight exact dossier filenames;
- PR6B3D's four overlay files;
- PR6B3C's four overlay files;
- source-evidence `manifest.json` plus its eight exact dossier filenames; and
- the snapshot manifest itself.

Every fixed leaf must exist, be a regular non-symlink/non-reparse file, have `st_nlink == 1`, and have a unique device/inode identity across the fixed-leaf set. Convert OS failures to stable domain errors without leaking host paths. Follow the hardened patterns in `class_review_overlay.py` around `_require_disjoint_trees` and `_require_unshared_v2_payloads`, `class_source_evidence.py` around `_read_layout`, and `manifest.py`'s no-follow/inode-checked `read_file`.

- [ ] **Step 4: Run the complete verifier/report tests to green**

Run:

```text
python -m pytest -q tests/pf2e_rules/test_class_legal_readiness.py --strict-markers
```

Expected: PASS with no network access, including real NTFS hardlink regressions and the pre-read canary.

- [ ] **Step 5: Commit the verifier contract**

```text
git add systems/pf2e/rules/ingestion/class_legal_readiness.py tests/pf2e_rules/test_class_legal_readiness.py
git commit -m "feat: verify class legal readiness evidence"
```

### Task 2: Publish and pin the immutable PR6B3E1 bundle

**Files:**
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/manifest.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/battlecry.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/dark-archive-remastered.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/guns-and-gears-remastered.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/impossible-magic.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/player-core.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/player-core-2.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/rage-of-elements.json`
- Create: `systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/dossiers/war-of-immortals.json`
- Create: `tests/pf2e_rules/test_current_class_legal_readiness.py`

**Production observations:**

- Player Core: PDF product `PZO12001E`, artifact SHA-256 `494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9`, ORC notice observed at printed page 463/PDF page 464, printing and revision unverified.
- Player Core errata: capture only normalized section/page/entry-type locators from `https://paizo.com/pathfinder/faq` as observed on 2026-10-11. Record the SHA-256 of the captured HTTPS response bytes and the capture timestamp. Copy no correction text. Mark all entry applicability unresolved.
- Other seven sources: final artifact and legal notice missing; printing/revision unverified; errata inventory unverified with null capture fields and an empty locator list; applicability unresolved.
- All eight sources: downstream compliance missing, readiness blocked, named reviewer missing, no approval, no mechanics.

**Exact manifest counts:**

```python
{
    "sources": 8,
    "artifact_hashes_observed": 1,
    "final_artifacts_missing": 7,
    "final_legal_notices_observed": 1,
    "final_legal_notices_missing": 7,
    "printings_unverified": 8,
    "revisions_unverified": 8,
    "errata_inventories_complete_as_captured": 1,
    "errata_inventories_unverified": 7,
    "license_reviews_ready": 0,
    "license_reviews_blocked": 8,
    "license_reviews_approved": 0,
    "enabled_mechanics": 0,
}
```

- [ ] **Step 1: Freeze and independently check the same-day FAQ capture metadata**

Fetch the public page body to a temporary file outside the repository with one
documented request equivalent to:

```text
curl.exe --location --fail --silent --show-error --compressed \
  --user-agent "Mozilla/5.0 PF2e-readiness-capture/1.0" \
  --output <temporary-body-path> \
  --write-out "%{http_code}\n%{url_effective}\n%{content_type}\n" \
  https://paizo.com/pathfinder/faq
```

Require HTTP `200`, an HTTPS final URL on `paizo.com`, an HTML content type,
and a body containing the Player Core Fall 2023, Spring 2025, and Spring 2026
sections already identified in the approved evidence review. With
`--compressed`, define the captured body as the decoded response-entity bytes
written by curl; HTTP headers and transfer/content encodings are not part of
the hash. Record a UTC timestamp immediately after the response completes and
calculate SHA-256 over that exact temporary file.

Build the locator inventory from every list item under every heading matching
`Player Core Errata (<release>, <printing>)`, stopping at the next book
heading. `release_label` and `printing_label` are the exact bounded heading
components. Expand an item's leading `Page`/`Pages` range into sorted distinct
positive integers. Set `entry_type` to `clarification` only when the leading
page locator explicitly includes `(Clarification)`; otherwise use `erratum`.
Do not infer classification from correction prose.

Before deleting the temporary body, have a fresh reviewer independently
extract and compare the full tuple list from the same bytes. Freeze the agreed
sorted tuples, response hash, timestamp, input URL, final URL, HTTP status, and
content type in the current-artifact test/remediation evidence. Only the
schema-approved fields enter the dossier. Do not commit the response body. If
the request is blocked, the required headings are absent, the two inventories
differ, the capture is not dated 2026-10-11, or implementation occurs after
that date, stop and revise the evidence decision rather than substituting
different source material.

- [ ] **Step 2: Write the failing pinned-current-artifact tests**

Define the exact reviewed locator tuple list as
`EXPECTED_PLAYER_CORE_ERRATA_LOCATORS`. Pin placeholders for the readiness
bundle hash and all eight readiness dossier hashes, then assert:

- the exact manifest-plus-eight-dossier layout and canonical LF-only bytes;
- exact upstream IDs/hashes and exact source roster;
- exact Player Core artifact/notice/errata observations and exact seven-source missing state;
- the exact 13-count table and deterministic report;
- no prose-shaped, approval, activation, or mechanics fields;
- unchanged hashes and bytes for every PR6B3C, PR6B3D, and source-evidence file;
- all eight `license_review` and all 29 `rules_review` values remain pending;
- PR6B3D still reports 8 approved source gates, 37 pending gates, and zero enabled mechanics;
- no successor overlay is created; and
- runtime import plus `/live` request neither imports `class_legal_readiness` nor reads the new bundle.

Add in-process audit-hook and subprocess canaries proving verifier/report execution does not read, create, or mutate `DATA_DIR`.

- [ ] **Step 3: Run the current-artifact test and observe the missing-bundle failure**

Run:

```text
python -m pytest -q tests/pf2e_rules/test_current_class_legal_readiness.py --strict-markers
```

Expected: FAIL because the immutable readiness bundle does not exist.

- [ ] **Step 4: Author, hash, and verify the canonical bundle**

Author all nine JSON files through patches using only the Step 1 capture
metadata, canonicalize key/list ordering, calculate each dossier SHA-256 over
its exact canonical UTF-8 bytes, then calculate `bundle_hash` over the
canonical manifest with only `bundle_hash` omitted. Replace the test hash
placeholders with those measured immutable values. Validate the resulting
bundle through the public verifier using the exact trusted chain, then run:

Run:

```text
python -m pytest -q tests/pf2e_rules/test_current_class_legal_readiness.py tests/pf2e_rules/test_class_legal_readiness.py --strict-markers
```

Expected: PASS with exact pinned hashes and no network access during verification.

- [ ] **Step 5: Commit the immutable bundle and pinned capture contract**

```text
git add systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1 tests/pf2e_rules/test_current_class_legal_readiness.py
git commit -m "feat: publish class legal readiness evidence"
```

### Task 3: Read-only verification and blocker-report CLI

**Files:**
- Modify: `tools/pf2e_rules.py`
- Modify: `tests/pf2e_rules/test_cli.py`

**Commands:**

```text
python tools/pf2e_rules.py class-legal-readiness-verify MANIFEST \
  --overlay OVERLAY \
  --snapshot SNAPSHOT \
  --corpus CORPUS \
  --parent PARENT \
  --source-evidence SOURCE_MANIFEST \
  [--expected-hash SHA256]

python tools/pf2e_rules.py class-legal-readiness-report MANIFEST \
  --overlay OVERLAY \
  --snapshot SNAPSHOT \
  --corpus CORPUS \
  --parent PARENT \
  --source-evidence SOURCE_MANIFEST \
  [--expected-hash SHA256]
```

Both commands require every chain path. Add no `--store`, compile, publish, approve, or activation command.

The verify command prints one ASCII-safe sorted JSON line with exactly `path`, `bundle_id`, `bundle_hash`, `scope`, `class_review_overlay_id`, `class_review_overlay_hash`, `source_bundle_id`, `source_bundle_hash`, and `counts`. The report command prints the exact pure-report result. Both pass strict bounded-parser readback before output.

- [ ] **Step 1: Write failing parser, forwarding, output, and isolation tests**

Add:

- `test_class_legal_readiness_verify_cli_uses_exact_chain_offline_and_nonmutating`;
- `test_class_legal_readiness_report_cli_is_deterministic_and_reports_exact_blockers`;
- `test_class_legal_readiness_commands_require_every_chain_input`;
- `test_class_legal_readiness_verify_cli_rejects_wrong_trusted_hash_without_traceback`;
- `test_class_legal_readiness_cli_forwards_paths_and_expected_hash_exactly`;
- `test_class_legal_readiness_cli_results_fit_strict_readback_budget`;
- `test_render_result_reparses_strict_json_before_emitting`; and
- `test_class_legal_readiness_cli_exposes_no_compile_or_approval_command`.

Use a shared parser-argument helper so verify/report flags cannot drift. The subprocess helper must deny socket access, assert `app` and `flask` remain absent from `sys.modules`, run under at least two `PYTHONHASHSEED` values for report determinism, and assert `DATA_DIR` remains empty.

- [ ] **Step 2: Run the CLI subset and observe unrecognized-command failure**

Run:

```text
python -m pytest -q tests/pf2e_rules/test_cli.py -k "class_legal_readiness" --strict-markers
```

Expected: FAIL because the two commands are not registered.

- [ ] **Step 3: Add exact parsers and dispatch**

Import the verifier/report beside the existing class-review imports. Add one local `_add_class_legal_readiness_arguments(...)` parser helper, then register both commands. Each dispatch branch verifies the complete chain exactly once. Report must consume only the verified detached bundle; it must never parse unverified JSON directly.

Add both command names to `_render_result(..., require_readback=True)`. Tighten that existing helper so `require_readback=True` means actual readback: after `json.dumps(..., sort_keys=True, ensure_ascii=True)` and ASCII encoding/size validation, pass the exact emitted bytes through `read_json`, require the parsed object to equal the bounded input result, and only then print. Retain the existing `bounded_json` precheck. Run the complete CLI suite because this strengthens the same boundary for existing review/evidence commands. Preserve existing exit behavior: success `0`, domain/IO failure `2`, empty stdout on failure, compact `{"error": ..., "path": ...}` stderr, and no traceback or absolute-path leakage.

- [ ] **Step 4: Run the complete CLI tests to green**

Run:

```text
python -m pytest -q tests/pf2e_rules/test_cli.py --strict-markers
```

Expected: PASS with bounded deterministic output.

- [ ] **Step 5: Commit the CLI boundary**

```text
git add tools/pf2e_rules.py tests/pf2e_rules/test_cli.py
git commit -m "feat: report class legal readiness"
```

### Task 4: Remediation record and operator handoff

**Files:**
- Create: `docs/remediation/pr6b3e1-class-legal-readiness.md`
- Modify: `docs/remediation/README.md`

- [ ] **Step 1: Write the remediation record**

Document:

- the immutable bundle ID/hash, eight dossier hashes, exact upstream bindings, and exact offline verification/report commands;
- the distinction between observation, readiness, legal review, approval, and runtime activation;
- one observed artifact/legal notice versus seven missing final artifacts/legal notices;
- all printings/revisions unverified and all errata applicability unresolved;
- all eight license reviews and all 29 mechanics reviews still pending;
- zero enabled mechanics and no runtime/UI change;
- the source hierarchy: official final publication/legal pages and Paizo errata first, Archives of Nethys later for rules reconciliation, never as a substitute for final-book legal evidence;
- the PR6B3E2 prerequisites and named-reviewer requirement; and
- the fact that this evidence inventory is not legal advice or redistribution approval.

- [ ] **Step 2: Add the record to the remediation index**

Link PR6B3E1 after PR6B3D in `docs/remediation/README.md` and state that the next mechanics slice remains gated by PR6B3E2 legal approval.

- [ ] **Step 3: Verify documentation references and formatting**

Run:

```text
rg -n "PR6B3E1|pf2e-class-legal-readiness-2026-10-11.1|class-legal-readiness-(verify|report)" docs systems tests tools
git diff --check
```

Expected: all required identifiers and commands are discoverable; no whitespace errors.

- [ ] **Step 4: Commit the operator documentation**

```text
git add docs/remediation/pr6b3e1-class-legal-readiness.md docs/remediation/README.md
git commit -m "docs: record class legal readiness blockers"
```

### Task 5: Repository verification, fresh review, PR, and merge

**Files:**
- Modify only if a failing test or fresh review proves a Critical/Important defect.

- [ ] **Step 1: Run focused and repository verification**

Run in order:

```text
python -m pytest -q tests/pf2e_rules/test_class_legal_readiness.py --strict-markers
python -m pytest -q tests/pf2e_rules/test_current_class_legal_readiness.py --strict-markers
python -m pytest -q tests/pf2e_rules/test_cli.py --strict-markers
python tools/check_templates.py
python -m pytest -q tests/pf2e_rules --strict-markers
python -m pytest -q -m "not postgresql and not browser" --strict-markers
git diff --check origin/main...HEAD
```

Expected: every command exits `0`. This metadata-only slice creates no new browser or PostgreSQL behavior obligation.

- [ ] **Step 2: Prove the branch did not change frozen/runtime surfaces**

Compare every PR6B3C, PR6B3D, and source-evidence file hash with the pinned integration constants. Inspect `git diff --name-status origin/main...HEAD` and confirm there are no changes under runtime routes, templates, persistence, character mechanics, or UI assets.

- [ ] **Step 3: Dispatch one fresh-context whole-branch review**

Build a review package from merge base `origin/main` containing this plan, the approved spec, Review Focus, all commits, all verification output, and explicit requests to audit schema closure, pre-read filesystem safety, trusted-chain binding, count/blocker derivation, legal overclaiming, runtime isolation, and backward compatibility.

- [ ] **Step 4: Re-grade findings, perform at most one TDD fix pass, and re-review**

For each verified Critical/Important finding: write a reproducing test, observe RED, implement the smallest fix, observe GREEN, and rerun every Step 1 command. Record false positives and deferred Minor findings. Commit verified fixes only:

```text
git add <only files changed by verified fixes>
git commit -m "fix: close PR6B3E1 review findings"
```

After any fix commit, send the fix delta and its reproducing tests to a fresh
reviewer and require explicit closure of each prior Critical/Important finding.
The merge gate is exactly zero unresolved Critical or Important findings. If
one TDD fix pass is insufficient, stop and report the remaining defects; do
not weaken the gate, open a misleadingly ready PR, or merge.

- [ ] **Step 5: Push, open and attach the pull request, wait for GitHub CI, and merge when green**

The pull request description must state clearly that the bundle records readiness blockers but approves no license review and activates no mechanics. Push/open only after the zero-unresolved-Critical/Important review gate is documented, and merge only after required GitHub checks pass. Follow the user's standing instruction not to wait for Railway after the main-branch merge; deployment failures will be handled if reported.
