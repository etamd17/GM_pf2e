# PE-10: Add D&D to shared identity schema and repositories

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** ready (PE-01 accepted and merged in PR #247; brief reconciled with its sections 1 and 11 on 2026-11-10)
- **Branch:** `claude/pe-10-dnd5e-identity` from current main (at or after `3b1c1f40`). Record the exact starting SHA.
- **Depends on:** [PE-01](PE-01-state-command-contract.md)
- **Context:** [PE-01 contract](../../player-release/state-command-contract.md) section 1 (system strings, `character_id`, D&D locator) and section 7 (character archive), [foundation baseline](../../player-release/foundation-baseline.md), `core/persistence/models.py::Campaign/Character`, `core/persistence/repositories.py::CampaignRepository/CharacterRepository.upsert_identity`, and PE-01's accepted locator representation.
- **Change budget:** roughly 500 changed lines including tests; split before expanding into runtime integration.

## Goal

Allow validated D&D campaign and character identities in the common transactional
schema while preserving existing PF2e/Cosmere identities and assignments. This is
schema/repository support only; it does not activate SQL ownership for D&D routes.

## Approach

Allowed product files are `core/persistence/models.py`,
`core/persistence/repositories.py`, `core/persistence/runtime.py`
(`_REQUIRED_REVISION` only), one new revision under `migrations/versions/`,
and focused tests under `tests/persistence/` (including `test_runtime_backend.py`).

1. Extend campaign/character system constraints and repository validation to the
   `dnd5e` system string (system strings are exactly `pf2e`, `dnd5e` and `cosmere`) and
   the D&D locator `legacy_storage = "dnd5e_characters"`, `legacy_file = "<pid>.json"`
   with `legacy_file == id + ".json"` on every D&D row, archived or not. Match PE-01 exactly;
   do not permit arbitrary system strings, paths or storage names.
2. Add a reversible schema migration preserving all existing rows, campaign/character composite relationships and
   assignment integrity, and the locator uniqueness constraint in whatever form exists
   at the base revision: the full `uq_characters_legacy_locator`, or PE-12's partial
   unique index `WHERE archived_at IS NULL` if PE-12 merged first. Do not drop, rename
   or redefine that constraint; PE-12 owns its replacement. If the system CHECK change
   rebuilds `characters` (SQLite batch mode), the rebuilt table must keep `archived_at`
   and the partial index's `sqlite_where`/`postgresql_where` unchanged, and the
   migration test must assert this when rebased onto PE-12.
   Refuse downgrade when D&D records would be invalid under the old constraints.
3. Exercise D&D identity creation/lookup/update and assignment scoping at the
   repository level. Assert that an identity cannot be reused across campaigns or
   changed to another rules system through a routine update.
4. Add `tests/persistence/test_dnd5e_identity_schema.py` and extend existing model/
   transactional tests where needed. Serialize migration ownership with PE-12;
   rebase onto the newest accepted migration head before merging, with one final head.
   Set `core/persistence/runtime.py::_REQUIRED_REVISION` to that head, or SQL and
   shadow mode refuse to start after `alembic upgrade head`; whichever of PE-10 and
   PE-12 lands second sets it to the final head.

## Acceptance

- [ ] Existing PF2e/Cosmere rows and grants survive upgrade without changed IDs or checksums.
- [ ] D&D campaign/character identities and owner assignments persist with `system = 'dnd5e'`, `legacy_storage = 'dnd5e_characters'` and `legacy_file == id + '.json'`.
- [ ] Unknown systems/stores, a D&D `legacy_file` other than `id + '.json'`, a D&D id outside ≤ 64 `[A-Za-z0-9_-]`, duplicate non-archived locators, mismatched campaign relationships and attempted cross-campaign identity reassignment fail.
- [ ] Migration upgrade/downgrade tests cover an empty D&D store and refusal to discard incompatible nonempty state.
- [ ] `python -m pytest -q tests/persistence/test_database_and_models.py tests/persistence/test_transactional_services.py tests/persistence/test_dnd5e_identity_schema.py tests/persistence/test_runtime_backend.py` passes.
- [ ] `python -m pytest tests/persistence -q -m postgresql --strict-markers` passes against an isolated test database; report missing setup explicitly rather than counting skipped checks as verification.
- [ ] `python -m alembic heads` reports one migration head after integration; `git diff --check` passes.
- [ ] Codex accepts the schema/repository diff before PE-11 is marked ready.

## Out of scope / do not touch

- `app.py`, D&D routes/store behavior, locator/import/export adapters, private drafts,
  ownership-backend settings, game-state tables, production migrations or data installs.
- Do not broaden public API permissions or remove live-campaign guards in this task.
- `characters.archived_at` and the replacement of `uq_characters_legacy_locator` (PE-12);
  the archive command and archive-aware lookups (PE-41).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
