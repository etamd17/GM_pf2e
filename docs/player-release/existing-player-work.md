# Existing player work: Stage 0 audit

Read-only audit on November 8, 2026, against `origin/main` at
`7aeb9549c1df2807935a4a44229284e13a5add1d`. Remote main was separately checked
and matched. This is repository evidence, not production verification. No tests
were run for this audit; test counts quoted below are earlier implementer reports.

The approved [plan](implementation-plan.md) and [decisions](decisions.md) now
govern priorities. Local evidence locations below are sibling worktree names in
the shared workspace, not portable repository links. Preserve their files.

## Work that needs reconciliation

| Local worktree / branch | Observed state | Treatment |
|---|---|---|
| `GM_pf2e_ui06_stage`, `codex/ui-11-pf2e-levelup-builder` | HEAD `7bfa259a`; 104 commits behind main, zero ahead. Five tracked files modified (228 additions, 25 deletions), three new test/fixture files. | UI-11 has substantial uncommitted implementation. Review and selectively port it through [PE-05](../handoff/tasks/PE-05-ui11-reconcile.md), after UI-18 is resolved. Do not restart it from the queue's stale `ready` label. |
| `GM_pf2e_ui18`, `claude/ui-18-levelup-locked-reason` | HEAD equals main. Five modified/new intent-to-add files (305 additions, 4 deletions). | Review the existing Claude change through [PE-04](../handoff/tasks/PE-04-ui18-review.md). It overlaps UI-11 in `templates/player_levelup.html`. |
| `GM_pf2e_design_brief`, `docs/ds-03-brief` | HEAD `7ccf891a`; five behind, zero ahead; tracked files clean. Untracked `docs/design/HANDOFF_design_refresh.md` and `docs/design/palette-q1/`. | Keep palette comparison evidence. The untracked handoff's claims that DS-00/01 are unmerged are stale; use current main and the task reports. |

UI-11's local [task file](../handoff/tasks/UI-11-pf2e-levelup-and-builder.md)
counterpart contains a report not yet present in main: read-only draft previews,
numbered navigation, builder progress and responsive presentation. It reports
417 focused/regression passes and 36 browser passes; visual acceptance is still
pending. It explicitly says level-up had no private saved drafts and adds none.
The new local files are `tests/test_pf2e_builder_levelup_ui.py`,
`tests/browser/test_pf2e_build_preview.py`, and
`tests/fixtures/pf2e_builder_levelup_ui_inventory.json`.

UI-18's local `docs/handoff/tasks/UI-18-levelup-picker-rail.md` records a CSS
fix that stops the gains rail covering the picker, while keeping GM override
reachable. It reports 15 focused browser/layer passes, six related focus passes
and two motion passes. The full-suite result still reads `FULL_SUITE_RESULT`;
that acceptance box is unticked. Linux, PostgreSQL and Railway checks are not
claimed. No review acceptance is recorded. The task file, template and tests
must remain together when the patch is eventually integrated.

UI-11 reports a pre-existing HP preview inconsistency: a CON change from -1 to
0 is discarded by `tempStats.con || conMod` in Review, while the ceremony helper
accepts zero. That expression remains in current main. Isolate its correction
in [PE-06](../handoff/tasks/PE-06-levelup-hp-consistency.md); do not mix it into
the presentation reconciliation.

## Merged work and misleading branch names

| Evidence | Finding / treatment |
|---|---|
| `GM_pf2e_claude_5e`, HEAD `ac9b10cd`, 98 behind / zero ahead | Tracked clean; only `.tmp_review_5e07/` remains untracked. 5E-07 is integrated in main as `17fcd71f`. Reuse main's fixes and [report](../handoff/tasks/5E-07-rehearsal.md), not old scratch artifacts. |
| `feature/dnd5e` tip `8943c742`; `codex/5e-04` tip `1213076a` | Both ancestors of main; no unmatched commits in `git cherry`. Existing builders, sheets, imports and combat are reusable foundations. |
| DS-00 `202d6eec`, DS-01 `3492d6c9`, DS-02 `435507d4` | All ancestors of main, empty `git cherry`. Reuse the inventory tool, tokens and motion foundations. |
| Main HEAD merges PR #242 | DS-03 is merged, despite the committed queue's old browser-CI-blocker label. |
| `GM_pf2e_pr6a` / `2576515d`; `GM_pf2e_pr6b1` / `31b8de0f`; `GM_pf2e_pr6b2` / `da240d77` | Clean worktrees, all ancestors of main. These are integrated rules foundations/evidence gates, not missing full rules implementations. |
| `GM_pf2e_pr6b3a`, HEAD `1d98710e` | Now a completed 5E-06 worktree, not active PR6B3A work. Preserve its two untracked campaign-Markdown-workspace plan/spec files; park new wiki implementation. |
| `GM_pf2e_cosmere_rules` / `7d5b4c7a`; `GM_pf2e_dnd5e` / `de1e7720`; `GM_pf2e_claude_docs` / `2692f4f7` | Clean, all ancestors of main. No pending tracked implementation observed. |

Two unmatched historical commits need interpretation, not blind merging:

- `remediation/pr6b3e1-legal-readiness` tip `312138ed` adds an old UI-00 ZIP
  checkpoint and handoff, not additional rules mechanics. Main has the later
  unified UI-00 handoff (`b9058145`). Preserve the bundle as historical evidence.
- `feature/cosmere-rules-kernel` tip `4d3c6d85` is an early large backend snapshot.
  Main contains the later private-bundle integration (`bd250028`) and MB fixes.
  Preserve the branch; do not merge its older implementation or bundled rules
  data wholesale. New Cosmere/Mistborn expansion is outside this first release.

## Dependencies the new briefs must retain

- **5E-03 and 5E-05:** current main still says database ownership and private
  drafts need briefs. Absorb these into the player-release state/draft work;
  do not create a competing D&D identity subsystem.
- **Character access:** PF2e's live cache must be removed from the relevant
  character reads/writes before relaxing the live-campaign guard. Existing
  SQL identity work does not already store all mutable game state.
- **Rules coverage:** [PR6B3E1](../remediation/pr6b3e1-class-legal-readiness.md)
  records zero enabled mechanics in the new package and pending class reviews.
  This does not mean the legacy app has no working rules. [PE-02](../handoff/tasks/PE-02-rules-coverage.md)
  must distinguish implemented behavior, tested behavior and reviewed package
  coverage for PF2e, D&D 2014 and D&D 2024.
- **Shared-file order:** PE-04 resolves UI-18, PE-05 reconciles UI-11, then PE-06
  fixes the HP projection. One task owns `player_levelup.html` at a time.
- **Design:** reuse current tokens and accepted presentation work. [PE-07](../handoff/tasks/PE-07-player-ux-design.md)
  designs the integrated player journeys without reopening settled product scope.

## Reproducing the audit

Run from a full checkout of this repository; replace the sibling name only with
one listed above. These commands inspect state without changing it:

```powershell
git rev-parse origin/main
git worktree list --porcelain
git -C ../GM_pf2e_ui18 status --short --untracked-files=normal
git -C ../GM_pf2e_ui18 diff --stat
git -C ../GM_pf2e_ui18 diff --cached --stat
git -C ../GM_pf2e_ui18 rev-list --left-right --count origin/main...HEAD
git -C ../GM_pf2e_ui18 merge-base --is-ancestor HEAD origin/main
git cherry origin/main claude/ds-01-tokens
```

An ancestor HEAD says nothing about uncommitted work. Recheck status, diff and
task reports before assigning or integrating a task. Do not reset, clean, stash,
checkout over, or delete another task's worktree. No source worktree was modified
or stopped by this audit.
