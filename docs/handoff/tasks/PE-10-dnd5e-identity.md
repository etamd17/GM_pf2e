# PE-10: Add D&D to shared identity schema and repositories

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked on PE-01 acceptance and merge
- **Branch:** `claude/pe-10-dnd5e-identity` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** [PE-01](PE-01-state-command-contract.md)
- **Context:** [foundation baseline](../../player-release/foundation-baseline.md), `core/persistence/models.py::Campaign/Character`, `core/persistence/repositories.py::CampaignRepository/CharacterRepository.upsert_identity`, and PE-01's accepted locator representation.
- **Change budget:** roughly 500 changed lines including tests; split before expanding into runtime integration.

## Goal

Allow validated D&D campaign and character identities in the common transactional
schema while preserving existing PF2e/Cosmere identities and assignments. This is
schema/repository support only; it does not activate SQL ownership for D&D routes.

## Approach

Allowed product files are `core/persistence/models.py`,
`core/persistence/repositories.py`, one new revision under `migrations/versions/`,
and focused tests under `tests/persistence/`.

1. Extend campaign/character system constraints and repository validation to the
   accepted `dnd5e` identity and canonical storage discriminator. Match PE-01 exactly;
   do not permit arbitrary system strings, paths or storage names.
2. Add a reversible schema migration preserving all existing rows, unique locator
   constraints, campaign/character composite relationships and assignment integrity.
   Refuse downgrade when D&D records would be invalid under the old constraints.
3. Exercise D&D identity creation/lookup/update and assignment scoping at the
   repository level. Assert that an identity cannot be reused across campaigns or
   changed to another rules system through a routine update.
4. Add `tests/persistence/test_dnd5e_identity_schema.py` and extend existing model/
   transactional tests where needed. Serialize migration ownership with PE-12;
   rebase onto the newest accepted migration head before merging, with one final head.

## Acceptance

- [ ] Existing PF2e/Cosmere rows and grants survive upgrade without changed IDs or checksums.
- [ ] D&D campaign/character identities and owner assignments persist under the agreed canonical storage discriminator.
- [ ] Unknown systems/stores, duplicate locators, mismatched campaign relationships and attempted cross-campaign identity reassignment fail.
- [ ] Migration upgrade/downgrade tests cover an empty D&D store and refusal to discard incompatible nonempty state.
- [ ] `python -m pytest -q tests/persistence/test_database_and_models.py tests/persistence/test_transactional_services.py tests/persistence/test_dnd5e_identity_schema.py` passes.
- [ ] `python -m pytest tests/persistence -q -m postgresql --strict-markers` passes against an isolated test database; report missing setup explicitly rather than counting skipped checks as verification.
- [ ] `python -m alembic heads` reports one migration head after integration; `git diff --check` passes.
- [ ] Codex accepts the schema/repository diff before PE-11 is marked ready.

## Out of scope / do not touch

- `app.py`, D&D routes/store behavior, locator/import/export adapters, private drafts,
  ownership-backend settings, game-state tables, production migrations or data installs.
- Do not broaden public API permissions or remove live-campaign guards in this task.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
