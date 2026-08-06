# GM_pf2e — Roadmap / Feature Backlog

Captured during in-session pre-game work.

> **Read the map sections carefully — they describe TWO different
> implementations.** The 2026-04-14 lists below belong to the *original* map,
> which was later removed. A new tactical map was written from scratch in
> 2026-08 (`core/scenes.py`, `services/scene_sync.py`, `static/js/map.js`,
> `app.py:6452-7261`) and does **not** inherit any of it. Treat the old
> "shipped" list as design intent to re-check, never as current state — several
> entries are not implemented in the new map. Current status and known defects
> live in `CLAUDE.md` → *Current work*.

## Map features (design decisions from 2026-04-14 — status re-checked 2026-08-06)

Goal: feature-parity with Roll20 / Foundry VTT for token-based play.

| # | Feature | Decision | Status in the new map |
|---|---|---|---|
| 1 | Fog of war / explored-area persistence (paint-burns-away as PCs enter rooms, separate from dynamic vision occlusion) | **Yes — implement explored persistence** | Partial. A persistent manual GM brush (reveal/hide ops) exists; automatic *explored* persistence does not. |
| 2 | Door types beyond `normal` + `secret` | **All Foundry types: add `locked` (key / thievery), `window` (blocks movement, allows vision), and `one-way`** | Not done. Model is `wall`\|`door` plus `open`/`secret` only. |
| 3 | Light source attached to tokens (torch-follows-token) | **Yes** | Not done. Lights are fixed at an x/y; tokens carry a `vision_radius` but emit no light. |
| 4 | Measured templates anchored to tokens (auras follow their owner) | **Yes — attach to token** | Not done. Templates are placed at fixed coordinates. |
| 5 | GM drawing layer (freehand sketches, shapes — separate from tokens/notes) | **Yes** | Not done. |
| 6 | Scene switching / multi-page maps | **Yes** | **Done.** Multiple scenes per campaign + an active-scene index; activation broadcasts over SSE. |
| 7 | Token auras + reach indicators (5/10/15 ft rings) | **Yes** | Not done. |
| 8 | Roll-all-NPC-initiative button (similar to encounter tracker) | **Yes** | Done in the tracker (`/api/roll_all_initiative`), not surfaced on the map. |

### Features the ORIGINAL map shipped (Phases 1–4, verified 2026-04-14)

That map was removed. Status below is the **new** implementation, re-checked
2026-08-06 — do not assume parity.

| Original feature | In the new map? |
|---|---|
| Token movement + vision with wall occlusion | Yes — client-side raycasting (`map.js:253`). See `CLAUDE.md` for a known angle-range defect. |
| Walls (normal) + secret doors with GM-only reveal | Yes, but **no shift-click promotion**; the only `shiftKey` handler is multi-target selection (`map.js:786`). |
| Hidden-character toggle (`visible_to_players` on tokens + combatants) | Yes — hidden tokens are dropped from the player payload server-side. |
| Ambient lighting (bright / dim / dark) | **No.** Settings expose only a `dynamic_lighting` bool and `default_vision`. `drawAmbientLights()` renders placed lights; there are no light bands. |
| Placed light sources | Yes — x/y, radius, color, intensity, `visible_to_players`. |
| AOE templates (burst / emanation / cone / line) with PF2e diagonals | Yes. Cone angle is wrong (60°, should be 90°) — see `CLAUDE.md`. |
| Ruler + range rings | Ruler yes (PF2e alternating diagonals). **Range rings: no.** |
| Spell card "Place on Map" with range visualization | **No.** Zero references anywhere in the repo. |

### Agreed direction for fog (2026-08-06)

Full fog: **server-authoritative vision plus background tiling**, so unexplored
map art never reaches the client. Today's fog is cosmetic only — the browser
downloads the entire unfogged background and paints darkness over it, and
receives every wall and every fog operation including `hide` strokes. Needs a
design spec before implementation.

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
