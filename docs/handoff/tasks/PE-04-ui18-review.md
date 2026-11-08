# PE-04: Review Claude's existing UI-18 picker-rail fix

- **Owner:** codex (independent reviewer of Claude's implementation)
- **Implementer responding to findings:** claude; Codex rechecks fixes.
- **Status:** ready; review only
- **Branch:** `codex/pe-04-ui18-review` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** none; DS-02 and DS-03 are merged.
- **Context:** [existing-work audit](../../player-release/existing-player-work.md), [handoff protocol](../PROTOCOL.md), local sibling `GM_pf2e_ui18/docs/handoff/tasks/UI-18-levelup-picker-rail.md`, [DS-03](DS-03-focus-ring-hover-gate.md).

## Goal

Determine whether the existing picker-rail fix is acceptable without losing
locked-choice explanations, keyboard focus, or the GM override control. Resolve
this patch before UI-11 reconciliation edits the same level-up template.

## Approach

Recheck `../GM_pf2e_ui18` HEAD, status and complete diff before reviewing. At
the Stage 0 audit it was based on `7aeb9549` with five dirty/new files:
`templates/player_levelup.html`, its UI-18 task, `docs/handoff/QUEUE.md`,
`tests/browser/test_levelup_picker_rail_browser.py` and a comment change in
`tests/browser/test_ds_focus_browser.py`. Read the source task's acceptance list.

Review CSS timing against the actual picker open/close classes, reserved 48px
band, armed/unarmed override, reduced motion, and focus/paint visibility.
Inspect the added tests and run the focused commands below against the source
patch with an isolated synthetic test environment. Do not use live party files
or point a test server at production data. Record the exact patch state reviewed.

Write findings here. Do not repair product code or tests during review. Claude
owns fixes in the existing implementation branch; rerun only affected checks
after changes. UI-11 must not be applied during this review. If accepted, the
implementer follows the handoff protocol to publish the accepted UI-18 patch;
acceptance is not a claim of merge or deployment.

## Acceptance

- [ ] Review covers every source-task acceptance item, including transition timing, GM override handler and keyboard behavior at 390x844, 390x600 and 1440x900.
- [ ] Existing changed/new files and exact HEAD/diff identity are recorded. Original source work is unchanged by review.
- [ ] Earlier report counts are labeled historical; `FULL_SUITE_RESULT` is not treated as a pass. Required full-suite/CI evidence is completed by the implementer or explicitly left unresolved before acceptance.
- [ ] Focused checks below have actual outcomes and exclusions recorded; no deployment or PostgreSQL result is invented.
- [ ] Numbered actionable findings or an explicit accepted verdict identify the reviewed patch. PE-05 remains blocked until the final accepted UI-18 outcome is integrated or explicitly superseded.

From `GM_pf2e_ui18`, using its project environment:

```powershell
git status --short --untracked-files=normal
git diff --check
python -m pytest -q tests/browser/test_levelup_picker_rail_browser.py tests/test_player_levelup_picker_layer.py --strict-markers
python -m pytest -q tests/browser/test_ds_focus_browser.py -k picker -m browser --strict-markers
python -m pytest -q tests/browser/test_ds_motion_browser.py -k 'picker or locked_row' -m browser --strict-markers
python tools/check_templates.py
```

`python` must be the configured project interpreter; on the audited Windows
workspace the existing environment is `../GM_pf2e/venv/Scripts/python.exe`.
Confirm it before running; this brief does not require modifying that environment.

## Out of scope / do not touch

- UI-11, HP math, new picker UX, product/test fixes, shared styles, data or deploys.
- Resetting, cleaning, stashing or overwriting another worktree.
- Turning known short-screen/long-reason follow-ups into an unbounded redesign.

## Report

Not started. Record patch identity, exact checks/results, unresolved acceptance
evidence and numbered findings. No acceptance is implied by this brief.

## Review

Claude responds to Codex's findings; Codex records final product acceptance.
