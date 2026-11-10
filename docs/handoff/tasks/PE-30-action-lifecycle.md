# PE-30: Specify the supported action lifecycle

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked
- **Branch:** `claude/pe-30-action-lifecycle` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** PE-01, PE-02, accepted PE-13 repository contracts and PE-14 command-receipt/event-outbox primitives. Do not start against guessed primitive APIs.
- **Context:** [approved plan](../../player-release/implementation-plan.md), [decisions](../../player-release/decisions.md), [existing sync/VTT work](../../player-release/existing-obsidian-vtt-work.md).

## Goal

Define one resumable, auditable action workflow for players, GMs and Obsidian,
covering PF2e, D&D 2014 and D&D 2024. Make costs, choices, dice, saves, reactions,
cancellation and corrections precise before implementation is split into small
reviewable tasks.

## Approach

This task produces `docs/player-release/action-lifecycle.md` and focused follow-on
brief proposals. It does not implement the action engine or change rule outcomes.
It elaborates, per rules profile, the fixed invariants of PE-01 section 5 (action
row, states, responders, action commands, dice, costs, clocks, effects); it does not
redefine them. A needed change revises PE-01 and goes back to review.

Inspect and map the current seams:

- PF2e `app.py::apply_pc_delta`, `_apply_hp_delta`, `_apply_condition_change`,
  `_obsidian_resolve_roll` and the current turn handler; preserve established
  persistence/lock ordering until PE-13 replaces the authority.
- `services/active_effects.py::{compute_for_action,instantiate_effect,resolve_save,consume_triggered,expire_round_effects}` and `services/session_ops_rolls.py::{roll_check,roll_damage}`.
- D&D `systems/dnd5e/service.py::{combat_action,resource_action,vitals_action,encounter_view}`, `systems/dnd5e/combat.py`, and `systems/dnd5e/dice.py`.
- `services/obsidian_sync.py::process_command` and `_dispatch` as transport
  clients of common services; the plugin must not own a rules engine.
- PE-13/PE-14 interfaces, and VTT receipt patterns only where PE-01/PE-03 identify
  reusable code. A VTT mirror or page-memory attempt is not authoritative state.

The specification must include:

1. **Command/result schema:** operation and stage IDs, request digest, actor and
   controlling principal, campaign/session/encounter IDs, rules profile/content
   version, revisions, legal targets, input provenance, and causal event links.
   Same key/different body is rejected; same committed request returns its result.
2. **Transition table:** over section 5's fixed action states (`open`, `completed`,
   `cancelled`), target states (`pending | resolved | held | voided`) and prompt kinds
   (`roll|choice|save|reaction|gm_ruling`). There is no persisted resolving, expired
   or failed state. Define legal initiators and authorized responders for every
   transition. No database transaction remains open while awaiting a person.
3. **Costs and partial commitment:** define each feature's `commit_point` (reservation and
   spending point) per rules profile, with PE-02's support level, within section 5's
   fixed cost rules: `used + reserved ≤ max` for competing actions, cancellation
   releases reservations and does not refund spent costs, and a spent cost is
   refunded only through `action.correct`. Persist each stage
   with its receipt/event. A reaction cannot retrospectively change an already
   finalized result without an explicit correction.
4. **Dice:** digital and physical input share resolution; capture individual raw
   dice whenever natural values or multiple-die selection affect the rule. Record
   physical-entry provenance and who supplied it. Validate range/count, distinguish
   raw dice from an explicitly entered total, and never silently reroll after a
   retry or reconnect. Unknown necessary input prompts rather than guessing.
5. **Pending responses:** identify who may supply each save/reaction within
   section 5's responder rules, and what happens after logout, disconnect, target
   removal or campaign change. Nothing times out: the only clocks are section 5's
   three GM-advanced clocks, a disconnect never advances them, a save is never
   defaulted, and a target that is no longer valid commits as `held`. Define resume,
   delegated GM resolution and cancellation paths.
6. **Effects and consequences:** system-specific source/target IDs, stacking,
   lifetime anchors, triggered changes and linked follow-up actions. Revalidate
   applicable revisions before commit and resolve multi-target concurrency without
   double costs or lost outcomes. Manual physical-table facts (range, cover, area
   membership) remain explicit inputs; no spatial simulation is required.
7. **Corrections and privacy:** a GM correction describes the deltas it reverses,
   validates current state and creates new causal events. Keep hidden targets,
   rolls, effects and GM-only reasons out of unauthorized projections. Ownership
   and permissions are rechecked when committing a delayed response.

Provide representative sequences for each of the three supported rules profiles,
using PE-02's verified coverage fixtures. Include a normal action, a pending
save/reaction, a multi-target effect, an interrupted/cancelled action and a GM
correction. Separate orchestration behavior from rule-specific mechanics.

## Acceptance

- [ ] The transition table has no unspecified cost, cancellation, retry, disconnect or held-target edge. Every transition names its principal, validation, writes, event and recoverable response.
- [ ] Sequence diagrams show lost response/retry, disconnect during a pending save, role removal before response, target change, resource contention and correction after intervening changes.
- [ ] PF2e/D&D 2014/D&D 2024 examples use the same orchestration contract and their own adapters; unsupported content uses an explicit ruling path without silently narrowing PE-02's agreed coverage.
- [ ] Run the unchanged baseline regressions: `python -m pytest -q tests/test_session_ops_rolls.py tests/test_active_effects.py tests/test_combat_turn_advance.py tests/test_dnd5e_combat.py tests/test_dnd5e_service.py tests/test_obsidian_sync.py --strict-markers`. Record actual results/environment; these validate the reused baseline, not the proposed lifecycle implementation.
- [ ] The document specifies future executable cases named `test_same_command_applies_cost_once`, `test_physical_and_digital_inputs_share_resolution`, `test_pending_response_survives_restart`, `test_cancel_releases_only_uncommitted_reservations`, `test_disconnect_never_resolves_or_defaults_a_prompt`, `test_role_removed_before_response_cannot_commit`, and `test_correction_preserves_intervening_changes`, to run as `python -m pytest -q tests/player_release/test_action_lifecycle.py --strict-markers` in the later implementation task. Do not claim these prospective tests were run here.
- [ ] Follow-on proposals fit separately reviewable changes and name PE-13/PE-14 APIs and exact adapters. They name the owner of the action and effect tables (PE-01 section 11), place activation in PE-32..34 after PE-24 and PE-30, and give every task that makes a website writer send envelopes section 3's client side: pending-operation binding, quarantine, status-read handling, and rerunning the client cases. No second rules engine or parallel state store is introduced.
- [ ] `git diff --check` passes; only the lifecycle document and task report/review are changed by this task.

## Out of scope / do not touch

- No product rules, storage, endpoint, template or plugin implementation in this specification task.
- No new VTT geometry, wiki, cross-system mechanics sharing or additional simultaneous live-session support.
- No blanket perfect-undo promise, automatic physical-dice reroll, or silent approval on timeout.
- No coverage reduction, production data changes or deployment.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
