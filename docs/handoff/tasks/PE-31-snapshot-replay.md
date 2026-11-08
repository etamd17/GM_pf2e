# PE-31: Inactive server snapshot and replay primitives

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked
- **Branch:** `claude/pe-31-snapshot-replay` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** PE-01, PE-03, PE-13 authoritative state repositories and PE-14 durable receipts/events/outbox. Record accepted primitive revisions before implementation.
- **Context:** [approved plan](../../player-release/implementation-plan.md), [existing sync/VTT evidence](../../player-release/existing-obsidian-vtt-work.md), and the PE-03 reuse report.

## Goal

Build the bounded server primitive for a consistent state snapshot, durable
watermark and authorized replay. This task does not connect live endpoints or
clients: they still need the accepted state writers and a verified cutover.

## Approach

Implement the bounded snapshot/replay contract using PE-13/PE-14, not a new ledger.
Allowed product files are the supplied event/state repository modules and
`services/session_events.py`, plus focused tests. Keep the primitive unreachable
from live routes. Target roughly 500 human-reviewed changed lines; split further
before coding if needed. PE-31a owns endpoint/system adapters after PE-15/18
writers and PE-19/20 migration/recovery acceptance; PE-31b owns shared-hub
coordination after PE-31a. They require their own briefs before implementation.

Current baseline seams (locations from `7aeb9549`; locate by symbol on the
accepted integration baseline):

- `app.py::player_state` (`/api/player_state`), `_get_tracker_state`,
  `_obsidian_sync_snapshot`, `sse_stream` (`/api/events`), `sse_broadcast`,
  `_sse_broadcast_locked` and the `_close_sse_subscribers_*` revocation helpers.
  The current SSE replay ring stores GM/player frames and in-memory sequence IDs;
  it is not a durable event watermark across restart.
- `templates/_sse_hub.html`: `window.appSSE`, one lazily opened EventSource per
  tab, `last_event_id`, `connected` handling and reconnect/wake behavior.
- `systems/dnd5e/routes.py::{api_combat_state,emit_encounter}`
  (`GET /api/dnd5e/combat`) and
  `systems/dnd5e/service.py::encounter_view(for_player=...)`.
- `services/obsidian_sync.py::{current_state,state,events,process_command}` and
  `core/obsidian_sync.py::read_events`; PE-03's selected generation guards,
  connection scoping and capability negotiation.
- Extend `services/session_events.py` and repository APIs supplied by PE-14.
  The preceding legacy seams are read-only context for later adapters; do not
  edit `app.py`, route policy, system routes, the shared hub or the plugin here.

The implementation must establish these guarantees:

1. Read the projected snapshot and its durable campaign/event watermark from
   one consistent database view. Return rules/encounter identity, state revisions,
   projection contract version and stream identity/epoch where required. A
   revision and an event cursor are distinct values, not interchangeable counters.
2. Catch up strictly after the snapshot watermark and bridge to live delivery
   without a gap. Coordinate registration and replay boundaries so an event
   committed during snapshot fetch or subscription appears either in the
   snapshot or subsequent delivery. Repeated delivery is acceptable; missing
   state is not. Consumers deduplicate by stable event ID.
3. Use bounded pages and explicit resync behavior for expired/invalid/ahead-of-
   stream cursors or incompatible projections. Restart cannot reinterpret an old
   cursor against a reset in-memory stream. A compacted live-delivery window
   does not delete the canonical complete session history.
4. Authorize snapshot, replay and later delivery with current membership,
   ownership and audience. Keep GM, owner, party and player-encounter projections
   separate. Do not reuse cached GM payloads for players or send selected-recipient
   data through a shared player frame. Revocation closes or invalidates the
   subscription before additional unauthorized records are delivered.
5. Bind cursor and subscription state to website origin, campaign, principal and
   projection context. Campaign/account switch invalidates in-flight responses;
   an old response cannot advance the new context's cursor. A visible resync state
   preserves pending operations rather than silently submitting them again.

Supply a tested API for later adapters to bridge snapshot and replay. Future
PE-31a/b must preserve the established shared hub, map/table projections and all
four systems; they must not add a second EventSource per feature. Live activation
is forbidden until every mutation family included in a snapshot writes state and
events through the accepted authoritative path. New empty repositories cannot
serve as a snapshot of legacy file/global state.

## Acceptance

- [ ] New focused tests in `tests/player_release/test_snapshot_replay.py` cover commit-before-snapshot, commit-between-snapshot-and-subscribe, commit-during-replay, duplicate delivery, cursor loss/expiry, server restart and a deliberate connection switch with an old response still in flight.
- [ ] Privacy fixtures verify hidden actors, exact enemy HP, unrevealed effects, secret rolls and private owner data are absent from unauthorized snapshot, replay and live payloads. Test demotion/revocation during paged replay and cross-campaign cursor misuse.
- [ ] Synthetic PF2e and D&D 2014/2024 repository fixtures establish state/watermark semantics. These are primitive tests, not proof of installed-content correctness or live adapter coverage.
- [ ] `python -m pytest -q tests/player_release/test_snapshot_replay.py tests/test_sse_event_replay.py tests/test_sse_security_and_revocation.py tests/test_obsidian_sync.py tests/test_obsidian_plugin_contract.py tests/test_route_policy_inventory.py --strict-markers` passes. Record exact totals and skips.
- [ ] With a disposable PostgreSQL database configured through the existing CI/test fixture, `python -m pytest -q tests/player_release/test_snapshot_replay.py -m postgresql --strict-markers` passes and collects/runs the concurrency cases. Missing PostgreSQL or zero collected concurrency tests is not a passing gate.
- [ ] No endpoint, hub, plugin, runtime setting or legacy mutation path changes. PE-31a/b contain explicit accepted-writer/cutover prerequisites and own real-client/browser verification.
- [ ] `git diff --check` passes and the diff stays within the bounded primitive scope.
- [ ] A documented failure injection proves that reconnecting across the snapshot/subscription race yields the latest state without applying any command twice. No production test or deployment is required for acceptance.

## Out of scope / do not touch

- No new rules, all-origin event production retrofit, economy implementation, full player UI or vault narrative edits.
- No second socket per feature, VTT renderer/protocol transplant, wiki work, multiple-worker rollout or new simultaneous-live-session promise.
- No weakening authorization for between-session access; PE-21/22 own policy changes after detached state paths exist.
- No live endpoint activation or shared-hub changes. PE-31a and PE-31b own those separately, after accepted state writers and migration/recovery evidence.
- No change to original VTT/Obsidian branches, plugin installation or production configuration.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
