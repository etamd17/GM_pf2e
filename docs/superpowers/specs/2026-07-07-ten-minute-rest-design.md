# Ten-minute rest block — player-driven exploration activities (design)

**Date:** 2026-07-07
**Status:** Approved scope (queue #2, locked 2026-07-04; forks locked
2026-07-07: auto-apply / enforced immunity with GM override / RAW Repair
check), pending plan.

## Goal

A "10-minute activities" panel on each PF2e player sheet: Treat Wounds
(RAW DCs and healing, server-rolled, auto-applied), Refocus, and shield
repair (RAW Repair check). Results roll up to the GM over SSE. Explicitly
NOT a GM party-wide button — each player drives their own activity.

## What already exists (verified in code, 2026-07-07)

- **Treat Wounds modal** (player_sheet.html ~8994, Wave 2 #6): target +
  tier pickers, but the roll is CLIENT-side Math.random with three RAW
  divergences — tier-scaled dice (2d8/4d8/6d8/8d8) instead of RAW's flat
  bonuses (+10/+30/+50 on 2d8), a flat +10 crit instead of doubled dice,
  and nat 20/1 OVERRIDING the degree instead of stepping it. Healing is
  NEVER applied to the target; no immunity tracking.
- **`/api/treat_wounds/<pc_name>`** (app.py ~13359, @require_pc_self_or_gm):
  log-only — appends to SESSION_HEALING_LOG + GM-only SSE `treat_wounds`
  frame. `/api/healing_log` serves the GM. These stay (the GM roll-up),
  but they must receive the SERVER result, not client-computed numbers.
- **Refocus button** exists (restores focus); **repairShield()** exists as
  a free full repair (Phase 11 convenience, no roll).
- Shield state on the PC: shield_hp / shield_max_hp / shield_bt /
  shield_broken / shield_destroyed.
- Party HP mutation for arbitrary targets: the tested
  `/api/adjust_party_hp` internals (test_dying_state.py contracts).

## RAW being implemented (Player Core; verify text against
~/Downloads + AoN during build)

- **Treat Wounds** (Medicine, 10 min, target any living creature incl.
  self): DC 15 trained / 20 expert (+10 healing) / 30 master (+30) /
  40 legendary (+50). Success 2d8 + bonus; crit success 4d8 + bonus;
  failure nothing; crit failure target takes 1d8. Nat 20/1 step the
  degree one (general check rules). Target then IMMUNE for 1 hour.
  Healer's tools are required by RAW — advisory text only, not enforced.
- **Refocus**: 10 min, regain 1 Focus Point up to max.
- **Repair** (Crafting, 10 min): vs the item's level-based DC (fallback
  DC 15 when the shield's item level is unknown to the build). Success:
  restore 5 HP per Crafting proficiency rank; crit: 10 per rank; crit
  failure: 2d6 damage to the item. Nat 20/1 step. Cannot repair a
  destroyed shield (hp 0).

## Design

### Backend (all server-rolled, shared-core style)

- `_resolve_treat_wounds(healer_pc, target_pc, tier, d20_raw=None)`:
  Medicine mod from the healer's derived skills (server-side, not client
  total), degree ladder WITH nat stepping, RAW healing/damage, applies
  the delta through the existing party-HP mutation internals (sheet SSE +
  combat log inherited), records immunity on the TARGET.
- Immunity: per-PC `treat_wounds_immune_until` (epoch seconds, persisted
  with combat state), set on any completed attempt per RAW ("immune to
  Treat Wounds for 1 hour"). A blocked attempt returns 409 with
  remaining minutes; `{"override": true}` (GM or the amber override
  toggle pattern) bypasses — mirrors the level-up prerequisite override.
- Routes (@require_pc_self_or_gm, JSON):
  - `POST /api/pc/<pc_name>/treat_wounds` {target, tier, d20?, override?}
  - `POST /api/pc/<pc_name>/refocus` — +1 focus up to max, 400 at max.
  - `POST /api/pc/<pc_name>/repair_shield` {d20?} — RAW Repair vs the
    shield; replaces the free full repair INSIDE the panel (the old
    convenience button is removed from the sheet; the roll result and
    new shield HP broadcast like other sheet mutations). 400 when no
    shield / already full / destroyed.
- GM roll-up: keep the SESSION_HEALING_LOG + `treat_wounds` GM SSE frame,
  now fed the server-authoritative result; refocus/repair emit compact
  GM-frame SSE (existing roll-feed/broadcast patterns) so the GM sees
  "who spent 10 minutes on what".

### Sheet panel

- One "10-Minute Activities" panel (exploration affordance, .no-print)
  grouping: Treat Wounds (reworked modal — same pickers, but the roll
  button POSTs the new route and renders the server outcome; physical-d20
  input like the recovery widget; immunity state shown per target with
  remaining minutes + the amber override), Refocus (moves the existing
  button in), Shield Repair (replaces the free full repair; shows shield
  HP/BT state and the Crafting mod).
- All GM free text/name rendering through the established escaping
  patterns; no dynamic strings in inline handlers.

## Out of scope

- Battle Medicine (a feat, in-combat action — different rules).
- Continued-care feats (Continual Recovery, Ward Medic, Risky Surgery):
  the immunity override covers tables that use them; proper feat-aware
  automation is a follow-up.
- Cosmere: has its own rest flow (cs-rest-btn), untouched.

## Testing & verification

- TDD the three routes + the Treat Wounds core: full degree ladder with
  nat stepping, RAW healing per tier, crit-fail damage, self-target,
  immunity set/blocked/409-payload/override, refocus cap, repair
  rank-scaled restore + destroyed/full guards, auth (owner/other/GM).
- Browser walk: panel renders; Treat Wounds on a wounded ally applies HP
  + immunity countdown appears + GM log receives the entry; refocus pip
  fills; shield repair restores by rank; override path.
- Full suite + check_templates; Railway verify post-merge.
