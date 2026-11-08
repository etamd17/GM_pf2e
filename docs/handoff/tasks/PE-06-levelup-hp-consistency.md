# PE-06: Keep level-up HP previews correct when Constitution becomes zero

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked on PE-04 and PE-05
- **Branch:** `claude/pe-06-levelup-hp-consistency` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** [PE-04](PE-04-ui18-review.md), [PE-05](PE-05-ui11-reconcile.md).
- **Context:** [existing-work audit](../../player-release/existing-player-work.md), [UI-11](UI-11-pf2e-levelup-and-builder.md), `templates/player_levelup.html` functions `getTempFinalStats`, `_ceremonyHpDelta` and the Review renderer; `app.py::submit_levelup`.

## Goal

Review, Draft changes and the level-up result must agree when a Constitution
modifier changes from -1 to 0. Zero is a valid value, not a missing value.

## Approach

Reproduce the current defect with the real template functions and synthetic
character state before editing. The audited Review renderer contains
`conMod = tempStats.con || conMod`; the ceremony helper distinguishes missing
from zero. A documented fixture with old HP 40 and class HP 10 yields 49 in
Review and 50 from the ceremony helper. Confirm those inputs against the current
baseline rather than treating the example as a universal HP formula.

Make the smallest correction to missing-value handling. Reuse the existing
calculation contract; do not introduce a new rules engine or broaden this into
a class-progression rewrite. Check absent, negative, zero and positive modifier
cases, partial selections and repeated rendering. Verify resulting server
publication for the same synthetic legal transition without changing live data.
If server rules require broader changes, report them as a separate rules task
instead of expanding this patch silently.

Add focused regression coverage in `tests/test_levelup_hp_consistency.py` using
the actual renderer/helper code, plus the applicable level-up server test if
needed. Preserve stat snapshots unless a separate reviewed rules correction is
explicitly authorized. Keep UI-18 timing and UI-11 layout unchanged.

## Acceptance

- [ ] A failing regression reproduces the zero-value defect before the fix.
- [ ] Review and Draft changes/ceremony agree for the reproduced transition; missing data still has the intended fallback.
- [ ] Negative/zero/positive cases and partial selections do not mutate state or double-apply HP increases. The same synthetic valid server transition agrees.
- [ ] No unrelated rules, snapshot, persistence or presentation changes are included.
- [ ] Exact outcomes are recorded for:

```powershell
python -m pytest -q tests/test_levelup_hp_consistency.py tests/test_pf2e_builder_levelup_ui.py tests/test_levelup_engine.py tests/test_levelup_chain.py tests/test_pc_snapshots.py tests/test_pb_ground_truth.py --strict-markers
python -m pytest -q tests/browser/test_pf2e_build_preview.py tests/browser/test_levelup_picker_rail_browser.py -m browser --strict-markers
python tools/check_templates.py
git diff --check
```

## Out of scope / do not touch

- General PF2e kernel activation, class-matrix changes, draft persistence, level
  unlocks, D&D mechanics, live characters and the two preserved source worktrees.

## Report

Not started. Record reproduction, changed logic, exact checks/results and any
separate server-rule discrepancy. No passing checks are claimed by this brief.

## Review

Codex verifies the reproduced defect and acceptance list; record findings or
acceptance without treating the UI correction as a complete PF2e rules audit.
