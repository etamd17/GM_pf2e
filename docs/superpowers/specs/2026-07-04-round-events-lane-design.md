# Round-events lane (tracker feature 7 — design)

**Date:** 2026-07-04
**Status:** Approved design (CLAUDE.md locked one-liner + user-locked forks), pending plan.

## Goal

A GM-authored timeline of round-triggered events on the combat tracker:
each event is a reminder that fires when combat reaches its round, with an
optional auto-apply payload. GM-only surface; per-event "show on table"
broadcasts a themed narrative banner to player screens.

## User-locked decisions

1. Payloads: apply/remove CONDITIONS on chosen combatants, and/or
   DAMAGE/HEALING (dice expression) on chosen combatants. (Reveal-combatant
   deferred — overlaps the future minions feature.)
2. Recurrence: fire at round N, with optional "repeat every N rounds after".
3. Table display: an ember-styled narrative banner via SSE (GM's flavor text
   only, never the mechanical payload). No cinematic variant in v1.

## Design

### Data & lifecycle

- `ROUND_EVENTS` joins the encounter-scoped globals: a list of event dicts
  `{id, round, repeat_every (int|null), title, text, show_on_table (bool),
  payload (null | {conditions: [{target_ids|'all', condition, value}...],
  damage: [{target_ids|'all', dice, kind: 'damage'|'heal'}...]}),
  last_fired_round (int|null)}`.
- Persisted inside the existing encounter state
  (`_persist_encounter_state`) and rehydrated on boot; cleared when the
  encounter is cleared; travels with saved encounters.
- CRUD via GM-prefix-gated endpoints (`GM_API_PREFIXES` — follow the
  existing route conventions; do NOT add per-route auth that duplicates the
  prefix gate).

### Firing

- Fires inside `cycle_turn` at the exact point `ROUND_NUMBER` increments
  (app.py ~10886): any event where `round == new_round` or
  (`repeat_every` and `new_round > round` and
  `(new_round - round) % repeat_every == 0`), and
  `last_fired_round != new_round` (idempotence; backward turn-cycling never
  re-fires or un-fires — `last_fired_round` only advances).
- On fire: (a) execute the payload through the EXISTING condition-update and
  damage internals (whatever `update_condition`/damage endpoints call — no
  new mutation paths, so sheet sync/SSE/combat-log behavior is inherited);
  (b) append a combat-log entry naming the event; (c) broadcast SSE
  `round_event` `{title, text, round, show_on_table}` — the player frame
  carries text ONLY when `show_on_table`, GM frame always gets it.

### Tracker lane (GM-only)

- A collapsible lane on the tracker between the round header and the
  initiative grid: upcoming events sorted by next-fire round ("R3 · The
  ceiling groans" with repeat badge "every 2"), fired events dimmed with a
  gilt tick (trkCondIn pop when they fire live).
- Authoring inline in the lane: round, repeat-every, title, narrative text,
  show-on-table toggle, and an optional payload builder — conditions
  (condition picker + value + target multi-select from current combatants +
  "all") and damage (dice expression + damage/heal + targets). Edit +
  delete per event.
- Everything ember-styled per the COMBAT IDENTITY vocabulary; no emojis.

### Table banner

- New self-contained partial `_round_event_banner.html` following the
  `_boss_reveal.html` pattern: subscribes via `window.appSSE('round_event',
  ...)` (NEVER a new EventSource), slides an ember-styled banner with the
  event title/text across the top, auto-dismisses ~6s or on tap,
  reduced-motion = fade only. Included wherever `_boss_reveal.html` is
  included (base.html chrome) so player sheets + table screens get it; the
  GM tracker shows it too (GM sees everything).

## Constraints

- High-risk surfaces untouched (derivation, PB import). Payload execution
  reuses existing mutation helpers only.
- Inline-handler escaping rule (event titles/text are GM free text —
  data-attributes/delegated listeners, `esc()` where rendered).
- Reduced-motion + print gating for all new animation; particles none.
- The lane must never intercept clicks over open modals (learn from the
  levelup rail: check stacking before shipping).
- Cosmere-mode tracker: the lane works there too (round numbers are
  system-agnostic); payload condition picker uses the ACTIVE system's
  condition set (the tracker already knows per-system conditions).

## Testing & verification

- TDD backend: fire-at-round, repeat-every, idempotence on re-cycling,
  backward-cycle no-refire, payload execution (condition applied via the
  real helper; damage applied incl. dice parsing reusing the existing dice
  roller), persistence round-trip (save/rehydrate), clear-on-encounter-end.
- check_templates + full suite green; snapshot guards untouched.
- Browser pass: author an event with condition+damage payload, advance
  rounds, watch it fire (log entry, combatant state change, banner on a
  player view with show_on_table on; NO banner text on player view with it
  off), repeat-every re-fires, fired tick pops, backward cycle doesn't
  double-fire.
- Verify on Railway post-merge.
