# PF2e player sheet: de-noise + ember/rune flair (UI-immersion arc, PR2b)

**Date:** 2026-07-02
**Status:** Approved design (user-validated), pending implementation plan
**Audit basis:** `.superpowers/sdd/pf2e-sheet-audit.md` (46+ numbered findings,
line-verified) — finding IDs below (R1, N1, F2...) refer to it. The audit file
is the authoritative line-reference map for implementers.
**Scope:** `templates/player_sheet.html` + `templates/_pc_sheet/*` partials +
a sheet-scoped extension of `static/css/system.css`'s COMBAT IDENTITY section
+ one test hardening. One PR.

## Goal

The PF2e parallel of the Cosmere sheet pass: remove at-rest redundancy and
port the tracker's ember/rune vocabulary to the page players stare at all
session. The audit confirmed the sheet's `pc_update` SSE handler does
targeted DOM patches (audit H4) — CSS transitions survive, so this port is
lower-risk than the tracker's wholesale-innerHTML model was.

## User-locked decisions

1. Conditions: strip + ONE editor (fold left-rail Quick Conditions and the
   Combat-tab matrix into a single expandable editor; delete the orphaned
   hidden `#cond-adder` panel).
2. Fold the two onboarding-prose blocks (crit/MAP reminder, caster
   mental-model); keep the header spell-slot row always visible.
3. Polish riders: death's-door pulse + level-up pop-in; Speed/Initiative
   dedup; escaping-guard test hardening. (Skills-rail de-emphasis: declined.)
4. Deferred to separate chips (already spawned): Inventory-tab restyle;
   duplicated skill-actions script consolidation.

## Design

### A. Conditions consolidation (audit R1)

Four editable surfaces become two roles:
- **Display + quick edit:** the existing top condition strip keeps its
  current behavior (shows actives, has its own controls).
- **Full editor:** ONE expandable editor. Keep the left-rail "Quick
  Conditions" block as that editor but folded by default behind the same
  `<details>`/toggle pattern the Cosmere sheet's conditions fold uses
  (active-relevant summary + expand).
- The Combat-tab "Conditions Matrix" (S16) is removed (its functions are
  fully covered by strip + editor; same backend calls).
- The orphaned hidden `#cond-adder` panel (player_sheet.html:3062-3083) and
  any JS that references only it are deleted.
- All surviving paint paths keep working: the strip and the editor are
  already painted by the `pc_update` handler; removal of the matrix must
  also remove its paint calls, not orphan them.

### B. Prose folds (audit N1, N2)

The Combat tab's crit/MAP rules reminder and the Magic tab's per-caster
"mental model" paragraph each fold behind the Feats tab's existing
`class-cheatsheet` `<details>` pattern (same classes/styling — no new
pattern). Default collapsed. Header spell-slot row unchanged.

### C. Speed/Initiative dedup + dead code

- Left-rail meta card loses its Speed line (Speed lives in the
  Defenses & Rolls grid tile).
- The left-rail Initiative row merges into the Perception tile in the
  defense grid (subtitle "Initiative" under the Perception value — PF2e
  initiative IS Perception by default; the sheet's separate row shows the
  same number, audit R-skills/Init finding).
- Dead code deleted: `_levelup_drawer.html` include + its ~70 lines of CSS +
  `openLevelUpDrawer()`/`closeLevelUpDrawer()` (audit: nothing calls them —
  the header button is a plain link now); `.action-badge-1/2/3/reaction/free`
  CSS block (player_sheet.html:914-926, zero usage, audit N6).

### D. Ember/rune flair (ports of the shipped COMBAT IDENTITY vocabulary)

The tracker's classes are `.trk`-scoped in system.css; the sheet gets a thin
"sheet ports" subsection appended to the COMBAT IDENTITY section that reuses
the EXISTING keyframes (trkPop, trkSparkRise, trkFlame, trkEmber...) with
sheet-scoped selectors — no duplicated keyframes, no new visual language.

1. **HP ghost + pops (F1):** the `.hp-gauge-bar` (_header.html:249-263)
   gains a trailing ghost child and floating damage/heal delta pops, driven
   from the sheet's existing HP paint site with a previous-value hook (same
   `_prev` + pulse approach as the Cosmere sheet's `_orb` hook). Heals pop
   green, damage pops red with the ghost trailing.
2. **Hero Points → flames + sparks (F2):** the three pip elements
   (_header.html:62-71) become `rn-flame` glyph flames (active flicker /
   spent outline — same classes as the tracker rows); spending sheds ember
   sparks (tracker's spark particle pattern, spawned at the snuffed flame).
3. **Class-rune watermark (F4/F5):** include `_pf2e_class_glyphs.html` once
   in player_sheet.html (currently NOT included — audit-confirmed) and set
   the portrait badge (_header.html:36-38) to render the class rune behind
   the monogram, exactly like the tracker's portrait treatment
   (`classRuneId`-equivalent mapping in Jinja: champion/kineticist/cleric/
   druid + rn-adventurer fallback).
4. **Action-cost unification (N5/F3):** retire the `.action-cost` badge
   styling in favor of the diamond-pip cluster everywhere the sheet shows a
   cost (Strikes card, skill-action buttons, spell rows). Where a Jinja
   `action_pips()` macro already exists, restyle its output classes to the
   COMBAT IDENTITY pip look rather than introducing a parallel system. The
   `.action-cost[data-cost]` hardcoded-hex rules are removed once no markup
   emits them.
5. **Ember ambient:** the sheet's main panel gets the tracker's
   `trkEmber`-driven glow, opacity tuned DOWN (~60% of tracker intensity —
   players stare at this page all session).
6. **Death's door (F6):** when the Dying condition becomes active, the HP
   card gets a slow red pulse (existing wound-tint language); clears when
   dying clears. Driven from the same conditions paint path.
7. **Level-up moment (F7):** the ready-to-level banner/button pops in with
   the overshoot animation (trkCondIn-style) when it first appears.

### E. Hardening (audit H1)

- `_header.html:136`: escape `stat.label` in the `logRoll(...)` onclick
  (one-line `|replace("'", "\\'")` parity with `stat.mod` handling).
- `tests/test_inline_handler_escaping.py`: tighten the greedy regex so an
  unescaped interpolation adjacent to an escaped one can't be masked
  (audit-documented false negative).

## Constraints (arc-standard)

- Every new animation inert under `prefers-reduced-motion`; JS particle
  spawns gated by matchMedia. Print: new decorative elements hidden
  (`@media print` conventions already in this file).
- No layout shift; particles absolutely positioned, ≤6/burst, self-removing.
- No emojis. Inline-handler escaping rule for any user string in JS strings.
- Legibility first: no flourish over numbers players read mid-combat.
- The sheet renders for any party PC (owner-interactive + GM view); all
  variants keep working. `pc_update` targeted patches must keep firing the
  new flourishes (hook at paint sites, not at event sites).

## Testing & verification

- `python3 tools/check_templates.py` + full `pytest -q` (snapshot tests
  exist for this surface: tests/test_pc_snapshots.py must stay green —
  markup changes to stat-bearing regions must not alter derived values).
- The hardened escaping test must fail against a deliberately unescaped
  interpolation (RED proof) before the `_header.html:136` fix.
- Live browser pass with imported fixtures (Go'el — apostrophe name — and
  Amadeus): conditions consolidation, folds, HP ghost/pops, flame sparks,
  rune watermark, pip unification, dying pulse, level-up pop, reduced-motion
  at code level, print preview sanity.
- Verify on Railway post-merge.
