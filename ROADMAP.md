# GM_pf2e — Roadmap / Feature Backlog

Captured during in-session pre-game work. Map features are deprioritized for
in-person play; this file exists so we don't lose the design decisions when
we come back to the map later.

## Map features (deferred — confirmed 2026-04-14)

Goal: feature-parity with Roll20 / Foundry VTT for token-based play.

| # | Feature | Decision |
|---|---|---|
| 1 | Fog of war / explored-area persistence (paint-burns-away as PCs enter rooms, separate from dynamic vision occlusion) | **Yes — implement explored persistence** |
| 2 | Door types beyond `normal` + `secret` | **All Foundry types: add `locked` (key / thievery), `window` (blocks movement, allows vision), and `one-way`** |
| 3 | Light source attached to tokens (torch-follows-token) | **Yes** |
| 4 | Measured templates anchored to tokens (auras follow their owner) | **Yes — attach to token** |
| 5 | GM drawing layer (freehand sketches, shapes — separate from tokens/notes) | **Yes** |
| 6 | Scene switching / multi-page maps | **Yes** |
| 7 | Token auras + reach indicators (5/10/15 ft rings) | **Yes** |
| 8 | Roll-all-NPC-initiative button (similar to encounter tracker) | **Yes** |

### Existing map features (shipped in Phases 1–4, verified 2026-04-14)
- Token movement + vision with wall occlusion
- Walls (normal) + secret doors with GM-only reveal + shift-click promotion
- Hidden-character toggle (now extended to `visible_to_players` on both
  tokens and combatants, post-Phase-4 hardening)
- Ambient lighting (bright / dim / dark)
- Placed light sources
- AOE templates (burst / emanation / cone / line) with PF2e diagonals
- Ruler + range rings
- Spell card "Place on Map" with range visualization

## Session-critical pre-game work (in progress)

1. ~~Hidden-NPC name/HP leak across `_broadcast_encounter_state`,
   `_combat_log`, `/api/combat_log`, `/api/get_logs`, `/api/get_full_log`,
   `/api/player_state`, and `player_view.html` render~~
2. Player sheet polish to Pathbuilder 2e / Demiplane Nexus quality
3. GM encounter tracker UX polish
4. Encounter builder UX polish
5. Player-side encounter viewer polish
6. End-to-end verification before tonight's game

## Dice-render toggle (shipped in Phase 1)

Tri-state `Physics → Animated → Instant`. Instant mode skips the renderer
and posts the raw numeric result like the pre-Phase-1 behavior.
