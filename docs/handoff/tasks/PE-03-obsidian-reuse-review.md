# PE-03: Reconcile the existing Obsidian v0.9 implementation

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex
- **Reviewer:** claude
- **Status:** done
- **Branch:** `codex/pe-03-obsidian-reuse-review` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** none; this is a read-only source review with a documentation deliverable, not a runtime port.
- **Context:** [approved plan](../../player-release/implementation-plan.md), [source inventory](../../player-release/existing-obsidian-vtt-work.md), `tools/obsidian-plugin/README.md`, and the existing Obsidian tests.

## Goal

Preserve reusable Obsidian recovery and session-provenance work before the player
release builds overlapping features. Produce a precise port/reuse decision for
each existing change without merging or modifying product code.

## Approach

1. Confirm current main and the candidate branch. Stage 0 found
   `feat/obsidian-session-operations-v0.9` at `1d0b895e` with five branch-only
   commits: `4f100aa9`, `5dd705e8`, merge `a991e379`, `fd223136`, `1d0b895e`.
   Main was `7aeb9549`; these counts must be recomputed, not assumed current.
2. Review each commit and the aggregate merge-base diff. The 13 touched paths are
   `app.py`, `core/obsidian_sync.py`, `core/route_policy.py`,
   `services/obsidian_sync.py`, `tests/test_obsidian_operations_v2.py`,
   `tests/test_obsidian_plugin_behavior.py`, `tests/test_obsidian_plugin_contract.py`,
   `tests/test_obsidian_sync.py`, `tests/test_route_policy_inventory.py`, and
   `tools/obsidian-plugin/{README.md,main.js,manifest.json,styles.css}`.
3. Compare against newer main authorization, character-workflow and combat
   preflight changes. Distinguish a merge commit from a feature slice; do not
   cherry-pick an old merge as the integration strategy.
4. Inspect the existing implementations of canonical package identity, generated
   output ownership, local materialization retry, origin/campaign queue scoping,
   connection-generation guards, event drain/deduplication and read-only room
   preflight/capabilities. Produce a table of reusable behavior, conflicts,
   accepted limitations, missing tests and a bounded port order.
5. Write `docs/player-release/obsidian-reuse-review.md`. Map each proposed port to
   exact files/functions and its tests. Keep current v0.9 narrative-package
   requirements distinct from newly agreed release requirements: these must not
   accidentally gate ordinary player use or D&D support.

Use the committed candidate through Git objects or a disposable detached review
checkout. The existing external `gm_pf2e_console` worktree had an unresolved Git
status/permission error in Stage 0; do not repair it, switch it, clean it, or
assume it is clean. Do not read its `data.json`, credentials or campaign vault.
Testing a clean committed copy does not require changing that worktree.

## Acceptance

- [ ] `git log --oneline origin/main..feat/obsidian-session-operations-v0.9` and `git diff --stat origin/main...feat/obsidian-session-operations-v0.9` are recorded with both full SHAs and any change from the Stage 0 inventory.
- [ ] Every branch-only product commit has a reuse/port/defer decision; the merge commit has an explicit ancestry-only treatment. The report names dependencies on PE-13 repositories and PE-14 receipts/outbox where applicable.
- [ ] The report distinguishes implemented client recovery from still-missing atomic server state/history and all-origin event coverage, and identifies PF2e-specific assumptions that require D&D 2014/2024 adapters.
- [ ] Run `node --version` and `python --version`, then `python -m pytest -q tests/test_obsidian_sync.py tests/test_obsidian_operations_v2.py tests/test_obsidian_plugin_contract.py tests/test_obsidian_plugin_behavior.py --strict-markers` separately on clean main and candidate checkouts. Record exact results and commit IDs. If an environment dependency prevents a run, report it explicitly; skipped real-plugin behavior tests do not count as verified recovery.
- [ ] Run `python -m pytest -q tests/test_route_policy_inventory.py --strict-markers` on both review baselines. Investigate candidate failures caused by its old baseline separately from integration risks; do not change source to make this review green.
- [ ] Recovery evidence covers lost remote acknowledgment, failed local writes, replay duplicates, connection switch during in-flight requests and generated-file collisions. Identify absent cases as required port tests.
- [ ] `git diff --check` passes for the documentation deliverable. The final diff changes only the review report and this task's report/review sections (plus normal queue bookkeeping by its assigned owner).

## Out of scope / do not touch

- No wholesale merge, cherry-pick, product/runtime edit, plugin installation, deployment or external-worktree repair.
- No vault notes, token files, live campaign data or production session inspection.
- No implementation of D&D support, new character state, new action rules or permanent history storage in this review.
- No VTT/wiki resumption or changes to their original branches.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

Completed the documentation/source review on 2026-11-08 in
`GM_pf2e_pe03`, branch `codex/pe-03-obsidian-reuse-review`, starting at
`d08085b0fd003b7c80e7ca152aa212c79419e445` (Stage 0 merged main).
Deliverable: [Obsidian reuse review](../../player-release/obsidian-reuse-review.md).

- Recomputed candidate head `1d0b895e6992dd46eb5172a8026f2f81a6b2e209`,
  merge base `cbefabe7761d98eea892560ed2d56c017922bd9f`, divergence 255/5,
  and the 13-file, +3439/-316 aggregate diff. Every candidate-only commit has
  a disposition; the merge commit is ancestry only.
- Mapped exact implementation functions and tests to bounded selective ports,
  preserving newer main authorization, character workflow and encounter resets.
  Recorded PE-13/14 durability dependencies and PE-31a activation boundary.
- Reproduced six candidate issues: unscoped generated evidence/event loss,
  stale snapshot retry, lost concurrent recovery items, inconsistent ID length,
  incomplete room validation, and failed history-backfill uniqueness checks.
  Separately reproduced inherited non-atomic state/event/receipt persistence.
  The report distinguishes inherited behavior from new regressions.
- No product files, original source worktrees, installed plugin, vault, or live
  runtime were changed. Only the report, this brief's status/report and queue
  bookkeeping changed. Both detached source checkouts remained Git-clean.

### Checks

Node `v24.12.0`; Python `3.11.9`; pytest `9.1.1`. Separate synthetic `DATA_DIR`
for each run; existing `GM_pf2e/venv/Scripts/python.exe`, no dependency changes.

```text
python -m pytest -q tests/test_obsidian_sync.py tests/test_obsidian_operations_v2.py tests/test_obsidian_plugin_contract.py tests/test_obsidian_plugin_behavior.py --strict-markers
  main d08085b0:      39 passed, 0 failed/skipped (125.29 s)
  candidate 1d0b895e: 74 passed, 0 failed/skipped (115.35 s)
python -m pytest -q tests/test_route_policy_inventory.py --strict-markers
  main d08085b0:      18 passed, 0 failed/skipped (80.98 s)
  candidate 1d0b895e: 17 passed, 0 failed/skipped (80.27 s)
```

Real-plugin Node behavior was exercised. Candidate had no old-baseline test
failures. Extra in-memory/fake-vault probes and isolated blueprint probes
reproduced report F1-F7; commands and outputs are described in the deliverable.
Two diagnostic main collection runs each found 18 route tests; these are not
additional test passes. No full suite, browser, installed-plugin or production
acceptance was attempted: this task makes no runtime changes.

`git diff --check` passed. Documentation validation confirmed exactly three
allowed changed files, all 27 named test functions exist, local links resolve,
status is `review`, and the new report has no trailing whitespace/conflict
markers. The documentation change is below 500 changed lines.

### Follow-ups

Claude reviews this three-file documentation diff against the brief. Fixes and
regression tests belong in separately briefed PE-60/61 ports and PE-13/14/70;
none is implemented or promoted to ready by this review. Continue PE-01 in its
own worktree. After acceptance, follow the normal docs-only PR/CI/merge process.

## Review

**Accepted** (Claude, 2026-11-08). Reviewed the uncommitted three-file diff on
`codex/pe-03-obsidian-reuse-review` against current main `d08085b0`.
The candidate's merge base is `cbefabe7`. No findings against the acceptance list.

Re-checked independently, not taken from the report:

- Refs: `origin/main` = `d08085b0fd00...`, candidate = `1d0b895e6992...`,
  merge base `cbefabe7...`, divergence 255/5, the same five commits and
  13 files +3439/-316. Stage 0's 253 is confirmed in
  `existing-obsidian-vtt-work.md:27`. `git merge-tree` exits 1 with the
  `app.py` conflict as stated.
- Suites, run on fresh `git archive` copies of both commits (not Codex's
  checkouts), separate synthetic `DATA_DIR`, Node v24.12.0, Python 3.11.9:
  Obsidian suite main 39 passed / candidate 74 passed; route-policy suite
  main 18 passed / candidate 17 passed; 0 failed or skipped in any run.
- Codex's probe scripts were read first (fakes only, no network, vault or
  real tokens), then run against the same archive copies. All seven findings
  reproduce as written: F2 revision 8 to 4 / HP 30; F3 `[tle-s001, tle-s002]`
  to `[]`; F1 candidate keeps only `A-event` with cursor 1, while main keeps
  both; F4 101-character ID passes preflight then fails ownership; F5
  `valid:true` for `elite_weak:'invalid'` and launch empties encounter and
  events on both copies; F6 `legacy-s001` reused with HTTP 200; F7 HP 30 to
  23 to 16, sequences `[1,1]`, on both copies.
- Line references spot-checked in candidate source (16 in `main.js`, 5 in
  `app.py`, 7 in the two sync modules) and main (`services/obsidian_sync.py:150`,
  `:273`, `app.py:28954`, `:29378`); all point at the named code. Candidate
  has no `preflight_targets` call and no Cosmere phase reset, as stated.
- All 27 named test functions exist in the candidate test modules.
- `git diff --check` is clean; the untracked report has no trailing
  whitespace. Only the report, this task file (status, report, review) and
  queue bookkeeping changed.

Non-blocking note for the PE-60/61 briefs, not a finding: the PF2e list in
the report's "Limits" section is by category. The candidate party panel also
shows PF2e-only dying/wounded recovery DC and hero points
(`tools/obsidian-plugin/main.js:593-642`); name these when briefing the D&D
adapters.

This acceptance covers the documentation deliverable only. It does not
authorize merging, cherry-picking or installing the old v0.9 branch; ports
go through separately briefed PE-60 slices.
