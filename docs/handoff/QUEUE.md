# Task queue

One shared list for Claude and Codex. Protocol: `docs/handoff/PROTOCOL.md`.

- **Status:** `needs brief` / `ready` / `in-progress` / `review` / `done` / `waiting on user`.
- **Owner** is the default tool; either may take a `ready` task. A task is `ready` only once
  its brief exists in `tasks/`.
- **Model column** = suggested setting for that step (high effort only where mistakes are costly).
- When you finish a task: write the report in its task file, update its row here, and add a
  plain one-line entry to **Log** at the bottom.

## D&D 5e

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| 5E-04 | Species versions as a builder choice (Draconic Ancestry, 2024 lineages) + fix raw text in sheet resistances | codex | standard | ready | `tasks/5E-04.md` |
| 5E-06 | Delete combatant pictures nothing uses | codex | standard | done (PR #180 merged after CI) | `tasks/5E-06.md` |
| 5E-03 | Database ownership for 5e characters (only needed before the site uses PostgreSQL for the web app) | claude | Opus, high | needs brief | - |
| 5E-05 | Private drafts for 5e characters | claude | Opus, high | needs brief (after 5E-03) | - |

Done: 5E-00 data stays out of git; 5E-01 merged as PR #175; 5E-02 data installed on Railway,
checked live, and survived a redeploy (2026-10-13).

## UI/UX (PF2e + Cosmere)

Approved directions for 18 screens are recorded in `tasks/UI-00.md` (Codex's design review; the
mockups are in the local-only bundle `assets/UI-00-review.zip`). Nothing has been built yet.
UI/UX design is Claude's by default; Codex implements mechanical parts from Claude's briefs.

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| UI-00 | Design review checkpoint: 18 screens approved as mockups | codex | - | done (review: accepted, see below) | `tasks/UI-00.md` |
| UI-01 | Finish the design review: Cosmere generators, then Cosmere sheet, level-up and builder. Mockups first; the user picks a direction for each | claude | Opus, medium-high | done (generators A, sheet A, builder B) | `tasks/UI-01.md` |
| UI-02a | Build Cosmere generators (A: labelled buttons) | codex | standard, medium | done (PR #183 merged) | `tasks/UI-02a-cosmere-generators.md` |
| UI-02b | Build Cosmere character sheet (A: vitals band + Play/Radiant/Gear/Character tabs) | codex | strongest, high | done (accepted by Claude) | `tasks/UI-02b-cosmere-sheet.md` |
| UI-02c | Build Cosmere builder / level-up (B: still-to-choose list, step badges, level-up badge) | codex | standard, high | ready | `tasks/UI-02c-cosmere-builder.md` |
| UI-02 | PF2e screens from UI-00 (18): briefs still to write | claude briefs, codex builds | briefs: Opus high | needs brief | - |
| UI-03 | Fix the real bugs the review found. Check each on current `main` first, then fix it with a test | codex | standard | needs brief | - |

UI-03 bug list (from `tasks/UI-00.md` > Anything broken or risky):

| Bug | Checked on current `main`? |
|---|---|
| PF2e GM screen: the polish script looks for `.masonry`, which no longer exists, so it never runs | **yes, real** |
| Map: with no scene, an error message can crash before the message system is set up | **yes, real (read in the code)** |
| PF2e builder: ancestry feat treated as text in the review step | not yet |
| PF2e level-up: the gains panel can block clicks on the picker | not yet |
| Cosmere loot: totals ignore gems; deleting a log doesn't undo the credit; failed saves can clear the form | not yet |
| Cosmere roster/vitals: no live updates; old maximums | not yet |
| Cosmere tracker shows PF2e-only controls (rules question for the user, not a plain bug) | not yet |
| Campaign delete: the wording says "delete" but it's moved to the trash | not yet |

## Cosmere: Mistborn

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| MB-00 | Mistborn rules backend (Era 1 and 2, Allomancy, Feruchemy, versioned rules packages, GM services) | codex | its strongest | done, reviewed by Claude (see below) | `GM_pf2e_cosmere_rules/docs/handoff/tasks/MB-00.md` |
| MB-01 | Bring MB-00 up to date with main, move the Mistborn data out of git (installed like 5e), make the full test run green, open a PR | codex | strongest model, high effort (history rewrite + test-failure hunt) | done (PR #182 merged; private bundle installed) | `tasks/MB-01.md` |
| MB-02 | Verify the 252 Mistborn records still blocked on source checks | codex | its strongest | needs brief | - |
| MB-03 | Generators: make Mistborn content for Mistborn campaigns (found in UI-01) | codex | standard | needs brief | - |

**MB-00 review (Claude, 2026-10-13):** accepted as a backend milestone, not ready to merge yet.
- 1024 Cosmere tests pass on the branch (623 new + 401 existing).
- Data is short rule summaries, not book text; 4.8 MB. It's a public repo, so the user decides
  whether it goes into git (see MB-01).
- Opening a sheet never rewrites the file; a character converts to the new format on its next save
  (tested for wallet and live state).
- Merging with main: one trivial conflict (`core/storage.py`).
- Not done yet: full test run is red (order-dependent), branch not pushed, 43 commits behind main.

## UI-00 review (Claude, 2026-10-13)

Accepted as a design checkpoint. Notes:
- The commit that added it (`312138ed`) is on `remediation/pr6b3e1-legal-readiness`, whose PR
  (#174) is already merged, so it isn't headed to `main`. This file now lives here instead.
- The 22 MB mockup bundle should not go into git (public repo, large). `docs/handoff/assets/`
  is now gitignored; keep bundles local.
- Two of the bugs were checked on current `main` (marked above); the rest are unconfirmed.

## Log

Newest first. One plain line per handoff: who, what, what's next.

- 2026-10-14 Claude: rechecked the monster/NPC sheet fix for UI-02b: no empty tabs now. The new Cosmere character sheet is accepted. Next: Codex opens the PR; after it merges, UI-02c (builder).
- 2026-10-14 Codex: removed the empty tabs from monster/NPC sheets; all 59 focused checks pass and player sheets are unchanged. Next: Claude reviews the small fix.
- 2026-10-14 Claude: checked the new Cosmere character sheet (UI-02b) on desktop and phone in both skins: tabs, live HP/conditions, wallet, notes, workshop and print all work. One small fix: monster/NPC sheets show two empty tabs. Next: Codex fixes that, then commits.
- 2026-10-14 Codex: built the approved Cosmere sheet tabs and vitals layout; all 441 checks pass and existing controls are preserved. Next: Claude reviews the changes and checks desktop, phone, live updates and printing in both skins.
- 2026-10-14 User: merged Mistborn backend PR #182 and generator PR #183; the 11-package, 695-record private Mistborn bundle is installed and Stormlight is unchanged. Next: UI-02b.
- 2026-10-14 Claude: checked the new Cosmere generators page (UI-02a) on desktop and phone in both skins: everything works and fits. Accepted, no fixes needed. Next: Codex commits and opens the PR; the user merges.
- 2026-10-14 Codex: built UI-02a labelled Cosmere generator controls; 24 focused tests pass and all 106 templates parse. Next: Claude reviews the diff and checks both skins at 1440px and 375px.
- 2026-10-14 Codex: PR #182's final full CI passed (4,812 standard tests plus browser and PostgreSQL); it is ready for the user to merge. Next: merge #182 before UI-02a / PR #183.
- 2026-10-14 Claude: reviewed MB-01 / PR #182 (safe: private rules stay out of GitHub, installer validated, Stormlight unaffected; 3 small notes); waiting on one full test run before merge. Wrote build briefs for the 3 Cosmere screens (UI-02a/b/c). Next: Codex builds UI-02a and UI-02b; UI-02c after #182 merges.
- 2026-10-14 Codex: put the Cosmere backend on current main, kept book-derived data out of Git, and opened draft PR #182. Focused checks passed; the long CI check timed out. Next: Claude reviews it, then one full check before any merge.
- 2026-10-13 Codex: merged 5E-06 as PR #180 after all CI jobs passed; the public site health checks returned ready. Next: continue the remaining UI and Mistborn tasks.
- 2026-10-13 Claude: reviewed Codex's Mistborn backend (MB-00): solid, 1024 tests pass, one easy merge conflict. User decided the Mistborn data stays out of git (like 5e). Next: Codex does MB-01.
- 2026-10-13 Claude: Cosmere builder/level-up design picked (B: a 'still to choose' list and level-up badge). Design review for all screens is now complete. Next: Claude writes build briefs (UI-02) so Codex can build the approved screens.
- 2026-10-13 Claude: Cosmere character sheet design picked (A: vitals band on top + Play/Radiant/Gear/Character tabs). Next: Cosmere builder / level-up mockups.
- 2026-10-13 Claude: Cosmere generators design picked (A: labelled buttons, like PF2e). Also noticed the generators make Stormlight content even in Mistborn mode (for the Mistborn task). Next: Cosmere character sheet mockups.
- 2026-10-13 Claude: checked Codex's fix for 5E-06; one stuck file no longer blocks the cleanup. Accepted. Next: the user decides when to push and open a PR.
- 2026-10-13 Codex: fixed cleanup so one locked picture does not block the others; 41 checks passed and 5E-06 is committed locally. Next: user decides if and when to merge; no push or deployment.
- 2026-10-13 Claude: reviewed 5E-06 (picture cleanup). It never deletes a picture in use; 40 tests pass. One small fix: a single locked file currently stops the whole cleanup. Next: Codex makes that fix, adds a test, commits locally.
- 2026-10-13 Codex: finished unused-picture cleanup; 22 checks passed and portraits/shared/recent pictures are protected. Next: Claude reviews deletion safety; the changes are local, and the wider checks need browser setup.
- 2026-10-13 Claude: merged UI-00 into this queue; added the UI/UX and Mistborn sections. Next: UI-01 (Claude), 5E-04/5E-06 (Codex), MB-00 (Codex).
- 2026-10-13 Codex: wrote UI-00, the design review checkpoint (18 screens approved as mockups).
- 2026-10-13 Claude: 5e merged (PR #175), installed on Railway and checked live.
