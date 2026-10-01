# PR4B: opt-in transactional identity and authorization

PR4A supplied the schema, import verification, and transactional services.
PR4B connects those services to the website through `OWNERSHIP_BACKEND`:

| Value | Authority | Database use |
| --- | --- | --- |
| `json` (default) | Existing JSON account and campaign stores | None required |
| `shadow` | JSON | Compare identity, membership, and character grants with an imported SQL snapshot; log only operation names on mismatch |
| `sql` | PostgreSQL accounts, campaigns, memberships, invitations, and character assignments | Required; missing configuration, schema, or connection fails closed |

The setting selects the complete identity boundary, despite its historical
`OWNERSHIP_BACKEND` name. Unknown values are errors. Startup never creates tables
or runs Alembic. SQLite remains available for local unit and HTTP integration
tests; production concurrency is validated against PostgreSQL 17.

## Runtime behavior

- Account registration, first-administrator bootstrap, password changes, session
  invalidation, and last-campaign selection use relational user records.
- Invite redemption commits account creation (when allowed), membership,
  character ownership, remaining uses, redemption history, and audit together.
  An authenticated retry of an already committed redemption is idempotent.
- Campaign membership mutations lock the campaign, recheck the acting GM's
  authority, and preserve at least one GM. Stale campaign documents cannot
  overwrite relational members, creator identity, or trash state.
- Character sheets, map controllers, private handouts, Chronicle recipients,
  and character cards resolve ownership through SQL. Legacy JSON grant fields
  are never a fallback in SQL mode. Owner/editor/viewer assignments require
  current campaign membership; editor permission does not imply ownership of
  private handouts or permission to delete a character.
- Character creation/save registers its identity and checksum. Existing ownership
  is preserved unless an explicit transactional claim/release operation changes
  it. Whole-party writes retain the existing file rollback journal and update
  SQL metadata together.
- Campaign exports and snapshots synthesize current SQL metadata and grants.
  Restoring a campaign archive creates new campaign and character identities,
  remaps references, and grants only the importer GM membership.
- SQL soft deletion retains file assets and marks the campaign unavailable in
  the database. Restore reactivates that campaign. Permanent purge and automatic
  expiration are deliberately unavailable in SQL mode until durable asset
  cleanup is implemented; explicit purge returns a conflict instead of deleting
  one side of a database/filesystem operation.

## Staged activation

1. Keep the current production setting at `json`. Back up the entire data volume
   and the PostgreSQL database independently. Rehearse with isolated copies.
2. Stop mutations and retain the source JSON snapshot. Follow the PR4A
   `plan`, `import`, and `verify` commands in `DEPLOY.md`; reject unresolved
   ownership conflicts or mismatched counts/checksums.
3. With the imported database URL supplied securely to the web process, run
   `OWNERSHIP_BACKEND=shadow` for a bounded read-only comparison. Shadow is not
   dual writing: later JSON mutations make the imported snapshot stale, so
   rehearse again against a fresh consistent snapshot before switching.
4. On staging, explicitly select `OWNERSHIP_BACKEND=sql`, start the existing
   single-worker Gunicorn command, and check `/live`, `/ready`, and `/health`.
   Exercise login, account creation, campaign creation/selection, invites,
   ownership, password resets, both character systems, and archive restore.
5. Before any production switch, review the staging evidence, verified backups,
   and current runtime export. Deploy/merge and production configuration changes
   remain separate actions. Keep exactly one web worker: live encounter globals
   and in-process SSE have not been replaced by an outbox.

## Runtime rollback export

PR4A's reverse export proves the original imported snapshot. After SQL-authority
mutations, use the runtime exporter for current state instead:

```bash
python tools/export_transactional_runtime.py \
  --source "$DATA_DIR" \
  --output-dir /path/to/new-runtime-export \
  --quiesced
```

`DATABASE_URL` must select the source database. `--quiesced` acknowledges that
all writers have been stopped; the command does not stop them itself. The target
must not already exist. The exporter reads one database snapshot, overlays SQL
grants onto current character payloads, verifies source files did not change,
and validates the resulting legacy migration plan before publishing its output.
An unresolved character batch journal blocks export until recovery.

Treat the result as sensitive backup data: it includes password hashes and
invitations. On POSIX, output directories/files use owner-only permissions. On
Windows, choose a destination whose inherited ACLs restrict access to the
operator; POSIX mode bits do not enforce Windows ACL privacy. A private history
sidecar retains SQL audit, redemption, draft, and
migration records. The legacy application and PR4A importer do not replay that
sidecar; retain the SQL backup and plan an explicit reconciliation before a
later return to SQL.

This exports the transactional subset, not scenes, audio, uploads, or the entire
volume. Restore those from the matching full volume backup. Never simply change
the setting back to `json` after SQL mutations: the original JSON authorization
files are stale.

## Remaining architecture boundaries

Character payloads, assets, encounter state, and other campaign documents still
live in files. File-write failures roll back corresponding SQL registration;
a crash or database commit failure after a successful filesystem operation can
require reconciliation. SQL authority prevents an orphan file from granting
access, but PR4B does not claim distributed atomicity across SQL and files.
Game-state repositories and the transactional event outbox remain later work
under ADR-0002 and remediation PR8. Character-draft UI remains PR5.

## Validation commands

```bash
python tools/check_templates.py
python -m pytest -q -m 'not postgresql' --strict-markers
python -m pytest tests/persistence -q -m postgresql --strict-markers
python tools/smoke_production_runtime.py
```

The PostgreSQL command requires an isolated test `DATABASE_URL`; its fixtures
create and remove uniquely named schemas. Run the production smoke with the
virtual environment on `PATH`. Local SQLite HTTP tests use temporary data and
do not read or mutate the production database.

## Cloud verification record (2026-10-01)

- Broad website regression run: 2,828 passed, 25 skipped, 8 PostgreSQL tests
  deselected, and 1 expected failure (16m28s). The final persistence rerun below
  includes the additional review regressions added while that run was active.
- Final persistence suite: 239 passed; 8 PostgreSQL tests selected separately.
- PostgreSQL 17 concurrency suite: 8 passed.
- All 87 Jinja templates parsed; `git diff --check` passed.
- The repository's production Gunicorn/gevent smoke passed in default JSON
  mode and SQL mode against a fresh, explicitly Alembic-migrated PostgreSQL
  database. SQL setup, campaign creation/activation, and requests concurrent
  with an open SSE stream were exercised.
- Windows directory-publication behavior has simulated regression coverage;
  this cloud machine is Linux, so no native Windows run was performed.

These are local cloud-development checks, not a Railway deployment or
production-data cutover. Staging rehearsal and operator review remain required.
