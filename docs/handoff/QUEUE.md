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
| 5E-04 | Species versions as a builder choice (Draconic Ancestry, 2024 lineages) + fix raw text in sheet resistances | codex | standard | done (accepted by Claude) | `tasks/5E-04.md` |
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
| UI-02 | PF2e screens from UI-00 (12): briefs written as UI-06 to UI-12 | claude | Opus high | done (briefs) | see rows below |
| UI-06 | PF2e GM dashboard (B) + GM Screen (B). Holds the shared conventions for UI-06 to UI-12 | codex | standard, medium | done (accepted by Claude) | `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md` |
| UI-07 | PF2e generators (A) + loot ledger (B) | codex | standard, medium | done (accepted by Claude) | `tasks/UI-07-pf2e-generators-and-loot.md` |
| UI-08 | PF2e encounter tracker (B: combat first, setup folds away) | codex | strongest, high | done (accepted by Claude) | `tasks/UI-08-pf2e-tracker.md` |
| UI-09 | PF2e party overview (A+B) + campaign management (B tabs) + My table (A) | codex | standard, high | done (accepted by Claude) | `tasks/UI-09-pf2e-party-and-campaign-pages.md` |
| UI-10 | PF2e player sheet (A: compact header, CSS only) | codex | standard, high | ready | `tasks/UI-10-pf2e-player-sheet.md` |
| UI-17 | PF2e sheet: Inventory "Add" row runs off-screen on phones (CSS only; inventory scope hold still applies) | codex | standard, low | ready after UI-10 | `tasks/UI-17-pf2e-sheet-inventory-add-row.md` |
| UI-11 | PF2e level-up (B) + builder (B): roadmap and live preview | codex | strongest, high | ready | `tasks/UI-11-pf2e-levelup-and-builder.md` |
| UI-12 | PF2e map (B: map first, right-side tabs) | claude | Opus, high | waiting on user (after the VTT plan) | `tasks/UI-12-pf2e-map.md` |
| UI-13 | Cosmere GM dashboard (hybrid) + GM Screen (B) + 3 hub error-handling fixes | codex | standard, medium | ready | `tasks/UI-13-cosmere-dashboard-and-gmscreen.md` |
| UI-14 | Cosmere tracker (B: phase/turn first, phase band) + check the Archer's missing strikes | claude | Opus, high | accepted (Claude commits; CI pending) | `tasks/UI-14-cosmere-tracker.md` |
| UI-15 | Cosmere Party Vitals (B) + roster (A cards + attention summary) | codex | standard, high | ready | `tasks/UI-15-cosmere-vitals-and-roster.md` |
| UI-16 | Cosmere loot (B: form beside searchable history, draft worth from the server table) | codex | standard, medium | ready after UI-05 | `tasks/UI-16-cosmere-loot.md` |
| UI-03 | Fix the seven confirmed bugs from the UI review | codex | standard | done (accepted by Claude) | `tasks/UI-03.md` |
| UI-04 | Cosmere tracker: hide every PF2e-only control (user: each system stays separate) | codex | standard, high | done (accepted by Claude) | `tasks/UI-04-cosmere-tracker-separation.md` |
| UI-05 | Cosmere spheres: gem changes value (rulebook table); wallets keep gems | codex | strongest, high | done (accepted by Claude) | `tasks/UI-05-cosmere-gem-values.md` |

UI-03 bug list (from `tasks/UI-00.md` > Anything broken or risky; all checked on `main` at `4664c887`):

| Bug | Status |
|---|---|
| PF2e GM screen: the polish script looks for `.masonry`, so pins, search highlight and clickable conditions never run | fixed in UI-03 |
| Map: with no scene, the error toast crashes before it is set up | fixed in UI-03 |
| PF2e builder: the Review step goes blank (ancestry feat treated as text) and shows stale class/spell/skill fields | fixed in UI-03 |
| PF2e level-up: the gains panel can block clicks on the picker | fixed in UI-03 |
| Cosmere loot: deleting a log doesn't undo the credit; ledger and wallet can disagree; the form clears on errors | fixed in UI-03 |
| Cosmere loot: totals and wallets ignore which gem a sphere is | user: gems change value -> UI-05 |
| Cosmere roster/vitals: no live updates; max Investiture; vitals keyed by name with frozen maximums | fixed in UI-03; missing Mistborn bundle fallback added |
| Cosmere tracker shows PF2e-only controls (MAP/agile, PF2e quick conditions, Elite/Weak, persistent damage, basic saves) | user: hide all PF2e in Cosmere -> UI-04 |
| Campaign delete: the wording says "permanently delete" but it's moved to the trash | fixed in UI-03 |

## Cosmere: Mistborn

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| MB-00 | Mistborn rules backend (Era 1 and 2, Allomancy, Feruchemy, versioned rules packages, GM services) | codex | its strongest | done, reviewed by Claude (see below) | `GM_pf2e_cosmere_rules/docs/handoff/tasks/MB-00.md` |
| MB-01 | Bring MB-00 up to date with main, move the Mistborn data out of git (installed like 5e), make the full test run green, open a PR | codex | strongest model, high effort (history rewrite + test-failure hunt) | done (PR #182 merged; private bundle installed) | `tasks/MB-01.md` |
| MB-02 | Verify the 252 Mistborn records still blocked on source checks | claude | Opus, high | needs brief | - |
| MB-03 | Generators: make Mistborn content for Mistborn campaigns (found in UI-01) | codex | standard, medium | done (accepted by Claude) | `tasks/MB-03.md` |
| MB-04 | Mistborn money: boxings and clips in wallets and the loot page under the Mistborn skin (today they show Roshar spheres) | - | - | waiting on user (how Mistborn currency should work) | - |

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
| VTT-017 | Fix the session issue Codex flagged; review Codex's first contract batch; publish handoff 017 | claude | Opus, high | done (handoff `VTT-20261015-017`, client commits `44650296`, `97f1eef9`); waiting on Codex's reply | `GM_pf2e_vtt/docs/vtt/handoffs/VTT-20261015-017-claude.md` |
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

- 2026-10-16 Codex: accepted UI-14 after rechecking all four fixes; 40 focused tests pass and 107 templates parse, with PF2e inventory and system separation intact. Next: Claude commits, opens the PR and merges after every CI job is green.
- 2026-10-15 Claude: fixed the four UI-14 review items: the phase strip follows the turn, the initiative mode syncs to every open tracker, the Strike mapping is exact and custom adversaries load again, and the boundary tests are added. Focused run 143 passed. Next: Codex rechecks.
- 2026-10-15 Codex: reviewed UI-14; the scoped layout and Archer mapping direction are sound, but live phase and initiative-mode state go stale across tracker clients, the broad Strike predicate breaks legacy custom-adversary normalization, and tracker deduplication/setup transitions need boundary tests. Next: Claude fixes the four focused review items and returns UI-14 for recheck.
- 2026-10-15 Claude: built UI-14. The Cosmere tracker now puts the turn and phase controls first, with a four-phase strip (fast heroes, fast NPCs, slow heroes, slow NPCs) that follows the turn. Also fixed most bestiary adversaries showing no attacks (127 of 142 now list their strikes). Next: Codex reviews the diff.
- 2026-10-15 User/Claude: new split. Claude builds the heavy or high-risk code (rules, saved data, live sync, the shared tracker and map, the VTT) and Codex reviews it; Codex keeps UI ports and smaller fixes. UI-14, UI-12 and MB-02 move to Claude; UI-11 stays with Codex (already started). Added UI-17 (phone Inventory Add button). Next: Codex continues UI-11.
- 2026-10-15 Claude: checked UI-09 in the browser: the Party page's watch strip updates live and jumps to the character, campaign management's three tabs work by mouse, keyboard and link, and its notices stay visible; My Table only got styling. Accepted. Next: Codex commits and merges after CI, then UI-10 (player sheet).
- 2026-10-15 Codex: built UI-09's PF2e Party watch and compact cards, three-tab campaign management and compact My table while preserving every role/form/control contract and isolating `/party` by system; the 82-check focused suite passed, final corrections passed 12 UI and 10 route checks, and all 107 templates parse. Next: Claude reviews the diff and checks all three pages at 1440px and 375px using a throwaway campaign.
- 2026-10-15 Claude: checked UI-08 in the browser: the PF2e tracker now puts combat first, the setup panel opens and closes on its own as the fight starts and ends, keyboard shortcuts still work, and on a phone every row button is reachable by scrolling the table. Cosmere is unchanged. Accepted. Next: Codex commits and merges after CI, then UI-09.
- 2026-10-15 Codex: built UI-08's PF2e combat-first tracker, live empty/populated setup disclosure and focusable phone scroll region without changing Cosmere or D&D; 134 focused checks pass, 21 skip and all 107 templates parse. Next: Claude reviews the diff and checks 1440px and 375px in the browser.
- 2026-10-15 Claude: checked UI-07 in the browser: the PF2e generators (all 19 cards, pins, Rapid Fire, filters, reroll all) and loot page (form beside history, awards deposit to the sheet, delete only removes the log) work on desktop and phone. Accepted. Next: Codex deletes a stray test folder, commits, merges after CI, then UI-08.
- 2026-10-15 Codex: built UI-07 PF2e generators A and loot ledger B, preserved their mechanics and isolated PF2e routes from Cosmere and D&D; 257 focused tests pass and all 107 templates parse. Next: Claude reviews both pages and checks them at 1440px and 375px.
- 2026-10-15 Codex: fixed UI-06 multi-topic search so every match is visible in All and clearing restores the prior topic; all 55 focused tests and 107 template parses pass. Next: UI-07 after UI-06 CI and merge.
- 2026-10-15 Claude: checked UI-06 in the browser: the new GM dashboard (party status under the combat banner) and the GM Screen tabs, pins, condition links, Checks drawer and print all work on desktop and phone. One fix: searching a word found in several topics only shows the results in one of them. Next: Codex fixes search (show every match), then commits and merges after CI.
- 2026-10-15 Codex: built UI-06 dashboard B and PF2e GM Screen B; 55 focused tests pass and all 107 templates parse. Next: Claude reviews both pages and checks 1440px/375px in the browser.
- 2026-10-15 Claude: wrote briefs for the 6 approved Cosmere screens as UI-13 to UI-16 (dashboard + GM Screen, tracker, Vitals + roster, loot). Also added MB-04 (Mistborn money), which needs a user decision. Next: Codex builds them after the PF2e screens, tracker after UI-08 and loot after UI-05.
- 2026-10-15 Claude: checked UI-05 in the browser: gem values match the rulebook, an emerald broam is worth 200 marks, old wallets show as diamond without being rewritten, the sheet updates live, and deleting awards restores the wallet exactly (and can't be paid back with a different gem). Accepted, no fixes needed. Next: Codex commits, opens the PR and merges when CI is green, then starts UI-06.
- 2026-10-15 Codex: implemented UI-05's exact ten-gem values, version-2 per-gem wallets, legacy projection, gem-isolated reversal, server totals and live sheet repaint; 62 focused checks and 33 regressions pass, with all 107 templates parsing. Next: Claude reviews the diff and the award/delete flow in both Cosmere skins.
- 2026-10-15 Claude: checked UI-04 in the browser: a Cosmere fight now shows only Cosmere controls, conditions and stats (hero and adversaries), PF2e actions are refused for Cosmere targets, and a PF2e fight keeps all its controls. Accepted, no fixes needed. Next: Codex commits, opens the PR and merges when CI is green, then starts UI-05.
- 2026-10-15 Claude: VTT-017 done. Fixed the table-screen bug Codex found (an old message could keep a stale or revoked scene on screen) and built the tool that turns Codex's message definitions into client code. Codex's first batch has one blocker: a duplicate check that makes a big scene take almost a second to load (fix proposed as S5). Next: Codex answers handoff 017.
- 2026-10-15 Codex: separated Cosmere tracker controls, vocabulary, conditions and routes from PF2e; 28 required checks and 38 PF2e regressions pass, with all 107 templates parsing. Next: Claude reviews both systems in the browser at 1440px.
- 2026-10-15 Claude: wrote build briefs for the 12 approved PF2e screens (UI-06 to UI-12), grouped by page and ordered by risk; the map waits until the VTT plan is settled. Next: Codex works through them after UI-04 and UI-05; Claude writes the Cosmere screen briefs (UI-13) after those.
- 2026-10-15 User: decided that gems change a sphere's value, and that PF2e, Cosmere and D&D never share mechanics (hide all PF2e controls in the Cosmere tracker). Claude wrote briefs UI-04 (tracker) and UI-05 (gem values). Next: Codex takes UI-04, then UI-05.
- 2026-10-15 Codex: fixed the MB-03 loot casing and Era 2 name groups, seeded the route test, and passed all 17 focused checks; Claude's review is accepted. Next: open the PR and merge it after every CI job is green.
- 2026-10-15 Claude: checked MB-03 in the browser in both skins on desktop and phone: Mistborn campaigns get original Scadrial content on every card, Stormlight is unchanged, and all buttons work. One small fix: four loot lines lose their capital letters (e.g. "kredik shaw"). Next: Codex fixes that (plus two small extras), then commits, opens the PR and merges once CI is green.
- 2026-10-15 Codex: made all nine Cosmere generator cards switch to Mistborn content in Mistborn campaigns, with Era 2 when it is the campaign's only era; 42 checks and all 107 templates pass. Next: Claude reviews the writing and browser behavior in both skins.
- 2026-10-15 Claude: rechecked the UI-03 follow-up: a Mistborn character without the Mistborn data no longer breaks the roster or vitals, and other characters show and update normally. UI-03 accepted. Next: the user merges PR #189 once CI is green, except the known glossary test, which has its own fix.
- 2026-10-15 Codex: fixed UI-03's missing Mistborn rules fallback for the roster, vitals, and broadcasts; 24 focused tests pass. Next: open the PR for CI and Claude's final recheck.
- 2026-10-15 Claude: checked UI-03 in the browser: the GM screen pins, map error message, builder Review, level-up picker clicks, loot add/remove, live roster and vitals all work. One required fix: a Mistborn character on a server without the Mistborn data now breaks the whole roster and vitals page. Next: Codex adds a per-character fallback plus a test, then opens the PR.
- 2026-10-15 Codex: fixed all seven UI-03 bugs in seven local commits with regression tests; focused verification and template parsing passed. Next: Claude reviews the diff and completes the brief's browser checks; no push, merge, or deploy yet.
- 2026-10-15 Claude: wrote the MB-03 brief (Mistborn content in the generators, Era 1 by default, all text original). The UI-03 brief travels with Codex's UI-03 branch. Next: Codex takes MB-03 after UI-03.
- 2026-10-14 Claude: checked 5E-04 in the 5e builder: Dragonborn shows one card with a required Draconic Ancestry choice (Black gives acid resistance), 2024 Elf and Tiefling get their three versions, switching species clears the old answer, and old saved characters still work. Accepted, no fixes needed. Next: Codex commits and opens the PR; the user merges after CI is green.
- 2026-10-14 Codex: made all 24 data-defined species version groups explicit builder choices and fixed structured/fixed resistance handling; 29 required checks pass and all 107 templates parse. Next: Claude reviews the engine diff and clicks through Dragonborn, Elf and Tiefling in the builder.
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
