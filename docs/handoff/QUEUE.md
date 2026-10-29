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
| 5E-07 | Monday rehearsal: D&D Beyond PDF Paladin/Warlock, all classes to 20, GM encounter flow; fixes | claude | Opus, high | done (PR #206, merges after CI) | `tasks/5E-07-rehearsal.md` |
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
| UI-10 | PF2e player sheet (A: compact header, CSS only) | codex | standard, high | done (accepted by Claude) | `tasks/UI-10-pf2e-player-sheet.md` |
| UI-17 | PF2e sheet: Inventory "Add" row runs off-screen on phones (CSS only; inventory scope hold still applies) | codex | standard, low | done (PR #207 merged after CI) | `tasks/UI-17-pf2e-sheet-inventory-add-row.md` |
| UI-11 | PF2e level-up (B) + builder (B): roadmap and live preview | codex | strongest, high | ready | `tasks/UI-11-pf2e-levelup-and-builder.md` |
| UI-12 | PF2e map (B: map first, right-side tabs) | claude | Opus, high | waiting on user: the VTT plan chose A, which replaces `/map` at M5. Is a redesign of the old map still wanted before then? | `tasks/UI-12-pf2e-map.md` |
| UI-13 | Cosmere GM dashboard (hybrid) + GM Screen (B) + 3 hub error-handling fixes | codex | standard, medium | ready | `tasks/UI-13-cosmere-dashboard-and-gmscreen.md` |
| UI-14 | Cosmere tracker (B: phase/turn first, phase band) + check the Archer's missing strikes | claude | Opus, high | done (PR #205 merged after CI) | `tasks/UI-14-cosmere-tracker.md` |
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

## Design system refresh (DS)

Plan: `docs/design/DESIGN_DIRECTION.md` (2026-10-28). It applies seven outside design resources
(beUI, Bencho, ui-layouts, SuperDesign, better-design, design-taste, motion-primitives) without
changing any behaviour: ids, handlers, `data-*`, ARIA, form names, SSE and server rules stay as
they are. 68 packages (DS-00 to DS-31, split into lettered PRs) are listed in its section 10, with
owner, dependencies and acceptance. Each one gets a brief in `tasks/DS-xx-<slug>.md` before work
starts. UI-11, UI-13, UI-15 and UI-16 above stay `ready` and do not wait for this. The user
answered its five questions on 2026-10-28 (section 12); PF2e goes back to the warm gilt palette.

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| DS-00 | Inventory diff tool: ids, data-*, handlers, ARIA, frozen class strings, before/after per route | claude | Opus, high | needs brief | - |
| DS-01 | Tokens, `ds-tokens.css`, empty `ds.css`, notices file, contrast tool | claude | Opus, high | needs brief (after DS-00) | - |
| DS-02..DS-31 | The rest of section 10, in dependency order; nothing starts until DS-00 and DS-01 are merged | see plan | see plan | needs brief | - |

## CI maintenance

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| CI-01 | Run PF2e rules tests once in CI, keeping the same coverage | codex | standard | accepted (Claude); publish after green CI | `tasks/CI-01-pf2e-once.md` |

## Cosmere: Mistborn

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| MB-00 | Mistborn rules backend (Era 1 and 2, Allomancy, Feruchemy, versioned rules packages, GM services) | codex | its strongest | done, reviewed by Claude (see below) | `GM_pf2e_cosmere_rules/docs/handoff/tasks/MB-00.md` |
| MB-01 | Bring MB-00 up to date with main, move the Mistborn data out of git (installed like 5e), make the full test run green, open a PR | codex | strongest model, high effort (history rewrite + test-failure hunt) | done (PR #182 merged; private bundle installed) | `tasks/MB-01.md` |
| MB-02 | Verify the 252 Mistborn records still blocked on source checks | codex | strongest, high | done (accepted; private install is the user's step) | `tasks/MB-02.md` |
| MB-03 | Generators: make Mistborn content for Mistborn campaigns (found in UI-01) | codex | standard, medium | done (accepted by Claude) | `tasks/MB-03.md` |
| MB-04 | Mistborn money: boxings and clips in wallets and the loot page under the Mistborn skin (one fixed rate, both eras; Era 2 notes are boxings) | codex | standard, high | done (accepted by Claude) | `tasks/MB-04.md` |
| MB-05 | Mistborn skin bleed: sheet prompt and Workshop, nav tooltips, player-nav text-node error | codex | standard, high | done (PR #224 merged and deployed) | `tasks/MB-05.md` |
| MB-06 | Mistborn browser pass across both eras (desktop/phone, GM/player), plus recording the user's Outlaw Boss knife ruling (GM decides the damage type) in the private bundle | codex | standard, high | done (PR #232 merged and deployed) | `tasks/MB-06.md` |
| MB-07 | Turn on the real Mistborn rules per campaign: GM rules switch, every Cosmere page working for Mistborn characters, Mistborn builder, era bestiary + tracker, sheet burn/store/tap | claude (Codex reviews) | Opus, high | done (PR #227 merged and deployed) | `tasks/MB-07.md` |
| MB-08 | Power talent trees require the power (16 talents with only runtime prerequisites were open to anyone) | claude (Codex reviews) | Opus, high | done (PR #228 merged and deployed) | `tasks/MB-08.md` |
| MB-09 | Feruchemy Store/Tap Stop is free and does not consume the named-action slot | claude (Codex reviews) | standard, high | done (PR #229 merged and deployed) | `tasks/MB-09.md` |
| MB-10 | Draft labels, successful publish navigation and inline metalmind Charges | claude (Codex reviews) | standard, high | done (PR #230 merged and deployed) | `tasks/MB-10.md` |
| MB-11 | Load the Cosmere rules once: 8x faster, at boot instead of on the first page, and an install hands over its verified registry. Admin page names the installed release; `/api/campaign` refuses rules fields; bundles may hold 64 files | claude (Codex reviews) | Opus, high | done (PR #231 merged and deployed; production boot log: 17 package versions in 1.3s) | `tasks/MB-11.md` |

**MB-00 review (Claude, 2026-10-13):** accepted as a backend milestone, not ready to merge yet.
- 1024 Cosmere tests pass on the branch (623 new + 401 existing).
- Data is short rule summaries, not book text; 4.8 MB. It's a public repo, so the user decides
  whether it goes into git (see MB-01).
- Opening a sheet never rewrites the file; a character converts to the new format on its next save
  (tested for wallet and live state).
- Merging with main: one trivial conflict (`core/storage.py`).
- Not done yet: full test run is red (order-dependent), branch not pushed, 43 commits behind main.

## VTT (table-top map rebuild)

Decided 2026-10-14: the VTT lives **on the site**, not in Obsidian. It shares the site's accounts,
campaigns and characters. Obsidian stays the prep and control side through the Session Operations
plugin.

Decided 2026-10-16 (plan `GM_pf2e_vtt/docs/vtt/design/vtt-alpha-plan.md`): the user chose
**option A**, the full alpha as the ADRs describe, over Claude's recommendation (B, keep `/map`).
- Milestones:
  - M0: contracts;
  - M1: backend read path;
  - M2: commands;
  - M3: table pairing;
  - M4: client transport and GM tools;
  - M5: import legacy scenes and replace `/map`;
  - M6: Docker, CI and Playwright;
  - M7: Railway staging, which needs the user's approval and spend at the time.
- `/map` stays the live product until M5.

This work runs on its **own channel**, not this queue: branch `side/vtt-rebuild`, handoffs in
`GM_pf2e_vtt/docs/vtt/handoffs/` (rules in `docs/vtt/README.md`), decisions in `docs/vtt/decisions/`
(ADR-0001 table-first, ADR-0002 wire protocol, ADR-0003 hosting). Under the 2026-10-15 split Claude
builds all of it, backend included, and Codex reviews each milestone (handoff 018). The rows below
only track where it stands.

| ID | Task | Owner | Model | Status | Where |
|---|---|---|---|---|---|
| VTT-017 | Fix the session issue Codex flagged; review Codex's first contract batch; publish handoff 017 | claude | Opus, high | done (handoff `VTT-20261015-017`, client commits `44650296`, `97f1eef9`); waiting on Codex's reply | `GM_pf2e_vtt/docs/vtt/handoffs/VTT-20261015-017-claude.md` |
| VTT-ALPHA | First usable milestone: GM workspace + shared table display talking to the new backend, on a Railway **staging** environment of the same project (ADR-0003). Production untouched until the user merges | claude (Codex reviews) | Opus, high | in progress: M0 accepted (handoff 022); M1 accepted (handoff 027); M2 in review (handoff 029) | `GM_pf2e_vtt/docs/vtt/design/vtt-alpha-plan.md` |
| MAP-R2 | Live `/map`: prep scenes leak to the player event stream (`_broadcast_scene` sends every saved scene's player view, not only the active one) | claude (Codex reviews) | Opus, high | done (accepted by Codex; merge after green CI) | `tasks/MAP-R2-scene-frames-gm-only.md` |
| MAP-R3 | Live `/map`: the TV runs as a GM session and gets the GM payload (exact NPC HP, GM-only lights); give `/map/table` its own server-side table projection | claude (Codex reviews) | Opus, high | done (accepted by Codex; merge after green CI), with MAP-R2 | `tasks/MAP-R3-table-screen-projection.md` |
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

- 2026-10-29 Claude: MB-06, MB-08, MB-09 and MB-11 are merged and deployed. Production now loads the Cosmere rules at boot ("17 package versions in 1.3s"), the first direct proof that the installed 1.1.2 bundle loads on Railway. Next: nothing queued for Mistborn; possible follow-up is validating rules fields on campaign import (user's call).

- 2026-10-29 Codex: MB-06 merged in PR #232 as 13fcb58d after all four current CI jobs succeeded. MB-11 now includes that main with both queue histories preserved; its new head must pass every CI job before merge. Production deploy verification continues.

- 2026-10-28 Codex: MB-11 focused recheck accepted at 86e27987. All five original false/empty JSON cases refuse without saving, and {} stays accepted; installed-data campaign tests plus probe: 35 passed, 1 skipped. Earlier private gate: 829 passed, 1 skipped. Wait for every current CI job before merging and verifying production.

- 2026-10-28 Claude: MB-11 review fix: `/api/campaign` now refuses `[]`, `0`, `false`, `""` and `null` as well as other non-object bodies, and `{}` still saves. The five new cases fail on the old line and pass now. Next: merge after every CI job is green.

- 2026-10-28 Codex: MB-11 loader, oracle, boot and install handover checks look sound. One small fix remains: empty arrays and false/zero/null bodies must be refused instead of treated as empty settings.

- 2026-10-28 Claude: checked the user's production install of the reviewed 1.1.2 bundle. It is verified as far as logs and a local replay can show: 17 package versions, hash 0d3e3e1b, and no campaign had been switched to Mistborn rules, so nothing is pinned to 1.1.0. Built MB-11 from what the check found. The rules load 8x faster, at boot instead of on the first page; an install no longer makes the next page re-verify; the admin page names the installed release; and `/api/campaign` refuses rules fields. Next: Codex reviews; merge after green CI.

- 2026-10-28 Claude: wrote the design-direction plan (`docs/design/DESIGN_DIRECTION.md`), checked by three reviewers and two verify passes. The user answered its five questions the same day (PF2e back to warm gilt). Next: Claude writes and builds DS-00.

- 2026-10-28 Codex: accepted MB-08 and MB-09 and merged PRs #228 and #229 after every CI job passed. Power-tree checks preserve ordinary and mixed trees; stopping a metalmind is free while restarting in the same turn stays refused. Preserved all task rows and logs in MB-06 queue reconciliation.

- 2026-10-28 Codex: Claude accepted MB-06, including the real phone Charges row. Publishing the public wording changes and zero-count test fix; user verified production release 1.1.2 with 17 versions and registry 0d3e3e1b. Private release files remain local.

- 2026-10-28 Claude: reviewed MB-06 and accepted it. The wording and skin fixes keep Stormlight's text; 48 UI tests and 18 release tests pass on rerun; the 1.1.2 archive matches its SHA; the Charges row is clean at 375px. Next: Codex commits, opens the PR and merges after green CI; then the user installs 1.1.2.

- 2026-10-28 Codex: MB-06 is ready for Claude review: private 1.1.2 archive with every old version, Mistborn wording fixes, 82 page checks and final inline controls checked. Full selection gave 1,238 passes and two fixture errors; all six tests passed after correcting that fixture. Final phone Charges layout and the documented engine follow-ups remain for review. Everything is uncommitted; no private Railway install.

- 2026-10-28 Codex: accepted MB-10 recheck; fractional or blank Charges now send nothing, and 24 Store/Tap boundary cases passed. Merge waits for the remaining test job, then the final MB-06 browser checks can use the inline controls.

- 2026-10-28 Codex: reviewed MB-10; draft labels and the publish redirect are accepted, with 14 focused controller/PF2e checks passing. One small fix remains: fractional Charges must be refused instead of rounded down before sending.

- 2026-10-28 Codex: MB-07 merged after all four checks passed, and production deployment succeeded. MB-06 work was backed up and restored onto main, with both review histories kept; final local checks continue.

- 2026-10-28 Codex: accepted MB-07 recheck b688a774; all 18 installed-data and original save-preservation checks passed. Merge waits for every CI job, then MB-06 resumes builder checks on main.
- 2026-10-28 Codex: MB-07 PR #227 review requests a builder save-preservation fix (four existing story fields erased); installed tests 39 passed/1 expected skip, sheet/policy regressions 34 passed, added preservation probe fails. MB-06 continued: 82 local page/role/viewport checks, wording fixes, 28 focused UI tests passed; final interactive checks remain. No commit, merge or Railway install.

- 2026-10-28 Claude: built MB-08 from an MB-07 finding: a talent in a power's tree now needs that power unlocked, closing 16 talents that anyone could take. 150 Mistborn tests pass with the bundle. Next: Codex reviews; merge after green CI.
- 2026-10-27 Claude: built MB-07 (user decision: wire the real Mistborn rules in). A GM can switch a fresh Cosmere campaign to Mistborn Era 1 or Era 2 rules, and every Cosmere page, the builder, bestiary, tracker and sheet actions work for Mistborn characters; Stormlight is unchanged. Next: Codex reviews the PR; then merge after green CI, and the user installs the 1.1.2 bundle.
- 2026-10-26 Codex: amended MB-06 bundle validates and installs with 17 versions and every old pin preserved; validators unchanged. Review tests give 17 passed and 1 KeyError at the zero-blocker count assertion. Stopped as instructed before browser work; archive and exact error are recorded. Next: Claude reviews the narrow test-only correction, then Codex resumes.
- 2026-10-26 User/Claude: amended MB-06 to retain tracked/unspecified knife metadata and bump all three packages to 1.1.2. Validators stay unchanged. Next: Codex builds the 17-version archive, then resumes the local browser pass.
- 2026-10-26 Codex: MB-06 stopped at its required release gate: the validator refuses tracked-to-GM adjudication and the loader refuses bumping only core. Two reproduction tests confirm both refusals. Existing data and validators are unchanged; no archive, browser pass or commit. Next: Claude briefs strict tooling support, then Codex resumes MB-06.

- 2026-10-26 Claude: the user decided the GM picks the Outlaw Boss knife's damage type at the table. Wrote MB-06: record that ruling in the private bundle as one archive for the user to install, then do a full Mistborn browser pass in both eras. Next: Codex takes MB-06; the user can wait for its single archive instead of installing 1.1.1.

- 2026-10-26 Claude: reviewed MB-02 and accepted it. Spot-checked the corrections against the source pages (Con Artist, Mistborn Assassin merged actions, Outlaw Boss knife). No removed record is still referenced, and no book text is in git. The review tests pass against the installed 1.1.1 release (18 passed, 0 skipped), and the rules suite has 640 passed and 4 skipped. Next: Codex commits, opens the PR and merges once every CI job is green. The user then installs the 1.1.1 archive on Railway and answers the Outlaw Boss knife question.

- 2026-10-26 Codex: MB-02 ready for Claude review: all 252 source blockers accounted for (44 verified, 198 corrected, 9 removed, 1 still blocked); private 1.1.1 bundle installs with old pins and Stormlight preserved, 1192 tests and 110 templates pass. No push or Railway data install.

- 2026-10-26 Codex: MB-05 merged in PR #224 with every CI job green, and production deployed successfully with healthy public checks. MB-02 has started from that main; the original 252 blocked records are reproduced and private source files remain outside git.

- 2026-10-26 Codex: fixed Claude's final MB-05 finding: empty Mistborn sheets no longer show a Fabrials heading, while carried devices and Stormlight stay visible; 91 tests and 110 templates pass. Next: publish, merge after green CI, verify production, then MB-02.

- 2026-10-26 Codex: MB-04 merged in PR #221 after all four CI jobs passed; Railway production deployed successfully and health, readiness, login and both new wallet assets are verified. Next: Claude reviews MB-05, then MB-02 after its release.

- 2026-10-26 Codex: MB-05 fixes the Mistborn sheet and navigation wording and the text-node tooltip error; 60 focused tests and all 110 templates pass. Next: Claude reviews both skins in the browser, then MB-02 after acceptance and release.

- 2026-10-26 Codex: MB-04 PR #221 hit new security advisories before tests. Updated only pypdf and filelock to the reported fixes, regenerated locks and passed the strict audit plus PDF checks. Next: fresh CI, then merge and verify production before MB-05.

- 2026-10-26 Codex: Claude accepted MB-04 after checking GM-only money editing, plurals and styling in the browser; all 87 focused tests and 110 templates pass. Next: publish, merge after green CI, confirm production, then MB-05 before MB-02.

- 2026-10-26 Codex: fixed MB-04 for recheck: only the GM can edit money, singular amounts read correctly, and wallets use styled pills and cards. All 87 focused tests pass. Next: Claude rechecks; after MB-04 merges, MB-05 fixes the Mistborn sheet/nav bleed before MB-02.

- 2026-10-26 Codex: Mistborn wallets and loot now use boxings and clips separately from Roshar spheres. Exact reversals, saved-wallet checks and live updates are tested; the rate is sourced from the installed bundle. Next: Claude reviews MB-04 and checks both skins, then MB-02 source checks.

Newest first. One plain line per handoff: who, what, what's next.

- 2026-10-26 Claude: reviewed MB-05 on desktop and phone in both skins: the Mistborn Investiture hint, Workshop and nav titles are fixed, the tooltip error is gone and Stormlight is unchanged (59 tests, 110 templates). One small change: hide the empty Fabrials heading on Mistborn sheets. Next: Codex fixes it, then commits, opens the PR and merges after green CI; then MB-02.
- 2026-10-26 User/Claude: Codex goes back to Mistborn. User decided Mistborn money is boxings and clips at one fixed rate in both eras (Era 2 banknotes are boxings), and moved MB-02 to Codex. Claude wrote briefs MB-04 and MB-02. Next: Codex builds MB-04, then MB-02; Claude reviews.
- 2026-10-16 Claude: accepted CI-01. The dedicated step selects the same 950 PF2e tests the full suite did (no markers, no 5e-data dependency, only host-capability skips), so coverage is unchanged. Next: Codex rebases after #209, publishes and merges after green CI.
- 2026-10-16 Codex: removed the duplicate PF2e run from the later CI step; the dedicated 950-test selection stays, YAML and file coverage checks pass. Next: Claude reviews the one-line workflow change, then publish and merge after green CI.
- 2026-10-16 Codex: accepted MAP-R2 and MAP-R3 together; 62 focused privacy/SSE/route checks pass, 107 templates parse, JavaScript syntax and diff checks pass. Next: publish the combined PR and merge only after every CI job is green.

- 2026-10-16 Claude: MAP-R3 ready for review, stacked on MAP-R2 on `claude/map-r2-prep-scene-leak` (uncommitted). The TV now has a server-side table audience and its own read, so no GM payload reaches it; 11 new tests (8 fail without the fix), 489 map/scene/SSE/auth tests pass, browser-checked. VTT M2 is ready for review (handoff 029, commit `00815eac`).
- 2026-10-16 Claude: MAP-R2 ready for review on `claude/map-r2-prep-scene-leak` (uncommitted). Scene frames are now GM-only; 7 new tests fail on main and pass with the fix; 457 map/scene/SSE tests pass. VTT M1 accepted (handoff 027); M2 starting.
- 2026-10-16 Claude: updated the VTT rows. The user chose option A (full alpha), Claude builds it and Codex reviews; M0 is accepted and M1 is in recheck. Added MAP-R2/R3 for the live map, marked UI-17 done, and asked whether UI-12 is still wanted given `/map` is replaced at M5. Next: Codex rechecks M1 (handoff 026).
- 2026-10-16 Claude: accepted UI-17. The phone Add row fits at 320px (two rows, 40px controls), 600px and desktop; it also fixes the Add button being clipped on desktop. 7 tests pass, 107 templates parse. Next: Codex commits, opens the PR and merges after CI.
- 2026-10-16 Codex: made the PF2e sheet's Inventory Add row fit phones with 21 added CSS lines; 25 focused checks pass and real item additions save at 320px, 375px and 1440px. Next: Claude reviews UI-17's local diff and layout; changes are uncommitted and unpushed.
- 2026-10-16 Codex: accepted 5E-07 after recheck 3; the actual cantrip handler and class-owned invocation prerequisites are fixed, all 40 rehearsal/PDF checks pass and 107 templates parse. Next: Claude commits the implementation and reviews, opens the PR and merges once every CI job is green.
- 2026-10-16 Claude: fixed both 5E-07 recheck items: casting a cantrip no longer breaks the Cast button, and Agonizing Blast's 2024 prerequisite now needs a cantrip picked as a Warlock. 34 rehearsal tests, 178 dnd5e passed. Next: Codex rechecks.
- 2026-10-16 Codex: rechecked 5E-07; 35 rehearsal/PDF tests pass and 107 templates parse; four original fixes and saved-use migration are accepted, but the casting refactor crashes on cantrips and a Wizard-only cantrip still satisfies a Warlock prerequisite. Next: Claude fixes the two numbered recheck findings and returns for review.
- 2026-10-16 Claude: fixed the five 5E-07 review items: free casts match only an explicit grant; innate spells take their limit or cost from the owning trait; spellbook rituals get a Ritual button; invocation spell prerequisites are checked; each grant keeps its own uses, with saved usage migrated. 29 rehearsal tests, 173 dnd5e passed. Next: Codex rechecks.
- 2026-10-16 Codex: reviewed all of 5E-07; 19 rehearsal/PDF tests pass and 107 templates parse, but free-cast matching, innate costs, unprepared Wizard rituals, invocation spell prerequisites and independent duplicate-spell grants need fixes. Next: Claude fixes the five numbered Review items and returns the uncommitted task for recheck.
- 2026-10-16 Codex: accepted UI-14 after rechecking all four fixes; 40 focused tests pass and 107 templates parse, with PF2e inventory and system separation intact. Next: Claude commits, opens the PR and merges after every CI job is green.
- 2026-10-15 Claude: built 5E-07, the Monday D&D rehearsal. The user's D&D Beyond PDF (Paladin 9 / Warlock 1) now imports matching the sheet; every class and subclass reaches level 20 with no errors; the GM encounter flow works end to end in the browser. Fixed 10 bugs with tests (armour guess, feat skills, invocation prerequisites, spell filters, free casts, level down, phone layout of builder and sheet, double tracker prompts). Next: Codex reviews the uncommitted diff in GM_pf2e_claude_5e.
- 2026-10-15 Claude: fixed the four UI-14 review items: the phase strip follows the turn, the initiative mode syncs to every open tracker, the Strike mapping is exact and custom adversaries load again, and the boundary tests are added. Focused run 143 passed. Next: Codex rechecks.
- 2026-10-15 Codex: reviewed UI-14; the scoped layout and Archer mapping direction are sound, but live phase and initiative-mode state go stale across tracker clients, the broad Strike predicate breaks legacy custom-adversary normalization, and tracker deduplication/setup transitions need boundary tests. Next: Claude fixes the four focused review items and returns UI-14 for recheck.
- 2026-10-15 Claude: built UI-14. The Cosmere tracker now puts the turn and phase controls first, with a four-phase strip (fast heroes, fast NPCs, slow heroes, slow NPCs) that follows the turn. Also fixed most bestiary adversaries showing no attacks (127 of 142 now list their strikes). Next: Codex reviews the diff.
- 2026-10-15 User/Claude: new split. Claude builds the heavy or high-risk code (rules, saved data, live sync, the shared tracker and map, the VTT) and Codex reviews it; Codex keeps UI ports and smaller fixes. UI-14, UI-12 and MB-02 move to Claude; UI-11 stays with Codex (already started). Added UI-17 (phone Inventory Add button). Next: Codex continues UI-11.
- 2026-10-15 Claude: checked UI-10 in the browser: the PF2e player sheet's new compact header and larger tap targets work on desktop and phone, and HP, hero points, spell slots and live updates from the tracker all still work. Accepted. Found an older bug: on a phone the Inventory 'Add' button is off-screen. Next: Codex commits and merges after CI, then UI-11.
- 2026-10-15 Codex: built UI-10 as 75 additive PF2e sheet CSS lines with no template deletions or behavior changes; the rendered behavior inventory and 80 other focused checks pass, 4 local-only snapshots skip and all 107 templates parse. Next: Claude reviews the diff and checks the sheet at 1440px and 375px, including live repaint controls.
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
