# Free Archetype Implementation Plan

> **For agentic workers:** read the spec's "Verified integration anchors"
> first — the wizard already has a dormant `archetype_feat` slot key, and
> the toggle/join/gating machinery all has precedents to follow exactly.

**Goal:** per-campaign standard Free Archetype: even-level archetype-only
feat slot through the level-up wizard AND builder, dedication rules
enforced, GM toggle on the campaign manage page.

**Spec:** `docs/superpowers/specs/2026-07-07-free-archetype-design.md`.

## Global Constraints

- Branch `feat/free-archetype`; commit per task; standing finish pipeline.
  Trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
  No emojis.
- ONE slot-injection wrapper; every consumer goes through it (wizard
  payload, builder, validator, rail counts). No per-surface slot math.
- High-risk surfaces: this arc TOUCHES the level-up flow — run the full
  PB-import/snapshot guard suites (tests/test_pc_snapshots.py,
  tests/test_pb_import_correctness.py, tests/test_levelup*.py) after every
  app.py/class_matrix change; FA off must be byte-identical behavior.
- If the compiled DB lacks feat traits, extend the PR #89 pack join by
  `_id` (never name-fallback for flags; remember the ambiguous-name
  collision lesson).
- `python3 -m pytest -q` full suite before each commit; check_templates
  after template edits.

---

### Task 1: Backend — toggle + slot wrapper + trait flags + validator (TDD)

**Files:** `app.py` (`_free_archetype_enabled()` near
`_cosmere_initiative_mode`; slot wrapper near the class_matrix imports'
consumers; extend `_build_feat_pack_prereq_index` (or sibling) to carry
`is_archetype`/`is_dedication` by `_id` if the DB lost traits; validator
acceptance in `_validate_new_character_feats` + the level-up submit path);
config write via the existing campaign-config endpoint;
`tests/test_free_archetype.py` (create).

**Tests first (RED):** wrapper injects `archetype_feat: 1` ONLY on even
levels with the toggle on (odd levels, toggle off, and a non-PF2e campaign
all unchanged — assert dict equality with the raw matrix); toggle
round-trip through campaign config incl. the corrupt-doc abort; trait
flags correct for a known dedication + non-dedication archetype feat +
plain class feat; validator: extra archetype feat accepted when on,
409 when off, non-archetype feat in the FA slot 409, force bypass;
dedication-rule helper (no dedication -> only dedications takeable; new
dedication blocked until 2 other feats from the current archetype).

- [ ] RED → implement → GREEN → full suite incl. snapshot/PB guards →
  commit: `Free Archetype: campaign toggle, slot injection, trait flags, validator (TDD)`

### Task 2: Wizard + builder + manage-page UI

**Files:** `templates/player_levelup.html` (light up the dormant
`archetype_feat` slot: rail entry label 'Archetype Feat', picker call with
the archetype filter + dedication gating reasons), `templates/player_builder.html`
(same slot for levels >= 2 builds), `templates/_choice_picker.html` (accept
an archetype-only filter + dedication-gate reason chips if not already
expressible), the campaign manage template (GM toggle switch following an
existing system_config control).

- [ ] Implement → check_templates → full suite → commit:
  `Free Archetype: wizard/builder slots + GM toggle UI`

### Task 3: Verification + finish

- [ ] Browser walk on the repro server (re-seed tmp data if reaped):
  toggle on → Champion L11→12 wizard shows the FA slot (rail + picker,
  dedications-only first, grey reasons), take a dedication, next even
  level blocks a second dedication until 2 feats, builder parity, toggle
  off hides all + validator 409s an injected extra feat. Then:
  adversarial final-review workflow → fixes → PR → CI → merge → Railway
  verify → memory/ledger updates.
