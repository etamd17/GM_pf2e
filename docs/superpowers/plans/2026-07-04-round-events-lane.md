# Round-Events Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GM-authored round-triggered events on the tracker: reminder + optional auto-apply payload (conditions and/or damage), once or every-N recurrence, per-event show-on-table ember banner.

**Architecture:** `ROUND_EVENTS` joins the encounter-scoped globals (persisted/rehydrated/cleared with encounter state); firing hooks the exact `ROUND_NUMBER += 1` site in `cycle_turn`; payloads execute through the EXISTING condition/damage mutation helpers (inheriting SSE/sheet-sync/combat-log behavior); the lane renders GM-only on tracker.html; the banner is a `_boss_reveal.html`-pattern partial on the `round_event` SSE event.

**Spec:** `docs/superpowers/specs/2026-07-04-round-events-lane-design.md` (data shape, firing rules, constraints — READ IT FIRST; it is the contract).

## Global Constraints

- Branch `feat/round-events-lane`; commit per task; NEVER push. Trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. No emojis.
- Do all work yourself in your turn — no background agents/monitors (repeated infra stalls this week).
- `python3 tools/check_templates.py` after template edits (python3, not python). Full `python3 -m pytest -q` before each commit (~996 passed / ~25 skipped baseline; mass failures = your environment — stray servers/DATA_DIR — before the code).
- Payloads MUST reuse existing mutation internals (find what `/api/update_condition` and the damage endpoints call; extract-and-share if needed, never duplicate). Derivation/PB-import surfaces untouched.
- GM auth via the `GM_API_PREFIXES` prefix gate (route your endpoints under an already-gated prefix or add the new prefix to the list — check how existing tracker mutations are gated and match).
- Inline-handler escaping for GM free text (titles/text) — data-attributes + delegated listeners in the lane; `esc()` at render sinks (tracker lane AND banner).
- Reduced-motion inert animation; the lane must not sit above modal overlays (verify stacking; the levelup rail's z-55 lesson).
- SSE via existing hub patterns: broadcast with `sse_broadcast('round_event', ..., player_filter=...)` semantics matching how GM-vs-player frames are pre-rendered elsewhere (see `_do_broadcast_encounter_state` and boss_reveal's broadcast for the pattern); client subscribes via `window.appSSE` ONLY.

---

### Task 1: Backend — store, lifecycle, firing, payload execution, SSE (TDD)

**Files:**
- Test: `tests/test_round_events.py` (create)
- Modify: `app.py` — globals near `TURN_REMINDERS` (~11010); persistence in `_do_persist_encounter_state` + the rehydrate path (find where ROUND_NUMBER/TURN_INDEX round-trip; saved-encounter save/restore too); firing inside `cycle_turn` at the `ROUND_NUMBER += 1` site (~10886); CRUD endpoints; `round_event` SSE broadcast.

**Interfaces (produced):**
- `ROUND_EVENTS: list[dict]` per the spec's shape; `_fire_round_events(new_round)` called from cycle_turn.
- Endpoints: `GET` list rides the existing tracker state payload (`round_events` key in `_get_tracker_state` — GM route already); `POST /api/round_events` (create, returns event with server-assigned id), `POST /api/round_events/<id>/update`, `POST /api/round_events/<id>/delete`. Follow neighboring endpoint conventions exactly (form vs JSON, response shapes).
- SSE `round_event`: GM frame `{title, text, round, show_on_table}`; player frame only when `show_on_table`, and then `{title, text, round}`.

**TDD requirements (write first, RED, then implement):** fire at round N; repeat-every fires at N, N+k, ...; `last_fired_round` idempotence (cycling backward then forward past the same round does NOT re-fire); payload conditions land on the target combatant via the real helper (assert combatant state, not mocks); damage payload rolls dice via the existing roller and applies via the existing HP path (assert HP changed within dice bounds); 'all' targeting; persistence round-trip (persist → clear globals → rehydrate → events intact incl. last_fired_round); encounter clear wipes events; unknown target ids skipped silently. Use the established test patterns (monkeypatch `_persist_encounter_state`/`_broadcast_encounter_state`, stub combatants like tests/test_tracker_visual_payload.py's `_PF2ePC`).

- [ ] RED → implement → GREEN → full suite → commit: `Round events: encounter-scoped store, round-fire engine, payload execution, SSE (feature 7 backend)` (+ trailer).

---

### Task 2: Tracker lane UI (GM-only)

**Files:**
- Modify: `templates/tracker.html` — lane markup between the round header and the initiative grid (`.grid-wrap` region); lane JS (render from `STATE.round_events`, which Task 1 ships in tracker state + the SSE patch path must merge — add the merge line like the cosmere one at ~4425); authoring form + payload builder; CSS in the tracker's extra_head following the `.trk` namespace + ember vocabulary.

**Contract:**
- Collapsible lane (`<details>` open by default when events exist, the qc-fold summary pattern): upcoming events sorted by next-fire round, each row "R{n} · {title}" + repeat badge ("every {k}") + show-on-table eye icon + payload summary chips ("Frightened 1 → Go'el", "2d6 → all") + edit/delete. Fired-this-combat events dimmed with a gilt tick; when one fires live (detected via the `round_event` SSE or a state diff), its tick pops via trkCondIn.
- Authoring: inline form (round number defaulting to current+1, repeat-every optional, title, text, show-on-table checkbox, payload builder: condition select from the ACTIVE system's condition list already available to the tracker + value + target multi-select of current combatants + "all"; damage rows: dice text + damage/heal + targets). POSTs to Task 1's endpoints; re-renders from the SSE-refreshed state (no local-only state).
- GM free text rendered through the tracker's existing `escapeHtml`; no `${...}` into inline handler strings — delegated listeners keyed by event id.
- Lane sits in normal flow (NO z-index promotion); verify no overlap with the tracker's modals.

- [ ] Implement → check_templates → full suite → dev-server smoke (author/edit/delete an event; lane renders) → commit: `Tracker: round-events lane — authoring, upcoming/fired states, payload chips` (+ trailer).

---

### Task 3: Table banner partial

**Files:**
- Create: `templates/_round_event_banner.html` (self-contained, `_boss_reveal.html` pattern: markup + scoped CSS + `window.appSSE('round_event', ...)` subscription — NO new EventSource)
- Modify: include it adjacent to the existing `_boss_reveal.html` include in the shared chrome (base.html — find the real include site; remember tracker.html's content-block lesson) and in any standalone player page that includes the flourish partials (grep which pages include `_boss_reveal.html` and mirror exactly).

**Contract:** ember-styled banner slides from the top ("Round {n} — {title}: {text}", `esc()`-rendered), auto-dismiss ~6s or on click, stacking above content but below/independent of modals, `prefers-reduced-motion` = opacity fade only, `.no-print`. Player pages receive the event ONLY when show_on_table (server enforces via the frame split — the client just renders what arrives). Idempotent re-fire display (a second event replaces the first cleanly — the boss-reveal restart idiom).

- [ ] Implement → check_templates → full suite → commit: `Round events: ember table banner via SSE (show-on-table)` (+ trailer).

---

### Task 4: Verification pass (controller-driven, live browser)

- [ ] Full suite + check_templates on final HEAD; stub window.alert/confirm BEFORE any tracker interaction (native dialogs block preview evals).
- [ ] Author events (once + every-2, with condition payload on Go'el + damage payload on all, one show-on-table on / one off); advance rounds via the real Next Turn flow; verify: fire log entries, condition/HP actually applied (inspect combatant state), banner appears on a player view for the shown event and NOT for the hidden one, repeat re-fires, backward cycle doesn't double-fire, fired ticks pop, persistence across a dev-server restart mid-combat, encounter clear wipes the lane.
- [ ] Cosmere-mode spot check: lane renders on a Cosmere encounter; condition picker shows Cosmere conditions.
