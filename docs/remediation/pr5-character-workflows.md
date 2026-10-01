# PR5: player character workflows

Branch: `remediation/pr5-character-workflows`

Base: `8edf7424` (PR4B)

Status: local implementation and verification complete, including the independent review and its regression-tested material fixes. On 2026-10-01 the user explicitly requested push and merge to GitHub. Integration is proceeding through a pull request and passing GitHub checks; the PR records its final merge status. No production database migration or backend configuration change is included.

## Behavior

Account members can create PF2e/Cosmere characters and prepare private imports,
save incomplete drafts, resume after refresh/restart, and explicitly publish.
PF2e supports Pathbuilder upload/paste and supported PDFs; Cosmere supports its
existing PDF parser. Imports first show a private confirmation page. Updates
require an explicit editable character ID. PF2e native builder updates and
level-up drafts remain outside this release.

Drafts are author-private, campaign-scoped, versioned, limited to 2 MiB each and
20 active/publishing drafts per author/campaign. There is no automatic expiry.
Autosave waits 750 ms, serializes requests, and displays Saved only after an
acknowledgment. Finish waits for the latest save. Conflicts retain local input and
offer reload or a copy that retains the original target/build fingerprint.
Lost responses reuse request keys or reconcile exact saved revisions. Publication
freezes the captured form until the result is definite; Retry remains available.
Membership loss stops mutations without erasing the browser's unsaved form.
Unsupported saved input versions fail without replacement or deletion. A form
restoration error is visible and cannot overwrite the saved draft.

New account characters have server-generated UUID-hex IDs and ID filenames.
Canonical sheet links are `/characters/<id>`. Existing IDs/locators and legacy
no-account GM workflows remain supported. GM reimports resolve an existing
name to its actual stored locator. New creates never merge implicitly by name.

Capabilities come from fresh membership and authoritative assignments, never
imported grants or browser bookmarks. SQL PF2e viewers get a read-only sheet
without selecting an acting character or receiving owner-private notes. Cosmere
viewers retain its existing denied-sheet boundary. Editors retain game controls
but not owner-private notes. Account roster labels and import targets reflect
actual capabilities; legacy stars are explicitly bookmarks, not ownership.

JSON remains the default; shadow remains JSON-authoritative; SQL stays opt-in.
One gevent worker/replica and the existing live-campaign restrictions remain
required. No rules engine, production dependency, sharing UI or whole-site
redesign is introduced. PR9 remains the broader visual/navigation work.

## Publication and repair

Publication persists a stable identity reservation and expected full-document
digest before writing the character file. SQL completion commits identity,
assignment, audit, draft state and receipt together. JSON uses a durable redo
journal. Files and SQL are **not** one distributed transaction; ambiguous
failures retain a publishing receipt rather than reporting success or blindly
overwriting a file.

Unresolved creates stay invisible to loaders/account lists; affected updates,
legacy writes, combat mutations and all-target batches fail closed. Unrelated
characters remain usable. Notifications occur only after completion. Membership
removal, target deletion, trash and export cannot bypass unresolved publication.
Receipts survive lifecycle deletion to prevent an old request from resurrecting
a discarded/revoked draft.

For `publication_repair_required` or an unresolved publishing draft:

1. Keep the receipt, draft and character files intact. Do not delete a private
   journal, clear receipts, rename provisional files or overwrite a divergent
   character to make a loader accept it.
2. Retry Finish from the same author's selected live campaign. A lost-response
   retry uses the original key. Reopening a publishing draft shows an author-only
   recovery page with Retry, without rendering the quarantined character or
   editable inputs. Proven writes complete without a new identity; proven
   non-writes become editable after Reload. A completed draft URL redirects to
   its character only if current access still permits viewing it.
3. If reconciliation still returns 503, quiesce writes and take both a full
   `DATA_DIR` backup and, in SQL mode, a consistent database snapshot. Preserve
   the failure evidence. A portable campaign ZIP is not sufficient.
4. An authorized operator must compare the scoped receipt's target locator,
   canonical content digest, registry checksum/state and identity to the actual
   file and supported backup. Do not log raw private inputs, credentials or
   database URLs. A mismatch requires a deliberate, backed-up repair decision;
   automatic recovery deliberately refuses to guess which divergent content wins.
5. Retry through the normal authorized workflow after reconciliation. Confirm
   one character ID, one owner assignment and one publication audit; only then
   resume affected writes. If authority was changed out-of-band, reconcile that
   separately rather than impersonating an author through a fabricated session.

PF2e still has a name-keyed live cache. Duplicate-name or duplicate-ID files
require identity repair before canonical interactive access; they must not
select an arbitrary cached character. Cosmere's existing stable-ID behavior is
unchanged.

## Migration and rollback

The additive migration is `20261001_0002_character_workflow_receipts`. Existing
draft rows remain present. Receipt rows are independent of cascading member,
target and draft deletion. Downgrading a nonempty receipt store is refused.

Use the existing offline planner/importer/verifier and current-runtime exporter
with writes quiesced. They now include resumable version-1 private drafts and
historical receipts, validate author/campaign/target scope and reject pending,
changed or unsupported sources. Reverse export writes the actual private JSON
layout, not only the old history sidecar. Historical receipt references do not
recreate removed memberships/assignments. Pre-PR5 unversioned draft history
remains sidecar-only and is not presented as resumable.

Back up the entire `DATA_DIR` root, including `character_drafts`, and SQL when
enabled. Portable campaign archives intentionally exclude private drafts and
refuse provisional workflow characters. Rollback must keep compatible PR5 code
and a coordinated file/database snapshot; old code cannot safely service pending
PR5 publication intents. See [the deployment runbook](../../DEPLOY.md) and
[the PR4B cutover runbook](pr4b-runtime-cutover.md).

## Verification map

| Contract | Executed coverage |
|---|---|
| Identity, capabilities, authority stripping, rules parity | `test_capabilities.py`, `test_identity.py`, `test_system_adapters.py`, existing builder and Pathbuilder ground-truth tests |
| Private lifecycle, quota, CAS, request-key replay | `test_drafts.py`, workflow schema/store tests, `test_http.py` |
| Live HP retained / stale build refused | `test_publication.py::test_stale_build_rejected_but_live_hp_preserved` |
| Lost create after removal/rejoin cannot resurrect | `test_invalidation.py::test_delayed_create_retry_after_rejoin_stays_revoked` |
| Fault boundaries and fresh-process recovery | `test_recovery.py::test_fault_boundaries_recover_in_a_new_process`, publication/recovery suites |
| Pending guards across legacy/batch/combat/Obsidian | `test_recovery.py`, existing character batch/race/ownership suites |
| GM reimport updates ID filename | `test_legacy_compatibility.py::test_gm_reimport_resolves_id_filename` |
| Viewer/editor privacy and no acting-character selection | `test_http.py`, access/route/containment/security regressions |
| Non-DOM picks, restore suppression, serialized finish, lost responses | Node-executed `test_draft_controller.py` and `test_builder_draft_contracts.py` |
| Pending publication resume, completed URL, unknown stored versions | Both-backend/both-system HTTP resume tests; Node recovery-state tests; `test_unknown_stored_version_is_never_reinterpreted_or_erased` |
| Actual builder/import/refresh/restart/two-tab/keyboard/role rendering | `tests/browser/test_character_workflows.py`, 2 systems × 2 backends × 2 viewports |
| Real database contention, quota/CAS/publication/revocation | `tests/persistence/test_workflow_postgresql.py`, distinct PostgreSQL backend PIDs and observed row-lock waiters |
| Private archive boundaries and resumable forward/reverse export | `test_migration.py`, `test_archive_privacy.py`, existing migration/export tests |

Final runs after the review fixes:

| Gate | Result |
|---|---|
| Normal regression suite | 3,197 passed, 38 skipped, 1 expected failure, 45 deselected, zero failed; 11 warnings; 1,084.55 s |
| Real browser matrix | 32 passed, zero failed/skipped, 262.10 s |
| PostgreSQL persistence | 13 passed, 240 deselected, zero failed/skipped, 7.23 s |
| Template parsing | All 92 templates parsed |
| Default JSON production-runtime smoke | Passed, exit 0, 38.543 s |
| Explicitly migrated disposable SQLite SQL runtime smoke | Passed, exit 0, 38.698 s; migration exit 0 |
| Synthetic migration/archive round-trip | 15 passed, zero failed/skipped, 4.70 s |

The independent whole-branch review identified three material issues: pending
publication resume, completed draft URLs, and overwriting unsupported stored
versions. Each was reproduced before its fix. Targeted verification and the
final complete suites passed. No second review is claimed. The existing expected
failure concerns selecting an acting character when an owner opens their sheet;
the new SQL viewer path explicitly preserves the session actor and passes.

One minor review item remains deferred: the Node adapter test reads template
source without explicit UTF-8, which can fail under Windows's cp1252 default.
Product runtime reads retain UTF-8; Linux validation does not prove Windows
test-runner compatibility.

Synthetic HTTP timings on the final code used 30 samples per operation/phase
after three warmups, with a restarted single gevent worker for each phase.
The pending phase contained one unrelated publication receipt. Values are
median / p95 in milliseconds; SQL here means local SQLite.

| Backend | Operation | No pending receipt | One unrelated pending receipt |
|---|---|---:|---:|
| JSON | Draft save | 3.152 / 3.745 | 3.499 / 5.316 |
| JSON | HP update | 3.010 / 3.572 | 3.686 / 8.565 |
| JSON | Health control | 1.129 / 1.337 | 1.528 / 4.515 |
| SQL | Draft save | 5.964 / 7.792 | 6.766 / 8.704 |
| SQL | HP update | 10.503 / 14.163 | 11.422 / 14.658 |
| SQL | Health control | 3.516 / 4.552 | 3.964 / 5.695 |

Both mutations and the health control slowed in the later phase. The small,
sequential sample shared a machine with other validation; it does not isolate
receipt overhead, prove a causal regression or establish production latency.
This is an observation, not a latency threshold or performance improvement claim.
All disposable smoke/benchmark servers stopped and their temporary data was
removed. Raw samples and detailed test logs remain in this branch's ignored
execution ledger directory until commit/handoff; no real data was used.

## Release gates still separate from local verification

- Railway preview smoke: pending an authorized isolated deployment target.
- Actual staging/database cutover and production-volume rollback rehearsal:
  pending authorized staging access; synthetic tests are not that evidence.
- Commit/push/create PR/merge: explicitly requested by the user on 2026-10-01.
  GitHub checks must pass before merge. Main automatically deploys to Railway;
  public health and deployed-revision checks follow integration. Authenticated
  production checks, Railway logs, and controlled restart checks require access
  unavailable in this environment. SQL and shadow deployments require the
  explicit schema migration documented above; JSON remains the default.
- Cloud setup draft revision 6 is saved and read-back verified; current-instance
  Python/PostgreSQL/Chromium checks passed. The draft is not published, and a
  future restored task has not yet validated it.
