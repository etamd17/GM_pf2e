# Task queue

One shared list for Claude and Codex. Protocol: `docs/handoff/PROTOCOL.md`.

- **Status:** `needs brief` / `blocked` (accepted dependencies required) / `ready` / `in-progress` / `review` / `done` / `waiting on user`.
- **Owner** is the default tool; either may take a `ready` task. A task is `ready` only once
  its brief exists in `tasks/`.
- **Model column** = suggested setting for that step (high effort only where mistakes are costly).
- When you finish a task: write the report in its task file, update its row here, and add a
  plain one-line entry to **Log** at the bottom.

## Player release: approved priority (2026-11-08)

The user approved the player-first plan and Stage 0 handoff work. Read
[the package index](../player-release/README.md), [baseline](../player-release/baseline.md)
and [dependency map](../player-release/task-map.md). This section supersedes older
ready/priority entries below where they conflict; historical logs remain intact.
New wiki and VTT/map work is parked, shipped tools retain maintenance, and
Cosmere/Mistborn expansion follows the PF2e plus D&D 2014/2024 release.

PE-00, PE-01, PE-03, PE-04, PE-10, PE-11a and PE-12 are done. PE-02 and PE-07 are ready; PE-11b and PE-11c are reconciled against main and in brief review.
Dependencies follow section 11 of the accepted
[state and command contract](../player-release/state-command-contract.md). Blocked briefs
cannot be claimed until accepted dependencies are integrated. Needs-brief rows
are planned slices and must be split/written against actual accepted contracts
before they become ready. Owners review each other's output under PROTOCOL.md.

| ID | Task | Owner | Model | Status | Brief / dependency |
|---|---|---|---|---|---|
| PE-00 | Stage 0 inventory and handoff package | codex | standard; high for review | done | [brief](tasks/PE-00-stage0.md) |
| PE-01 | State, command, migration and authority contract | claude | high; select current model | done (accepted; PR #247) | [brief](tasks/PE-01-state-command-contract.md) |
| PE-02 | Evidence-backed rules coverage and fixture matrix | codex | standard; high for review | ready | [brief](tasks/PE-02-rules-coverage.md) |
| PE-03 | Review and map unmerged Obsidian recovery work | codex | standard; high for review | done | [brief](tasks/PE-03-obsidian-reuse-review.md) |
| PE-04 | Review existing UI-18 picker/rail patch | codex | standard; high for review | done (Codex accepted UI-18 with no findings; review in `tasks/UI-18-levelup-picker-rail.md`; PR #245) | [brief](tasks/PE-04-ui18-review.md) |
| PE-05 | Reconcile UI-11 presentation after UI-18 disposition | codex | standard; high for review | blocked (PE-04) | [brief](tasks/PE-05-ui11-reconcile.md) |
| PE-06 | Fix existing level-up HP preview inconsistency | claude | high; select current model | blocked (PE-04, PE-05) | [brief](tasks/PE-06-levelup-hp-consistency.md) |
| PE-07 | Player journey mockups and UI contract | claude | high; select current model | ready | [brief](tasks/PE-07-player-ux-design.md) |
| PE-10 | Extend D&D campaign/character identity constraints | claude | high; select current model | done (merged in PR #250) | [brief](tasks/PE-10-dnd5e-identity.md) |
| PE-11a | D&D locator, read-side identity and forward-import planning | claude | high; select current model | done (merged in PR #254) | [brief](tasks/PE-11a-dnd5e-locator.md) |
| PE-11b | SQL-mode campaign-archive restore for D&D characters | claude | high; select current model | brief review (Codex) | [brief](tasks/PE-11b-dnd5e-archive-restore.md) |
| PE-11c | Runtime reverse export of D&D characters | claude | high; select current model | brief review (Codex) | [brief](tasks/PE-11c-dnd5e-runtime-export.md) |
| PE-12 | Character/encounter state, session, clock and authority tables; `migration_runs` and `characters.archived_at` changes | claude | high; select current model | done (merged in PR #249) | [brief](tasks/PE-12-game-state-schema.md) |
| PE-13 | Campaign-scoped state repositories, revision check order, conflict body and lock order | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-12 |
| PE-14 | Campaign streams, command receipts, gameplay events (the outbox), grants, operation-status read, session start/end and stream-locked authorization writes | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-01, PE-12, PE-13 |
| PE-15 | D&D state adapter: build, play and encounter commands preserving build revisions (inventory, currency and item charges go to PE-41) | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-11a, PE-11b, PE-11c, PE-13, PE-14 |
| PE-16 | PF2e detached read/derive adapter | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-01, PE-02, PE-13 |
| PE-17 | PF2e vitals command migration, including `shield_block` HP and reaction | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-16, PE-14 |
| PE-18 | PF2e conditions/resources command migration, including `toggle_shield`; with PE-17, the PF2e encounter writers | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-17 |
| PE-18b | PF2e build/level-up publish and create/import command on the command layer, wrapping existing derivation | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-17, PE-18 |
| PE-19 | Dry-run report and isolated rehearsal import, including gear and wallets; never changes live authority | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-15, PE-18, PE-41 |
| PE-20 | Reverse export writer and round-trip mapping, tested in isolation without a live authority flip | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-19 |
| PE-21 | Anytime read access for detached character routes | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-15, PE-16, PE-20 |
| PE-22 | Anytime writes for converted operation families only | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-18, PE-15, PE-20 |
| PE-23 | Cross-campaign and concurrent-save regression evidence | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-21, PE-22 |
| PE-24 | Per-campaign activation gate: file → migrating → db, migration gate, abort, db → exporting → file | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-18b, PE-19, PE-20 |
| PE-30 | Pending action, dice, costs and correction contract | claude | high; select current model | blocked (PE-01, PE-02, PE-13, PE-14) | [brief](tasks/PE-30-action-lifecycle.md) |
| PE-31 | Inactive server snapshot/replay primitives | claude | high; select current model | blocked (PE-01, PE-03, PE-13, PE-14) | [brief](tasks/PE-31-snapshot-replay.md) |
| PE-31a | Snapshot/replay endpoint adapters and replay activation | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-31, PE-24 |
| PE-31b | Shared-hub reconnect coordination and client race tests | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-31a |
| PE-32 | PF2e targeted action resolution; split by action family | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-30, PE-24, PE-18 |
| PE-33 | D&D targeted action resolution; both editions | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-30, PE-24, PE-15 |
| PE-34 | Effect clocks, own-condition commands and corrections | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-32, PE-33 |
| PE-35 | D&D shared private drafts and publication (absorbs 5E-05) | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-11a, PE-11b, PE-11c, PE-15 |
| PE-36 | PF2e drafts and publication on PE-18b's publish command; reconcile PR6/PR7 | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-02, PE-16, PE-14, PE-18b |
| PE-37 | GM advancement unlocks and retraining transitions; split by workflow | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-35, PE-36, PE-24 |
| PE-40 | Item instances, wallets, ledger lines, locks and id backfill (no cascade from characters) | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-11a, PE-13, PE-14 |
| PE-41 | Containers/equipment/investment/attunement storage; D&D and PF2e inventory, equipment and currency writers, item charges, character archive, and item input to derivation | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-40, PE-15, PE-17, PE-18, PE-18b |
| PE-42 | Accepted transfers and pending-item locks | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-40, PE-24, PE-14 |
| PE-43 | Persistent shops and catalog purchase requests | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-40, PE-24 |
| PE-44 | GM-approved atomic purchase fulfillment | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-43, PE-14 |
| PE-45 | Crafting project/recipe records and validated inputs | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-40, PE-24, PE-02 |
| PE-46 | GM downtime and retry-safe crafting completion | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-45, PE-14 |
| PE-47 | Shared stash and loot reservations | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-40, PE-24, PE-14 |
| PE-48 | Narrow quartermaster grant and atomic loot finalization | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-47 |
| PE-50 | PF2e integrated Play view from accepted UI contract | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-07, PE-32, PE-34, PE-31b |
| PE-51 | D&D integrated Play view from accepted UI contract | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-07, PE-33, PE-34, PE-31b |
| PE-52 | Inventory/economy UI; split per screen/workflow | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-07, PE-41, PE-42, PE-44, PE-46, PE-48 |
| PE-53 | Advancement UI bound to authoritative previews | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-07, PE-37, PE-05, PE-06 |
| PE-54 | Compact phone companion and touch/keyboard verification | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-50, PE-51, PE-52, PE-53, PE-55 |
| PE-55 | My Characters, noncombat sheets, prepare/rest and access/error states; split by system | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-07, PE-21, PE-22, PE-35, PE-36 |
| PE-60 | Port the Obsidian plugin to the command envelope (section 3 client side, digest and cursor migration) and accepted recovery slices; no old-base merge | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-03, PE-31a |
| PE-61 | System-aware room/effect preview and atomic launch | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-60, PE-32, PE-33, PE-34 |
| PE-62 | Obsidian GM approvals and downtime controls | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-60, PE-37, PE-44, PE-46, PE-48 |
| PE-63 | All-origin live summaries and complete session history | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-60, PE-31a, PE-34, PE-37, PE-41, PE-42, PE-44, PE-46, PE-48 |
| PE-70 | Cross-client failure-injection and privacy acceptance | codex | standard; high for review | needs brief | [map](../player-release/task-map.md); PE-23, PE-34, PE-42, PE-44, PE-46, PE-48, PE-61, PE-63 |
| PE-71 | Isolated staging migration and rollback rehearsal | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-20, PE-70 |
| PE-72 | Three-profile full-table rehearsal and release evidence | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-54, PE-61, PE-62, PE-63, PE-71 |
| PE-73 | Release/cutover runbook and exact runtime approval boundary | claude | high; select current model | needs brief | [map](../player-release/task-map.md); PE-72 |

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

## PF2e sheet maintenance

Requested by the GM 2026-11-12; design approved by the user the same day:
[spec](../superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md).

| ID | Task | Owner | Model | Status | Brief |
|---|---|---|---|---|---|
| SE-01 | Sheet edit part 1: meta fields (deity fix) and GM-only stat adjustments | claude | Opus, high | review (Codex) | [brief](tasks/SE-01-pf2e-sheet-edit-core.md) |
| SE-02 | Sheet edit part 2: feats/features, companion edit, Import JSON | claude | Opus, high | review (Codex; stacked on SE-01) | [brief](tasks/SE-02-pf2e-sheet-edit-lists.md) |

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
| UI-18 | PF2e level-up: the gains rail covered the open picker (search field, locked rows and their reasons); keyboard focus on a locked row was hidden under it | claude | Opus, high | done (accepted by Codex; PR #245) | `tasks/UI-18-levelup-picker-rail.md` |
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
| DS-00 | Inventory diff tool (incl. 00b VTT states, 00c account/Cosmere/Mistborn/5e profiles): ids, data-*, handlers, ARIA, frozen class strings, before/after | claude | Opus, high | done (PR #235); the VTT half (00b) is still unmerged, on its own branch | `tasks/DS-00-inventory-diff-tool.md` |
| DS-01 | Tokens, warm gilt (27a removed), `ds-tokens.css`, empty `ds.css`, notices file, contrast tool, screenshot harness | claude | Opus, high | done (PR #236) | `tasks/DS-01-tokens.md` |
| DS-02 | Motion base: `--t-*` curve, global reduced-motion block, new motion classes used by no page yet, a reduced-motion audit command | claude | Opus, high | done (PR #238) | `tasks/DS-02-motion-base.md` |
| DS-03 | Global focus ring and hover gate, plus a tab-through audit command | codex | standard, high | done (PR #242 merged at 7aeb9549; Stage 0 remote-verified) | `tasks/DS-03-focus-ring-hover-gate.md` |
| DS-04..DS-31 | The rest of section 10, in dependency order | see plan | see plan | needs brief | - |

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

- 2026-11-14 Claude: checked the PE-11b and PE-11c briefs against main now that PE-11a is merged. For players: nothing changes yet; these two tasks let a GM restore a D&D campaign backup on the new database and let the site roll D&D characters back to files safely. Corrections: updated line numbers; backup path-safety checks now apply to every game system; a D&D sheet with no rules system is refused; the export names a bad D&D file and checks D&D characters against the files rather than the database; test lines are budgeted separately. Next: Codex reviews the corrected briefs (docs PR); then Claude builds PE-11c and PE-11b. PE-07 player-screen mockups can go ahead alongside.
- 2026-11-13 Claude: merged PE-11a (PR #254) once all four checks passed. For players: nothing visible yet. Behind the scenes, D&D characters can now be recognised, checked and carried into the new database safely, which later D&D player screens need. Next: check PE-11b (restoring D&D backups) and PE-11c (writing D&D characters back to files) against the merged code, then build them; PE-07 player-screen mockups can go ahead alongside.
- 2026-11-13 Codex: accepted PE-11a after confirming that offline backup checks work without database libraries again. D&D character files can now be identified and checked for import safely; this prepares the player-data foundation and adds no new player screen yet. Next: Claude commits this review and merges PR #254 after every check is green; backup restoration and reverse export (PE-11b/11c) stay blocked until then. Nothing was committed or merged by this review.
- 2026-11-13 Claude: fixed Codex's PE-11a finding. Checking an offline backup works again without the database libraries installed, for old and D&D campaigns alike, and a new test proves it. Full persistence suite passes. Next: Codex re-reviews PR #254; PE-11b and PE-11c stay blocked.
- 2026-11-13 Codex: reviewed PE-11a / PR #254 and requested one fix: checking an offline backup must still work without the database libraries installed. The D&D file rules, ownership safeguards and unchanged-file round trips passed review. Next: Claude fixes Implementation R1-1 and returns the new head for review; PE-11b and PE-11c stay blocked. Review notes are uncommitted; nothing was merged.
- 2026-11-13 Claude: built PE-11a. The site now recognises D&D character files at their exact location and refuses to write them through the shared code until PE-15. The migration tool can plan and import D&D campaigns, warning about odd files and blocking unsafe ones. D&D characters stay off the SQL /me list for now. Full persistence suite passes; the two PostgreSQL tests run only in CI. Next: Codex reviews the PR.
- 2026-11-13 Claude: built part 2 of sheet editing (SE-02). The GM or the player can now add, remove and reorder feats (with search), hide class features, and edit a companion in place; the GM can also bring an exported character file back in. Like a Pathbuilder update, an import takes rules from the file and keeps HP, notes and sheet edits. All new tests pass; the only failing tests also fail on main or need a browser this machine lacks. Next: Codex reviews part 1 (#253) then part 2.
- 2026-11-13 Codex: accepted all three PE-11 split briefs and closed both findings. The export now specifies safe handling of links to omitted D&D characters, and every new ID check uses the exact shared rule. Next: Claude commits the review and two small text clarifications, merges the docs PR after its check passes, then implements PE-11a. PE-11b and PE-11c still wait for PE-11a. No product code changed; nothing was committed or merged by this review.
- 2026-11-13 Claude: answered Codex's two brief findings for PE-11. If a D&D character's file is gone, the rollback export now keeps the player in the campaign but clears their character choice, and leaves out invites that pointed at that character instead of turning them into open invites; the database and its history copy keep everything. Every D&D character id check is now exact, so an id with a stray line break is refused everywhere. Only the task briefs changed. Next: Codex re-reviews PR #251; PE-11a starts after that.
- 2026-11-13 Codex: reviewed the three PE-11 briefs and requested two corrections before implementation: handle invitations and membership links when a D&D character file is gone, and require an exact character ID check rather than the current helper that also accepts a trailing newline. The split and dependencies are sound. Next: Claude revises the briefs and returns them for review; do not start PE-11a or merge this brief PR yet. No product code changed, and nothing was committed or merged.
- 2026-11-12 Claude: built part 1 of sheet editing (SE-01). The GM or the player can now fix a character's details, such as Go'el's deity, on the sheet, and the fix survives re-importing from Pathbuilder. Only the GM can nudge numbers up or down, and players see a small GM marker on those. All new tests pass; the only failing tests also fail on main or need a browser/database this machine lacks. Next: Codex reviews the PR; part 2 waits for the save-bug fixes (#252) to merge.
- 2026-11-12 Claude: designed a way for the GM and the owning player to fix a character on its own sheet without re-importing from Pathbuilder. The user approved the design: anyone can fix details such as the deity, only the GM can nudge numbers up or down, and the engine still computes every stat. Next: Claude builds part 1 (SE-01) and Codex reviews it; part 2 waits for part 1 and the save-bug fixes.
- 2026-11-11 Claude: merged PE-10 (PR #250) once all four checks passed, then checked the PE-11 brief against the merged code. It was too big for one change (about 700 lines against a 500-line limit) and missed some of how D&D characters are stored, so it is now three tasks. PE-11a teaches the shared code and the import planner to recognize D&D character files; it is ready. PE-11b (restoring a D&D campaign backup) and PE-11c (the rollback export) wait for it. Until the D&D pages move over (PE-15), the D&D character file stays the source of truth for who owns a character. Found while checking: since PE-10, a database-mode rehearsal that creates a D&D campaign can no longer produce a rollback export. Production runs file mode, so nothing live is affected; PE-11a fixes it. Next: Codex checks the decisions in the three briefs, then Claude builds PE-11a.
- 2026-11-11 Codex: accepted PE-10 with no findings. D&D identities follow the agreed file names; existing characters and access records survive the schema change and failed-change retries. All four CI jobs are green. One local test timed out and passed alone; PostgreSQL passed in CI. Next: Claude commits the review, confirms green checks and merges, then prepares PE-11 against the merged code. Nothing was committed or merged by this review.
- 2026-11-11 Claude: merged PE-12 (PR #249) once all four checks passed, then built PE-10. The database can now hold D&D campaigns and characters alongside PF2e and Cosmere ones. Each D&D character's record must point at its own file, and a character can no longer be moved to another campaign or rules system by an ordinary save. Taking the change back out is refused while any D&D record exists. Nothing on the site uses this yet. Persistence tests: 347 passed; PostgreSQL runs in CI. Next: Codex reviews the draft PR; PE-11 waits for its acceptance.
- 2026-11-11 Codex: accepted PE-12 review 3 with no remaining code findings. Undoing a finish now also clears the remembered result after it has been read. All 306 local persistence checks passed, as did the main and PostgreSQL CI jobs. Browser CI still fails downloading dependencies from the Ubuntu mirror. Next: Claude commits the review, gets all four checks green and merges; dependent work waits for that merge. Nothing was committed or merged by this review.
- 2026-11-11 Claude: fixed PE-12 R2-1. Undoing part of a save after looking at a finished export record now also undoes what the program remembers, so a retry sees the record as still open. Persistence tests: 306 passed. The browser CI failure was a download mirror outage; the new push reruns it. Next: Codex re-reviews draft PR #249; nothing is merged.
- 2026-11-11 Codex: re-reviewed PE-12 and closed both original findings. One correction remains: reading a finish result before rolling back a savepoint leaves the returned object and a retry showing a finish that was rolled back. All 305 local persistence checks passed; PostgreSQL CI passed. Browser CI failed installing dependencies because the Ubuntu mirror could not resolve; the main job was still running. Next: Claude fixes R2-1, reruns browser CI and returns for re-review with all four jobs green; nothing was committed or merged.
- 2026-11-11 Claude: fixed both PE-12 review findings. An old copy of an export record can no longer mark it finished a second time; the database row decides. The constraint tests now fail if the specific rule they test is missing. A follow-up review found one more small case (undoing part of a save could leave the record looking finished) and it is fixed too. Persistence tests: 305 passed. Next: Codex re-reviews draft PR #249; nothing is merged.
- 2026-11-10 Codex: reviewed PE-12 and requested two fixes: prevent an old in-memory copy from finishing an export twice, and make two constraint tests fail for the intended reason. All PR CI jobs are green; local checks had one timeout that passed on rerun, and migration failure/retry checks passed. Next: Claude fixes the numbered findings and returns for review; nothing was committed or merged.
- 2026-11-10 Claude: built PE-12, the storage that will hold character sheets, live encounters, session history, campaign clocks and which side (files or database) is in charge of each campaign. Nothing reads it yet; the site still runs on files. Taking the change back out is refused unless every campaign's state has first been written back to files. Two review passes found that a failed upgrade or rollback could leave a local test database half-changed; that is fixed and tested. Tests pass on Windows and on PostgreSQL in CI (draft PR #249). Next: Codex reviews PE-12; PE-10 waits until it merges, since both change the characters table.
- 2026-11-10 Claude: merged PE-01 (PR #247) and brought the task map, queue and affected briefs in line with it. The order of work now matches the contract: gear storage comes before the practice migration, a new step (PE-24) is the only one that switches a campaign over, and everything that needs the new storage waits for it. PE-10 and PE-12 are ready; PE-12's brief grew to cover the migration-record and archive changes the contract gives it. For Codex: the contract calls unsupported content `manual`, while the PE-02 brief says "GM ruling/unsupported"; please align them in PE-02. PE-05 can be unblocked now that UI-18 is merged; that is left for Codex. No product code changed. Next: Claude starts PE-12 or PE-10 (one at a time, since both change the same table).
- 2026-11-10 Claude: committed Codex's PE-01 acceptance, merged main's UI-18 and Obsidian review notes into the queue, and opened the docs-only PR. No product code changed. Next: merge once `changes` passes, then reconcile the task map and downstream briefs with the accepted contract before PE-10 or PE-12 becomes ready.
- 2026-11-09 Codex: accepted PE-01 round 4 with no remaining findings. Archived characters keep their file locations, stay out of active lookup and export, and cannot be overwritten by a new character with the same name. Next: Claude commits the review, reconciles the queue with main, opens the docs-only PR and merges after the required check passes; downstream briefs must be reconciled before implementation starts.
- 2026-11-09 Claude: fixed Codex's last PE-01 point. Archiving a character no longer moves its saved-file location, so D&D characters keep the location format they require; archived characters are simply left out when the site looks characters up, so a new character can reuse the name. No product code changed. Next: Codex rechecks it; dependent work stays blocked until it is accepted and merged.
- 2026-11-09 Codex: rechecked PE-01 round 3 and closed all three previous findings. One correction remains: the new character archive rule conflicts with the required D&D file-location format. Next: Claude reconciles those two rules and returns the small revision for re-review; dependent work stays blocked.
- 2026-11-08 Codex: accepted UI-18 with no findings. All 23 focused checks passed, all templates parse, and extra phone checks confirmed the fade and keyboard override. Next: Claude commits the review notes, opens the PR and merges after every CI job passes; the existing short-screen follow-ups remain separate.
- 2026-11-08 Claude: UI-18 ready for review. On the level-up page, the progress rail no longer covers an open picker, so a locked row's reason and its focus ring show again. The GM override switch stays usable above the picker. 15 new browser checks pass. Next: Codex reviews.
- 2026-11-08 Claude: accepted the Obsidian reuse review. Re-ran the checks on clean copies and got the same numbers, and all seven reported problems showed up again exactly as described. The old branch still must not be merged as a whole. Next: Codex commits, opens the docs-only PR and merges once checks pass.
- 2026-11-08 Codex: completed the Obsidian reuse review. The focused checks passed on both copies, and extra probes found six issues to fix during reuse plus an older save-and-retry gap. The report maps what to keep and the order to bring it forward. Next: Claude reviews PE-03; website and installed plugin behavior are unchanged.
- 2026-11-08 Claude: fixed Codex's three round-2 points on PE-01. A retry's outcome now counts as settled only when one consistent, authorized check says so; the HP bookkeeping survives a move to files and back unchanged; and the inventory conversion work now sits in the task that has the storage it needs. No product code changed. Next: Codex rechecks it; dependent work stays blocked until it is accepted and merged.
- 2026-11-08 Codex: rechecked PE-01 round 2. The original 13 points are addressed; three corrections remain for retry results, HP preservation when moving between storage formats, and task order. Next: Claude fixes the numbered review findings and returns for re-review; dependent work stays blocked.
- 2026-11-08 Claude: revised PE-01 for all 13 of Codex's points. Retries now stay tied to the account or token that sent them, players cannot probe hidden records, sessions and the switch-over to the database have clear owners and steps, and crafting, loot and self-reported gear follow the confirmed rules. The report answers each point by number. No product code changed. Next: Codex rechecks it; dependent work stays blocked until it is accepted and merged.
- 2026-11-08 Codex: reviewed PE-01 and requested 13 contract corrections covering safe retries, private records, migration and rollback, resource reservations, and economy permissions. The user confirmed pending player reports of gear and coin. Next: Claude revises the contract, then Codex rechecks it; dependent implementation stays blocked.
- 2026-11-08 Claude: wrote PE-01, the rulebook for how character and encounter changes are saved, retried, checked and shared, with five worked examples (editing between sessions, a level-up during combat, a double purchase approval, two people wanting the same loot, an interrupted area spell). No product code changed. Next: Codex reviews it; PE-10 and PE-12 stay blocked until it is accepted and merged.
- 2026-11-08 Codex: reviewed existing work and prepared the approved player-release task package. Five bounded Stage 0 tasks are ready; existing Obsidian recovery and UI-11/UI-18 patches must be reviewed before reuse. New wiki and VTT work is parked; original worktrees and live settings are unchanged. Next: Claude PE-01 and Codex PE-03, with PE-02/04/07 available in parallel.

- 2026-11-02 Claude: found why the phone Chronicle rings failed on Linux CI. Linux shows a different fallback font, and its "J" runs under the ring; at the smallest phone width the last tab also stayed cut off. Fixed by giving the ring 2px of clearance and scrolling the focused tab into view; checked against the Linux font locally and in the real tests. Next: push the fix to PR #242 and merge once CI is green.

- 2026-11-02 Codex: published accepted DS-03 as PR #242 after all 76 requested local checks passed, with stylesheets unchanged. PostgreSQL passed in CI, but six phone Chronicle checks failed on Linux. The PR is not merged. Next: Claude reviews the recorded failure before any CSS change or merge; local report and queue notes are uncommitted.

- 2026-11-02 Codex: completed the three test fixes from Claude's acceptance. Both changed files passed all 76 tests; the stylesheets and application code are unchanged. Next: publish the accepted change and merge only after every CI job succeeds, including PostgreSQL. The full-stroke audit remains follow-up DS-03t.

- 2026-11-02 Claude: rechecked DS-03 and accepted it. The menu-link rings now show on all four sides at normal size and at 200% zoom, the phone Chronicle tabs ring cleanly, and 588 focused checks pass. I accepted the phone Chronicle focus spacing and the audit's sampling limits as documented, not as proof. Next: Codex tightens three tests (no CSS change), then commits, opens the PR and merges once CI is green.

- 2026-11-02 Codex: fixed the clipped menu highlights, stray tile ring and missed audit failures, and checked the expanded panels. The final Windows run has 5,625 tests passed, no test failures and the same eleven database setup errors; 588 focused checks passed. Remaining layout and measurement limits are recorded with local screenshots. Nothing is committed. Next: Claude rechecks the fixes and the listed review decisions.

- 2026-11-02 Codex: added visible keyboard highlights and touch hover safeguards; 543 focused checks passed. Recorded overlay and rail problems separately. Next: Claude reviews the changes and, as the user chose, checks 200% browser zoom before accepting; nothing is committed.

- 2026-11-02 Claude: reviewed DS-03 and sent it back with fixes. The focus ring around the top menu links is cut off on every page, one tile shows a stray second ring, and the new audit tool can miss a ring that blends into its background. The 200% browser zoom check is done: no page scrolls sideways, and rings look the same as at normal size. Next: Codex fixes the findings in the task file and reruns the full tests.

- 2026-11-02 Claude: wrote the DS-03 brief for Codex: a visible focus ring everywhere, and no stuck tile or card hovers on touch. It also gives locked picker rows a ring; their explanation stays with DS-15c and DS-09. Corrected plan 5.7, whose check would have passed invisible rings. DS-02 follow-up: build logs show production installed pypdf 6.21.4; a live PDF import still needs someone signed in. Next: Codex builds DS-03.

- 2026-11-01 Claude: merged DS-02 (PR #238) after all CI jobs passed, including PostgreSQL. It is live: the site's two stylesheets match main and its health checks pass; no signed-in page was checked. Next: DS-03 onward need briefs; the VTT half of DS-00 is still unmerged. Level-up rail and locked-row keyboard focus remain follow-ups.

- 2026-11-01 Claude: DS-02's PR hit 7 new pypdf security advisories before its tests ran. Updated only pypdf to 6.21.4 and regenerated the locks; the strict audit and PDF import tests pass. Next: fresh CI, then merge and verify production.

- 2026-11-01 Codex: accepted the DS-02 fix: locked rows return to their normal look under reduced motion. The original reproduction, 69 focused checks and 35 browser checks pass. Level-up overlap and keyboard focus remain separate follow-ups; the visual limits stay explicit. Next: Claude publishes and merges only after every CI job, including PostgreSQL, is green, then verifies the deployment.

- 2026-11-01 Claude: fixed the DS-02 locked-row finding by removing the static highlight, with a test on the real builder. Under reduced motion a locked-row click now shows nothing, and on level-up the gains rail can hide the reason chip (separate fix underway). Windows suite: 5598 passed, with the same 3 failures and 11 no-database errors. Next: Codex rechecks.

- 2026-10-30 Codex: DS-02 needs one fix: a clicked locked option stays highlighted under reduced motion. Reproduced on the real builder; 69 focused and 33 existing browser tests pass. The tiny corner-pixel exception and disclosure/shimmer helpers are accepted as documented. Next: Claude fixes the feedback reset, then Codex rechecks before publishing.

- 2026-10-30 Claude: built DS-02, the motion base: one easing curve, a site-wide reduced-motion block, new motion classes no page uses yet, and a `motion` audit command. Windows suite: 5596 passed, with the same 3 failures and 11 no-database errors. Two pages differ from main by 1/255 at corners, cause unknown; two reduced-motion rules touch existing pages. Next: Codex reviews.

- 2026-10-30 Claude: merged DS-00 (PR #235) and DS-01 (PR #236) after all CI jobs passed, including PostgreSQL. Both are live: the site's stylesheets match main and its health checks pass. The Windows full suite showed only the same 3 failures and 11 no-database errors as before. Wrote the DS-02 brief. Next: Claude builds DS-02; the VTT half of DS-00 is still unmerged.

- 2026-10-30 Codex: DS-01 is accepted now that the inventory tool recheck is clear. The colour and token decisions stand. Next: Claude publishes its own task PR after DS-00, and every CI job including PostgreSQL must pass before merge; DS-02 still waits for both.

- 2026-10-30 Codex: DS-00 recheck accepted at 96c2ccab. All four original reviewer cases pass, and 57 inventory/screenshot checks pass. The extra commit-stamp, unchecked-capture, fieldset and URL handling changes are accepted. Next: Claude commits, opens the PR and merges after every CI job is green.

- 2026-10-30 Claude: fixed Codex's three inventory tool findings, plus three more problems found by an independent review and one false alarm found rerunning the tool. The tool now shows only the new stylesheet links between the two versions on all pages. Nothing is pushed. Next: Codex rechecks, then both tasks go to a PR, and every CI job, including PostgreSQL, must pass before merge.

- 2026-10-30 Codex: DS-01 colour and token review is clear, including the tint exception and Cosmere root pins. Publication waits for the inventory tool fixes, then every CI job including PostgreSQL.

- 2026-10-30 Codex: DS-00 needs three fixes: values in controls outside forms, identified link destinations and login redirects can pass unchecked. Four reviewer cases demonstrate the gaps. Next: Claude fixes, then Codex rechecks before publishing.

- 2026-10-30 Claude: built DS-01. PF2e and 5e pages are now warm gilt (Q1); Stormlight and Mistborn are pixel-identical. An independent review confirmed 16 findings, all fixed (`9c70fadc`). PostgreSQL tests have not run on this branch (Docker would not start). The design-system tokens and colour roles are in `system.css`, with a generated `ds-tokens.css` for the account shell, an empty `ds.css` linked everywhere, the notices file, `tools/check_contrast.py` (0 failures in four themes) and a before/after screenshot harness (`tools/ui_screens.py`). The inventory diff shows only the new links on 67 routes. Also fixed a DS-00 false positive (the deploy token in the three.js import map). Next: Codex reviews DS-00, then DS-01.

- 2026-10-28 Claude: built DS-00, the inventory diff tool (`tools/ui_inventory.py`): it fails a design PR that removes or changes any id, handler, ARIA, form control or script. 19 new tests pass; checked on 18 real PF2e pages in both modes. Next: Codex reviews; DS-00b/c add the VTT and other-system pages.

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
