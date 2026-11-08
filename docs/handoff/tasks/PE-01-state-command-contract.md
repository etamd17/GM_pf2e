# PE-01: Define player state, commands and authority contracts

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** ready
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
- **Changed:** Pending.
- **Tests run:** Pending; docs-only checks expected.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
