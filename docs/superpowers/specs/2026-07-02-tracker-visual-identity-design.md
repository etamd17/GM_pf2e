# Tracker visual identity — PF2e ember / Cosmere Stormlight (PR1 of the UI-immersion arc)

**Date:** 2026-07-02
**Status:** Approved design, pending implementation plan
**Validated:** interactively via visual-companion mockups
(`.superpowers/brainstorm/92102-1782924232/content/tracker-final.html` is the
reference artifact — both rows, final behavior, clickable).

## Goal

Give the combat tracker a distinct, publisher-grade visual identity per game
system, so a campaign *feels* like its TTRPG the moment combat starts:

- **PF2e** — torchlit / rune-carved: warm ember-flicker ambience, carved
  class-rune portrait watermarks, Hero Points as living flame icons that shed
  sparks when spent.
- **Cosmere (Stormlight)** — luminous / Invested: cool breathing storm-glow
  ambience, Radiant order-glyph portrait watermarks, an Investiture sphere
  that breathes light at rest and sheds motes when a Surge is used.
- **Shared combat juice** (identical mechanics, per-system palette): HP bar
  drains with a trailing "ghost" bar, damage numbers pop and float away, the
  hit row flashes and shakes, new conditions pulse in.

The two systems deliberately use the same row skeleton with *different visual
vocabulary*: irregular/steppy ember flicker vs smooth celestial breathe; carved
rune vs luminous glyph ring; sparks vs light-motes. Convergence is the failure
mode.

## Non-goals (this PR)

- `mobile_combat.html` and `cosmere_combat.html` (player companion views) get
  only what falls out of the shared CSS cascade — bespoke motion/glyph work is
  a fast follow-up after the pattern is proven on the main tracker.
- No sheet work (that is PR2 — the components are built reusable for it).
- No audio (removed from the app deliberately; visual-only).
- No new "actions remaining" tracker on rows — cut during design review
  (in-person table tracks its own actions). Existing `actions_used` UI on the
  tracker, if any, is left untouched — neither restyled nor removed.
- Action-**cost** glyphs (◆/◆◆/◆◆◆/⟳/◈) stay, upgraded to the diamond-pip
  cluster style, but only where they describe what an ability costs (strike /
  spell reference rows), never as a spent/remaining meter.
- No lookalike trade dress *assets* (glyphs are originals in the spirit of the
  published books, extending the existing `_cosmere_glyphs.html` approach).

## Architecture

All work extends existing patterns; nothing is replaced.

**1. `templates/_pf2e_class_glyphs.html` (new)** — an SVG `<symbol>` sprite
partial, structurally identical to the existing `_cosmere_glyphs.html`
(currentColor stroke, `<use href="#rn-...">`). Original carved-style rune
marks for the party's four classes — Champion, Kineticist, Cleric, Druid —
plus one generic fallback rune so an unmapped class never renders empty.
Included by `tracker.html`; designed to be included by the PF2e sheet in PR2.

**2. `static/css/system.css` (extend)** — a new "combat identity" section with
reusable classes, all inside the existing `.trk` namespace where
tracker-specific and split PF2e/Cosmere via the established
`body.system-cosmere` scoping:

- `.trk-ambient--ember` / `.trk-ambient--storm`: the panel-level pseudo-element
  glow (steps(6) flicker vs 9s ease breathe).
- `.trk-hpbar` with `.fill` + `.ghost`: fill snaps fast (180ms), ghost trails
  slow (700ms cubic-bezier, 120ms delay).
- `.trk-dmgpop`: the floating damage number (per-system display font).
- `.trk-row-hit`: flash + shake keyframes, applied for one beat on damage.
- `.trk-cond-in`: condition-chip pop-in (overshoot scale).
- `.hero-flame` (+ `.spark` particles): Hero Point flames — flicker at rest,
  outline when spent.
- `.cos-sphere` (+ `.mote` particles): Investiture sphere — breathing glow at
  rest, dim/grayscale when spent.
- `.rune-watermark` / glyph watermark treatment inside the portrait disc
  (PF2e: circle + Cinzel initial; Cosmere: rounded-square + Cormorant initial —
  as in the validated mockup).
- `.action-pips` / `.action-pip`: the diamond cost-pip cluster for ability rows.
- Every animation gated behind `@media (prefers-reduced-motion: reduce)`
  (same discipline as `_boss_reveal.html` / `_initiative_flourish.html`):
  reduced-motion gets instant state changes, no particles, no shake.

**3. `templates/tracker.html` (extend)** — a small trigger layer in the
existing script, following the `window.pf2e*` flourish pattern (plain
functions, called from the tracker's existing SSE/state-diff handlers — no new
EventSource, no dependencies):

- On HP decrease: set fill/ghost widths, spawn a damage-pop with the delta,
  add `.trk-row-hit` for one animation cycle.
- On HP increase: same bar mechanics, green-tinted pop (`+N`), no shake/flash.
- On new condition key: apply `.trk-cond-in` to the new chip only.
- On hero-point change (PF2e PC rows): re-render flames; on decrease, spawn
  spark particles from the snuffed flame.
- On Investiture change (Cosmere PC rows): sphere dim/brighten; on spend,
  spawn motes.
- Particle spawner: one tiny shared helper (`spawnParticles(el, className, n)`)
  parameterized by class so sparks and motes share code but not appearance.
  Nodes are removed on animationend; capped (≤6 per burst) so a burst can
  never accumulate DOM.

**4. Row markup changes in `tracker.html` render functions** — portrait gains
the glyph watermark (`class_name` → rune id for PF2e PCs; radiant order →
`cg-order-*` id for Cosmere PCs; adversaries keep the initial-only disc),
PF2e PC rows gain the hero-flame cluster, Cosmere PC rows gain the sphere.
User-controlled strings interpolated into any inline handler follow the
mandatory JS-escaping rule (`.replace(/'/g, "\\'")`) — this feature mostly
avoids inline handlers by using delegated listeners.

## Data flow

Everything rides the existing tracker state broadcast; the delta detection
(old HP vs new HP, old conditions vs new) happens client-side in the existing
state-merge code, which already diffs rows in place.

Payload additions to `_get_tracker_state` (app.py), all trivially derived from
objects already in `ACTIVE_ENCOUNTER`:

- `hero_points` for PF2e PC rows (already merged client-side at
  tracker.html:4367 from the award event; adding it to the full state payload
  makes cold loads correct too).
- `radiant_order` (slug) for Cosmere PC rows, for the glyph lookup
  (`CosmereBuild.order()`, systems/cosmere/build.py:302).
- `investiture_current` / `investiture_max` for Cosmere PC rows, for the
  sphere state (already tracked per-PC in saved player state — see the
  vitals-board payload at app.py:16086 — just not yet in tracker state).
- `class_name` is already shipped for PCs — no change.

No schema/persistence changes. No new endpoints. GM and player correctness:
the tracker is the GM surface; the player SSE frame already masks combatants
via `visible_to_players`, and this feature adds no numeric information to any
player-visible frame that wasn't already there (flourishes render from data
the frame already contains).

## Error handling

- Glyph lookup misses → generic fallback rune (PF2e) / plain initial disc
  (Cosmere). Never a broken `<use>`.
- Damage-pop / particles are fire-and-forget; a missing element is a no-op
  (guard clauses, same as `pf2eFlash`).
- Rapid successive hits: pop/shake restart via the reflow-retrigger idiom
  already used in `_screen_flash.html`; ghost bar simply re-targets the new
  width (interrupted transition is visually fine).

## Testing & verification

- `python tools/check_templates.py` (Jinja parse) + `pytest -q`.
- If the class→rune / order→glyph map grows beyond a literal dict, a small
  unit test pins it; otherwise template-parse + visual verification suffice.
- Manual verification on the dev server, both systems: stage an encounter,
  apply damage/healing/condition/hero-point/Investiture changes, confirm each
  flourish; confirm `prefers-reduced-motion` suppresses all motion; confirm a
  PC with an apostrophe name ("Go'el") renders and functions.
- Verify on Railway after merge (prod-facing working agreement).

## Sequencing (the arc this belongs to)

1. **PR1 (this spec):** tracker, both systems.
2. **PR2:** player sheet + Cosmere sheet — reuse the component classes
   (hero flames, sphere, ambient panels, action-cost pips on strike/spell
   rows, rune/glyph watermarks on sheet headers).
3. **Fast follow:** `mobile_combat.html` / `cosmere_combat.html` get the
   bespoke treatments once the pattern is proven.
