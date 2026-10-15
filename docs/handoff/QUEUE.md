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
| UI-02c | Build Cosmere builder / level-up (B: still-to-choose list, step badges, level-up badge) | codex | standard, high | done (accepted by Claude) | `tasks/UI-02c-cosmere-builder.md` |
| UI-02 | PF2e screens from UI-00 (18): briefs still to write | claude briefs, codex builds | briefs: Opus high | needs brief | - |
| UI-03 | Fix the seven confirmed bugs from the UI review | codex | standard | done (accepted by Claude) | `tasks/UI-03.md` |

UI-03 bug list (from `tasks/UI-00.md` > Anything broken or risky; all checked on `main` at `4664c887`):

| Bug | Status |
|---|---|
| PF2e GM screen: the polish script looks for `.masonry`, so pins, search highlight and clickable conditions never run | fixed in UI-03 |
| Map: with no scene, the error toast crashes before it is set up | fixed in UI-03 |
| PF2e builder: the Review step goes blank (ancestry feat treated as text) and shows stale class/spell/skill fields | fixed in UI-03 |
| PF2e level-up: the gains panel can block clicks on the picker | fixed in UI-03 |
| Cosmere loot: deleting a log doesn't undo the credit; ledger and wallet can disagree; the form clears on errors | fixed in UI-03 |
| Cosmere loot: totals and wallets ignore which gem a sphere is | **needs a user rules decision** |
| Cosmere roster/vitals: no live updates; max Investiture; vitals keyed by name with frozen maximums | fixed in UI-03; missing Mistborn bundle fallback added |
| Cosmere tracker shows PF2e-only controls (MAP/agile, PF2e quick conditions, Elite/Weak, persistent damage, basic saves) | **needs a user rules decision** |
| Campaign delete: the wording says "permanently delete" but it's moved to the trash | fixed in UI-03 |

## Cosmere: Mistborn

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| MB-00 | Mistborn rules backend (Era 1 and 2, Allomancy, Feruchemy, versioned rules packages, GM services) | codex | its strongest | done, reviewed by Claude (see below) | `GM_pf2e_cosmere_rules/docs/handoff/tasks/MB-00.md` |
| MB-01 | Bring MB-00 up to date with main, move the Mistborn data out of git (installed like 5e), make the full test run green, open a PR | codex | strongest model, high effort (history rewrite + test-failure hunt) | done (PR #182 merged; private bundle installed) | `tasks/MB-01.md` |
| MB-02 | Verify the 252 Mistborn records still blocked on source checks | codex | its strongest | needs brief | - |
| MB-03 | Generators: make Mistborn content for Mistborn campaigns (found in UI-01) | codex | standard, medium | ready | `tasks/MB-03.md` |

**MB-00 review (Claude, 2026-10-13):** accepted as a backend milestone, not ready to merge yet.
- 1024 Cosmere tests pass on the branch (623 new + 401 existing).
- Data is short rule summaries, not book text; 4.8 MB. It's a public repo, so the user decides
  whether it goes into git (see MB-01).
- Opening a sheet never rewrites the file; a character converts to the new format on its next save
  (tested for wallet and live state).
- Merging with main: one trivial conflict (`core/storage.py`).
- Not done yet: full test run is red (order-dependent), branch not pushed, 43 commits behind main.

## VTT (table-top map rebuild)

Decided 2026-10-14: the VTT lives **on the site**, not in Obsidian. It reuses the site's characters,
tracker, live sync and the existing map (`/map`, `/map/table`). Obsidian stays the prep and control
side through the Session Operations plugin.

This work runs on its **own channel**, not this queue: branch `side/vtt-rebuild`, handoffs in
`GM_pf2e_vtt/docs/vtt/handoffs/` (rules in `docs/vtt/README.md`), decisions in `docs/vtt/decisions/`
(ADR-0001 table-first, ADR-0002 wire protocol, ADR-0003 hosting). Codex owns `vtt/` and the backend,
Claude owns `frontend/vtt/`. The rows below only track where it stands.

| ID | Task | Owner | Model | Status | Where |
|---|---|---|---|---|---|
| VTT-017 | Fix the session issue Codex flagged in client commit `de880953`; review Codex's first contract batch (`c5653e05`); publish handoff 017 | claude | Opus, high | ready (idle since 2026-10-12) | `GM_pf2e_vtt/docs/vtt/handoffs/VTT-20261012-016-codex.md` |
| VTT-ALPHA | First usable milestone: GM workspace + shared table display talking to the new backend, on a Railway **staging** environment of the same project (ADR-0003). Production untouched until the user merges | codex + claude | strongest / Opus high | needs plan (after VTT-017) | ADR-0003 |
| VTT-PLAYERS | Per-player screens and per-player vision | - | - | deferred by the user (ADR-0001) | - |

Order: finish the Cosmere UI-02 screens first, then pick VTT back up at VTT-017. Creating the
Railway staging environment or spending money needs the user's approval at that time.

## UI-00 review (Claude, 2026-10-13)

Accepted as a design checkpoint. Notes:
- The commit that added it (`312138ed`) is on `remediation/pr6b3e1-legal-readiness`, whose PR
  (#174) is already merged, so it isn't headed to `main`. This file now lives here instead.
- The 22 MB mockup bundle should not go into git (public repo, large). `docs/handoff/assets/`
  is now gitignored; keep bundles local.
- Two of the bugs were checked on current `main` (marked above); the rest are unconfirmed.

## Log

Newest first. One plain line per handoff: who, what, what's next.

- 2026-10-15 Claude: rechecked the UI-03 follow-up: a Mistborn character without the Mistborn data no longer breaks the roster or vitals, and other characters show and update normally. UI-03 accepted. Next: the user merges PR #189 once CI is green, except the known glossary test, which has its own fix.
- 2026-10-15 Codex: fixed UI-03's missing Mistborn rules fallback for the roster, vitals, and broadcasts; 24 focused tests pass. Next: open the PR for CI and Claude's final recheck.
- 2026-10-15 Claude: checked UI-03 in the browser: the GM screen pins, map error message, builder Review, level-up picker clicks, loot add/remove, live roster and vitals all work. One required fix: a Mistborn character on a server without the Mistborn data now breaks the whole roster and vitals page. Next: Codex adds a per-character fallback plus a test, then opens the PR.
- 2026-10-15 Codex: fixed all seven UI-03 bugs in seven local commits with regression tests; focused verification and template parsing passed. Next: Claude reviews the diff and completes the brief's browser checks; no push, merge, or deploy yet.
- 2026-10-15 Claude: wrote the MB-03 brief (Mistborn content in the generators, Era 1 by default, all text original). The UI-03 brief travels with Codex's UI-03 branch. Next: Codex takes MB-03 after UI-03.
- 2026-10-14 Claude: checked the new Cosmere builder / level-up (UI-02c) on desktop and phone in both skins: level badge, to-do list, Go buttons, step ticks, sticky layout and the PF2e draft bar all work. Accepted, no fixes needed. Next: Codex commits this review and opens the PR; the user merges.
- 2026-10-14 Codex: built the Cosmere level-up guide and compact draft bar; 399 focused checks and all 32 browser checks passed. Next: Claude reviews level-up and scrolling on desktop and phone.
- 2026-10-14 Claude: added the VTT to this list. Decision: build it on the site (not Obsidian). It continues on its own branch and handoff files; next step is VTT-017 (Claude), after the Cosmere screens.
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
