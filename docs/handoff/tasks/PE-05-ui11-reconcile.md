# PE-05: Reconcile the existing UI-11 builder and level-up presentation

- **Owner:** codex
- **Reviewer:** claude, including browser/visual acceptance
- **Status:** blocked on PE-04
- **Branch:** `codex/pe-05-ui11-reconcile` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** [PE-04](PE-04-ui18-review.md); do not race its template changes.
- **Context:** [UI-11 brief](UI-11-pf2e-levelup-and-builder.md), [existing-work audit](../../player-release/existing-player-work.md), local sibling `GM_pf2e_ui06_stage`, [approved plan](../../player-release/implementation-plan.md).

## Goal

Reuse the completed-but-uncommitted UI-11 presentation work on the current
baseline, preserving the accepted picker fix and all existing workflow behavior.
This is a presentation integration, not the new authoritative advancement system.

## Approach

Read the local UI-11 report and inspect the entire dirty diff, including its
three untracked tests/fixture. Record source HEAD and diff/file hashes before
porting. Leave the source worktree untouched. Do not copy whole old templates
over current main or carry its queue history wholesale.

Selectively port builder progress/navigation/preview and level-up roadmap/draft
preview into `templates/player_builder.html` and `templates/player_levelup.html`.
Keep current stylesheet links, DS tokens, UI-18 picker timing/band/override,
handlers, IDs and draft contracts. Previews remain read-only projections of
existing helpers: no new math or writes to `state`, drafts or server state.

Port and reconcile `tests/test_pf2e_builder_levelup_ui.py`,
`tests/browser/test_pf2e_build_preview.py`,
`tests/fixtures/pf2e_builder_levelup_ui_inventory.json`, and the scoped change
to `tests/browser/test_character_workflows.py`. Compare any behavior inventory
against the current pre-change baseline: do not replace expectations simply to
make removed controls pass. Document legitimate additions and source provenance.

The existing generated inventory fixture is about 2,262 lines (37 KB), in
addition to roughly 224 changed tracked lines, 197 new unit-test lines and 59
browser-test lines. Treat its deterministic capture as a separately reported
generated artifact: record the generator/inputs/hash and a semantic inventory
diff. The roughly 500-line target applies to human-reviewed source/test changes;
if those exceed it, split the builder and level-up ports into PE-05a/05b before
implementation. A fixture refresh must never hide a removed or changed control.

Keep the known zero-CON HP discrepancy visible in the report and linked to
[PE-06](PE-06-levelup-hp-consistency.md). Do not fix or disguise it here. The old
UI-11 report confirms this work does not add private saved level-up drafts.

## Acceptance

- [ ] Source worktree and its dirty/untracked files remain intact; report lists what was reused, omitted or adapted.
- [ ] Approved UI-11 directions work at 1440px and 375px; tablet sizing and keyboard navigation remain usable. Claude verifies against the existing B mockups.
- [ ] Preview values reflect existing helpers, escape names, update on choices/resume, and do not mutate build or draft state.
- [ ] UI-18's picker/override checks still pass; builder private-draft controls and legacy publication behave as before.
- [ ] No stat snapshots or ground-truth expectations are rewritten for presentation differences; the zero-CON issue is tracked separately.
- [ ] The following checks have actual results in the report, with skips explained:

```powershell
python tools/check_templates.py
python -m pytest -q tests/test_pf2e_builder_levelup_ui.py tests/test_player_levelup_picker_layer.py tests/test_inline_handler_escaping.py tests/test_pc_snapshots.py tests/test_pb_ground_truth.py tests/test_levelup_chain.py tests/character_workflows --strict-markers
python -m pytest -q tests/browser/test_pf2e_build_preview.py tests/browser/test_character_workflows.py tests/browser/test_levelup_picker_rail_browser.py -m browser --strict-markers
git diff --check
```

## Out of scope / do not touch

- Rules, HP/boost math, feat eligibility, persistence, authorization, new saved
  level-up drafts, D&D/Cosmere behavior, or the integrated Play redesign.
- Original worktree files, original private fixture data and unrelated queue
  history. Coordinate the normal task-status change with the shared queue owner.

## Report

Not started. Record source/current commit IDs, reconciliation decisions, exact
checks/results, visual evidence, known zero-CON discrepancy and limitations.

## Review

Claude records numbered findings or acceptance. PE-06 begins only after this
template integration is resolved and incorporated into its baseline.
