# Player release task map

Approved direction: [implementation plan](implementation-plan.md). Authoritative task status remains in [QUEUE.md](../handoff/QUEUE.md); this is the dependency/decomposition map.

Only `ready` briefs may be claimed. `blocked` means a brief exists but dependencies need acceptance and integration. `needs brief` is a planned slice, not executable work: write a cold-start brief against the accepted contracts first. A row covering several operation families or screens MUST split into numbered child briefs before implementation; target roughly 500 changed lines per reviewed diff. Every selected capability remains in the first complete release.

Briefs are based on verified main `7aeb9549c1df2807935a4a44229284e13a5add1d`. The Stage 0 package is on `docs/player-stage0`; when the package is integrated, cut implementation branches from current main. Until then, documentation/review tasks may branch from the Stage 0 package's recorded commit. Never start from a dirty source worktree.

| ID | Stage | Owner | Status | Dependencies | Deliverable / brief |
|---|---|---|---|---|---|
| PE-00 | P0 | Codex | done | — | [Stage 0 inventory and handoff package](../handoff/tasks/PE-00-stage0.md) |
| PE-01 | P0 | Claude | ready | — | [State, command, migration and authority contract](../handoff/tasks/PE-01-state-command-contract.md) |
| PE-02 | P0 | Codex | ready | — | [Evidence-backed rules coverage and fixture matrix](../handoff/tasks/PE-02-rules-coverage.md) |
| PE-03 | P0 | Codex | ready | — | [Review and map unmerged Obsidian recovery work](../handoff/tasks/PE-03-obsidian-reuse-review.md) |
| PE-04 | P0 | Codex | ready | — | [Review existing UI-18 picker/rail patch](../handoff/tasks/PE-04-ui18-review.md) |
| PE-05 | P3 carryover | Codex | blocked | PE-04 | [Reconcile UI-11 presentation after UI-18 disposition](../handoff/tasks/PE-05-ui11-reconcile.md) |
| PE-06 | P3 carryover | Claude | blocked | PE-04, PE-05 | [Fix existing level-up HP preview inconsistency](../handoff/tasks/PE-06-levelup-hp-consistency.md) |
| PE-07 | P0 | Claude | ready | — | [Player journey mockups and UI contract](../handoff/tasks/PE-07-player-ux-design.md) |
| PE-10 | P1 | Claude | blocked | PE-01 | [Extend D&D campaign/character identity constraints](../handoff/tasks/PE-10-dnd5e-identity.md) |
| PE-11 | P1 | Claude | blocked | PE-10 | [D&D locator, ownership, import/export integration](../handoff/tasks/PE-11-dnd5e-locator.md) |
| PE-12 | P1 | Claude | blocked | PE-01 | [Character and encounter state schema](../handoff/tasks/PE-12-game-state-schema.md) |
| PE-13 | P1 | Claude | needs brief | PE-12 | Campaign-scoped state repositories and optimistic revisions |
| PE-14 | P1 | Claude | needs brief | PE-01, PE-12, PE-13 | Command receipt, gameplay event and outbox primitives |
| PE-15 | P1 | Claude | needs brief | PE-11, PE-13, PE-14 | D&D state adapter preserving build revisions |
| PE-16 | P1 | Claude | needs brief | PE-01, PE-02, PE-13 | PF2e detached read/derive adapter |
| PE-17 | P1 | Claude | needs brief | PE-16, PE-14 | PF2e vitals command migration |
| PE-18 | P1 | Claude | needs brief | PE-17 | PF2e conditions/resources command migration |
| PE-19 | P1 | Claude | needs brief | PE-15, PE-18 | Migration dry-run and identity/state reconciliation |
| PE-20 | P1 | Claude | needs brief | PE-19 | Current-state reverse export and recovery rehearsal |
| PE-21 | P1 | Claude | needs brief | PE-15, PE-16, PE-20 | Anytime read access for detached character routes |
| PE-22 | P1 | Claude | needs brief | PE-18, PE-15, PE-20 | Anytime writes for converted operation families only |
| PE-23 | P1 | Codex | needs brief | PE-21, PE-22 | Cross-campaign and concurrent-save regression evidence |
| PE-30 | P2 | Claude | blocked | PE-01, PE-02, PE-13, PE-14 | [Pending action, dice, costs and correction contract](../handoff/tasks/PE-30-action-lifecycle.md) |
| PE-31 | P1/P6 | Claude | blocked | PE-01, PE-03, PE-13, PE-14 | [Inactive server snapshot/replay primitives](../handoff/tasks/PE-31-snapshot-replay.md) |
| PE-31a | P1/P6 | Claude | needs brief | PE-31, PE-15, PE-18, PE-19, PE-20 | Snapshot/replay endpoint adapters and authority activation gate |
| PE-31b | P1/P6 | Claude | needs brief | PE-31a | Shared-hub reconnect coordination and client race tests |
| PE-32 | P2 | Claude | needs brief | PE-30, PE-18 | PF2e targeted action resolution; split by action family |
| PE-33 | P2 | Claude | needs brief | PE-30, PE-15 | D&D targeted action resolution; both editions |
| PE-34 | P2 | Claude | needs brief | PE-32, PE-33 | Effect clocks, own-condition commands and corrections |
| PE-35 | P3 | Claude | needs brief | PE-11, PE-15 | D&D shared private drafts and publication (absorbs 5E-05) |
| PE-36 | P3 | Claude | needs brief | PE-02, PE-16, PE-14 | PF2e authoritative build/draft publication; reconcile PR6/PR7 |
| PE-37 | P3 | Claude | needs brief | PE-35, PE-36 | GM advancement unlocks and retraining transitions; split by workflow |
| PE-40 | P4 | Claude | needs brief | PE-13, PE-19 | Stable item instance migration and inventory authority |
| PE-41 | P4 | Claude | needs brief | PE-40 | Persisted containers/equipment/investment/attunement |
| PE-42 | P4 | Claude | needs brief | PE-40, PE-14 | Accepted transfers and pending-item locks |
| PE-43 | P4 | Claude | needs brief | PE-40 | Persistent shops and catalog purchase requests |
| PE-44 | P4 | Claude | needs brief | PE-43, PE-14 | GM-approved atomic purchase fulfillment |
| PE-45 | P4 | Claude | needs brief | PE-40, PE-02 | Crafting project/recipe records and validated inputs |
| PE-46 | P4 | Claude | needs brief | PE-45, PE-14 | GM downtime and retry-safe crafting completion |
| PE-47 | P4 | Claude | needs brief | PE-40, PE-14 | Shared stash and loot reservations |
| PE-48 | P4 | Claude | needs brief | PE-47 | Narrow quartermaster grant and atomic loot finalization |
| PE-50 | P5 | Codex | needs brief | PE-07, PE-32, PE-34, PE-31b | PF2e integrated Play view from accepted UI contract |
| PE-51 | P5 | Codex | needs brief | PE-07, PE-33, PE-34, PE-31b | D&D integrated Play view from accepted UI contract |
| PE-52 | P5 | Codex | needs brief | PE-07, PE-41, PE-42, PE-44, PE-46, PE-48 | Inventory/economy UI; split per screen/workflow |
| PE-53 | P5 | Codex | needs brief | PE-07, PE-37, PE-05, PE-06 | Advancement UI bound to authoritative previews |
| PE-54 | P5 | Codex | needs brief | PE-50, PE-51, PE-52, PE-53, PE-55 | Compact phone companion and touch/keyboard verification |
| PE-55 | P5 | Codex | needs brief | PE-07, PE-21, PE-22, PE-35, PE-36 | My Characters, noncombat sheets, prepare/rest and access/error states; split by system |
| PE-60 | P6 | Claude | needs brief | PE-03, PE-31a | Port accepted Obsidian recovery slices without old-base merge |
| PE-61 | P6 | Claude | needs brief | PE-60, PE-32, PE-33, PE-34 | System-aware room/effect preview and atomic launch |
| PE-62 | P6 | Claude | needs brief | PE-60, PE-37, PE-44, PE-46, PE-48 | Obsidian GM approvals and downtime controls |
| PE-63 | P6 | Claude | needs brief | PE-60, PE-31a, PE-34, PE-37, PE-41, PE-42, PE-44, PE-46, PE-48 | All-origin live summaries and complete session history |
| PE-70 | P7 | Codex | needs brief | PE-23, PE-34, PE-42, PE-44, PE-46, PE-48, PE-61, PE-63 | Cross-client failure-injection and privacy acceptance |
| PE-71 | P7 | Claude | needs brief | PE-20, PE-70 | Isolated staging migration and rollback rehearsal |
| PE-72 | P7 | Claude | needs brief | PE-54, PE-61, PE-62, PE-63, PE-71 | Three-profile full-table rehearsal and release evidence |
| PE-73 | P7 | Claude | needs brief | PE-72 | Release/cutover runbook and exact runtime approval boundary |

## Scheduling constraints

- Claude starts PE-01; Codex can independently start PE-03 and then PE-02. PE-04 is a review of existing work, not permission to modify its source worktree. PE-07 design may proceed against provisional API bindings, but UI code waits for accepted contracts and the user's visual selection.
- PE-10 and PE-12 both add Alembic migrations: integrate one first, rebase the other, and maintain one migration head.
- Serialize PE-04, PE-05 and PE-06 around `templates/player_levelup.html`. Review-only verdict, accepted source integration, presentation port, and mechanics correction are distinct steps.
- Serialize shared `app.py`, route-policy, persistence-model and event-delivery edits by explicit task ownership. Parallel work uses separate modules and tests.
- PE-01 sets the envelope and invariants; PE-30 only elaborates action lifecycle against them. If PE-30 requires a change, revise/review PE-01 rather than create a second protocol.
- P1 completion requires all currently offered character operations to be detached before their live-only restrictions are lifted. PE-22 may enable only the converted families; build and inventory routes wait for PE-35/36/40 and corresponding acceptance. Do not claim full anytime editing at that intermediate checkpoint.
- Draft lifecycle/source-coverage work is not a blanket verification of every published class, spell or item. PE-02 freezes the matrix; later rule slices add explicit tested coverage without silently dropping selected scope.
- No new map renderer, spatial simulation or website wiki is required. Existing VTT transaction/replay code is reference material, not a new live dependency.
- PE-31 is an inactive server primitive. PE-31a adapters wait for accepted state writers and migration/recovery evidence; PE-31b owns hub coordination. Never advertise a live snapshot as authoritative while its mutation path still writes legacy files/globals.
- PE-55 owns the noncombat character journeys. PE-63 includes advancement, equipment and transfers so complete history means all agreed mutation origins.
- Creating staging resources, changing production backend modes, migrating live data, merging product changes and deploying remain concrete later actions. Stage 0 changes no runtime settings and incurs no new service spend.
