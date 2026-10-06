# PR5 release acceptance: 2026-10-01

Revision: `e669facd5012dbab5603761121779217654cd0d7`, GitHub PR #162.

Latest disposition: the full 2026-10-06 rerun below accepted PR5 after the
private-notes remediation shipped.

## Verified

- Railway records deployment success for this revision, deployment
  `24bf34e6-6b1a-47ab-9c7b-fc69161d87ed`.
- Public `/live`, `/ready`, `/health`, login and both new workflow JS assets
  return HTTP 200. Anonymous account/character access requires login.
- Premerge CI: 3,197 passed, 38 skipped, 45 deselected, one existing expected
  failure; browser 32 passed; PostgreSQL 13 passed.
- Main run `36939168189` completed successfully: 3,197 regression tests passed,
  38 skipped, 45 deselected, one existing expected failure; browser 32 passed;
  PostgreSQL 13 passed. Production-only Gunicorn/gevent smoke and all 92 template
  parses also passed. Counts were checked against the completed job logs.
- Browser cases use isolated synthetic accounts and campaigns and cover both
  systems, both backends, desktop/mobile, drafts, imports, recovery and privacy.
- Local PR6A verification, not part of deployed `e669facd`: reproduced the
  handoff's Windows-only test issue. The PF2e adapter fixture
  crashed while decoding `player_builder.html` with cp1252. Its file read now
  explicitly uses UTF-8. This changes test portability, not application behavior.

## Production disposition update: 2026-10-05

**Status on 2026-10-05: BLOCKED — PR5 was not accepted.**

The production/deployment baseline and the focused, browser, template,
PostgreSQL, and full regression suites were healthy. During an out-of-order SQL
role-boundary preflight, however, an assigned editor directly exported and
overwrote an owner's private notes even though the rendered character sheet hid
the owner-private controls. This is a server-side authorization and privacy
defect, not a browser-control or presentation failure.

Acceptance stopped immediately. Gate 6 failed; gates 2 through 5 and gate 7 were
not completed, and gate 8 was not reached. Passing automated suites do not close
production acceptance. At that point, PR5 remained incomplete pending a
separate tested remediation and a complete rerun of all eight gates from gate 1.

## Eight-gate production sequence

Use named synthetic accounts, a named test draft, and named test characters in
an appropriate test campaign. Record identifiers without copying private note
contents into logs or screenshots.

| Gate | Required evidence | 2026-10-05 result |
|---:|---|---|
| 1. Production/deploy baseline | Confirm the expected `main` revision and Railway deployment, authenticated GM `/health`, healthy storage flags, `persistence_proven=true`, and record `boots_observed` before mutation. | Passed before the stop condition. |
| 2. Normal-player draft/resume | Save a player draft, observe Saved, refresh, return to `/me`, reopen it, and verify the name plus at least one non-name choice. | Not completed. |
| 3. Builder publication | Finish once, land on canonical `/characters/<id>`, and verify exactly one matching `Your Characters` entry. | Not completed. |
| 4. Synthetic import | Preview/import as a new private owned character, publish once, receive a second canonical ID, and verify exactly one matching account entry. | Not completed. |
| 5. Cross-account privacy | From player B, prove player A's unpublished draft is absent/denied, the private-note marker is absent, and non-owner raw JSON retrieval is denied. | Not completed. |
| 6. Viewer/editor authorization | Respect the active backend. JSON is a justified N/A for SQL assignments; never switch production authority merely for acceptance. In existing SQL mode, editor gameplay controls remain available but owner-private export/notes are denied; PF2e viewer is read-only and note-free; Cosmere viewer remains denied. | **Failed.** An assigned SQL editor could directly export and overwrite owner-private notes. |
| 7. Backup and controlled restart | Verify a full `DATA_DIR` backup and matching SQL snapshot when enabled, perform a non-game restart, confirm health and an incremented boot counter, then verify the draft, both characters, and private marker each persist exactly once. | Not completed. |
| 8. Cleanup and record | Discard only the named test draft; remove named test characters only when authorized and safe; record anything intentionally left behind. | Not reached. |

The remediation contract, local verification, deploy order, and rerun worksheet
are in [the private-notes remediation runbook](pr5-private-notes-remediation.md).
Do not restart production during an active game solely to complete this sequence.
Staging database cutover and production-volume rollback rehearsal remain separate
operator tasks. Deploying PR4B/PR5 alone does not enable SQL authority.

## Production acceptance rerun: 2026-10-06

**Status: ACCEPTED — all eight production gates completed on 2026-10-06.**

The rerun covered 2026-10-06 11:54–13:29 EDT / 15:54–17:29 UTC against
`https://tableview.up.railway.app`. The tested production revision was
`6514fc162d9f69bb0f0aa0c68da169eeec44c871`, merged in GitHub PR #166 and
served by Railway deployment `8f341ce8-4b1b-49c1-8bae-a9410db7952d`.
The designated PF2e campaign was `new`, ID
`5a41ad57e1ea4ae08a91ea69f81f6213`. Production remained on the documented
JSON ownership backend throughout the rerun.

The participating roles were a site administrator/campaign GM, Player A as a
normal non-GM campaign member and character owner, and Player B as a distinct
normal non-GM campaign member. Account identifiers, credentials, invitation
codes, private-note contents, storage paths, raw health output, and screenshots
are intentionally omitted.

| Gate | Sanitized production evidence |
|---:|---|
| 1. Production/deploy baseline | **PASS.** Production served the expected revision and active successful deployment. Authenticated pre-test health returned `status=healthy`, no configuration issues, storage configured / separate from the repository / writable, `persistence_proven=true`, and `boots_observed=47`; no encounter was active. Public `/live` returned `alive` and `/ready` returned `ready`. Current-main workflow `37480595240` and PR #166's test, browser, and PostgreSQL jobs were green. |
| 2. Normal-player draft/resume | **PASS.** Player A saved draft `26fb370c2f3a4ad3b3173267ebc54715`, named `PR5-Accept-Create-20261006-1216`, at `/player/builder?draft_id=26fb370c2f3a4ad3b3173267ebc54715`. The acknowledged draft preserved deity `Iomedae`, level 1, and Human ancestry across refresh, `/me`, and reopen; `/me` listed exactly one matching draft. |
| 3. Builder publication | **PASS.** The valid saved build published once and redirected to canonical character `87e2c82eefde4056be12fccc22650e35`. Its sheet showed the expected synthetic name, and `/me` showed exactly one matching Your Characters entry and no matching draft. |
| 4. Synthetic import | **PASS.** The prescribed Fighter / Human / level 1 payload created import draft `295db2189ef543bbb3254d1282bc64ad`, which previewed `PR5-Accept-Import-20261006-1216` as a private, unpublished new character owned by Player A. One confirmation redirected to canonical character `87a8ce36058d4d5c93d6bd824e9e71a6`; `/me` showed exactly one matching import and exactly one matching builder character. |
| 5. Cross-account privacy | **PASS.** Player A saved unpublished draft `40944d42062b46d38c00cb1da0b54888`, named `PR5-Accept-Private-20261006-1216`, and an opaque private-note marker. Player B could not discover the draft; its exact URL returned `draft_not_found` / `Draft not found.` without its name or values. The canonical character URL returned `character_access_required`, and plain-JSON export returned `character_owner_private_access_required` / `Character owner or campaign GM access required for private character data.` No private-note marker or note control was exposed. The marker value is intentionally omitted. |
| 6. Viewer/editor authorization | **N/A by configured backend.** Production used JSON authority, so live SQL viewer/editor assignments did not exist and SQL was not enabled for acceptance. Forged JSON grants and SQL authorization remained covered by the passing focused suite and PostgreSQL CI job. |
| 7. Backup and controlled restart | **PASS.** Before restart, the operator verified an access-controlled full-site data backup, sanitized ID `pr5-data-20261006T165629Z`, at 2026-10-06 13:08 EDT / 17:08 UTC. The archive contained 1,286 regular files, passed gzip and tar-readability checks, had no unsafe member names, and had SHA-256 `8a9a542e1acafdc0b9f4cedbb0dfc794a854b94ac16f8e7b13d06630be918882`. A SQL snapshot was N/A because JSON authority was active. With no active encounter, the existing release was restarted at 13:12 EDT / 17:12 UTC; Railway retained operation/deployment ID `8f341ce8-4b1b-49c1-8bae-a9410db7952d`. The sanitized recovery-log window, 17:12:42–17:13:58 UTC, showed orderly worker termination, volume mount, Gunicorn/gevent startup, and the expected catalog load without traceback. Post-restart `/live` and `/ready` passed. Sanitized health returned `status=healthy`, `configuration_issues=[]`, storage `configured=true`, `separate_from_repo=true`, `writable=true`, and `persistence_proven=true`; `boots_observed` increased from 47 to 48. Player A reopened the unpublished draft with the same name, level 1, and `Sarenrae`; both published characters existed exactly once; and the exact opaque note marker persisted. |
| 8. Cleanup and record | **PASS.** After evidence capture and explicit confirmation, only unpublished draft `40944d42062b46d38c00cb1da0b54888` was discarded. `/me` then omitted it and continued to show exactly one of each published synthetic character. The published characters were retained because their deletion was not separately authorized. The temporary Player B test account/campaign membership, one unused test invitation, and the opaque marker on the retained builder character also remain. No unrelated production data was changed. |

Closeout verification on the acceptance branch passed: all 92 templates parsed;
the focused PR5/privacy selection reported 418 passed and 14 skipped; the full
non-browser/non-PostgreSQL suite reported 3,347 passed, 39 skipped, 45
deselected, one expected xfail, and zero failures; and the browser suite
reported 32 passed. PostgreSQL authority was not enabled in production; the
repository's PostgreSQL CI job remained green. The temporary Railway SSH key
used for the operator-controlled backup was revoked and its local key files were
removed after use. The verified backup was retained outside the repository in
its access-controlled location.
