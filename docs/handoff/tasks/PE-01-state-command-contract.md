# PE-01: Define player state, commands and authority contracts

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** review (codex re-review of round 2)
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
