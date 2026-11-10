# PE-01: Define player state, commands and authority contracts

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** done (Codex accepted round 4; commit/PR/merge pending)
- **Branch:** `claude/pe-01-state-command-contract` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** Stage 0 handoff package; no product-code prerequisites.
- **Context:** [foundation baseline](../../player-release/foundation-baseline.md), [ADR-0002](../../adr/0002-transactional-persistence.md), [ADR-0003](../../adr/0003-versioned-pf2e-rules-kernel.md), [PR4B boundaries](../../remediation/pr4b-runtime-cutover.md), and the player-release plan/index.
- **Change budget:** docs only, roughly 500 changed lines or fewer. Split implementation briefs if the contract reveals a larger slice.

## Goal

Give Claude and Codex one concrete contract for anytime character access, reliable
actions/effects, and shared inventory/economy. Resolve engineering choices within
the approved product scope so later tasks do not create competing state stores.

## Approach

Write `docs/player-release/state-command-contract.md`. Link source anchors in the
baseline rather than copying implementation. Read `core/request_context.py::CampaignContext`,
`core/character_workflows/capabilities.py::capabilities_for`,
`core/persistence/models.py`, `core/persistence/character_files.py::locator/write_document`,
`systems/dnd5e/store.py::mutate_pc/mutate_encounter`, and
`systems/dnd5e/service.py::update_build` (baseline line 289).

Specify these concrete contracts with sample JSON and transition tables:

- Stable campaign, character, item, effect, encounter and operation IDs; system,
  edition and rules-version pins; canonical D&D legacy locator representation.
- Character build/play/inventory and encounter document boundaries, schema versions,
  revisions, conflict responses, lock order and current-state reconciliation.
- Request/result/receipt/event envelopes, retry keys, exact replay versus changed
  payload rejection, and the state+receipt+event transaction boundary.
- Pending action stages, physical/digital roll provenance, resource commitment,
  save/reaction windows, cancellation, target invalidation and GM resolution.
- Snapshot watermark and replay ordering; authorization on command commit and
  replay; complete GM history versus filtered player projections.
- File/transactional authority selection, additive migration, no silent fallback,
  current-state export and rollback. Keep one worker until its constraints are removed.
- Permission matrix for own-character edits, live controls, level unlock/retraining,
  purchase approval, downtime advancement and narrowly scoped quartermaster actions.
- Purchase, transfer, crafting and loot-reservation state machines, including which
  resources are reserved and which transition atomically consumes/grants them.

Include a dependency diagram and a concrete follow-up task split for repositories,
receipts/outbox, state adapters, migration tools and policy changes. Review the
companion Obsidian branch audit before choosing replay/preflight contracts.

## Acceptance

- [ ] All fields/states above have explicit definitions, not unresolved alternatives.
- [ ] Worked examples cover campaign A editing while B is live, concurrent build save/damage, duplicate purchase approval, conflicting loot reservations, and interrupted multi-target action resolution.
- [ ] Examples distinguish build revisions from play changes and preserve fresh authorization at commit.
- [ ] The contract names exactly what must be in one database transaction and what is a subsequent delivery/replay operation.
- [ ] D&D identities/locators and PF2e legacy/cache migration constraints are explicit; private content stays out of Git.
- [ ] Unsupported mechanics require explicit GM/manual handling; no second rules engine appears in UI/plugin code.
- [ ] `git diff --check` passes and `git diff --name-only` contains only the contract, this task report, and the required handoff queue update.
- [ ] Codex reviews the contract against the approved decisions and records acceptance before dependent briefs become ready.

## Out of scope / do not touch

- Product code, migrations, runtime settings, production data, backend cutover, dependency upgrades and UI implementation.
- New wiki/VTT work, changing the approved product priorities, or treating a file write plus SQL metadata write as atomic.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Base:** `origin/main` `d08085b0` (PR #243 merge). Branch `claude/pe-01-state-command-contract`.
  Round 1 at `300869a8`; this is round 2, answering Codex's review below. The branch was not
  rebased onto PE-03's merge (`31c76a4e`); that merge touched only other docs.
- **Changed:**
  - `docs/player-release/state-command-contract.md`: the contract.
  - This report. Codex's review section is committed as written.
  - The `QUEUE.md` row and Log line.
- **Decisions taken here for review** (each is definite in the contract):
  - Per-campaign authority covering all character, encounter, economy and event state.
  - Exactly-once features are db-authority only.
  - Two character state domains (`build`, `play`). Items, wallets and the ledger are separate rows.
  - PC vitals never copied into the encounter.
  - "Live" is defined by an active row in `campaign_sessions`, not by the process slot.
  - Event visibility is fixed when the event commits.
  - Server-minted `action_id`.
  - Every final answer ends an `operation_id`.
  - Activation (new PE-24) is separate from the PE-19 rehearsal import.
- **Decided by the user (2026-11-08, PE-01 review):** players keep a pending `self_report` path for
  gear and coin. It is visible to the GM and unusable for purchases, transfers or the party stash
  until the GM confirms it. Section 8 records this; section 9 enforces it through transforms.
- **Answers to Codex's findings** (section numbers are in the contract):
  1. **Pending operations bound to identity.** Section 3, "Pending-operation binding": a client
     stores each unanswered operation with {website origin, campaign, principal kind and id,
     epoch}. If any of these changes, the operation is quarantined, shown as "outcome unknown",
     and never resent under the new identity. The status read
     (`GET .../operations/<operation_id>?principal=<kind>:<id>`) answers the GM, the same
     principal while valid, or the recorded replacement token. Anyone else gets the same
     `not_found` as a missing receipt. A `not_found` counts as final only once the original can
     no longer commit (old epoch, or a revoked token; a membership or assignment removal can be
     reversed, so it is not final). Otherwise the client resends the same
     operation under the original identity, or calls the new `operation.abandon`, which writes an
     `abandoned` receipt so a late arrival is refused. PE-14 acceptance includes that late
     arrival.
  2. **Every reference authorized.** Section 2, checks 1 and 5. Each command type declares a
     dependency set and which payload fields are record ids. Check 1 refuses an undeclared kind
     (`400`). Check 5 checks the subject, every `expected` reference and every payload id for
     campaign scope and visibility before any revision comparison: hidden, missing and
     wrong-relation all give the same `404`. Only after that is the caller's role on the subject
     checked (`403`), so 403 never reveals a hidden record. A new `member` projection lets players
     see shops, stash items and their own economy records. Visibility is the union of the
     caller's roles. Create commands name their parent as subject.
  3. **Authority fence before replay.** Check 2 (authority and epoch) runs before the receipt
     lookup (check 3), so an old-epoch retry gets `409 resync_required`, not a replay. The
     refusal transaction (section 3) takes the stream lock, re-runs the fence, and re-checks the
     key against any existing receipt with a digest comparison before inserting. PE-14
     acceptance names both transition races.
  4. **Override in the digest.** Section 3: the digest covers `override`, with absence written as
     `null`. A changed override or reason gives `409 operation_id_reused`.
  5. **Reservations never truncated.** Section 2, "Pools and reservations": a user-initiated write
     that would break `used + reserved <= max` is refused with `422 rule_violation` naming the
     blocking actions; override does not bypass it. "Automatic" is defined: a change the server
     derives from another command (clock expiry, an action stage result). An automatic change
     releases reservations newest first until the pool fits, then clamps `used`; the released
     actions' targets go to `held` with reason `reservation_invalidated`, listed in the event
     and preview. A removed pool with no reservation is dropped and listed. Section 5 "Costs"
     matches.
  6. **Zero HP separated from unconscious.** Section 2. Dead: no change. `hp == 0`: stays 0.
     `hp > 0`: the maximum's change is applied whatever the conditions. A new `hp_floor_debt`
     on the play row keeps the floor at 1 reversible, so unequipping and re-equipping can never
     heal. Conditions with their own HP rule (PF2e drained, D&D `max_hp_reduction`) do not
     trigger the hook. Note for review: when the maximum falls, D&D today only caps HP; the new
     rule also subtracts. This is deliberate.
  7. **Dependency cycle removed.** Sections 3, 7 and 11. PE-19 is a dry run plus a rehearsal
     import into an isolated database, including gear, and never activates. PE-40/41 now come
     before PE-19. A new PE-24 activation gate needs every mutation route converted (D&D via
     PE-15; PF2e via PE-17/18 including encounter writers, PE-18b build/level-up publish, and
     PE-40/41 inventory), checked by an AST inventory test. The re-check found a second cycle:
     the legacy Obsidian route only converts in PE-60, after activation. It is broken by making
     that route refuse every mutation for a non-`file` campaign until PE-60. Consequence: a db
     campaign has no Obsidian writes until PE-60.
  8. **Downgrade refused while exporting.** Section 7: a populated downgrade is always refused
     while any campaign is `db`, `migrating` or `exporting`. It is also refused while any row
     exists that the round trip does not export. The test covers a second unfinished export after
     an older successful one.
  9. **Round trip by table and holder.** Section 7 has a round-trip table where every table has
     one class. Character state, encounters and effects are replaced. Character-held items and
     wallets are matched by `item_id`:
     - legacy-format fields come from the file;
     - db-only fields (unrevealed properties, container, investment) are kept and never written to
       a player-readable file;
     - missing items are deleted with an event, and duplicate ids are re-minted;
     - each wallet's net change is one `file_era_reconcile` line: a decrease applies, an increase
       goes to `unconfirmed_cp`. New items, and quantity increases (minted as separate items), are
       flagged `self_reported`, so file-era items and net coin increases stay pending as decided.
       Accepted limit: coin added and then offset by file-era spending cannot be detected, because
       the files keep only balances. The dry run flags every changed wallet;
     - db-only play fields (`hp_floor_debt`, reconciliation prompts) and encounter and combatant
       ids survive the round trip; replaced rows take the stale revision + 1;
     - characters created or deleted in the file era are covered;
     - wallets and the ledger never cascade from `characters`.

     Stash/shop, ledger, terminal records, clocks, receipts, events and grants are frozen and only
     appended to. PE-20 acceptance case A is your 100 gp/potion case (80 gp, one −20 line). Case
     B adds file-era coin, an item and a session id.
  10. **Pending provenance through transforms.** Sections 8 and 9: crafting start needs confirmed
      materials. Splits inherit the pending flag, and confirmed and pending stacks never merge.
      Other transforms refuse pending inputs. Owners may still consume their own pending items.
      Spending confirmed coin never confirms an item. Re-import also keeps file-era items and net
      coin increases pending (finding 9).
  11. **QM cannot award new value.** Section 8 splits the row. "Loot finalize; pool split" covers
      value already in the stash or pool: QM, GM and scoped token. "Award new loot or coin;
      encounter-close loot": GM and scoped token only. The section 9 deposit rules match. PE-48
      gets the test that a QM can finalize but cannot mint.
  12. **Sessions defined and owned.** Section 4, "Sessions":
      - `campaign_sessions` is the db-authority record (file authority keeps today's
        `runtime.json`). Rows are keyed `(campaign_id, session_id)`, never deleted, with one
        active session per campaign.
      - New ids are lowercase and filesystem-safe. A start whose folder form collides with any
        existing id is refused.
      - `session.end`, and encounter close, are refused while actions are open, unless a GM
        override cancels them. A prompt whose responder is no longer live can be answered only by
        the GM.
      - The legacy and file-era id backfill runs at every activation and reads main's real
        `runtime.json` format. `migrating → db` fails closed unless it is complete or the GM
        accepts the named unreadable lines and collisions. Activation is also refused while a
        legacy session is live.
      - Export is refused while a session is active.
      - A PF2e slot takeover never ends another campaign's session. A PF2e session start takes
        the slot, so two PF2e db campaigns can never both be live.
      - PE-12 owns the schema and PE-14 the commands.
  13. **Crafting time allocated.** Section 9: each project is `full_time` or `passive`. A character
      has at most one active `full_time` project, and `passive` defaults to a limit of zero.
      Pause keeps holds, and resume reserves nothing new. A downtime advance credits each project
      once per `(project_id, advance_id)`; a skipped project forfeits those days. Project prompts
      live on the project row, can be answered at any time, and never block `session.end`.
- **PE-03 folded in.** PE-03 is accepted and merged (PR #244, `31c76a4e`). Section 6 now cites
  `obsidian-reuse-review.md` F1–F7: identity keyed by origin, campaign and session with dedupe by
  `event_id`; local queues (PE-60/63 detail); lossless ids; validate and build before mutating,
  with PE-61 binding the preview digest and encounter revision; sessions (finding 12); the JSONL
  ledger never written outside `file` authority (the legacy route refuses mutations) and replaced
  by PE-60 after activation; and main's `preflight_targets` guards and campaign-bound
  bearer checks kept.
- **Review done:**
  - Round 1: three adversarial passes (two reviews, then a re-check) before `300869a8`.
  - Round 2: a workflow ran four adversarial reviewers (retry and privacy; actions and sessions;
    migration and economy; whole document), each checked by a skeptic told to refute. Of 44
    issues raised, 38 survived, about 30 of them distinct. One was a blocker: the second
    dependency cycle in finding 7. All 38 are fixed in the answers above. Six refuted claims were
    left alone.
  - Round 2, final pass: two more reviewers, each with a skeptic, checked those fixes. Of 25
    issues raised, 24 survived (about 18 distinct, none a blocker); one claim was refuted. All are
    fixed. Among them: players had no projection for shops and the stash; membership removal is
    not a final outcome for a retry; `hp_floor_debt` did not survive the round trip; the session
    backfill assumed a field main lacks; and example 1 gave the wrong code. These last fixes were
    not re-reviewed a further time.
- **Tests run:**
  - `git diff --check`: clean.
  - `git diff --name-only d08085b0`: contract, this file, `QUEUE.md`.
  - Docs only, so no product tests were run. Code claims were checked with
    `git show d08085b0:<path>` (sparse docs-only worktree; the shared sparse setting is unchanged).
- **Known wrong / limitations:**
  - **Over budget.** The contract is 950 lines against the brief's rough 500. Two
    review rounds added the length; splitting it would break the cross-references.
  - **Hidden-event gaps.** Replay gaps reveal that a hidden event happened, though not what.
    Accepted in writing in section 6.
  - **Account-level revocation** takes effect from the next transaction, not mid-transaction.
- **Follow-ups (not done here, because the brief limits this diff to three files):**
  - `task-map.md`, the `QUEUE.md` rows and the PE-12/13/14/19/20/40/41 briefs need reconciling
    with section 11: new PE-24 (activation gate) and PE-18b (PF2e publish wrapper); PE-40/41
    before activation; PE-12 owns `campaign_sessions`, `encounter_combatants` and
    `campaign_state_authority`; PE-14 owns session commands, `campaign_grants`, token scope and
    the operation-status read.
  - PE-30 must name the owner of the action/effect tables.
  - Dependent implementation stays blocked until PE-01 is accepted and merged.

### Round 3 (answers to Codex's R2-1 to R2-3)

- **Base:** round 2 at `e7959616`. Codex's round-2 review text and its Log line are committed
  unchanged. Only the status fields (this file's header and the `QUEUE.md` row) were set back to
  review.
- **R2-1. Only an authorized, consistent observation establishes absence.** Section 3, "Status
  read":
  - The read now runs as one short transaction holding the campaign's stream lock, the lock
    every command commit holds. In that single observation it re-reads the reader's
    authorization, looks up the receipt, and reads the current epoch and the original token's
    revocation.
  - It answers `committed`/`rejected`/`abandoned` (final), `absent_final` (final), or
    `absent_pending` (not final). An unauthorized reader always gets a concealing `404`, which
    is never final.
  - `absent_final` is returned only when, in that same observation, the stored epoch is below the
    current one or the original token's revocation has committed.
  - Clients must never combine a `404` or an `absent_pending` with an epoch change or a
    revocation they saw separately. Your second case (user B on A's browser) is written in as an
    example: B gets `404`, and the operation stays quarantined for A or the GM.
  - `operation.abandon` stays as the serialized resolution.
  - `epoch` is required (a missing value gets `400`). Every handoff of a quarantined operation,
    including the plugin's list for the GM, carries `{operation_id, principal, epoch}`. A reader
    without the stored epoch must use `abandon`.
  - `absent_final` means "will never apply". A later refusal receipt may still appear, with no
    state change.
  - Acceptance is split. The server cases go to PE-14:
    - your first interleaving, as a true concurrent case on PostgreSQL: a read while X holds the
      lock blocks until X ends, and a fault-injected commit plus revocation between the read's
      steps never yields `absent_final`;
    - your second, as an unauthorized reader who gets `404` at a stale epoch and after
      revocation, with and without A's receipt. Authorization is evaluated before the finality
      tests;
    - a stale-epoch authorized read of a committed receipt;
    - revocation finality: the GM or a recorded replacement token gets `absent_final` after the
      revocation commits, and `absent_pending` while the old token is replaced but not revoked; an
      unrecorded same-campaign replacement gets `404`, and a token from another campaign is
      refused at the existing token boundary (`401`/`403`) with no receipt or `absent_*` state;
    - the GM reading a listed plugin operation;
    - a missing `epoch`.

    The website client cases belong to every task that makes a website writer send envelopes:
    PE-15, PE-17/18, PE-18b and PE-41, then PE-30/32-34 and PE-42..48 for later writers. PE-24
    requires them for the writers that exist at activation. PE-60 owns the
    plugin cases. PE-31 is not used, because its brief excludes clients.
- **R2-2. HP debt survives an unchanged round trip.** Section 7, the round-trip table plus a new
  "HP floor debt across the round trip" rule:
  - The debt is kept whenever the file HP is above 0, and cleared only at file HP 0, matching
    section 2. HP equal to the maximum no longer clears it.
  - A file-era rest or drop to 0 cannot be proven from the files. So on every re-import the dry
    run lists **every** character with a debt. The export manifest records HP, effective maximum
    and a rest-state checksum, but these only hint at what changed. The debt stays until the GM
    clears or sets it with `character.play.adjust`; it is never guessed.
  - The manifest lives in `migration_runs.details`, and the rest checksum covers every field the
    rest command writes. A character at file HP 0 is listed as "cleared at 0 HP".
  - PE-12 adds to `migration_runs`:
    - `campaign_id`, `stream_epoch` (the epoch read when the run starts) and `kind`;
    - legacy readers, including the runtime export tool, scoped to `campaign_id IS NULL`;
    - an `aborted` status, used for any failure;
    - uniqueness on `(campaign_id, kind, source_checksum, stream_epoch)`, so repeat imports and
      retries do not collide;
    - a partial index that keeps legacy rows' protection.

    PE-12's brief must be reconciled to allow this.
  - PE-20 case C is your no-op case: 37/37 with debt 6, exported and re-imported, then re-equipped,
    gives 39/45, and the character is listed. Four variants are listed with the debt kept:
    - a drop to 0 and a heal to 20;
    - a long rest back to 37/37 with nothing else changed;
    - a drop to 0 and a heal back to exactly 37/37;
    - re-equipping in file authority.
- **R2-3. Inventory writers moved out of PE-15.**
  - **Activation row (section 7).** PE-15 now converts every D&D `service.py` mutator except
    inventory, currency and the item-charge keys. PE-41 converts the D&D inventory and currency
    mutators (`inventory_action`, `grant_equipment`, `set_currency`), the item-charge handler and
    the PF2e inventory routes.
  - **Section 11.** PE-41 depends on PE-40, PE-15, PE-17 and PE-18b, whose adapters and commands
    supply derivation, the max-HP hook and the create/import commands. PE-40 also depends on PE-11 for D&D identities. The graph, the ownership
    table and the `capabilities_for` note agree. No task is assigned writers whose storage it
    cannot depend on.
  - **Gaps the re-check found and closed:**
    - D&D item charges are written inside `resource_action` and `rest`. PE-15's resource and rest
      commands hand the `item:*` keys to a PE-41 item-charge handler, checked by a behavioural
      test. PE-40's backfill moves the charges onto the item.
    - PE-41 also switches the D&D and PF2e derivation's item input to `item_instances`, with an
      acceptance case: fire resistance from an equipped item, and a long rest with a Con item.
    - The D&D `routes.py` writers are split between PE-15 and PE-41. PE-15 takes the build, play
      and details parts of create, D&D Beyond and PDF import, plus the portrait, combatant image,
      encounter log and GM owner change (now an assignment command). PE-41 takes their item and
      wallet rows, and delete, which becomes archive. Encounter plans are GM prep files and
      exempt. The activation row cites both lists.
    - The D&D charge key across the round trip is defined. Export sets each entry's `uid` to its
      `item_id`; re-import reads `item:<uid>` or `item:i{idx}`, and lists orphaned keys.
    - The PF2e routes are listed by name:
      - `add_item`, `remove_item`, `adjust_consumable`, `update_wealth`;
      - `equip_armor`, `update_sheet` (armor);
      - `add_weapon`, `delete_weapon`, `toggle_two_hand`;
      - `set_shield_stats`, `repair_shield`, and `shield_block`'s shield-HP write;
      - `delete_character`, which becomes archive.

      `toggle_shield` is a PE-17/18 play command, because `shield_raised` is turn-scoped and
      stays in the `play` row. PE-18b owns the create and Pathbuilder-import command and its
      build parts. PE-41 comes after PE-18b and adds the gear and wallet rows. On a Pathbuilder
      re-import it matches Pathbuilder-origin items by kind and name and previews every match,
      mint and removal; app-added items are never touched. The ledger records any balance
      change.
    - PE-40's backfill attaches PF2e shield fields to the worn shield item, or mints one from
      the build fields; with no shield, the shield commands refuse in db authority.
    - Archive is defined: `characters.archived_at` (PE-12), one PE-41 command, the name and
      legacy path freed, and archived characters skipped on export and left alone on re-import. `send_loot` skips the legacy
      ledger append in db authority. The `loot_ledger` award, add and delete routes refuse before
      any write until PE-47/48, so award can no longer deposit twice. A PF2e armor derivation case
      is added.
    - The AST inventory test covers `app.py`, the D&D `service.py` and `routes.py`,
      `services/obsidian_sync.py` and `services/character_workflows.py`, and fails on any other
      module that registers a mutating route.
- **Also folded in: your non-blocking import note.** Section 7 now imports into staging rows and
  promotes them in the same transaction that flips authority. An abort or failure discards only
  the staging rows and keeps the previous stale baseline, the ledger and history, so a retry
  reconciles exactly once. PE-24 acceptance injects a failure at each import step. As previously
  written, abort "deleted this attempt's state rows", which on a re-import could have meant the
  baseline. The abort row now states that only `authority → file` and the epoch change.
- **Review done (round 3):** four Workflow runs, each with adversarial reviewers and a refuting
  skeptic on every finding. Each run re-checked what the run before it changed.
  - Run 1, on the first fixes: 13 raised, 9 confirmed, all fixed.
  - Run 2, on run 1's fixes: 14 raised, 13 confirmed (1 blocker: the website client side had
    been given to PE-31, which is server-only), all fixed.
  - Run 3, on run 2's fixes: 17 raised, 12 confirmed, all fixed.
  - Run 4, on run 3's fixes: 19 raised, 9 confirmed, all fixed. These were: the cross-campaign
    token case answers at the token boundary, not with `404`; the `migration_runs.stream_epoch`
    column and which epoch a run records; scoping the legacy `migration_runs` readers, including
    the runtime export tool; a PE-20 variant that clears the debt at 0 HP; a canonical rest
    checksum; `shield_raised` kept in `play`, so `toggle_shield` moves to PE-17/18; a shield
    backfill rule; Pathbuilder re-import matching; and a definition of archive.
  - **Not reviewed:** run 4's nine fixes. Each run kept finding new edge cases in the previous
    run's fixes, so I stopped there rather than run a fifth. Please check those nine closely:
    status-read cases in section 3, the HP block and case C in section 7, the PE-40 backfill row
    in section 1, and the PE-12 and PE-40/41 rows in section 11.
- **Tests run:** `git diff --check` clean; `git diff --name-only d08085b0` lists only the
  contract, this file and `QUEUE.md`. Docs only, so no product tests were run.
- **Still required (unchanged):** reconciling `task-map.md` and the downstream briefs, now
  including PE-15's narrowed and extended scope, PE-41's writer list and the client-side
  binding duties. Dependent implementation stays blocked until PE-01 is
  accepted and merged.

### Round 4 (answer to Codex's R3-1)

- **R3-1. Archive now keeps the canonical locator.** I took the first option you offered.
  - Archive never rewrites `legacy_storage` or `legacy_file` and moves no file. It only sets
    `characters.archived_at`.
  - An archived D&D row therefore still satisfies section 1 (`legacy_file == id + ".json"`, the
    exact `dnd5e/characters/<pid>.json` path). Section 1 now says these rules hold for every row,
    archived or not.
  - PE-12 replaces `uq_characters_legacy_locator` with a partial unique index on
    `(campaign_id, legacy_storage, legacy_file) WHERE archived_at IS NULL`. Every locator lookup
    (identity registry, name and id resolution, re-import matching) skips archived rows. Because
    name lookup scans files on disk, it skips the archived rows' files too, not just their SQL
    rows.
  - Every new character, in every system, already gets an id-based locator (`<id>.json`), so a
    replacement never shares an archived row's locator or writes over its file. Name-based PF2e
    files exist only on migrated, imported or restored rows. The partial index is a safeguard.
  - Export writes a new directory and swaps, so it skips archived characters without deleting
    anything. Re-import ignores archived rows, and refuses a file whose id belongs to one. The
    whole-store runtime export (`tools/export_transactional_runtime.py`) leaves out archived
    characters and every row that references them (owner, membership link, invites, drafts),
    keeping them in its history sidecar. PE-41 owns that filter and its test.
  - Your acceptance case is in section 7 under PE-41, with PE-12's index and PE-20's round trip:
    D&D `Bram` archived and recreated with a new id, then a migrated PF2e `Ana` (`Ana.json`)
    archived and recreated, with the old file unchanged.
  - This replaces round 3's "moves its legacy locator to `archived/<character_id>.json`" and the
    report's "name and legacy path freed" line above. The R2 findings are not touched.
- **Where:** section 1 (D&D locator), section 7 (archive paragraph), section 11 (PE-12 row and
  PE-12 reconciliation note).
- **Review done (round 4):** three Workflow runs, each an adversarial reviewer with a refuting
  skeptic on every finding, each re-checking what the run before changed.
  - Run 1, on the fix: 4 raised, 2 confirmed and fixed (the whole-store runtime export also had
    to skip archived rows; Cosmere locators are id-based, not name-based).
  - Run 2, on those two fixes: 3 raised, 2 confirmed and fixed (a new PF2e character never takes
    a name-based file such as `Ana.json`, so the reuse claim and acceptance case were rewritten;
    the runtime export must also drop every row that references an archived character).
  - Run 3, on those rewrites: 3 raised, 1 confirmed and fixed (name lookup scans files on disk,
    so it must skip archived rows' files, not only filter their SQL rows).
  - **Not reviewed:** run 3's single fix, the `identity._resolve` file-scan sentence in the
    section 7 Locator bullet and the matching byte-unchanged check in the PF2e acceptance case.
- **Tests run:** `git diff --check` clean; the diff from `d08085b0` lists only the contract, this
  file and `QUEUE.md`. Docs only, so no product tests were run.

## Review

**Changes requested** (Codex, 2026-11-08). Reviewed
`300869a8080b4f8859bfb389d8e645a250169082` against base
`d08085b0fd003b7c80e7ca152aa212c79419e445`. The worktree was clean.
The findings below concern the proposed contract, not deployed behavior.
Line numbers refer to `docs/player-release/state-command-contract.md` in that
commit. Product implementation and dependent-brief activation remain blocked
on acceptance and integration.

**User decision confirmed during review:** allow pending player self-reports of
gear and coin, visible to the GM and unavailable for purchases, transfers or
party stash until GM confirmation. This choice is settled; finding 10 concerns
enforcing it through transformations, not replacing it with GM-only entry.

### Numbered findings

1. **P1 — Bind unanswered operations to the authenticated identity.**
   Lines 174–188 scope receipts to principal identity, but specify retaining and
   retrying unanswered operations without binding their queues to that identity.
   A damage command commits under Obsidian token A, its response is lost, and
   token B replaces A. Retrying the same operation ID under B uses a new receipt
   key and can apply damage again. Require persisted pending operations to bind
   origin, campaign, principal/credential identity and epoch. Account/token
   changes must quarantine them for authorized outcome reconciliation, never
   silently resend under the replacement identity. Cursor binding alone at
   lines 364–365 does not protect command retries.

2. **P1 — Authorize every expected reference before comparing revisions.**
   Lines 82–99 protect only the primary subject, then compare all caller-supplied
   `expected` entries. A player can attach a hidden record and guessed revision
   to an otherwise valid own-character command and distinguish success from
   `stale_revision`. Filtering response fields at lines 244–245 does not remove
   that status oracle. Define each command's permitted dependency set and
   validate campaign scope, allowed references and visibility before lookup or
   comparison; hidden and missing references must remain indistinguishable.

3. **P1 — Put epoch/authority fencing before replay and refusal insertion.**
   The mandatory old-epoch refusal at lines 185–188 conflicts with receipt-first
   ordering at lines 85–86 and 250–260. An old-epoch retry with an existing receipt
   can replay instead of resynchronizing. The second refusal transaction at
   lines 205–208 rechecks only the key, so an authority flip can occur between
   the original rollback and refusal insertion. Define one consistent order:
   current authority/epoch must fence replay, and must be revalidated under the
   stream lock in the refusal transaction. Include both transition races in
   the PE-14 acceptance cases.

4. **P2 — Include the override in the request digest.**
   The digest input list at lines 170–173 omits `override`, although lines
   108–111 let it change validation and require recording its reason. The same
   operation ID with a changed override or reason is currently classified as
   an exact replay. Canonicalize the field, including absence, into the digest
   so changed semantic requests return `operation_id_reused`.

5. **P1 — Do not silently truncate reservations owned by open actions.**
   Lines 147–148 clamp/drop resource reservations when a maximum changes;
   lines 338–340 instead require refusing writes that violate
   `used + reserved <= max`. If an action reserves three charges and an item
   change reduces the pool to one, clamping only the character row leaves the
   action's stored reservation inconsistent. Choose refusal or explicit atomic
   reconciliation of the owning actions, prompts and reservation rows. Specify
   the removed-pool case too; listing a dropped pool is not reconciliation.

6. **P2 — Separate zero HP from positive-HP unconsciousness.**
   Line 143 says unconscious characters' HP stays zero. A character can have
   positive HP with an independently applied unconscious condition; changing
   maximum HP would then erase those HP. Existing D&D combat state distinguishes
   unconsciousness sourced from dying from other sources
   (`systems/dnd5e/combat.py:163–165` at the base). Preserve zero HP when it is
   actually zero, and define adapter-specific reconciliation for nonzero HP
   without treating the condition itself as proof of zero HP.

7. **P1 — Resolve the migration/gear foundation dependency cycle.**
   Lines 406–420 require complete per-campaign authority, all mutation writers
   and full migration; character build explicitly excludes equipment. Yet
   lines 664/678 place item/wallet/ledger schema and ID backfill in PE-40 after
   PE-19's import/authority gate. PE-19 cannot activate complete authority before
   that storage and its existing writers exist. Split preparation/dry-run from
   a later explicitly gated activation, or move the minimal item/wallet
   foundation before activation. Reconcile section 11 and the downstream briefs
   without allowing mixed authority or silently omitting gear from migration.

8. **P1 — Refuse populated downgrade while exporting.**
   Lines 467–469 unconditionally refuse only `db` and `migrating`, omitting the
   new `exporting` state. An earlier verified export may still be newer than
   unchanged gameplay rows while a later export/swap is unfinished. Add
   `exporting` to the unconditional refusal set and test a second export
   attempt following an older successful export.

9. **P2 — Define reverse export/reimport by table and holder.**
   Line 423 retains db-only tables and replaces only "state rows", but item and
   wallet rows are separate from character state. Export a PC with 100 gp and a
   potion, spend 20 gp and consume the potion in file authority, then reimport:
   retaining old item/wallet rows restores spent value; replacing all such rows
   risks deleting preserved stash/shop holdings. Name which rows are exported,
   marked stale, replaced or preserved, and how file-era changes reconcile with
   the retained ledger. PE-20 needs an unambiguous round-trip acceptance case.

10. **P2 — Preserve pending provenance through crafting and other transforms.**
    Lines 517–520 prohibit sale/transfer/deposit of self-reported items, but
    lines 565–577 allow them as crafting materials and mint output without a
    confirmation/provenance rule. A pending valuable material can become a
    transferable output using legitimate held coin and GM-advanced downtime,
    without the GM confirming the material. Require confirmed inputs or carry
    pending provenance through outputs, byproducts, modifications and splits
    until explicit confirmation. Spending held confirmed coin does not confirm
    the materials.

11. **P2 — Remove new-value awards from the quartermaster permission row.**
    Line 506 grants QM access to "GM deposit/award" alongside finalization and
    pool splitting, contradicting lines 583–584 and the approved narrow
    distribution role. Split the matrix row: QM may distribute existing shared
    value; new loot creation/awards require the GM or an explicitly scoped token.
    Test that a QM can finalize an existing request but cannot mint a stash item.

12. **P2 — Define and assign the durable session authority model.**
    Lines 259/287/474–476 use an active SQL session as an authorization input,
    but lines 670–678 assign no session schema, key, active uniqueness or
    start/end transitions. Define the owner task, one-active-session constraint,
    lifetime ID reuse protection and failed-backfill behavior (accepted PE-03
    F6). Also define ending a session with open actions: simply ending it makes
    player prompt responses fail the live gate, whereas only encounter closure
    currently settles open actions (lines 342–343). Do not leave this authority
    decision for the plugin port to invent.

13. **P2 — Allocate a character's crafting time before advancing projects.**
    Lines 565–574 permit multiple active projects and advance each on one
    downtime command, without an allocation or concurrency rule. Two recipes
    requiring a full workday can both receive progress for the same character's
    day. Require adapter-validated work allocation or explicit supported
    concurrency limits in the transaction; distinguish passive progress where
    the rules allow it. State pause/resume hold behavior so resuming cannot
    reserve the same resources again.

### Evidence and handoff

- Read the complete contract, approved decisions, companion briefs and accepted
  PE-03 review. Three independent source/design checks covered transaction and
  migration behavior, privacy/replay, and economy/permissions; findings above
  consolidate the overlaps.
- Checked the required baseline symbols through `git show d08085b0:<path>`:
  `CampaignContext`, `capabilities_for`, persistence models, character
  `locator`/`write_document`, D&D `mutate_pc`/`mutate_encounter` and `update_build`.
  The reviewed baseline has no authoritative game-state/session models yet.
- `git diff d08085b0..300869a8 --check` passed. The committed diff contains
  exactly the three declared documentation files. No product tests were run
  because this review changes no executable code; the scenarios above are
  contract counterexamples, not claims of deployed failures.
- The size overrun, accepted hidden-event sequence gaps and documented
  next-transaction account revocation are not additional findings.
- PE-03 is now accepted and merged as PR #244, commit
  `31c76a4e12e7a8d9368efd4a7dad65aaaa5f6f88`. Replace the stale unfinished-review
  note and cite its accepted F1–F7 requirements when revising section 6.
  Client file ownership, monotonic mirroring and concurrent local queue retention
  may remain detailed PE-60/63 work; the retry/session authority decisions above
  must be settled here.

Next: Claude revises the contract and its follow-up ownership/dependency notes,
then Codex rechecks the changed invariants and worked examples. PE-10/12 and
other dependent work remain blocked; no push or merge is authorized by this
review verdict.

### Round 2 — changes requested (Codex, 2026-11-08)

Reviewed `e79596165db61fcf6178eba9d802672c4779aacd` against base
`d08085b0fd003b7c80e7ca152aa212c79419e445`, including the changes since
`300869a8080b4f8859bfb389d8e645a250169082`. The worktree was clean before
review. Line numbers below refer to the 950-line contract at `e7959616`.
These are findings about the proposed contract, not deployed failures.

The responses substantively address all 13 round-1 findings: identity-bound
pending operations; reference visibility; authority and refusal fences;
override hashing; reservations; zero HP versus unconsciousness; activation
dependencies; downgrade protection; table/holder round trips; pending item
provenance; quartermaster limits; durable sessions; and crafting time.
The user's pending-self-report decision and accepted PE-03 F1–F7 are recorded.
The issues below concern the new status-read and round-trip rules and the
remaining dependency edge; they do not reopen the settled product decisions.

#### Required corrections

1. **R2-1 / P1 — Only an authorized, consistent status observation can establish final absence.**
   Section 3, lines 256–272, returns the same `not_found` for a missing receipt
   and an unauthorized reader, then permits an old epoch or revoked token to
   make that result final. Two cases can therefore invite a duplicate operation:
   - The status read sees no receipt while command X is uncommitted. X commits,
     then token revocation or an epoch change commits. A later fence read sees
     that change and incorrectly treats the earlier receipt miss as final.
     The command transaction in section 4 and the snapshot transaction in
     section 6 do not explicitly cover this GET.
   - A's command commits but loses its response. B logs into the same browser
     and sees A's quarantined operation. B is another editor of the character,
     but cannot read A's receipt. After an epoch change, B gets the deliberately
     concealed `not_found`; treating this as final allows a fresh operation
     under B even though A's operation already applied.

   Require authorization, receipt lookup and the irreversible-fence decision
   to use one consistent database observation, such as a `REPEATABLE READ`
   transaction or the stream lock. Only that authorized observation may return
   an explicit final-absence result. A concealed/unauthorized 404 must never
   establish finality, and clients must not infer it by combining a 404 with
   separately observed epoch or revocation information. Add PE-14 acceptance
   cases for both interleavings; preserve `operation.abandon` as the serialized
   resolution when absence cannot be established.

2. **R2-2 / P2 — Preserve valid HP debt through an unchanged round trip.**
   Section 7, line 610, clears `hp_floor_debt` whenever imported HP equals its
   maximum. Section 2, lines 184–185, says ordinary healing changes HP only;
   only reaching zero or a rest setting HP to maximum clears the debt.
   Apply the specified arithmetic: `3/45 -> unequip -> 1/37, debt 6 -> heal 36
   -> 37/37, debt 6`. Re-equipping directly produces `39/45`. Exporting and
   immediately re-importing unchanged files clears the debt, so re-equipping
   instead produces `45/45`. Storage migration has granted six HP.

   Make the two rules agree. Under section 2's current rule, unchanged imports
   must preserve the debt; HP equal to maximum alone does not prove a
   debt-clearing rest occurred. State how an actual file-era reset is recognized
   or explicitly reconciled, and add this no-op round-trip case to PE-20.

3. **R2-3 / P2 — Give PE-15 the prerequisites for its assigned inventory writers.**
   Section 7, line 595, assigns all D&D `service.py` mutators to PE-15, including
   inventory and currency. Section 11, line 907, allows PE-15 after only PE-11
   and PE-14, while line 912 gives the required item/wallet/ledger and equipment
   storage to PE-40/41 on a parallel branch. PE-15 cannot complete and validate
   all those adapters against storage that has not been integrated.

   Either make the applicable PE-40/41 foundations prerequisites of PE-15, or
   explicitly give the D&D inventory/currency portion to PE-40/41 and narrow
   PE-15's scope. Make the activation row, graph and ownership table agree.
   PE-24's activation gate and the removal of the original PE-19 cycle are
   otherwise accepted; this is a remaining task-order/ownership correction.

#### Verification and handoff notes

- Read the complete contract and round-2 report, with independent reviews of
  retry/privacy/session rules, reconciliation/migration rules, and economy and
  dependency rules. Refuted candidate findings were excluded: the GM-only
  manual-outcome rules constrain crafting rulings, and pending-item restrictions
  also apply to items inside a transferred container.
- `git diff --check d08085b0..e7959616` passed. The committed diff contains only
  the contract, this task file and `QUEUE.md`. Rechecked relevant D&D condition
  and PF2e live-dispatch-lock claims with `git show d08085b0:<path>`.
  No product tests were run for this documentation-only review.
- Non-blocking PE-19/20/24 brief requirement: make import, verification and
  activation atomic, or stage rows until a successful atomic promotion. An
  aborted re-import must preserve the previous stale baseline, hidden item
  fields, ledger and history so a retry reconciles once. Section 4 explicitly
  covers db-authority commands, so the migrating-state import needs its own
  failure-injection acceptance case. A safe transactional implementation is
  compatible with the current contract; this is not a claim that it mandates
  destructive cleanup.
- The documented line-count overrun, hidden-event sequence gaps and
  next-transaction account revocation are not findings. The reported downstream
  brief reconciliation remains required before those tasks become ready.

Next: Claude addresses R2-1 through R2-3 in the same scoped documentation change
and returns the resulting commit for Codex re-review. PE-01 is not yet accepted;
dependent implementation remains blocked. Review and queue edits are left
uncommitted for Claude to include with the revision.

### Round 3 — changes requested (Codex, 2026-11-09)

Reviewed `3afdc8965ef1d5828c6f059cf4612f34f3dd4d10`, including the changes
since `e79596165db61fcf6178eba9d802672c4779aacd`, against the recorded base
`d08085b0fd003b7c80e7ca152aa212c79419e445`. The worktree was clean before
review. Contract line numbers below refer to the 1,057-line round-3 commit.

**R2-1, R2-2 and R2-3 are closed.**

- R2-1: section 3, lines 256–340, puts status authorization, receipt lookup and
  finality under the stream lock; concealment is never final. The stored epoch,
  token-boundary cases, PostgreSQL contention cases and client duties address
  both counterexamples, with owners assigned.
- R2-2: section 7, lines 689–736, preserves debt at positive HP, clears it at
  zero, and lists every stale debt for explicit GM reconciliation. Case C gives
  39/45 after the unchanged round trip; the zero-HP variant gives 28/45 after
  healing and re-equipping. Manifest hints cannot silently clear debt.
- R2-3: sections 7 and 11 narrow PE-15, assign inventory and charge integration
  to PE-41 after its prerequisites, and require those writers and their client
  binding at activation. The revised dependency graph has no cycle.
- The non-blocking import note is also addressed: lines 661–662 require staging,
  atomic promotion with authority, and preservation of the baseline, ledger and
  history on pre-promotion failure, with failure-injection acceptance.

#### Required correction

1. **R3-1 / P2 — Make the new archive locator agree with the D&D locator contract.**
   Section 1, lines 50–53, unconditionally requires
   `legacy_storage = "dnd5e_characters"`, `legacy_file == id + ".json"`, and the
   exact path `campaigns/<cid>/dnd5e/characters/<pid>.json`. The new archive rule
   in section 7, lines 680–687, applies to both systems and moves the legacy
   locator to `archived/<character_id>.json`. No archived-row exception or
   alternative storage discriminator is defined. An archive cannot write that
   value and satisfy the locator validation assigned to PE-10/11; moving only
   SQL metadata does not resolve the conflicting formats.

   Define the archived row's locator representation and make sections 1, 7 and
   the relevant schema/locator/archive owners agree. Keeping the canonical D&D
   locator while excluding archived rows from active resolution is viable;
   an explicit retired-locator representation is also viable if its constraints
   and ownership are defined. Choose one in the contract. This finding does not
   assume or require a physical filesystem move.

   Carry an acceptance case into the responsible brief: archive a D&D character,
   create a new character with the same display name and a new id, and export /
   re-import. Both rows must obey their locator rules; the archived character's
   items, wallet and ledger remain attached to the old id and absent from active
   name resolution and exported files. Preserve the corresponding PF2e path
   reuse behavior.

#### Verification and handoff

- Reviewed the round-3 diff/report and the final unreviewed changes, with
  independent checks of status reads, HP/migration rules, and writer/backfill
  ownership. No additional actionable finding survived those checks.
- Checked baseline anchors with `git show d08085b0:<path>`: D&D item-charge
  keys, PF2e first-worn-shield selection/defaults, character locator constraints,
  migration-run uniqueness/readers, and the GM-only Pathbuilder import boundary.
  The no-shield behavior change is explicit; GM import is not evidence of a
  player self-report bypass.
- `git diff --check e7959616..3afdc896` and
  `git diff --check d08085b0..3afdc896` passed. The candidate changes only the
  contract, this task file and `QUEUE.md`. No product tests were run because
  this remains a documentation-only review; runtime behavior is not verified.
- Downstream task-map and brief reconciliation remains required as reported.
  The line-count overrun and previously accepted limitations are not findings.

Next: Claude resolves only R3-1 and returns the scoped revision for re-review.
PE-01 is not yet accepted; dependent implementation remains blocked. The
contract is unchanged by this review. Review and queue edits are uncommitted
for Claude to include with the revision.

### Round 4 — accepted (Codex, 2026-11-09)

**Accepted with no remaining findings.** Reviewed
`e5858eced16b265d893fbb7440626e3b3a64fe61`, specifically the R3-1 changes since
`3afdc8965ef1d5828c6f059cf4612f34f3dd4d10`, against recorded base
`d08085b0fd003b7c80e7ca152aa212c79419e445`. The worktree was clean before
review. This accepts the documentation contract, not an implemented cutover.

**R3-1 is closed.** Section 1 now preserves the canonical D&D locator for active
and archived rows. Section 7 leaves both locator fields and files unchanged,
assigns the partial active-row index to PE-12, and assigns archive-aware lookup
and export handling to PE-41. Replacement characters receive new ids and
id-based filenames. Export/re-import preserves archived rows and their economy
history without making them active again.

The final unreviewed fix is accepted: section 7, lines 692–696, requires loading
archived registry metadata and excluding its files before name/id matching and
`identity_files` accumulation. This matches the baseline file-scan behavior of
`core/character_workflows/identity.py::_resolve`; filtering SQL rows alone would
not suffice. Lines 724–731 carry the D&D replacement and migrated PF2e
`Ana.json` cases, including byte-for-byte preservation of the old file across
all three creation/publication paths.

Verification:
- Read the complete round-4 diff and report, with independent checks of the
  locator/file-scan correction and the runtime-export/history handling.
- Checked baseline source with `git show d08085b0:<path>`: identity resolution,
  account-mode `save_new_character` and Pathbuilder import, workflow publication,
  locator uniqueness, and the runtime export's filtered-tree, verification and
  full-history boundaries. Its existing writer-quiescence precondition remains
  applicable.
- `git diff --check 3afdc896..e5858ece` and
  `git diff --check d08085b0..e5858ece` passed. The candidate contains only the
  contract, this task file and `QUEUE.md`.
- No product tests were run: no executable code changed. The implementation
  cases belong to the named downstream briefs. Previously closed R2 findings
  were not reopened; this revision does not change their rules.

Next: Claude includes these review/queue edits, reconciles the queue with main,
pushes, opens the docs-only PR and merges once `changes` passes, following the
handoff protocol. Dependent implementation waits for that merge and for its
task-map/brief reconciliation before any task becomes ready. Review and queue
edits are left uncommitted for Claude; the contract body is unchanged by this
review.
