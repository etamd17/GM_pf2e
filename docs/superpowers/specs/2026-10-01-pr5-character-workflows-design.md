# PR5: player character workflows

Date: 2026-10-01
Status: specification and Native implementation plan approved; implementation and local verification complete. User explicitly requested push and merge on 2026-10-01.
Baseline: `8edf7424` (PR4B merged).

## 1. Purpose and approved scope

A campaign player must be able to create or import their own character, leave
and resume unfinished work, and open the resulting sheet without using a GM-only
endpoint. They must not be able to modify another character by supplying a name,
ID, filename, ownership field, or override flag.

The user approved extending the existing PF2e and Cosmere builders with stable
IDs, server-checked capabilities, private saved drafts, explicit new-versus-update
actions, and accurate ownership labels. This implements remediation PR5's exit
condition in `docs/remediation/README.md`.

Preserve the current JSON default, opt-in SQL support, game calculations, existing
GM workflows, and live-campaign restrictions. Deployment and staging verification
remain pending; approval of this specification does not approve a production
database cutover, merge, or push.

## 2. Boundaries and approach

Extend the existing server-rendered builders and vanilla JavaScript. Introduce a
small shared workflow boundary with JSON and SQL adapters; do not build another
builder, rules engine, or application framework.

A PF2e-only endpoint patch would leave drafts and cross-system consistency
unfinished. SQL-only functionality would depend on the pending database rollout.
Supporting both current modes meets the approved scope without that dependency.

Included:

- PF2e creation, Pathbuilder JSON upload/paste, and supported Paizo PDF import.
- Cosmere creation, its existing builder's edit flow, and supported PDF import.
- Private drafts for these workflows, including staged reimport of an existing
  PF2e character selected by ID.
- Canonical ID-based character entry points, capability-aware navigation, and
  safe compatibility adapters for existing links.
- Draft migration, backup, and rollback support needed to avoid losing new data.

Excluded:

- PF2e rules/schema modernization and authoritative rules cutover (PR6/PR7).
- PF2e level-up wizard drafts; the separate existing level-up workflow remains.
- Replacing name-keyed combat globals, map references, or SSE infrastructure;
  multi-worker support and a transactional outbox remain PR8.
- Cross-campaign copying, collaborative drafts, draft sharing, new permission
  management screens, unrestricted renaming, or a UI redesign.
- Production configuration changes or enabling SQL by default.

## 3. Current evidence

- `core/route_policy.py` permits members to open `player_builder`, but classifies
  `save_new_character` and `import_pathbuilder` as live-campaign GM operations.
  `app.py` also lists those API paths in `GM_API_PREFIXES`.
- Both PF2e write paths currently choose files by sanitized display name. Import
  can merge by name; builder save can inherit an existing file's identity.
- `core/storage.py::ensure_character_envelope` is a compatibility helper, not a
  trust boundary for player-supplied imports. Imported metadata cannot be trusted.
- PF2e creation's `force` and `force_save` flags currently rely on the endpoint
  being GM-only. Player access needs an independent server-side override check.
- Cosmere already permits player creation/import and assigns new characters to
  the player; GM-created characters remain unclaimed.
- Both builders retain unfinished inputs only in page memory. The SQL `Draft`
  model exists, but has no runtime repository or UI.
- The PF2e roster's browser-local "Mine" star is a bookmark, not ownership.
- PR4B preserves SQL drafts in an export history sidecar, but the JSON runtime
  and forward importer cannot resume those drafts from that sidecar.

## 4. Components and responsibilities

Use focused modules alongside the existing application:

| Boundary | Responsibility | Inputs/dependencies |
|---|---|---|
| Character capabilities | Pure decisions for view/edit/create/delete/override and ownership management | Verified principal, campaign membership, authoritative assignments, system and legacy mode |
| Character resolver | Resolve one immutable ID within one verified campaign to its stored locator | Existing JSON envelopes or SQL character registry |
| Draft service | Author-private lifecycle, revisions, versioned inputs, and publication state | Explicit campaign/user context and selected persistence adapter |
| Workflow receipts | Retain request-key deduplication, invalidation, and interrupted-publication evidence independently of draft deletion | Private JSON records or additive SQL storage; no dependency on a surviving draft row |
| Character workflow service | Validate create/import/update intent, authorize again, preserve state, publish once | Resolver, capabilities, drafts, existing system serializers and writers |
| Flask/UI adapters | Translate requests, errors, and capabilities; serialize and restore builder inputs | Existing CSRF, route inventory, templates, and shared SSE hub |

Names and exact module splits belong in the implementation plan. Keep these
responsibilities separate from Flask globals. Extract only existing builder/import
normalization and save seams needed by this workflow; do not copy rule calculations
into a second implementation.

## 5. Character identity and compatibility

1. New account-mode characters receive a server-generated UUID-hex ID and an
   ID-based filename. The selected campaign supplies campaign/system metadata.
   Player-created characters belong to the authenticated creator; GM-created
   characters keep the existing unclaimed default.
2. Existing IDs and filenames remain unchanged. Resolve updates by the explicit
   target ID inside the authorized campaign, never by the submitted name.
3. Strip imported identity, ownership, assignments, campaign IDs, filesystem
   locators, and internal publication metadata. Never pass an untrusted envelope
   into a helper that preserves authority from that envelope.
4. New account-mode create/import routes are distinct from the old GM-only
   compatibility endpoints. Do not relax the latter's policies or prefixes.
   Both paths may share normalization code; player workflows must not call a GM
   endpoint internally or from the browser. Account-mode GM compatibility writes
   must resolve an existing unambiguous name to its stored ID and locator, including
   new ID-based files, rather than constructing a second name-based file. Duplicate
   name protection covers every account-mode creation path. Legacy no-account
   filename behavior remains unchanged.
5. Add canonical ID-based sheet entry points and use them in newly returned URLs,
   drafts, roster actions, and account links. The adapter may invoke existing
   sheet rendering after resolving the authorized character.
6. Existing name URLs remain compatible only when they resolve exactly one
   character in the authorized campaign. Missing, duplicate-ID, or ambiguous
   mappings fail closed rather than selecting the first match.
7. PF2e's live runtime remains name-keyed. Refuse a new duplicate display name
   with a clear conflict message. Distinct names that previously sanitized to the
   same filename must not overwrite each other; new ID filenames remove that
   storage collision. Legacy aliases that cannot resolve uniquely are rejected.
8. Updating an existing PF2e character preserves its display name in this release.
   A differently named reimport returns an actionable conflict instead of silently
   renaming live combat references. Users can correct the import or create a new
   character. Preserve Cosmere's existing ID-based builder name editing and do not
   expand existing GM compatibility behavior.

No bulk filename migration, implicit campaign switch, or combat rekey is required.

## 6. Capabilities and trust boundary

Compute capabilities server-side and use the same decisions in service checks,
route enforcement, and UI rendering. A capability sent to the browser is a display
hint, never a credential. Recheck membership and assignments on each request and
again at the publication boundary under the appropriate locks.

| Action | Account-mode rule |
|---|---|
| Create/import through personal workflow | Current campaign member, including a GM member |
| Read/save/discard/publish a draft | Its author, while still a current member; no implicit GM bypass |
| Edit/reimport a published character | Owner, SQL editor, or authorized campaign GM/admin |
| PF2e sheet access | Owner, SQL editor/viewer, or authorized campaign GM/admin; viewers are read-only |
| Cosmere sheet access | Preserve existing owner/editor/GM/admin route boundary; no blanket member access |
| Delete a published character | Preserve GM-only PF2e deletion and owner-or-GM/admin Cosmere deletion |
| Manage ownership/release or override existing validation | Existing authorized GM/admin authority only |

SQL owner/editor/viewer assignments remain authoritative in SQL mode. JSON mode
uses its existing owner model; do not invent editor/viewer grants from imported
JSON fields. Existing invite-based claim/release flows remain the way to transfer
ownership; a roster bookmark never claims a character.

Read capability does not grant access to owner-private fields or messages. Preserve
existing field-level privacy and SSE filtering; viewers must not receive mutation
controls or gain write access by calling an existing sheet endpoint directly.

Personal drafts require real membership even for a site admin, matching the SQL
membership foreign key. An admin without membership retains existing administrative
tools, but does not receive fabricated membership or another user's draft access.

Legacy open/password modes keep their existing behavior. New private, account-owned
drafts are not exposed to anonymous or legacy player sessions. Existing legacy GM
builders remain usable without setting up accounts or SQL.

All affected routes remain in the exhaustive policy inventory and retain CSRF
protection and live-campaign containment. An inactive campaign returns the existing
409 contract; selecting a campaign must not silently load it for the whole table.

## 7. Draft data and lifecycle

Use one service contract for JSON/shadow and SQL. SQL uses the existing `Draft`
model with additive changes only if needed. JSON/shadow store private draft records
under `DATA_DIR/character_drafts/<campaign_id>/<user_id>/<draft_id>.json`, outside
portable campaign archives. Validate every path segment and use durable atomic
writes. Keep workflow receipts in a separate private record namespace under this
root; SQL requires equivalent additive receipt storage. Shadow remains
JSON-authoritative; do not introduce dual writes.

A draft contains server-owned identity/author/campaign/system, a supported kind
(`pf2e_builder`, `pf2e_import`, `cosmere_builder`, or `cosmere_import`), input format
version, lifecycle state, monotonically increasing revision, timestamps, optional
target character ID, base-build fingerprint for updates, and publication metadata.
The payload contains editable inputs and UI state, not trusted grants or rules
authority. Keep service metadata separate from client-editable inputs.
Draft kind, selected parser, and any target character must match the authorized
campaign's system. Reject cross-system requests rather than trusting a payload's
system label or interpreting it through the wrong builder.

Store enough to restore the actual form: wizard position, choice state, and
DOM-only fields such as PF2e ancestry boost method and deity. Persist normalized
import content and source checksum; do not retain uploaded PDF binaries in drafts.
Render draft text as untrusted content. Bound serialized inputs to 2 MiB per draft
and allow at most 20 active/publishing drafts per author per campaign; existing
upload limits still apply before parsing. Return an actionable limit error.

Lifecycle:

`active -> publishing -> committed` or `active -> discarded`.

- Opening a GET page alone creates no draft. First user change or explicit Save
  creates one; creation retries use a stable request key scoped to author/campaign.
- Saves, discard, and publication require the expected revision. Stale operations
  return 409 and do not modify stored inputs. A late autosave cannot revive a
  publishing, committed, or discarded draft.
- Incomplete inputs may be saved. Current build validation applies at publication,
  not at every draft save. Unknown payload versions fail explicitly without erasing
  the stored draft or pretending it was an empty build.
- Committed records retain the publication result for retry deduplication and are
  excluded from the resume list. Discard removes editable payload but retains a
  workflow receipt so delayed requests cannot recreate the same draft. Request-key
  deduplication and publication evidence must not depend on a surviving draft row.
- No automatic expiry in this release. Explicit discard is confirmed in the UI.
- Membership removal invalidates that author's drafts for the campaign and erases
  editable payloads, matching the SQL draft cascade. Persist invalidation/request-key
  receipts independently before deletion; rejoining must not resurrect drafts even
  through a delayed create retry. Existing-character deletion likewise invalidates
  linked drafts. JSON requires recoverable cleanup, not just best-effort file deletion.
- Receipts retain only scoped IDs, request-key fingerprints, lifecycle/result
  metadata, and recovery checksums/locators, not builder payloads. They must survive
  membership, target-character, and draft cascades; do not give them cascading
  foreign keys to those records. Historical target IDs are evidence, not authority
  or live foreign-key requirements. Check current authorization before returning
  any surviving result. Include these receipts in private migration/backup data.
- Serialize removal/deletion with publication and reconcile pending publication
  intents before erasing their supporting records. If reconciliation is uncertain,
  block that destructive operation with a repair-required response; do not lose
  recovery evidence. Receipt invalidation and membership/character removal are
  transactional in SQL and use durable intent/recovery in JSON.
- Campaign trash makes drafts inaccessible; restore may resume them if membership
  still exists. Permanent purge removes associated private JSON drafts when the
  existing backend supports purge; SQL purge remains unsupported as in PR4B.

## 8. Creation, import, and update flow

1. From the current roster or builder, a player chooses Create or Import. Import
   defaults to **new character**. Updating requires selecting a specific editable
   character; there is no same-name automatic merge in the new workflow.
2. Create or resume the author's draft and show its saved state. For an update,
   capture the target's build fingerprint, not its volatile combat state.
3. Parse imports with the existing system parser and show the resulting name,
   system, and new/update target before confirmation. Single-file import is the
   minimum player UI; existing GM multi-file import remains available.
4. On Finish, flush pending autosave, submit the expected draft revision, resolve
   the target again, reauthorize, and run current system validation. PF2e force
   flags only work for an authorized GM; no override bypasses identity, payload
   shape, membership, or stale-update checks. PR5 does not claim full rules legality.
5. For an update, reject a changed base build with 409. Re-read and preserve the
   latest runtime fields, including HP, conditions, notes, wallet, inventory/custom
   data according to the existing builder/import behavior. Preserve PF2e's dirty
   combat flush-before-read ordering and existing smart-merge field rules.
6. Publish using the server-owned ID/locator and return the canonical sheet URL.
   A created player character appears in the existing account character list.
   Existing-character updates retain identity and assignments.

The build fingerprint includes fields the particular operation would replace,
including display name, and excludes preserved live fields. Each system adapter
owns and tests this projection. A combat HP tick alone must not invalidate a draft;
a second builder or import changing its target build must.

## 9. Concurrency, retries, and failure recovery

Publication uses a stable operation identity and, for creation, a character ID
reserved durably in the independent receipt before any character file is published.
The receipt preserves intent even if a draft row is subsequently removed. Retrying
an identical publication returns the same result. Reusing a request key for different
content returns a conflict. A retry must still verify current membership and result access.

Use the established campaign-first lock ordering; within the workflow use campaign,
draft, then target character. Recheck authority and base fingerprint after locking.
SQL completion must compose draft state, character metadata, assignment, and audit
in one transaction, using session-bound ownership primitives rather than nested
independent commits. JSON uses the supported single-worker locking model, durable
publication intent, atomic record/file replacement, and recoverable completion.

Character payloads still live in files. This is not a distributed transaction:

- Validation, access, or stale-revision failures publish nothing and retain the
  editable draft.
- A failed file write must not commit new identity/ownership or a successful draft
  result. Restore an active draft when failure is known to precede publication.
- A process crash or uncertain SQL commit after a file write leaves a recoverable
  publishing intent. Do not report success, generate another ID, or blindly restore
  an old target file over a later change.
- Recovery checks the operation identity, expected content fingerprint, current
  draft/registry state, and target locator. Complete a proven publication, retry
  a proven non-publication, or fail closed with a repair-required error. Ambiguous
  state stays preserved for operator reconciliation.
- New unresolved workflow files must not become usable characters simply because
  a loader scans their directory. Readers must exclude or quarantine unresolved
  creates before exposing them. Updates with uncertain completion require recovery
  before another write can proceed. Keep this boundary limited to new PR5 writes.
- Emit normal post-save refresh notifications only after successful completion,
  through the existing campaign-scoped SSE hub. Durable event replay remains PR8.

Tests must inject failures at each persistence boundary; no claim of exactly-once
physical file writes or all-or-nothing database/filesystem commits is permitted.

## 10. User experience and errors

Keep existing builder steps and styling. Add Save draft, a saved/unsaved/saving/error
indicator, Resume drafts, and Discard. Autosave after a short idle debounce, serialize
requests, and coalesce pending changes; display Saved only after acknowledgment.
Finish waits for the latest save. Warn on navigation while changes are unsaved;
do not rely on unload requests for durability.

On revision conflict, preserve the form, stop autosave, and offer reload of the
saved draft or an explicit copy as a new draft. Copy preserves the operation target
and original base fingerprint; it must not bypass stale-target validation.
Permission loss stops saves and removes mutation actions without erasing the local
form. Transient failures offer retry without claiming work was saved.

Use real ownership/access labels in account mode. Replace misleading "claim" stars
with authoritative owned/editable labels; any retained bookmark is clearly labeled
as a bookmark and scoped by user/campaign/character ID. Do not show sheet/edit/delete
links that the displayed user's capabilities deny. A read-only rendering branch in
a template does not justify broadening the current Cosmere route authorization.

New API errors use stable codes and a readable message: 400 malformed/unsupported
input, 401 login required, 403 insufficient capability, 404 inaccessible/missing
private resource, 409 revision/target/name/live-campaign conflict, 413 input too large,
429 draft quota exceeded, and 503 unavailable storage or repair-required publication.
Build validation uses 422 with field/general issues. Adapt existing validation
responses at the new boundary without changing legacy endpoint contracts. Never
expose raw exception text, server paths, or another author's draft existence.

## 11. Backups, migration, and rollback

Private drafts must not appear in ordinary portable campaign ZIP exports or player
character downloads. They belong in access-controlled full-site backups, including
the SQL snapshot where applicable. Verify backup code does not silently omit the
new private JSON root.

Extend migration plan/import/verify and current-runtime reverse export to include
usable draft records, versions, revisions, lifecycle metadata, and stable publication
results, together with independent workflow receipts. SQL-to-JSON rollback must
produce drafts the JSON service can actually resume; the existing history sidecar
alone is insufficient. Forward migration must preserve those same records and reject
inconsistent live draft author/membership/target links. Historical receipts are a
separate record type and may legitimately reference removed memberships or characters;
never recreate those grants or targets on import.

Quiesce writers and reconcile pending publication intents before migration/export.
Unresolved work blocks the operation rather than being silently omitted. Portable
campaign import continues to generate new identities and grants and does not import
private drafts. Feature rollback after drafts are written requires a verified backup
and draft-capable code; deploying old code alone is not a validated data rollback.

## 12. Verification and acceptance

The implementation plan must cover these gates with executable checks:

1. Account-player end-to-end creation/import for both systems and supported formats;
   no GM endpoint is called, the player owns the result, and account/sheet links work.
2. Owner/editor/viewer/GM/nonmember boundaries across JSON and SQL; forged IDs,
   envelopes, ownership, campaigns, filenames, and force flags cannot grant access.
3. Duplicate display names, sanitized-name collisions, duplicate IDs, invalid target
   IDs, cross-campaign targets, and legacy link ambiguity never overwrite another PC.
4. Save/refresh/resume/discard across users, campaigns, tabs, and process restart;
   incomplete forms and DOM-only selections round-trip. Unsupported versions and
   quota/size failures preserve existing drafts.
5. Stale drafts, stale target builds, simultaneous publish, lost responses, duplicate
   clicks, and late autosaves; revoked membership, character deletion, trash/restore,
   and rejoin behavior match between adapters. Receipts survive cascade deletion,
   delayed creation retries cannot resurrect revoked drafts, and removal cannot
   erase an unresolved publication intent.
6. PF2e smart-merge and Cosmere edit preservation of current runtime state; existing
   calculation goldens and snapshots remain unchanged.
7. File/SQL failure injection, recoverable uncertain completion, and no exposure of
   unresolved new files. PostgreSQL checks exercise real locking/concurrent publication.
8. Draft-aware migration dry-run/count/checksum reconciliation and reverse-export
   resume; full backup retention and portable archive privacy are tested separately.
9. Browser journeys at phone and desktop widths: create, upload, resume after refresh,
   conflict handling, ownership labels, keyboard access, and accessible save/error
   announcements. Visible controls must agree with server enforcement.
10. Existing pytest suite, route inventory, Jinja parsing, and applicable PostgreSQL
    checks; Railway preview smoke before merge, with missing external access reported
    as pending rather than converted into a local-pass claim.

## 13. Delivery and approval status

Build as reviewable layers within PR5: identity/capabilities and shared normalization;
draft persistence/recovery and migration; HTTP integration and existing-builder UI;
end-to-end security, browser, and deployment verification. The separate implementation
plan and Native inline execution have been approved. Implementation and integrated
verification are recorded in that plan and the PR5 release handoff.

The user explicitly requested push and merge on 2026-10-01 after the local
verification handoff. Integration proceeds through a pull request with passing
GitHub checks. No production data or backend configuration has been changed.
Railway preview and actual staging/database cutover remain unverified; public
production health and revision checks follow the automatic main deployment.
