# Free Archetype toggle — standard FA (design)

**Date:** 2026-07-07
**Status:** Approved scope (queue #3, locked 2026-07-04: STANDARD Free
Archetype variant), pending plan.

## Goal

A per-campaign GM toggle for the standard Free Archetype variant (GM Core):
every even level grants one extra class-feat slot that may ONLY hold
archetype feats, with the normal dedication rules enforced (must take a
Dedication first; no new Dedication until two other feats from the current
archetype are taken). Rides the level-up gains rail + shared picker from
PR #89 and the builder for from-scratch characters.

## Verified integration anchors (2026-07-07)

- Slots: `class_matrix.get_required_slots_at_level(class_name, level)`
  returns the per-level `{slot: count}` dict the wizard/builder/validator
  consume; even levels carry `class_feat: 1` in every progression table.
- The wizard already enumerates an `'archetype_feat'` slot key
  (player_levelup.html ~1568) — dormant scaffolding to light up.
- Feat classification: the Foundry pack carries `archetype` and
  `dedication` traits (verified on Gladiator/Curse Maelstrom Dedication);
  PR #89's pack join + `prereqs_struct` and `_knownDedications` provide
  the dedication awareness.
- Toggle home: `campaign.json system_config` via the hardened
  `_save_campaign_config`; read-helper precedent is
  `_cosmere_initiative_mode()` (~app.py 2340). GM UI precedent: the
  campaign manage page's per-campaign settings.

## Design

- `_free_archetype_enabled()` reads `system_config.free_archetype`
  (bool, default False) for the active campaign; a small GM-only toggle
  on the campaign manage page writes it via the existing config endpoint.
- **Single-source slot injection**: a wrapper around
  `get_required_slots_at_level` adds `archetype_feat: 1` on even levels
  when the toggle is on. Every consumer (level-up wizard payload, builder,
  `_validate_new_character_feats`, ceremony/rail counts) goes through the
  wrapper — no per-surface forks (the PR #89 predicate lesson).
- **Picker behavior for the archetype slot**: lists feats bearing the
  `archetype` trait at legal level; eligibility grey-out reuses
  `prereqs_struct`. Dedication gating: if the PC has no dedication, only
  `dedication`-trait feats are takeable in the slot; a NEW dedication is
  blocked (grey + reason) until every existing archetype has two other
  feats taken — never-false-block contract: unclassifiable cases stay
  advisory, and the amber GM override continues to bypass.
- **Server backstop**: the builder/level-up validator accepts the extra
  slot only when the toggle is on and rejects non-archetype feats in it
  (with the established `force` bypass).
- Trait data path: the compiled DB's feat rows must expose traits to the
  picker/validator — if the compilation lost traits (like it lost
  prerequisites), extend the pack join by `_id` to carry
  `is_archetype`/`is_dedication` flags.

## Out of scope

- FA house variants (any-feat FA, half-level archetypes). Retraining.
- Dual-class / other GM Core variants.
- Auto-granting the L2 dedication for existing PCs mid-campaign: the next
  level-up simply presents the accumulated slot for its level only (no
  retroactive back-fill) — table can GM-override extra picks if desired.

## Testing & verification

- TDD: wrapper injection on/off, odd/even levels, every class table;
  validator accepts/rejects (trait filter, toggle off = 409 on extra
  feat, force bypass); dedication-rule eligibility states; toggle
  round-trip through campaign config (corrupt-doc abort preserved).
- Browser: toggle on manage page; wizard even-level shows the FA slot in
  rail + picker with dedication gating; builder parity; toggle off hides
  everything; Railway verify post-merge.
