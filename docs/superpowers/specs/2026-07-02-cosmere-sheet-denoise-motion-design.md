# Cosmere sheet: de-noise + full Stormlight motion (UI-immersion arc, sheet pass 1)

**Date:** 2026-07-02
**Status:** Approved design (user-validated choices), pending implementation plan
**Scope:** `templates/cosmere_sheet.html` only (plus minimal shared CSS if needed).
The PF2e sheet gets its own parallel pass next. One PR.

## Goal

Remove the redundancy a player sees on their own sheet (name and vitals each
rendered twice at rest; three always-expanded low-signal blocks) and give the
sheet the full Stormlight motion vocabulary the tracker shipped — tuned for a
page a player stares at all session.

## User-locked decisions

1. Vitals bar: appear only after scrolling (hidden while the spheres are
   visible); drop its name text.
2. All four de-noise items: header tag diet, conditions fold, empty-goals
   fold, build-notes compression.
3. Motion: full Stormlight treatment (not resource-moments-only).

## Design

### A. Vitals bar — scroll-reveal (`#cs-vbar`, template line ~376)

- Bar starts hidden (`.cs-vbar` gets `opacity:0; transform:translateY(-8px);
  pointer-events:none;` via a default state class) and reveals (`.vis`) when
  the sphere bank (`.cs-orbs`) is NOT intersecting the viewport.
- IntersectionObserver on `.cs-orbs`; `rootMargin` small negative top so the
  bar arrives just as the last sphere edge leaves. Fallback: if
  IntersectionObserver is unavailable, the bar stays always-visible (today's
  behavior — graceful degradation).
- `position:sticky` stays as-is; only visibility changes. `.vb-name` span is
  removed (header owns the name).
- Reduced-motion: reveal without transition (instant), still scroll-gated.

### B. Header tag diet (template lines ~336-349) — PC view only

- PC (`pc` truthy) keeps: `Tier N`, path, culture, ancestry, Form (singer),
  `Radiant`, and the ready-to-level link. Size shown only when != 'medium'
  (rules-relevant then).
- PC drops: `a.type|capitalize` ("Character"), `a.role` ("Hero"),
  size-when-Medium, and the `Cosmere RPG` tag (the whole campaign is Cosmere).
- Non-PC (bestiary adversary view) keeps today's tags unchanged, except the
  `Cosmere RPG` tag which drops everywhere.

### C. Conditions fold (interactive block, template lines ~396-407)

- Default (folded): one row showing ONLY active condition chips (tap still
  clears), or a quiet "No conditions" muted line — plus a small "edit"
  toggle.
- Expanded: today's full 14-chip picker. State is per-pageload (starts
  folded), toggling is instant, no persistence.
- The active-condition effects summary (`#cos-cond-fx`) renders in both
  states (it only shows for active conditions).
- Folding is display-only: `cosCond()` and the chip `data-cond` contract are
  unchanged, so GM-pushed condition updates keep painting correctly.

### D. Goals & build-notes compression

- Goals (interactive, lines ~413-419): when the JS-rendered `#cos-goals` is
  empty, the section renders as a single quiet line: "No goals yet — 
  [+ Add goal]". With goals present, today's layout.
- Build notes (lines ~369-373): compress to one line —
  "Build notes (N) ▸" in the same amber, expanding on tap to the full list.
  Rendered only when `pc and warnings` as today.

### E. Full Stormlight motion

All hooks live at the single paint point `_orb(key, val, max)` (template
~line 928): it already receives every change from every mutation path
(cosAdjust/cosApply/cosBreathe/cosEnhance/cosRegenerate/cosSurge/rest and
GM-pushed state), so per-key previous-value tracking there catches
everything with no per-caller wiring.

1. **Sphere change pulses.** On value decrease: brief rim-glow pulse in the
   sphere's own `--orb-glow` (health additionally flashes a red-tinted ring —
   damage reads as a wound, not a spend). On increase: soft bright pulse
   (health heal tints green via the existing `--orb-mid`).
2. **Motes on spend.** Focus and Investiture decreases shed 4-5 rising light
   motes from the sphere (same visual language as the tracker's `.mote`,
   implemented sheet-locally, colored per sphere: gold for Focus, order-accent
   for Investiture). Health damage sheds NO motes (rim pulse only).
   "Breathe Stormlight" (refill) plays a stronger inhale: motes fall INTO the
   sphere (reverse travel) + glow pulse.
3. **Crest breathe.** The order-crest glyph in the header (`.cs-crest`)
   breathes on the existing `highstorm` cadence (very slow glow oscillation,
   ~9-11s), only for Radiants (crest only renders then).
4. Existing effects kept: `.orb.low` ember-pulse, `.orb.dun`, Shardplate
   order-glow, ambient highstorm edge, roll flare-opp/flare-comp.

### Constraints (unchanged from the arc)

- Every new animation inert under `prefers-reduced-motion` (CSS media block +
  JS matchMedia gate for particles).
- No layout shift from any flourish; particles are absolutely positioned and
  self-removing (animationend + timeout backstop, ≤6 per burst).
- No emojis. Inline-handler escaping rule applies to any user string in JS
  strings (existing code paths already comply; new code avoids inline
  handlers where possible).
- Legibility first: no flourish may overlap or obscure the sphere numbers.
- Print styles: new decorative elements hidden in print (`no-print` or under
  the existing print block).

## Error handling

- Missing elements are silent no-ops (guard clauses, matching existing
  sheet-JS style).
- IntersectionObserver absent → vitals bar behaves exactly as today.

## Testing & verification

- `python tools/check_templates.py` + full `pytest -q` (sheet render routes
  are covered by existing tests: test_cosmere_sheet_*, test_cosmere_theme,
  test_cosmere_print — tag/section changes may need those updated).
- Browser pass on the dev server with a seeded Radiant PC (scroll-reveal
  behavior, fold/unfold, damage/heal/spend/breathe flourishes, reduced-motion
  spot-check at code level, print preview sanity).
- Verify on Railway post-merge.
