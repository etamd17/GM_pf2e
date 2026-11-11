# PE-10: Add D&D to shared identity schema and repositories

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** done (accepted by Codex 2026-11-11; implementer to commit review and merge after green CI)
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
- **Starting SHA:** `c64d63fc` (main, with PE-12 merged). PE-10 therefore lands second and sets the final head.
- **Changed:**
  - `core/persistence/models.py`:
    - New constants: `SYSTEMS` (`pf2e`, `dnd5e`, `cosmere`), `LEGACY_STORAGES` (adds `dnd5e_characters`) and their CHECK texts.
    - `ck_campaigns_known_system` and `ck_characters_known_system` admit `dnd5e`, and `ck_characters_known_legacy_storage` admits `dnd5e_characters`.
    - New `ck_characters_dnd5e_locator`: a D&D row uses `dnd5e_characters` with `legacy_file = id || '.json'`, and no other system uses that store. It holds for archived rows too.
  - `core/persistence/repositories.py`:
    - `CampaignRepository.upsert` accepts exactly the three systems.
    - `CharacterRepository.upsert_identity` checks that D&D, and only D&D, uses `dnd5e_characters`. For D&D it also checks the id (at most 64 `[A-Za-z0-9_-]`, full match) and `legacy_file == id + '.json'`.
    - `upsert_identity` now refuses an existing identity whose campaign or system would change, in every system. Campaign system changes are not frozen, because `campaign_runtime.save_campaign`'s system repair is an existing feature.
  - New revision `migrations/versions/20261111_0004_dnd5e_identity.py`:
    - It swaps the checks: plain DROP/ADD CONSTRAINT on PostgreSQL, and an explicit-shape (`copy_from`) batch rebuild of `campaigns` and `characters` on SQLite.
    - It keeps PE-12's SQLite guards and single-transaction wrapping.
    - On SQLite, `uq_characters_active_legacy_locator` is dropped and recreated with the identical columns and `WHERE archived_at IS NULL`, because a table rebuild drops indexes. Tests compare its `sqlite_master` DDL byte for byte. PostgreSQL never touches it.
    - The downgrade refuses (online only) while any D&D campaign or character exists. It refuses offline `--sql`.
  - `core/persistence/runtime.py::_REQUIRED_REVISION` is set to `20261111_0004`, the single head.
  - Tests:
    - New `tests/persistence/test_dnd5e_identity_schema.py`.
    - `test_runtime_backend.py`: the pin moves to `20261111_0004`.
    - `test_database_and_models.py`: the offline check-name test allows a revision that drops and re-adds a check, as long as each drop is followed by an add.
    - `test_game_state_schema.py`: PE-12's downgrade, failure and offline cases now target PE-12's own revision (`PE12 = 20261110_0003`). Running to `head` would otherwise test 0004's guards, or stop a refused multi-step downgrade at 0003 rather than at head.
- **Tests run (Windows, repo venv):**
  - `pytest -q tests/persistence -m "not postgresql"`: 347 passed, 19 deselected.
  - The brief's acceptance command (four files): 89 passed, 2 errors. The errors are the two new PostgreSQL-marked tests, which assert `DATABASE_URL` like the existing PostgreSQL tests do.
  - `pytest tests/persistence -q -m postgresql --strict-markers`: 2 skipped, 17 errors, all from missing `DATABASE_URL`. Docker cannot start here, so PostgreSQL is verified only by the CI `postgresql` job. This includes `test_postgresql_dnd5e_checks` and `test_postgresql_upgrade_downgrade_round_trip`.
  - `alembic heads`: `20261111_0004 (head)`.
  - A fresh SQLite file goes up to head, then `alembic check` reports no new operations. Up to 0003, down from 0004 and up again also completes cleanly.
  - `git diff --check` is clean.
- **Review before hand-off:** a 4-agent review covered the migration, the repository, test strength and integration. Each finding was re-checked by an agent asked to refute it.
  - Confirmed and fixed (all in tests):
    - the offline check-name count (each widened name now appears 3 times in offline SQL);
    - 8 PE-12 downgrade-refusal tests that expected the head stamp after a refusal;
    - 3 PE-12 failure and offline tests that had silently started exercising 0004's guards instead of 0003's;
    - a PostgreSQL case that broke two checks at once.
  - Refuted:
    - "the PostgreSQL tests should skip without `DATABASE_URL`": that matches the repo convention;
    - "removing the post-rebuild FK check is not caught by a mutation": that check is a defensive assertion with no reachable failing path.
  - The migration reviewer also confirmed on seeded SQLite that every row, `archived_at` and every index survive both directions. The only DDL difference is constraint order inside `CREATE TABLE`.
- **Skipped / not verified:**
  - PostgreSQL locally (CI only).
  - The full repository suite was not run locally; the CI `test` job runs it.
- **Known wrong / limitations:**
  - The change is about 880 lines against the roughly 500-line budget, mostly tests (578 lines). Runtime integration was not added.
  - The D&D id rule (at most 64 `[A-Za-z0-9_-]`) is enforced by the repository, not by a database CHECK. A portable regex CHECK does not exist across SQLite and PostgreSQL. `String(64)` bounds the length only on PostgreSQL.
  - Refusing to move an identity applies to PF2e and Cosmere too. Nothing in the repository moves a character between campaigns today: the only caller is the snapshot import, which keeps ids in their campaign.
  - In SQL ownership mode, creating or repairing a campaign as `dnd5e` now succeeds where it used to fail at the database. D&D routes still do not use SQL ownership (PE-11).
- **Follow-ups (not done here):** PE-11 (locator path rules, ownership, import/export for `dnd5e_characters`).

## Review
<!-- Codex appends numbered findings or acceptance. -->

### Review 1 — accepted (Codex, 2026-11-11)

Reviewed `2b86738fd7f21837bd16d54d3ab4864a20f45f12` against base
`c64d63fc90c5a2a484eb2773299b632e4674a85c`, matching draft PR #250.
The worktree was clean before review. **Accepted with no actionable findings.**

The D&D system/store checks and exact `<id>.json` locator hold for active and
archived rows. Repository validation uses a full match for the allowed ID, and
refuses an existing character's campaign or system changing before writing its
fields. Applying that restriction to PF2e and Cosmere is consistent with the
brief's identity invariant. Independent probes covered all three systems. The
only product `upsert_identity` caller is the snapshot importer, behind its
empty-target check; the existing campaign-system repair path still works.

The disclosed repository-only ID syntax rule is acceptable here. Direct ORM or
SQL writes do not receive that regex validation; PE-11's adapters must preserve
the same validated ID boundary. No D&D route/ownership activation was added.

SQLite's physical index recreation preserves PE-12's definition and semantics:
the name, columns, uniqueness and `WHERE archived_at IS NULL` predicate remain
unchanged, with byte-identical index DDL. This satisfies the brief's explicit
table-rebuild exception rather than redefining the locator rule. The copied
table shapes retain all columns, foreign keys and other indexes. PostgreSQL
only swaps the intended CHECKs using `op.f()` and does not touch the index.

PE-12's retargeted tests still prove its own refusal/rollback behavior at `0003`;
its successful upgrade test continues through the new head. An independent
head-to-`0002` probe with an archived character confirms `0004` completes, then
`0003` refuses and leaves the database at `0003`, with rows intact and a successful
re-upgrade. Atomicity is per revision, not across that whole SQLite downgrade.
Independent late-failure probes in both `0004` directions populated 14 tables,
failed after the campaign rebuild, and verified exact schema/row/stamp rollback
plus successful retry. The larger diff remains within schema/repository scope;
its size alone is not a finding.

Verification with the repository venv:

- `python -m pytest -q tests/persistence -m "not postgresql"`:
  **346 passed, 1 failed, 19 deselected in 420.47s**. The sole failure was a
  120-second subprocess timeout in the unchanged
  `test_runtime_http.py::test_database_outage_never_opens_legacy_gm_access[sql]`.
  An isolated rerun passed: **1 passed in 11.19s**. No assertion failure was
  reproduced; the timeout's cause was not established. This is not presented
  as an entirely green initial local suite.
- The exact four-file acceptance command: **89 passed, 2 errors in 22.18s**.
  Both errors are PostgreSQL fixture setup requiring a local `DATABASE_URL`.
  Those two cases executed successfully in CI; local PostgreSQL was unavailable.
- `python -m alembic heads`: one head, `20261111_0004`.
- Fresh isolated SQLite: upgrade to head and `alembic check` passed with no new
  operations; downgrade to `20261110_0003`, re-upgrade and a second check passed.
- `git diff --check c64d63fc..2b86738f` passed.
- Synthetic review evidence is under workspace `output/pe10-review/`:
  `migration-retry-result.json` records the populated failure/retry probes;
  `migration-result.json` also records the multistep downgrade refusal.

All four jobs on the reviewed head are green in
[CI run 43084583473](https://github.com/etamd17/GM_pf2e/actions/runs/43084583473):
`changes`, `test`, `postgresql`, `browser`. PostgreSQL reports **19 passed,
347 deselected**, including both new PE-10 cases, with successful upgrade and
schema comparison. Main CI reports 950 PF2e contract tests passed, then **4566
passed, 136 skipped, 199 deselected, 1 xfailed**; browser reports **176 passed,
4 skipped**. The local `gh pr checks 250` command could not connect to
`api.github.com`; statuses and logs were independently read through the GitHub
connector instead.

Next: Claude commits these review/queue edits and pushes, confirms every CI job
is green on the resulting PR head, then merges under the handoff protocol.
PE-11 remains gated on integration and its reconciled brief. This review changed
only the task and queue documentation; no product edits, commits, pushes or
merges were made.
