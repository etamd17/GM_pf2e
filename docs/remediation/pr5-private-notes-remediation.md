# PR5 private-notes authorization remediation

Date: 2026-10-05
Last locally verified: 2026-10-06

Status: the remediation was merged in GitHub PR #166 and deployed from revision
`6514fc162d9f69bb0f0aa0c68da169eeec44c871`. The complete 2026-10-06
production rerun passed gates 1 through 5, recorded gate 6 as a justified N/A
under the active JSON ownership backend, and passed gates 7 and 8. PR5
production acceptance is complete.

## Security contract

`notes` and `session_notes` are owner-private. Only the character owner, the
campaign GM, or a site administrator may read or mutate them. SQL editor and
viewer assignments confer gameplay or read capabilities, never owner-private
capability. Direct HTTP requests must enforce the same boundary as the rendered
controls. A denied write returns HTTP 403 and leaves the character file
byte-for-byte unchanged.

The broader capability contract remains authoritative in
[the PR5 workflow documentation](pr5-character-workflows.md) and
[the workflow design](../superpowers/specs/2026-10-01-pr5-character-workflows-design.md).

## Root cause

Five private endpoints inherited the generic live-character owner/editor policy
while the UI used the stricter `owner_private` capability. Existing SQL tests
therefore blessed editor note writes. Hiding the controls did not protect direct
HTTP requests.

Cosmere existing-character reconstruction and publication could also replace a
whole document without restoring trusted private fields. That created an
alternate path for editor-submitted builder data to omit or replace owner notes.
The PF2e condition strip additionally depended on the raw character export even
though it needed only note-free live condition state.

## Affected surfaces

| Surface | Risk before remediation | Remediation boundary |
|---|---|---|
| `export_character` | Editor could retrieve PF2e raw character JSON, including private fields. | Strict owner-private route policy. |
| `save_notes` | Editor could replace PF2e notes. | Strict owner-private route policy. |
| `save_session_note` | Editor could append PF2e session notes. | Strict owner-private route policy. |
| `delete_session_note` | Editor could delete PF2e session notes. | Strict owner-private route policy. |
| `cosmere_pc_notes` | Editor could replace Cosmere notes. | Strict owner-private route policy. |
| Cosmere builder bootstrap, reconstruction, and publication | Private values could be exposed to an editor or replaced through a full-document update. | Project private fields out of editor bootstrap data and preserve them only from the trusted current document. |
| Draft fingerprint transport | A legacy Cosmere draft fingerprint could be derived from a document that included private fields. | Keep fingerprints server-owned; omit them from draft APIs and builder bootstrap payloads. |
| PF2e condition refresh | Sheet JavaScript fetched the private raw export for non-private state. | Read conditions from `/api/pc_state/<pc_name>`. |

## Implementation

- `RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE` applies to exactly the five private
  endpoints above. Owners, campaign GMs, and administrators retain access;
  editors, viewers, ordinary members, and outsiders do not.
- Existing editor gameplay mutations continue through the established
  owner/editor policy. Live-campaign containment and authoritative SQL
  assignment checks are unchanged.
- Shared workflow helpers preserve `notes` and `session_notes` from the trusted
  current character document, remove them from editor-facing bootstrap data,
  ignore submitted replacements, and exclude them from build fingerprints.
- New fingerprints use a `v2:` prefix and exclude private fields. Exact legacy
  unversioned fingerprints remain valid for drafts created before deployment,
  while malformed or unknown versions fail closed. The server retains the
  fingerprint for conflict detection but no draft API or builder bootstrap
  serializes it to the client.
- The condition strip consumes the existing note-free state endpoint and
  replaces its local condition map on every refresh so cleared conditions do
  not leave stale penalties. There is no visible UI design change.
- JSON mode continues to reject imported or file-forged grants. SQL mode
  continues to treat database assignments as authoritative.

## Local verification

Current local evidence:

- Final focused authorization, persistence, workflow, migration, and client
  controller gate: 173 passed and one intentional skip.
- Expanded role/backend cases distinguish a non-admin campaign GM from a
  non-member site administrator and cover PF2e/Cosmere plus JSON/SQL.
- Complete real-browser matrix across JSON/SQL, PF2e/Cosmere, and
  mobile/desktop: 32 passed in 507.69 seconds. Non-owner DOMs contained neither
  sentinel; direct editor requests returned 403; owner notes and session notes
  survived; cleared conditions did not remain in client state.
- Full non-browser regression gate: 3,347 passed, 39 skipped, 45 deselected,
  and one established expected failure in 3,322.16 seconds.
- All 92 templates parse and `git diff --check` is clean.
- Two independent fresh reviews found no residual actionable privacy gap and no
  Critical or Important defect.

The 13 PostgreSQL-marked cases were not run locally: `DATABASE_URL` is unset and
no `psql`, Docker, or Podman runtime is installed. They remain a mandatory CI
gate; unavailable database prerequisites are not treated as a passing skip.

## Deploy sequence

1. Review the authorization diff and confirm only the five intended endpoints
   use the strict policy; verify representative editor gameplay controls remain
   on their existing policy.
2. Require green CI, including hash-locked installs, full non-browser coverage,
   the complete browser matrix, template parsing, production runtime smoke, and
   PostgreSQL coverage.
3. Deploy to an isolated staging target using the existing backend selection.
   Do not switch a production JSON deployment to SQL merely to exercise a role.
4. In staging, repeat the owner/GM/editor/viewer route matrix and verify denied
   writes do not change file bytes or private values after publication.
5. Follow [the deployment runbook](../../DEPLOY.md): verify backups, record the
   last non-vulnerable revision, merge only through reviewed `main`, watch
   Railway build/readiness, and confirm the deployed revision.
6. Rerun production gates 1 through 8 below in order. A failure stops the
   sequence; preserve evidence and do not continue to restart or cleanup gates.

## Production rerun worksheet

| Gate | Pass evidence to record |
|---:|---|
| 1. Production/deploy baseline | Revision, deployment ID, authenticated health/storage flags, `persistence_proven`, and pre-test boot count. |
| 2. Normal-player draft/resume | Draft ID, saved/reopened name, and one non-name choice. |
| 3. Builder publication | First canonical character ID and exactly-one account/roster count. |
| 4. Synthetic import | Second canonical character ID and exactly-one account/roster count. |
| 5. Cross-account privacy | Player-B denial/absence for player-A draft, private marker, and raw JSON. |
| 6. Viewer/editor authorization | Active backend; editor gameplay success; private endpoint 403s; PF2e viewer read-only/no notes; Cosmere viewer denial. |
| 7. Backup and controlled restart | Backup/snapshot references, post-restart health, incremented boot count, and exact-once persistence for draft, two characters, and private marker. |
| 8. Cleanup and record | Named artifacts removed or an explicit list of safe leftovers. |

### 2026-10-06 rerun record

The complete sanitized evidence is in
[the acceptance record](pr5-acceptance-2026-10-01.md). Production stayed on
JSON authority; it was not switched to SQL merely to exercise assignments.

- Gates 1–5 passed against deployed revision
  `6514fc162d9f69bb0f0aa0c68da169eeec44c871`, including two normal-player
  workflows and cross-account draft, sheet, note, and raw-JSON denials.
- Gate 6 was N/A by configured backend. Forged JSON grants and SQL assignment
  boundaries remained covered by the green focused and PostgreSQL CI suites.
- Gate 7 passed with full backup `pr5-data-20261006T165629Z`, a controlled
  non-game restart of Railway deployment
  `8f341ce8-4b1b-49c1-8bae-a9410db7952d`, healthy post-restart readiness and
  storage, `persistence_proven=true`, and `boots_observed` increasing from 47
  to 48. The private draft, both canonical characters, and the opaque private
  marker all persisted.
- Gate 8 discarded only the named unpublished privacy draft. The two published
  synthetic characters, temporary Player B test account/campaign membership,
  one unused test invitation, and the opaque marker on the retained builder
  character remain
  because broader production deletion was not authorized.
- Final disposition: **ACCEPTED — all eight production gates completed on
  2026-10-06.**

## Stop and rollback rules

Stop immediately for any private-data disclosure, unexpected successful private
mutation, lost private field, cross-account access, duplicate publication,
unhealthy readiness, or persistence mismatch. Preserve the involved character,
workflow receipt, and deployment logs without copying private contents into the
incident record.

The previously deployed revision is known vulnerable under SQL editor
assignments and is not a safe automatic rollback target. If this remediation
must be withdrawn, contain delegated access or take the affected surface out of
service under operator control until a non-vulnerable build is available. Do
not change authority backends, delete journals, or discard files to manufacture
a passing result. Use a coordinated `DATA_DIR` backup and SQL snapshot as
described in [the deployment runbook](../../DEPLOY.md).
