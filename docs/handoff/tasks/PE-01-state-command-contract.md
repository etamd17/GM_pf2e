# PE-01: Define player state, commands and authority contracts

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** review (codex)
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
- **Changed:**
  - `docs/player-release/state-command-contract.md` (new): the contract.
  - This report.
  - The `QUEUE.md` row and Log line.
- **Decisions taken here for review** (each is definite in the contract):
  - Per-campaign authority covering all character, encounter, economy and event state.
  - Exactly-once features are db-authority only.
  - Two character state domains (`build`, `play`). Items, wallets and the ledger are separate rows.
  - PC vitals never copied into the encounter.
  - "Live" is defined by a durable SQL session record, not by the process slot.
  - Event visibility is fixed when the event commits.
  - Server-minted `action_id`.
  - Every final answer ends an `operation_id`.
- **Decision the user may want to revisit:**
  - Players keep a `self_report` path for gear and coin received at the table. It is always
    flagged, ledgered, visible to the GM and reversible, and never counts as a purchase. This
    preserves today's D&D `inventory_action('add')`/`set_currency` and PF2e `/api/add_item`.
  - Self-reported coin and items stay out of purchases, transfers and the stash until the GM
    confirms them.
  - The alternative was making all item and coin creation GM-only. That would remove an existing
    player ability, so I did not choose it without the user.
- **Review done:**
  - Two independent adversarial reviews of the first draft: one against the code, decisions and
    dependent briefs, one hunting concurrency, privacy and economy holes.
  - They found 9 blockers and 37 should-fix items between them, largely overlapping. All were
    folded in, among them held currency, action-id collision, private notes in events, stale
    authorization reads, migration-gate lost writes, receipted refusals locking retries,
    reconciliation at 0 HP, and inventory minting.
  - A third adversarial re-check of the rewrite found 2 more blockers, both in the rollback from
    db to files: writes could land during the export, and db-only economy state would be
    destroyed. It also found 10 smaller gaps. All were fixed: a new `exporting` state, refusal
    instead of forced cancellation, the epoch carried in the envelope, per-projection visibility,
    one full check order, the clock revision on downtime advances, crafting holds, currency moves
    on transfer accept, and the GM override field. This last round was not re-reviewed a fourth
    time.
- **Tests run:**
  - `git diff --check`: clean.
  - `git diff --name-only origin/main`: contract, this file, `QUEUE.md`.
  - Docs only, so no product tests were run.
  - Code claims were checked with `git show d08085b0:<path>`. The worktree uses a sparse
    checkout with docs only, and I did not change the shared sparse setting.
- **Skipped / not verified:**
  - No pytest run, since nothing executable changed.
  - The Obsidian v0.9 branch was used only through `existing-obsidian-vtt-work.md`. PE-03 (Codex)
    is not finished; its findings may refine the PE-60 cursor/digest notes in section 6.
  - No coverage claims are made (PE-02 owns them).
- **Known wrong / limitations:**
  - **Over budget.** The contract is 683 lines against the brief's rough 500. Three review
    rounds added the extra length; splitting it would have broken the cross-references.
  - **Hidden-event gaps.** Replay gaps reveal that a hidden event happened, though not what.
    This is accepted in writing in section 6.
  - **Account-level revocation** takes effect from the next transaction, not mid-transaction.
- **Follow-ups (not done here):**
  - PE-12/13/14 briefs should be reconciled to section 11. PE-12 now also owns
    `encounter_combatants` and `campaign_state_authority`; PE-14 owns `campaign_grants` and
    token scope.
  - PE-30 must name the owner of the action/effect tables.

## Review
<!-- Codex appends numbered findings or acceptance. -->
