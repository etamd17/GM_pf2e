# PE-03: Reconcile the existing Obsidian v0.9 implementation

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex
- **Reviewer:** claude
- **Status:** ready
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

## Review
