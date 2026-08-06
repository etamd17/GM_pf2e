# PF2e level-up + builder: Pathbuilder-grade UX (design spec)

**Date:** 2026-07-03
**Audit basis:** `.superpowers/sdd/levelup-builder-audit.md` (findings W/B/E/G/H).
**Scope:** `templates/player_levelup.html`, `templates/player_builder.html`, a
new shared picker partial, additive-only backend eligibility data, light
ceremony. One PR, four reviewable tasks.

## User-locked decisions

1. Greyed options are BLOCKED with the reason shown (hover/tap), with a
   visible wizard-level "Ignore prerequisites" GM-override toggle.
2. Light ceremony: gains-checklist items tick with a pop as completed; one
   celebratory beat on confirm (ember burst + stat-change roll-up). No
   full-screen cinematics.
3. Builder included: creation and level-up share one vocabulary.

## Design

### A. Eligibility backbone (backend, ADDITIVE ONLY — audit E1/E2/G4)

Today `prereqs_parsed` is regex-extracted client-side from feat description
HTML and only catches ability/skill-rank prereqs; feat-chain and
class-feature prereqs silently pass (`meetsPrereqs` returns true). Fix at
the source, additively:

- At data load (where BUILDER_FEATS is built), parse each feat's
  prerequisite text ONCE server-side into a structured
  `prereqs: {level, abilities:{str:16,...}, skills:{athletics:'expert'},
  feats:[names], features:[names], raw: '<original text>'}` field. Purely
  additive to the payload; no existing field changes, no derivation changes.
- Ship `SKILL_FEAT_PREREQS` enrichment to the builder route too (levelup
  already gets it — plumbing gap, audit E-finding).
- Client `meetsPrereqs` consumes the structured field when present (raw
  regex fallback retained for safety); feats/features checks compare against
  the PC's known feats/features (both wizards already have those lists).
- `checkable` vs `uncheckable` prereqs: anything the parser can't classify
  lands in `raw` only → the option is NOT greyed for it, but the raw text
  renders as an advisory line ("Verify: <raw>") — never a false block.

### B. One shared picker (audit B/G duplication findings)

Extract the duplicated `#uni-modal` (markup + `meetsPrereqs`/
`renderModalList`/`filterModal`/`closeModal`) into ONE shared partial
(`templates/_pc_sheet/_choice_picker.html` or sibling) consumed by BOTH
wizards. The levelup copy is the newer one (has the spell-heightening fix) —
it becomes the base. Behavior:

- ALL options always render. Eligible: normal. Ineligible: greyed
  (opacity + no hover-lift), a compact reason chip ("Lv 8+", "Requires
  Expert Athletics", "Requires Power Attack", "Already known"), full reason
  on hover/tap. Clicking a greyed option shakes it gently (existing shake
  keyframe) and surfaces the reason — never selects.
- Already-taken options render greyed with "Already known" (not hidden).
- Sort: eligible first, then ineligible, alphabetical within each; search
  filters both groups.
- The GM-override toggle (below) flips ineligible → selectable-with-amber-
  border; reason chip stays visible on the selection afterward.

### C. Level-up wizard (audit W findings)

- **Gains checklist rail**: a persistent sidebar/top rail built from the
  existing `reqs` structure ("Level 11: Class Feat · Skill Increase · ...").
  Each entry shows state (empty circle → gilt tick) and clicking it jumps to
  that step. Completion ticks pop in (trkCondIn reuse). This REPLACES the
  current step-nav-only affordance; step navigation stays.
- **Wizard-level Ignore-Prereqs toggle**: one visible switch in the rail
  header ("GM override: ignore prerequisites"), amber-styled, replacing the
  per-modal checkbox (state feeds the same code paths + levelup's existing
  `force_save` retry so server behavior is unchanged).
- **Confirm ceremony**: the review step's confirm button triggers one beat —
  ember spark burst (existing particle vocabulary) + a compact stat-change
  roll-up (HP +X, new proficiencies, picks list) before redirecting to the
  sheet. Reduced-motion: instant summary, no particles.
- Everything else (step order, validation gating, submit payload,
  revert_level) unchanged.

### D. Builder parity (audit B/H findings)

- Consumes the same shared picker + greyed-with-reason + override toggle +
  gains-style progress rail (its existing steps, restyled to the same rail).
- Plumb the missing skill-feat prereq enrichment (E-finding).
- Minimal server backstop parity: `save_new_character` gains the same
  NON-BLOCKING validate + `force` pattern levelup already has (validation
  reuses the same eligibility structures read-only; a `force` flag preserves
  the GM-override path). No derivation changes; rejects only what levelup
  would reject.

### E. Constraints (arc-standard + high-risk surface)

- ZERO changes to stat derivation: `Character.__init__`, `class_matrix.py`,
  `submit_levelup`'s derivation path are untouched. Snapshot + PB
  ground-truth tests must stay green UNMODIFIED.
- New backend code is additive payload + read-only validation; unit tests
  for the prereq parser (feat-chain, ability, skill-rank, level, unparsable
  → raw-only) are required.
- Reduced-motion + print gating for all new animation; no emojis; inline-
  handler escaping rule; both wizards keep working for every class in
  BUILDER_DATA (not just the party's four).
- Legibility: greyed options must remain readable (opacity floor ~0.5) —
  grey means "locked", not "invisible".

## Testing & verification

- Unit: prereq parser (all classifications + unparsable fallback);
  builder-validate parity (rejects what levelup rejects; force bypasses).
- Existing guards unmodified-green: test_pc_snapshots, PB ground-truth,
  escaping tests (extended to any new inline handlers).
- Browser pass: Go'el L10→L11 full walk (gains rail matches class_matrix
  for Cleric 11; greyed feats show reasons; override unlocks; ceremony
  fires; revert works); builder from-scratch walk (Champion L1) with the
  same vocabulary; reduced-motion code-level check.
- Verify on Railway post-merge.
